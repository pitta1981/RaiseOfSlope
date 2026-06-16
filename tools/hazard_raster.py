# -*- coding: utf-8 -*-
"""Accumulator for the Factor-of-Safety / slip-surface-depth hazard rasters.

Two paired single-band GeoTIFFs share the same grid:
 - the Factor-of-Safety (FoS) raster keeps, per cell, the *minimum* FoS among all
   slip surfaces whose ground trace crosses that cell;
 - the depth raster keeps the slip-surface depth associated with that minimum FoS.

The update rule is min-FoS: a cell is written only when the incoming FoS is lower
than the one already stored; FoS and depth are then updated together so the two
rasters stay consistent.

The class is intentionally free of any UI/QGIS-widget dependency (it only uses
numpy and GDAL, both shipped with QGIS) so it stays easy to test and reuse.
"""
import numpy as np

try:
    from osgeo import gdal, osr
    GDAL_AVAILABLE = True
except ImportError:  # pragma: no cover - GDAL is always present inside QGIS
    GDAL_AVAILABLE = False


class HazardRasterAccumulator:
    """Holds the FoS and depth grids and applies the per-cell min-FoS rule."""

    NODATA = -9999.0

    def __init__(self, geotransform, width, height, crs_wkt):
        self.geotransform = tuple(float(v) for v in geotransform)
        self.width = int(width)
        self.height = int(height)
        self.crs_wkt = crs_wkt or ''
        self.fos = np.full((self.height, self.width), self.NODATA, dtype=np.float32)
        self.depth = np.full((self.height, self.width), self.NODATA, dtype=np.float32)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def from_template(cls, extent, cell_size, crs_wkt):
        """Build an empty grid from an extent (x_min, y_min, x_max, y_max) and a cell size.

        The extent is expected in the same CRS as ``crs_wkt`` (the output CRS).
        """
        if not GDAL_AVAILABLE:
            raise RuntimeError("GDAL (osgeo) is not available")
        x_min, y_min, x_max, y_max = (float(v) for v in extent)
        cell = float(cell_size)
        if cell <= 0:
            raise ValueError("Cell size must be positive")
        if x_max <= x_min or y_max <= y_min:
            raise ValueError("Invalid output extent")

        width = max(1, int(np.ceil((x_max - x_min) / cell)))
        height = max(1, int(np.ceil((y_max - y_min) / cell)))
        # Top-left origin, north-up geotransform (negative y pixel size).
        geotransform = (x_min, cell, 0.0, y_max, 0.0, -cell)
        return cls(geotransform, width, height, crs_wkt)

    @classmethod
    def from_existing(cls, fos_path, depth_path):
        """Load two paired GeoTIFFs and verify they share the same grid/CRS."""
        if not GDAL_AVAILABLE:
            raise RuntimeError("GDAL (osgeo) is not available")
        fos_ds = gdal.Open(fos_path, gdal.GA_ReadOnly)
        depth_ds = gdal.Open(depth_path, gdal.GA_ReadOnly)
        if fos_ds is None:
            raise ValueError(f"Cannot open FoS raster: {fos_path}")
        if depth_ds is None:
            raise ValueError(f"Cannot open depth raster: {depth_path}")

        gt_f = tuple(fos_ds.GetGeoTransform())
        gt_d = tuple(depth_ds.GetGeoTransform())
        if (fos_ds.RasterXSize != depth_ds.RasterXSize or
                fos_ds.RasterYSize != depth_ds.RasterYSize or
                not np.allclose(gt_f, gt_d)):
            raise ValueError(
                "The FoS and depth rasters do not share the same grid "
                "(size or geotransform differ).")

        obj = cls(gt_f, fos_ds.RasterXSize, fos_ds.RasterYSize, fos_ds.GetProjection())

        fos_band = fos_ds.GetRasterBand(1)
        depth_band = depth_ds.GetRasterBand(1)
        obj.fos = fos_band.ReadAsArray().astype(np.float32)
        obj.depth = depth_band.ReadAsArray().astype(np.float32)

        # Normalise the source nodata values to our internal sentinel so the
        # min-FoS rule treats empty cells uniformly.
        for band, arr in ((fos_band, obj.fos), (depth_band, obj.depth)):
            src_nd = band.GetNoDataValue()
            if src_nd is not None:
                arr[arr == np.float32(src_nd)] = cls.NODATA
            arr[~np.isfinite(arr)] = cls.NODATA

        return obj

    def reset(self):
        """Clear all cells back to nodata (used for the 'overwrite' mode)."""
        self.fos.fill(self.NODATA)
        self.depth.fill(self.NODATA)

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------
    @property
    def cell_size(self):
        return abs(self.geotransform[1])

    def world_to_pixel(self, x, y):
        """Return (col, row) integer indices for a world coordinate, or None if outside."""
        ox, px_w, _, oy, _, px_h = self.geotransform
        col = int(np.floor((x - ox) / px_w))
        row = int(np.floor((y - oy) / px_h))
        if 0 <= col < self.width and 0 <= row < self.height:
            return col, row
        return None

    # ------------------------------------------------------------------
    # Accumulation
    # ------------------------------------------------------------------
    def accumulate_segment(self, xs, ys, fos, depths):
        """Burn one slip-surface trace into the grid with the min-FoS rule.

        ``xs``/``ys`` are world coordinates (in the output CRS) of points along the
        surface trace; ``depths`` is the slip-surface depth at each point; ``fos`` is
        the (scalar) Factor of Safety of the surface. Consecutive points are densified
        so that no cell along the trace is skipped.
        """
        xs = np.asarray(xs, dtype=float)
        ys = np.asarray(ys, dtype=float)
        depths = np.asarray(depths, dtype=float)
        try:
            fos = float(fos)
        except (TypeError, ValueError):
            return
        if not np.isfinite(fos):
            return
        if xs.size < 1:
            return

        step = max(self.cell_size * 0.5, 1e-9)
        for i in range(xs.size - 1):
            x0, y0, d0 = xs[i], ys[i], depths[i]
            x1, y1, d1 = xs[i + 1], ys[i + 1], depths[i + 1]
            if not (np.isfinite(x0) and np.isfinite(y0) and np.isfinite(x1) and np.isfinite(y1)):
                continue
            seg_len = float(np.hypot(x1 - x0, y1 - y0))
            n = max(1, int(np.ceil(seg_len / step)))
            for k in range(n + 1):
                t = k / float(n)
                self._write_cell(x0 + (x1 - x0) * t,
                                 y0 + (y1 - y0) * t,
                                 d0 + (d1 - d0) * t,
                                 fos)
        # Single-point trace: still burn the lone vertex.
        if xs.size == 1 and np.isfinite(xs[0]) and np.isfinite(ys[0]):
            self._write_cell(xs[0], ys[0], depths[0], fos)

    def _write_cell(self, x, y, depth, fos):
        idx = self.world_to_pixel(x, y)
        if idx is None:
            return
        col, row = idx
        cur = self.fos[row, col]
        if cur == self.NODATA or fos < cur:
            self.fos[row, col] = np.float32(fos)
            self.depth[row, col] = np.float32(depth if np.isfinite(depth) else self.NODATA)

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------
    def save(self, fos_path, depth_path):
        """Write the two grids as single-band Float32 GeoTIFFs with nodata + CRS."""
        if not GDAL_AVAILABLE:
            raise RuntimeError("GDAL (osgeo) is not available")
        self._write_geotiff(fos_path, self.fos)
        self._write_geotiff(depth_path, self.depth)

    def _write_geotiff(self, path, array):
        driver = gdal.GetDriverByName('GTiff')
        ds = driver.Create(path, self.width, self.height, 1, gdal.GDT_Float32,
                           options=['COMPRESS=LZW', 'TILED=YES'])
        if ds is None:
            raise RuntimeError(f"Cannot create raster: {path}")
        ds.SetGeoTransform(self.geotransform)
        if self.crs_wkt:
            srs = osr.SpatialReference()
            srs.ImportFromWkt(self.crs_wkt)
            ds.SetProjection(srs.ExportToWkt())
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(self.NODATA)
        band.WriteArray(array)
        band.FlushCache()
        ds = None
