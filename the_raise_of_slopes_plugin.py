# -*- coding: utf-8 -*-
"""Main entry point for QGIS plugin.

Implemented features:
 - Selection of two points on the DEM
 - Sampling of elevation profile along the line
 - Handling nodata and CRS transformation (project -> raster)
 - Graphic visualization and CSV export  
 - Stability analysis with Morgenstern & Price method using GLE framework
"""
import os
import sys
import numpy as np
import json
from datetime import datetime
from qgis.PyQt.QtGui import QIcon, QColor
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtCore import Qt
from qgis.core import (
    QgsPointXY,
    QgsCoordinateTransform,
    QgsProject,
    QgsGeometry,
    QgsWkbTypes,
)
from qgis.gui import QgsRubberBand


from .ui.profile_dialog import ProfileDialog
from .tools.point_selection_tool import TwoPointSelectionTool

# Import updated framework from external/gwf-le/src (no fallback to local copies)
plugin_dir = os.path.dirname(__file__)
gwf_root = os.path.join(plugin_dir, 'external', 'gwf-le')
gwf_src = os.path.join(gwf_root, 'src')
gwf_lem = os.path.join(gwf_src, 'LEM')
gwf_search = os.path.join(gwf_src, 'searchCriticalF')
for p in (gwf_root, gwf_src, gwf_lem, gwf_search):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from lemInterface import Soil, lemOptions, uniform_subdivision
    from gleMethods import bishop, morgerstern_price, spencer
    from circularSlipSurfaces import circularSlipSearchDomain
    from searchInterface import lemMethod, find_critical, simplex
    LIMIT_EQUILIBRIUM_AVAILABLE = True
except ImportError as e:
    LIMIT_EQUILIBRIUM_AVAILABLE = False
    print(f"Warning: limit-equilibrium modules not available: {e}")
    print(f"Expected paths: {gwf_lem} and {gwf_search}")


def classFactory(iface):  # QGIS will call this function
    return TheRaiseOfSlopesPlugin(iface)


class TheRaiseOfSlopesPlugin:
    def __init__(self, iface):
        """Initialize the plugin state."""
        self.iface = iface
        self.action = None
        self.dlg = None
        self.selection_tool = None
        self.profile_distances = []
        self.profile_elevations = []
        
        # Save profile data for subsequent calculations
        self.profile_p1 = None
        self.profile_p2 = None
        self.profile_raster_layer = None
        
        # Store ALL calculated critical slip surfaces (list of dict)
        # Each item: {'search': 'grid'|'simplex', 'method': 'Bishop'|..., 'x': np.array, 'y': np.array, 'fs': float}
        self.slip_surfaces = []
        
        # Rubber bands for visualization
        self.p1_rubber_band = None
        self.p2_rubber_band = None
        self.line_rubber_band = None

        # Rubberbands and labels on map for critical slip surfaces (plan view)
        self.surface_rubber_bands = []  # list of QgsRubberBand
        self.surface_label_items = []   # list of QGraphicsSimpleTextItem or annotation items

    def initGui(self):
        self.action = QAction(QIcon(self._icon_path()), "The Raise Of Slopes - Profile", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("TheRaiseOfSlopes", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("TheRaiseOfSlopes", self.action)
        self._restore_map_tool()
        self._clear_rubber_bands()

    def run(self):
        """Show the main dialog."""
        if not self.dlg:
            self.dlg = ProfileDialog()
            self.dlg.set_plugin(self)  # Pass the plugin reference
            self.dlg.startSelectionRequested.connect(self._start_point_selection)
            self.dlg.computeProfileRequested.connect(self._compute_profile)
            self.dlg.exportRequested.connect(self._export_profile)
            # Connections to new export and project save signals
            self.dlg.exportResultsRequested.connect(self._export_results)
            self.dlg.exportImageRequested.connect(self._export_profile_image)
            self.dlg.exportDxfRequested.connect(self._export_profile_dxf)
            self.dlg.saveProjectRequested.connect(self._save_project)
            self.dlg.loadProjectRequested.connect(self._load_project)
            self.dlg.gridStabilityAnalysisRequested.connect(self._analyze_grid_stability)
            self.dlg.simplexStabilityAnalysisRequested.connect(self._analyze_simplex_stability)
            self.dlg.clearSurfacesRequested.connect(self._clear_surfaces)
        self.dlg.show()
        self.dlg.raise_()

    def _icon_path(self):
        return os.path.join(os.path.dirname(__file__), 'icon.png')

    def _start_point_selection(self):
        """Activate the map tool to collect two user clicks."""
        self._clear_rubber_bands()
        
        self.selection_tool = TwoPointSelectionTool(self.iface.mapCanvas())
        self.selection_tool.firstPointSelected.connect(self._on_first_point_selected)
        self.selection_tool.pointsSelected.connect(self._on_points_selected)
        self.iface.mapCanvas().setMapTool(self.selection_tool)
        self.dlg.setStatus("Select the first point on the DEM...")

    def _restore_map_tool(self):
        self.iface.mapCanvas().unsetMapTool(self.selection_tool)
        self.selection_tool = None
    
    def _clear_rubber_bands(self):
        """Remove rubber bands from the map."""
        if self.p1_rubber_band:
            try:
                self.iface.mapCanvas().scene().removeItem(self.p1_rubber_band)
            except Exception:
                pass
            self.p1_rubber_band = None
        if self.p2_rubber_band:
            try:
                self.iface.mapCanvas().scene().removeItem(self.p2_rubber_band)
            except Exception:
                pass
            self.p2_rubber_band = None
        if self.line_rubber_band:
            try:
                self.iface.mapCanvas().scene().removeItem(self.line_rubber_band)
            except Exception:
                pass
            self.line_rubber_band = None

        # Rimuovi le rubber bands delle superfici in pianta
        for rb in list(self.surface_rubber_bands):
            try:
                self.iface.mapCanvas().scene().removeItem(rb)
            except Exception:
                try:
                    rb.reset(True)
                except Exception:
                    pass
        self.surface_rubber_bands = []

        # Rimuovi le etichette delle superfici (se presenti)
        for ti in list(self.surface_label_items):
            try:
                self.iface.mapCanvas().scene().removeItem(ti)
            except Exception:
                try:
                    del ti
                except Exception:
                    pass
        self.surface_label_items = []
    
    def _on_first_point_selected(self, p1):
        """Handle the first point selection."""
        self._clear_rubber_bands()
        
        self.p1_rubber_band = QgsRubberBand(self.iface.mapCanvas(), QgsWkbTypes.PointGeometry)
        self.p1_rubber_band.setColor(QColor(255, 0, 0))
        self.p1_rubber_band.setIconSize(15)
        self.p1_rubber_band.addPoint(p1)
        
        self.dlg.setStatus("First point (P1) selected. Select the second point (P2)...")

    def _create_placeholder(self, p1, p2):
        """Create a visual placeholder on the map."""
        self.p2_rubber_band = QgsRubberBand(self.iface.mapCanvas(), QgsWkbTypes.PointGeometry)
        self.p2_rubber_band.setColor(QColor(0, 255, 0))
        self.p2_rubber_band.setIconSize(12)
        self.p2_rubber_band.addPoint(p2)
        
        self.line_rubber_band = QgsRubberBand(self.iface.mapCanvas(), QgsWkbTypes.LineGeometry)
        self.line_rubber_band.setColor(QColor(0, 0, 255))
        self.line_rubber_band.setWidth(2)
        self.line_rubber_band.addPoint(p1)
        self.line_rubber_band.addPoint(p2)

    def _on_points_selected(self, p1, p2):
        self.dlg.setSelectedPoints(p1, p2)
        self._create_placeholder(p1, p2)
        self._restore_map_tool()
        
        # Automatically compute the profile
        raster_layer = self.dlg.cboRaster.currentData()
        if raster_layer:
            self.dlg.setStatus("Automatic profile calculation running...")
            self._compute_profile(raster_layer, p1, p2)
        else:
            self.dlg.setStatus("Points selected. Choose a DEM raster and press 'Compute profile'.")

    def _compute_profile(self, raster_layer, p1, p2):
        """Sample the elevation profile between the two points."""
        if not raster_layer or not p1 or not p2:
            self.dlg.setStatus("Missing parameters for the profile.")
            return
        
        # Save profile data for subsequent calculations
        self.profile_p1 = p1
        self.profile_p2 = p2
        self.profile_raster_layer = raster_layer
            
        provider = raster_layer.dataProvider()
        extent_length = p1.distance(p2)
        if extent_length == 0:
            self.dlg.setStatus("The two points coincide.")
            return
            
        px = raster_layer.rasterUnitsPerPixelX()
        py = raster_layer.rasterUnitsPerPixelY()
        step = (abs(px) + abs(py)) / 2.0
        if step <= 0:
            step = extent_length / 100.0
            
        n = int(extent_length / step) + 1
        distances = []
        elevations = []
        band = 1
        no_data = provider.sourceNoDataValue(band)
        
        raster_crs = raster_layer.crs()
        project_crs = QgsProject.instance().crs()
        need_transform = project_crs.isValid() and raster_crs.isValid() and (project_crs != raster_crs)
        transformer = None
        if need_transform:
            transformer = QgsCoordinateTransform(project_crs, raster_crs, QgsProject.instance())
            
        for i in range(n + 1):
            d = min(i * step, extent_length)
            t = d / extent_length
            x = p1.x() + (p2.x() - p1.x()) * t
            y = p1.y() + (p2.y() - p1.y()) * t
            pt = QgsPointXY(x, y)
            if transformer is not None:
                try:
                    pt = transformer.transform(pt)
                except Exception:
                    distances.append(d)
                    elevations.append(None)
                    continue
            val = self._sample_with_bilinear(provider, pt, band, no_data, raster_layer)
            distances.append(d)
            elevations.append(val)
            
        self.profile_distances = distances
        self.profile_elevations = elevations
        
        # Clear previous critical slip surfaces (new profile)
        self.slip_surfaces = []
        
        self.dlg.updateProfile(distances, elevations)
        self.dlg.setProfileDistances(distances)  # Save distances in dialog
        self.dlg.setStatus("Profile computed: {} points.".format(len(distances)))

    def _export_profile(self, path):
        """Export the profile as CSV."""
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write('distance,elevation\n')
                for d, z in zip(self.profile_distances, self.profile_elevations):
                    f.write(f"{d},{z}\n")
            self.dlg.setStatus(f"Profile saved: {path}")
        except Exception as e:
            self.dlg.setStatus(f"Save error: {e}")

    def _export_results(self, path):
        """Export text results (grid and simplex) to a text file."""
        try:
            timestamp = datetime.utcnow().isoformat() + 'Z'
            header = [f"The Raise Of Slopes - Exported results: {timestamp}"]
            if self.profile_p1 and self.profile_p2:
                header.append(f"P1: ({self.profile_p1.x():.3f}, {self.profile_p1.y():.3f})  P2: ({self.profile_p2.x():.3f}, {self.profile_p2.y():.3f})")
            if self.profile_raster_layer is not None:
                try:
                    header.append(f"Raster: {self.profile_raster_layer.name()}")
                except Exception:
                    header.append("Raster: <unknown>")

            # Stratigrafia
            try:
                strat_info = self._format_stratigraphy_info(self.dlg._get_stratigraphy_params())
            except Exception:
                strat_info = ''

            grid_text = ''
            simplex_text = ''
            try:
                grid_text = self.dlg.grid_results_text.toPlainText() if hasattr(self.dlg, 'grid_results_text') else ''
                simplex_text = self.dlg.simplex_results_text.toPlainText() if hasattr(self.dlg, 'simplex_results_text') else ''
            except Exception:
                pass

            # Compose content
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(header) + '\n\n')
                if strat_info:
                    f.write('STRATIGRAPHY:\n')
                    f.write(strat_info + '\n\n')
                f.write('--- GRID RESULTS ---\n')
                f.write(grid_text + '\n\n')
                f.write('--- SIMPLEX RESULTS ---\n')
                f.write(simplex_text + '\n\n')

                # Surface summary
                f.write('--- CALCULATED SURFACES ---\n')
                for s in self.slip_surfaces:
                    try:
                        f.write(f"- {s.get('search')} / {s.get('method')}: FS={s.get('fs'):.4f}, points={len(s.get('x', []))}\n")
                    except Exception:
                        f.write(f"- {s}\n")

                # Include profile as CSV at the end of the file
                f.write('\n--- PROFILE (CSV) ---\n')
                f.write('distance,elevation\n')
                for d, z in zip(self.profile_distances, self.profile_elevations):
                    f.write(f"{d},{z}\n")

            self.dlg.setStatus(f"Results saved: {path}")
        except Exception as e:
            import traceback
            self.dlg.setStatus(f"Error saving results: {e}")
            print(traceback.format_exc())

    def _export_profile_image(self, fmt, path):
        """Export profile image using Qt-native renderer from the dialog."""
        try:
            if not self.dlg or not hasattr(self.dlg, 'export_profile_image'):
                raise RuntimeError('Profile canvas not available')

            include_legend = True
            try:
                include_legend = self.dlg.include_legend_in_export()
            except Exception:
                include_legend = True

            self.dlg.export_profile_image(fmt=fmt, path=path, include_legend=include_legend)
            self.dlg.setStatus(f"Image saved: {path}")
        except Exception as e:
            import traceback
            self.dlg.setStatus(f"Error exporting image: {e}")
            print(traceback.format_exc())

    def _export_profile_dxf(self, path):
        """Export the profile and surfaces as DXF. Uses ezdxf if installed, otherwise writes a minimal ASCII DXF."""
        try:
            import math
            import re

            def _finite(v):
                try:
                    return v is not None and math.isfinite(float(v))
                except Exception:
                    return False

            def _hex_to_rgb(hex_color):
                if not hex_color:
                    return (0, 0, 0)
                c = str(hex_color).strip()
                m = re.fullmatch(r"#?([0-9a-fA-F]{6})", c)
                if not m:
                    return (0, 0, 0)
                h = m.group(1)
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

            def _rgb_to_aci(r, g, b):
                palette = [
                    (1, (255, 0, 0)),
                    (2, (255, 255, 0)),
                    (3, (0, 255, 0)),
                    (4, (0, 255, 255)),
                    (5, (0, 0, 255)),
                    (6, (255, 0, 255)),
                    (7, (255, 255, 255)),
                ]
                best = 7
                best_d = None
                for aci, (pr, pg, pb) in palette:
                    d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
                    if best_d is None or d < best_d:
                        best_d = d
                        best = aci
                return best

            points = [(float(d), float(z)) for d, z in zip(self.profile_distances, self.profile_elevations) if _finite(d) and _finite(z)]
            if not points:
                raise ValueError('No valid points to export DXF')

            min_x, max_x = float(min(p[0] for p in points)), float(max(p[0] for p in points))
            min_y, max_y = float(min(p[1] for p in points)), float(max(p[1] for p in points))
            x_span = max(max_x - min_x, 1.0)
            y_span = max(max_y - min_y, 1.0)

            legend_x0 = min_x + 0.02 * x_span
            legend_y0 = max_y + 0.06 * y_span
            legend_dy = 0.04 * y_span
            legend_line_len = 0.06 * x_span
            legend_text_h = 0.02 * y_span

            try:
                import ezdxf
                from ezdxf import colors as ezcolors
                import ezdxf.enums
                doc = ezdxf.new(dxfversion='R2010')
                msp = doc.modelspace()
                # Add profile as LWPOLYLINE
                msp.add_lwpolyline(points, dxfattribs={'layer': 'PROFILE'})
                if 'LEGEND' not in doc.layers:
                    doc.layers.new('LEGEND')

                # Legend title
                msp.add_text(
                    'Slip surfaces legend',
                    dxfattribs={'layer': 'LEGEND', 'height': legend_text_h}
                ).set_placement((legend_x0, legend_y0), align=ezdxf.enums.TextEntityAlignment.LEFT)

                # Add surfaces (if present)
                legend_row = 1
                for idx, s in enumerate(self.slip_surfaces, start=1):
                    pts = []
                    xs = s.get('x')
                    ys = s.get('y')
                    if xs is None or ys is None:
                        continue
                    for x, y in zip(xs, ys):
                        if _finite(x) and _finite(y):
                            pts.append((float(x), float(y)))
                    if pts:
                        lname = f'SURFACE_{idx}'
                        if lname not in doc.layers:
                            doc.layers.new(lname)

                        color_hex = None
                        try:
                            color_hex = self._get_color_for_surface(idx - 1, s.get('search'), s.get('method'))
                        except Exception:
                            color_hex = None
                        r, g, b = _hex_to_rgb(color_hex)
                        true_color = ezcolors.rgb2int(r, g, b)

                        msp.add_lwpolyline(pts, dxfattribs={'layer': lname, 'true_color': true_color})

                        # Legend row (segment + text)
                        y = legend_y0 - (legend_row * legend_dy)
                        fs = s.get('fs')
                        search = s.get('search') or ''
                        method = s.get('method') or ''
                        if isinstance(fs, (int, float)) and _finite(fs):
                            label = f"{idx}. {search}/{method}  FS={float(fs):.3f}"
                        else:
                            label = f"{idx}. {search}/{method}"

                        msp.add_line(
                            (legend_x0, y),
                            (legend_x0 + legend_line_len, y),
                            dxfattribs={'layer': 'LEGEND', 'true_color': true_color}
                        )
                        msp.add_text(
                            label,
                            dxfattribs={'layer': 'LEGEND', 'height': legend_text_h, 'true_color': true_color}
                        ).set_placement(
                            (legend_x0 + legend_line_len + 0.01 * x_span, y - 0.5 * legend_text_h),
                            align=ezdxf.enums.TextEntityAlignment.LEFT
                        )
                        legend_row += 1
                doc.saveas(path)
                self.dlg.setStatus(f"DXF saved: {path}")
                return
            except Exception:
                # If ezdxf is not available, write a minimal ASCII DXF
                def _write_lwpolyline(f, layer, pts, closed=True, aci=None):
                    f.write('  0\nLWPOLYLINE\n')
                    f.write(f'  8\n{layer}\n')
                    if aci is not None:
                        f.write(f' 62\n{int(aci)}\n')
                    f.write(f' 90\n{len(pts)}\n')
                    f.write(f' 70\n{1 if closed else 0}\n')
                    for x, y in pts:
                        f.write(f' 10\n{x}\n 20\n{y}\n')

                def _write_line(f, layer, x1, y1, x2, y2, aci=None):
                    f.write('  0\nLINE\n')
                    f.write(f'  8\n{layer}\n')
                    if aci is not None:
                        f.write(f' 62\n{int(aci)}\n')
                    f.write(f' 10\n{x1}\n 20\n{y1}\n 30\n0.0\n')
                    f.write(f' 11\n{x2}\n 21\n{y2}\n 31\n0.0\n')

                def _write_text(f, layer, x, y, text, height, aci=None):
                    f.write('  0\nTEXT\n')
                    f.write(f'  8\n{layer}\n')
                    if aci is not None:
                        f.write(f' 62\n{int(aci)}\n')
                    f.write(f' 10\n{x}\n 20\n{y}\n 30\n0.0\n')
                    f.write(f' 40\n{height}\n')
                    f.write(f'  1\n{text}\n')

                with open(path, 'w', encoding='utf-8') as f:
                    # HEADER
                    f.write('  0\nSECTION\n  2\nHEADER\n  0\nENDSEC\n')

                    # TABLES (layers)
                    f.write('  0\nSECTION\n  2\nTABLES\n')
                    f.write('  0\nTABLE\n  2\nLAYER\n')
                    f.write('  0\nLAYER\n  2\nPROFILE\n 70\n0\n 62\n7\n  6\nCONTINUOUS\n')
                    f.write('  0\nLAYER\n  2\nLEGEND\n 70\n0\n 62\n7\n  6\nCONTINUOUS\n')
                    for idx, s in enumerate(self.slip_surfaces, start=1):
                        color_hex = None
                        try:
                            color_hex = self._get_color_for_surface(idx - 1, s.get('search'), s.get('method'))
                        except Exception:
                            color_hex = None
                        r, g, b = _hex_to_rgb(color_hex)
                        aci = _rgb_to_aci(r, g, b)
                        f.write(f'  0\nLAYER\n  2\nSURFACE_{idx}\n 70\n0\n 62\n{aci}\n  6\nCONTINUOUS\n')
                    f.write('  0\nENDTAB\n  0\nENDSEC\n')

                    # ENTITIES
                    f.write('  0\nSECTION\n  2\nENTITIES\n')

                    # Profile
                    _write_lwpolyline(f, 'PROFILE', points, closed=True, aci=7)

                    # Legend title
                    _write_text(f, 'LEGEND', legend_x0, legend_y0, 'Slip surfaces legend', legend_text_h, aci=7)

                    # Surfaces + legend rows
                    legend_row = 1
                    for idx, s in enumerate(self.slip_surfaces, start=1):
                        xs = s.get('x')
                        ys = s.get('y')
                        if xs is None or ys is None:
                            continue
                        pts = [(float(x), float(y)) for x, y in zip(xs, ys) if _finite(x) and _finite(y)]
                        if len(pts) < 2:
                            continue

                        color_hex = None
                        try:
                            color_hex = self._get_color_for_surface(idx - 1, s.get('search'), s.get('method'))
                        except Exception:
                            color_hex = None
                        r, g, b = _hex_to_rgb(color_hex)
                        aci = _rgb_to_aci(r, g, b)

                        _write_lwpolyline(f, f'SURFACE_{idx}', pts, closed=True, aci=aci)

                        y = legend_y0 - (legend_row * legend_dy)
                        _write_line(f, 'LEGEND', legend_x0, y, legend_x0 + legend_line_len, y, aci=aci)
                        fs = s.get('fs')
                        search = s.get('search') or ''
                        method = s.get('method') or ''
                        if isinstance(fs, (int, float)) and _finite(fs):
                            label = f"{idx}. {search}/{method}  FS={float(fs):.3f}"
                        else:
                            label = f"{idx}. {search}/{method}"
                        _write_text(f, 'LEGEND', legend_x0 + legend_line_len + 0.01 * x_span, y - 0.5 * legend_text_h, label, legend_text_h, aci=aci)
                        legend_row += 1

                    f.write('  0\nENDSEC\n  0\nEOF\n')
                self.dlg.setStatus(f"Minimal DXF saved: {path}")
        except Exception as e:
            import traceback
            self.dlg.setStatus(f"Error exporting DXF: {e}")
            print(traceback.format_exc())

    def _sanitize_float(self, v):
        """Convert to float or return None if not finite (NaN, Inf) or not convertible."""
        try:
            fv = float(v)
            if not np.isfinite(fv):
                return None
            return fv
        except Exception:
            return None

    def _jsonable(self, obj):
        """Convert objects (incl. QGIS layers) into something JSON-serializable."""
        if obj is None:
            return None
        if isinstance(obj, (bool, int, str)):
            return obj
        if isinstance(obj, float):
            return self._sanitize_float(obj)

        # Numpy scalars
        try:
            if isinstance(obj, (np.floating,)):
                return self._sanitize_float(obj)
            if isinstance(obj, (np.integer,)):
                return int(obj)
        except Exception:
            pass

        # Liste/tuple
        if isinstance(obj, (list, tuple)):
            return [self._jsonable(v) for v in obj]

        # Dict
        if isinstance(obj, dict):
            return {str(k): self._jsonable(v) for k, v in obj.items()}

        # QGIS layer (es. QgsRasterLayer): salviamo id + name (se disponibili)
        try:
            has_name = hasattr(obj, 'name') and callable(getattr(obj, 'name'))
            has_id = hasattr(obj, 'id') and callable(getattr(obj, 'id'))
            if has_name or has_id:
                layer_name = obj.name() if has_name else None
                layer_id = obj.id() if has_id else None
                # Preferiamo una struttura esplicita per poter ricostruire in futuro
                return {'__qgis_layer__': {'name': layer_name, 'id': layer_id}}
        except Exception:
            pass

        # Fallback: stringa
        try:
            return str(obj)
        except Exception:
            return None

    def _gather_project_state(self):
        """Raccoglie lo stato corrente del plugin in un dict serializzabile.
        Sanitizza i numeri per evitare NaN/Infinity che renderebbero il JSON non valido."""
        state = {}
        state['version'] = 1
        state['timestamp'] = datetime.utcnow().isoformat() + 'Z'

        # Profili: converti i numeri in valori JSON-validi (None per valori non-finito)
        distances_clean = [self._sanitize_float(d) for d in (self.profile_distances or [])]
        elevations_clean = [ (None if e is None else self._sanitize_float(e)) for e in (self.profile_elevations or [])]

        state['profile'] = {
            'distances': distances_clean,
            'elevations': elevations_clean,
            'p1': [self.profile_p1.x(), self.profile_p1.y()] if self.profile_p1 else None,
            'p2': [self.profile_p2.x(), self.profile_p2.y()] if self.profile_p2 else None,
            'raster': self.profile_raster_layer.name() if self.profile_raster_layer is not None else None
        }

        # Main parameters
        params = {
            'gamma': float(self.dlg.gamma_spinbox.value()),
            'cohesion': float(self.dlg.cohesion_spinbox.value()),
            'porosity': float(self.dlg.porosity_spinbox.value()),
            'friction_angle': float(self.dlg.friction_angle_spinbox.value()),
            'num_slices': int(self.dlg.slices_spinbox.value()),
            'depth_factor': float(self.dlg.depth_factor_spinbox.value())
        }
        # Stratigraphy and water table
        params.update(self.dlg._get_stratigraphy_params())
        # Grid & Simplex params
        try:
            params.update({
                'grid_method': self.dlg.grid_method_combo.currentText(),
                'num_in_pts': int(self.dlg.num_in_pts_spinbox.value()),
                'num_out_pts': int(self.dlg.num_out_pts_spinbox.value()),
                'min_eta_inc': float(self.dlg.min_eta_inc_spinbox.value()),
                'in_interval_min': float(self.dlg.in_interval_min_spinbox.value()),
                'in_interval_max': float(self.dlg.in_interval_max_spinbox.value()),
                'out_interval_min': float(self.dlg.out_interval_min_spinbox.value()),
                'out_interval_max': float(self.dlg.out_interval_max_spinbox.value()),
                'simplex_method': self.dlg.simplex_method_combo.currentText(),
                'x_in_min': float(self.dlg.x_in_min_spinbox.value()),
                'x_in_max': float(self.dlg.x_in_max_spinbox.value()),
                'x_out_min': float(self.dlg.x_out_min_spinbox.value()),
                'x_out_max': float(self.dlg.x_out_max_spinbox.value()),
                'eta_min': float(self.dlg.eta_min_spinbox.value()),
                'eta_max': float(self.dlg.eta_max_spinbox.value()),
                'max_iterations': int(self.dlg.max_iterations_spinbox.value())
            })
        except Exception:
            pass
        # Rende serializzabile (evita QgsRasterLayer non JSON serializable)
        state['params'] = self._jsonable(params)

        # Calculated surfaces: also save those already visualized
        # Note: to avoid JSON issues (NaN/None), save only finite (x,y) pairs.
        stored = []
        for s in self.slip_surfaces:
            try:
                xs_raw = s.get('x') or []
                ys_raw = s.get('y') or []
                xs = []
                ys = []
                for xi, yi in zip(xs_raw, ys_raw):
                    x_f = self._sanitize_float(xi)
                    y_f = self._sanitize_float(yi)
                    if x_f is None or y_f is None:
                        continue
                    xs.append(x_f)
                    ys.append(y_f)
                stored.append({
                    'search': s.get('search'),
                    'method': s.get('method'),
                    'fs': self._sanitize_float(s.get('fs')),
                    'x': xs,
                    'y': ys
                })
            except Exception:
                continue
        state['slip_surfaces'] = stored

        # Stato UI: quali superfici sono visibili (per ripristinare la stessa situazione)
        try:
            visible_indices = []
            if hasattr(self.dlg, 'surface_visibility_list') and self.dlg.surface_visibility_list is not None:
                for i in range(self.dlg.surface_visibility_list.count()):
                    try:
                        if self.dlg.surface_visibility_list.item(i).checkState() == 2:
                            visible_indices.append(i)
                    except Exception:
                        continue
            state['ui'] = {'visible_surface_indices': visible_indices}
        except Exception:
            state['ui'] = {'visible_surface_indices': []}

        # Result texts
        try:
            state['results'] = {
                'grid': self.dlg.grid_results_text.toPlainText(),
                'simplex': self.dlg.simplex_results_text.toPlainText()
            }
        except Exception:
            state['results'] = {'grid': '', 'simplex': ''}

        return state

    def _save_project(self, path):
        """Save the current state to a .rslope file (JSON)."""
        try:
            state = self._gather_project_state()
            with open(path, 'w', encoding='utf-8') as f:
                # allow_nan=False rende il JSON strettamente valido
                json.dump(state, f, ensure_ascii=False, indent=2, allow_nan=False)
            self.dlg.setStatus(f"Project saved: {path}")
        except Exception as e:
            import traceback
            self.dlg.setStatus(f"Error saving project: {e}")
            print(traceback.format_exc())

    def _load_project(self, path):
        """Carica uno stato da file .rslope e ripristina l'interfaccia."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                txt = f.read()
            try:
                state = json.loads(txt)
            except ValueError:
                # JSON invalido (spesso per token NaN/Infinity): tentiamo un fallback sostituendo NaN/Infinity con null
                cleaned = txt.replace('NaN', 'null').replace('Infinity', 'null').replace('-Infinity', 'null')
                try:
                    state = json.loads(cleaned)
                    cleaned_flag = True
                    print("Warning: JSON pulito sostituendo NaN/Infinity con null durante il caricamento del progetto.")
                except Exception:
                    raise

            # Profiles (read but don't apply until user confirms)
            prof = state.get('profile', {})
            distances = prof.get('distances', [])
            elevations = prof.get('elevations', [])
            p1 = prof.get('p1')
            p2 = prof.get('p2')
            raster_name = prof.get('raster')

            # Helper to resolve profile raster by name (if present)
            resolved_profile_raster = None
            if raster_name:
                for lyr in QgsProject.instance().mapLayers().values():
                    try:
                        if lyr.name() == raster_name:
                            resolved_profile_raster = lyr
                            break
                    except Exception:
                        continue

            def _resolve_layer_ref(layer_ref):
                """Resolve a saved layer reference in .rslope to a QgsMapLayer in the project (or None)."""
                if not layer_ref:
                    return None
                layer_name = None
                layer_id = None
                if isinstance(layer_ref, str):
                    layer_name = layer_ref
                elif isinstance(layer_ref, dict) and '__qgis_layer__' in layer_ref:
                    info = layer_ref.get('__qgis_layer__') or {}
                    layer_name = info.get('name')
                    layer_id = info.get('id')
                else:
                    # fallback
                    try:
                        layer_name = str(layer_ref)
                    except Exception:
                        layer_name = None

                # Prima prova per id
                if layer_id:
                    try:
                        lyr = QgsProject.instance().mapLayer(layer_id)
                        if lyr is not None:
                            return lyr
                    except Exception:
                        pass

                # Poi per name
                if layer_name:
                    for lyr in QgsProject.instance().mapLayers().values():
                        try:
                            if lyr.name() == layer_name:
                                return lyr
                        except Exception:
                            continue
                return None

            # Parameters (also used to detect missing layers)
            params = state.get('params', {})

            # Rileva layer mancanti e chiedi conferma all'utente (in inglese)
            missing = []
            if raster_name and resolved_profile_raster is None:
                missing.append(f"Profile raster layer: {raster_name}")

            try:
                if params.get('enable_layer2') and int(params.get('layer2_definition_mode', 0)) == 1:
                    layer2_ref = params.get('layer2_raster_layer')
                    if layer2_ref and _resolve_layer_ref(layer2_ref) is None:
                        # Try to extract a nice name
                        lname = None
                        if isinstance(layer2_ref, dict) and '__qgis_layer__' in layer2_ref:
                            lname = (layer2_ref.get('__qgis_layer__') or {}).get('name')
                        if isinstance(layer2_ref, str):
                            lname = layer2_ref
                        missing.append(f"Layer 2 raster layer: {lname or '<unknown>'}")
            except Exception:
                pass

            try:
                if params.get('enable_water') and int(params.get('water_definition_mode', 0)) == 1:
                    water_ref = params.get('water_raster_layer')
                    if water_ref and _resolve_layer_ref(water_ref) is None:
                        wname = None
                        if isinstance(water_ref, dict) and '__qgis_layer__' in water_ref:
                            wname = (water_ref.get('__qgis_layer__') or {}).get('name')
                        if isinstance(water_ref, str):
                            wname = water_ref
                        missing.append(f"Water table raster layer: {wname or '<unknown>'}")
            except Exception:
                pass

            if missing:
                try:
                    from qgis.PyQt.QtWidgets import QMessageBox
                    details = "\n".join(f"- {m}" for m in missing)
                    msg = (
                        "Some referenced layers were not found in the current QGIS project:\n\n"
                        f"{details}\n\n"
                        "The profile will be restored using the sampled profile data saved inside the .rslope file.\n"
                        "Do you want to continue loading the project anyway?"
                    )
                    choice = QMessageBox.question(
                        self.dlg,
                        "Missing layers",
                        msg,
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.Yes,
                    )
                    if choice != QMessageBox.Yes:
                        self.dlg.setStatus("Project load cancelled by user.")
                        return
                except Exception as e:
                    # If we can't prompt, continue but warn in status
                    print(f"Warning: could not show missing-layers prompt: {e}")

            # At this point the user has accepted (or there were no missing layers): apply the state
            self.profile_distances = distances
            self.profile_elevations = elevations

            if p1 and p2:
                self.profile_p1 = QgsPointXY(p1[0], p1[1])
                self.profile_p2 = QgsPointXY(p2[0], p2[1])
                self.dlg.setSelectedPoints(self.profile_p1, self.profile_p2)
                # Draw the segment on the map as if it had been selected by hand
                try:
                    self._clear_rubber_bands()
                    self._create_placeholder(self.profile_p1, self.profile_p2)
                except Exception as e:
                    print(f"Warning: could not draw profile segment on map: {e}")

            # Profile raster (if found)
            if resolved_profile_raster is not None:
                self.profile_raster_layer = resolved_profile_raster
                for i in range(self.dlg.cboRaster.count()):
                    if self.dlg.cboRaster.itemData(i) == resolved_profile_raster:
                        self.dlg.cboRaster.setCurrentIndex(i)
                        break
            else:
                self.profile_raster_layer = None
            try:
                self.dlg.gamma_spinbox.setValue(params.get('gamma', self.dlg.gamma_spinbox.value()))
                self.dlg.cohesion_spinbox.setValue(params.get('cohesion', self.dlg.cohesion_spinbox.value()))
                self.dlg.porosity_spinbox.setValue(params.get('porosity', self.dlg.porosity_spinbox.value()))
                self.dlg.friction_angle_spinbox.setValue(params.get('friction_angle', self.dlg.friction_angle_spinbox.value()))
                self.dlg.slices_spinbox.setValue(params.get('num_slices', self.dlg.slices_spinbox.value()))
                self.dlg.depth_factor_spinbox.setValue(params.get('depth_factor', self.dlg.depth_factor_spinbox.value()))
            except Exception:
                pass

            # Stratigrafia: proviamo a impostare i valori se presenti
            try:
                if params.get('enable_layer2'):
                    self.dlg.enable_layer2_checkbox.setChecked(True)
                    mode = params.get('layer2_definition_mode', 0)
                    self.dlg.layer2_definition_group.button(mode).setChecked(True)
                    if mode == 0:
                        self.dlg.layer2_const_depth_spinbox.setValue(params.get('layer2_const_depth', 5.0))
                    elif mode == 1 and params.get('layer2_raster_layer'):
                        lyr = _resolve_layer_ref(params.get('layer2_raster_layer'))
                        if lyr is not None:
                            for i in range(self.dlg.layer2_raster_combo.count()):
                                item_lyr = self.dlg.layer2_raster_combo.itemData(i)
                                if item_lyr is not None and item_lyr == lyr:
                                    self.dlg.layer2_raster_combo.setCurrentIndex(i)
                                    break
                    elif mode == 2:
                        self.dlg.layer2_elevation_spinbox.setValue(params.get('layer2_elevation', 0.0))
                    self.dlg.gamma_2_spinbox.setValue(params.get('gamma_2', self.dlg.gamma_2_spinbox.value()))
                    self.dlg.cohesion_2_spinbox.setValue(params.get('cohesion_2', self.dlg.cohesion_2_spinbox.value()))
                    self.dlg.porosity_2_spinbox.setValue(params.get('porosity_2', self.dlg.porosity_2_spinbox.value()))
                    self.dlg.friction_angle_2_spinbox.setValue(params.get('friction_angle_2', self.dlg.friction_angle_2_spinbox.value()))

                if params.get('enable_water'):
                    self.dlg.enable_water_checkbox.setChecked(True)
                    mode = params.get('water_definition_mode', 0)
                    self.dlg.water_definition_group.button(mode).setChecked(True)
                    if mode == 0:
                        self.dlg.water_const_depth_spinbox.setValue(params.get('water_const_depth', 2.0))
                    elif mode == 1 and params.get('water_raster_layer'):
                        lyr = _resolve_layer_ref(params.get('water_raster_layer'))
                        if lyr is not None:
                            for i in range(self.dlg.water_raster_combo.count()):
                                item_lyr = self.dlg.water_raster_combo.itemData(i)
                                if item_lyr is not None and item_lyr == lyr:
                                    self.dlg.water_raster_combo.setCurrentIndex(i)
                                    break
                    elif mode == 2:
                        self.dlg.water_elevation_spinbox.setValue(params.get('water_elevation', 0.0))
            except Exception:
                pass

            # Superfici
            loaded_surfaces = state.get('slip_surfaces', [])
            self.slip_surfaces = []
            for s in loaded_surfaces:
                try:
                    xs = []
                    ys = []
                    for xi, yi in zip(s.get('x', []) or [], s.get('y', []) or []):
                        try:
                            if xi is None or yi is None:
                                continue
                            x_f = float(xi)
                            y_f = float(yi)
                            if not np.isfinite(x_f) or not np.isfinite(y_f):
                                continue
                            xs.append(x_f)
                            ys.append(y_f)
                        except Exception:
                            continue
                    fs_v = s.get('fs', 0.0)
                    try:
                        fs_f = float(fs_v) if fs_v is not None and np.isfinite(float(fs_v)) else 0.0
                    except Exception:
                        fs_f = 0.0
                    self.slip_surfaces.append({'search': s.get('search'), 'method': s.get('method'), 'x': np.array(xs, dtype=float), 'y': np.array(ys, dtype=float), 'fs': fs_f})
                except Exception:
                    continue

            # Result texts
            try:
                res = state.get('results', {})
                if 'grid' in res:
                    self.dlg.updateStabilityResults(res.get('grid', ''), 'grid')
                if 'simplex' in res:
                    self.dlg.updateStabilityResults(res.get('simplex', ''), 'simplex')
            except Exception:
                pass

            # Aggiorna grafico e lista superfici
            self._update_profile_with_all_surfaces()

            # Restore surface visibility (if present in file). Don't block if not present.
            try:
                ui = state.get('ui', {}) or {}
                vis = ui.get('visible_surface_indices', None)
                if isinstance(vis, list) and hasattr(self.dlg, 'surface_visibility_list') and self.dlg.surface_visibility_list is not None:
                    lw = self.dlg.surface_visibility_list
                    lw.blockSignals(True)
                    for i in range(lw.count()):
                        try:
                            lw.item(i).setCheckState(2 if i in vis else 0)
                        except Exception:
                            continue
                    lw.blockSignals(False)
                    # Redraw with updated visibility
                    try:
                        self.dlg._refresh_profile_display()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                if 'cleaned_flag' in locals() and cleaned_flag:
                    self.dlg.setStatus(f"Progetto caricato: {path} (attenzione: NaN/Infinity sostituiti con null)")
                else:
                    self.dlg.setStatus(f"Progetto caricato: {path}")
            except Exception:
                self.dlg.setStatus(f"Progetto caricato: {path}")
        except Exception as e:
            import traceback
            self.dlg.setStatus(f"Errore caricamento progetto: {e}")
            print(traceback.format_exc())
    def _clear_surfaces(self):
        """Clear all drawn surfaces and update the graph."""
        self.slip_surfaces = []
        # Remove plan view overlays (rubberband and labels)
        self._clear_plan_overlays()
        # Force a redraw of the profile without surfaces
        self.dlg.updateProfile(self.profile_distances, self.profile_elevations, slip_surfaces_list=[])
        self.dlg.setStatus("Surfaces cleared")

    def _clear_plan_overlays(self):
        """Remove only the overlay related to slip surfaces in plan view (not P1/P2 points)."""
        for rb in list(self.surface_rubber_bands):
            try:
                self.iface.mapCanvas().scene().removeItem(rb)
            except Exception:
                pass
        self.surface_rubber_bands = []
        for ti in list(self.surface_label_items):
            try:
                self.iface.mapCanvas().scene().removeItem(ti)
            except Exception:
                pass
        self.surface_label_items = []

    def _sample_with_bilinear(self, provider, pt, band, no_data, raster_layer):
        """Sample with bilinear fallback."""
        import math
        val, ok = provider.sample(pt, band)
        if ok:
            if no_data is not None:
                if (isinstance(no_data, float) and isinstance(val, float) and math.isnan(no_data) and math.isnan(val)) or val == no_data:
                    pass
                else:
                    return val
            else:
                return val

        extent = raster_layer.extent()
        px = raster_layer.rasterUnitsPerPixelX()
        py = raster_layer.rasterUnitsPerPixelY()
        if px == 0 or py == 0:
            return None
        col_f = (pt.x() - extent.xMinimum()) / px
        row_f = (extent.yMaximum() - pt.y()) / abs(py)
        col0 = int(math.floor(col_f))
        row0 = int(math.floor(row_f))
        col1 = col0 + 1
        row1 = row0 + 1
        stats = provider.xSize(), provider.ySize()
        max_col = stats[0] - 1
        max_row = stats[1] - 1
        if not (0 <= col0 <= max_col and 0 <= col1 <= max_col and 0 <= row0 <= max_row and 0 <= row1 <= max_row):
            return None
            
        def read_cell(c, r):
            x = extent.xMinimum() + (c + 0.5) * px
            y = extent.yMaximum() - (r + 0.5) * abs(py)
            v, okc = provider.sample(QgsPointXY(x, y), band)
            if not okc:
                return None
            if no_data is not None:
                if (isinstance(no_data, float) and isinstance(v, float) and math.isnan(no_data) and math.isnan(v)) or v == no_data:
                    return None
            return v
            
        v00 = read_cell(col0, row0)
        v10 = read_cell(col1, row0)
        v01 = read_cell(col0, row1)
        v11 = read_cell(col1, row1)
        if None in (v00, v10, v01, v11):
            return None
        dx = col_f - col0
        dy = row_f - row0
        v0 = v00 * (1 - dx) + v10 * dx
        v1 = v01 * (1 - dx) + v11 * dx
        vb = v0 * (1 - dy) + v1 * dy
        return vb

    def _sample_raster_at_x_coordinates(self, x_coords, raster_layer, default_value=0.0):
        """Sample a raster at x coordinates along the profile.
        
        Args:
            x_coords: Numpy array of x coordinates along the profile (distances)
            raster_layer: QGIS raster layer to sample
            default_value: Default value if sampling fails
            
        Returns:
            Numpy array with values sampled from the raster
        """
        if not self.profile_distances or not self.profile_elevations:
            # If we don't have a profile, return the default value
            return np.full_like(x_coords, default_value, dtype=float)
        
        # Converti x_coords in array numpy se necessario
        x_coords = np.atleast_1d(x_coords)
        result = np.full_like(x_coords, default_value, dtype=float)
        
        # Get provider and raster parameters
        provider = raster_layer.dataProvider()
        band = 1
        no_data = provider.sourceNoDataValue(band)
        
        # Gestione CRS
        raster_crs = raster_layer.crs()
        project_crs = QgsProject.instance().crs()
        need_transform = project_crs.isValid() and raster_crs.isValid() and (project_crs != raster_crs)
        transformer = None
        if need_transform:
            transformer = QgsCoordinateTransform(project_crs, raster_crs, QgsProject.instance())
        
        # The P1 and P2 points of the profile
        if not self.profile_p1 or not self.profile_p2:
            return result
        
        p1 = self.profile_p1
        p2 = self.profile_p2
        extent_length = p1.distance(p2)
        
        if extent_length == 0:
            return result
        
        # For each x coordinate, calculate the geographic position and sample
        for i, x in enumerate(x_coords):
            if x < 0 or x > extent_length:
                # Outside the profile range
                result[i] = default_value
                continue
            
            # Parameter t along the P1-P2 segment
            t = x / extent_length
            
            # Geographic coordinates of the point
            geo_x = p1.x() + (p2.x() - p1.x()) * t
            geo_y = p1.y() + (p2.y() - p1.y()) * t
            pt = QgsPointXY(geo_x, geo_y)
            
            # CRS transformation if necessary
            if transformer is not None:
                try:
                    pt = transformer.transform(pt)
                except Exception:
                    result[i] = default_value
                    continue
            
            # Sample the raster
            val = self._sample_with_bilinear(provider, pt, band, no_data, raster_layer)
            
            if val is not None:
                result[i] = val
            else:
                result[i] = default_value
        
        return result

    def _analyze_grid_stability(self, params):
        """Execute stability analysis with grid of circles using Bishop."""
        if not self.profile_distances or not self.profile_elevations:
            self.dlg.setStatus("Compute the profile first.")
            return
        
        if not LIMIT_EQUILIBRIUM_AVAILABLE:
            error_msg = "Limit-equilibrium modules not available. Please check installation."
            self.dlg.setStatus(error_msg)
            self.dlg.updateStabilityResults(error_msg, 'grid')
            return
        
        try:
            # --- LOG HEADER ---
            print("=" * 60)
            print("GRID STABILITY ANALYSIS - START")
            print("=" * 60)
            print(f"Soil parameters layer 1: γ={params['gamma']:.1f} kN/m³, c={params['cohesion']:.1f} kPa, φ={params['friction_angle']:.1f}°, n={params['porosity']:.2f}")
            
            # Stratigraphy log
            if params.get('enable_layer2', False):
                print(f"Second layer active: γ₂={params['gamma_2']:.1f} kN/m³, c₂={params['cohesion_2']:.1f} kPa, φ₂={params['friction_angle_2']:.1f}°")
            if params.get('enable_water', False):
                print("Water table active")

            # 1. Create ground_surface function
            ground_surface = self._create_ground_surface_function(
                self.profile_distances, 
                self.profile_elevations
            )
            
            # 2. Create function for second layer interface (if enabled)
            layer2_interface = None
            if params.get('enable_layer2', False):
                layer2_interface = self._create_layer2_interface_function(params, ground_surface)
                print(f"Second layer interface created: mode {params.get('layer2_definition_mode', 'unknown')}")
                # Test the interface on a sample point
                x_min_temp = self.profile_distances[0]
                x_max_temp = self.profile_distances[-1]
                test_x = (x_min_temp + x_max_temp) / 2
                test_interface_y = layer2_interface(test_x)
                test_ground_y = ground_surface(test_x)
                # Ensure scalar floats for formatted printing (handle numpy arrays)
                try:
                    ti_val = float(np.asarray(test_interface_y).item())
                    tg_val = float(np.asarray(test_ground_y).item())
                    thickness = tg_val - ti_val
                    print(f"  Test point x={test_x:.1f}: ground={tg_val:.1f}m, interface={ti_val:.1f}m, thickness={thickness:.1f}m")
                except Exception:
                    # Fallback: print arrays/repr without numeric formatting
                    ti_arr = np.asarray(test_interface_y)
                    tg_arr = np.asarray(test_ground_y)
                    thickness_arr = tg_arr - ti_arr
                    print(f"  Test point x={test_x:.1f}: ground={tg_arr}, interface={ti_arr}, thickness={thickness_arr}")
            
            # 3. Create function for water table (if enabled)
            water_table = None
            if params.get('enable_water', False):
                water_table = self._create_water_table_function(params, ground_surface)
            
            # 4. Calculate search bounds
            valid_elevations = [e for e in self.profile_elevations if e is not None]
            if not valid_elevations:
                raise ValueError("No valid data in the profile")
            
            x_min = self.profile_distances[0]
            x_max = self.profile_distances[-1]
            
            # 5. Grid options (using parameters from the interface)
            in_interval_min = params['in_interval_min'] * x_max
            in_interval_max = params['in_interval_max'] * x_max
            out_interval_min = params['out_interval_min'] * x_max
            out_interval_max = params['out_interval_max'] * x_max

            in_range = (min(in_interval_min, in_interval_max), max(in_interval_min, in_interval_max))
            out_range = (min(out_interval_min, out_interval_max), max(out_interval_min, out_interval_max))

            # Warn when ranges overlap (would produce backward surfaces with negative FoS)
            if in_range[0] < out_range[1]:
                grid_overlap_warning = (
                    "⚠️ ATTENZIONE: i range In e Out si sovrappongono "
                    f"(in_min={in_range[0]:.1f} < out_max={out_range[1]:.1f}).\n"
                    "Le superfici con x_in ≤ x_out verranno scartate automaticamente.\n"
                    "Per risultati migliori imposta In nella parte alta del pendio "
                    "(es. 60–100%) e Out nella parte bassa (es. 0–40%).\n\n"
                )
                print(grid_overlap_warning)
            else:
                grid_overlap_warning = ""

            print(f"Search ranges: in={in_range}, out={out_range}")
            print(f"Grid: in=[{in_interval_min:.1f}, {in_interval_max:.1f}], out=[{out_interval_min:.1f}, {out_interval_max:.1f}]")
            print(f"Grid points: in={params['num_in_pts']}, out={params['num_out_pts']}, min_eta={params['min_eta_inc']}°")

            # Stability method selector
            method_label, solver = self._get_solver(params.get('stability_method', 'Bishop'))
            print(f"Stability calculation method: {method_label}")
            
            # Soil and method setup
            soil = self._create_soil_properties_and_state(
                params, ground_surface, layer2_interface, water_table
            )
            method_options = lemOptions(
                max_iteration=200,
                tolerance=1e-4,
                subdivision_method=lambda interval: uniform_subdivision(interval, int(params['num_slices']))
            )
            method = lemMethod(solver, soil, method_options)

            domain = circularSlipSearchDomain(
                ground_surface=ground_surface,
                in_range=in_range,
                out_range=out_range,
                eta_min_shift=np.radians(1.0),
            )

            grid_options = {
                'num_in_points': int(params['num_in_pts']),
                'num_out_points': int(params['num_out_pts']),
                'min_eta_inc': np.radians(float(params['min_eta_inc'])),
            }
            
            # Run calculation
            self.dlg.setStatus("Calculation in progress... (grid of circles)")

            print("Calling searchInterface.find_critical...")
            geometries = domain.sample_grid(grid_options)
            # Filter geometries where in_pt.x <= out_pt.x: these produce backward slip surfaces
            # (the mass would slide uphill), yielding very large negative FoS values.
            # Convention: in_pt (crown/entry) must be upslope, i.e. at higher x than out_pt (toe).
            n_before = len(geometries)
            geometries = [g for g in geometries
                          if hasattr(g, 'in_pt') and hasattr(g, 'out_pt')
                          and float(g.in_pt[0]) > float(g.out_pt[0])]
            n_filtered = n_before - len(geometries)
            if n_filtered > 0:
                print(f"  Filtered {n_filtered} reversed geometries (x_in <= x_out); {len(geometries)} remain.")
            if not geometries:
                raise ValueError(
                    "Nessuna geometria valida dopo il filtraggio.\n"
                    "Assicurati che l'intervallo 'In' (corona) sia nella parte alta del pendio "
                    "(frazione > intervallo 'Out' che è il piede).\n"
                    "Esempio: In=[0.5, 1.0], Out=[0.0, 0.5]"
                )
            results, computation_time = find_critical(method, geometries, num_geometries=len(geometries))

            print(f"\nCalcolo completato in {computation_time:.2f} secondi")

            if results is None or len(results) == 0:
                raise ValueError("Nessuna superficie di scivolamento valida trovata")

            all_results = results
            print(f"Superfici analizzate: {len(all_results)}")
            
            # Trova il risultato critico (FS minimo)
            critical_geometry, critical_result = all_results[0]
            factor_of_safety = critical_result.factor_of_safety
            
            print(f"\nRISULTATO CRITICO:")
            print(f"Fattore di Sicurezza (FS): {factor_of_safety:.4f}")

            # Geometria superficie critica
            x_out = float(critical_geometry.landslide_interval[0])
            x_in = float(critical_geometry.landslide_interval[1])
            
            y_in = ground_surface(x_in)
            y_out = ground_surface(x_out)
            
            eta_deg = float(np.degrees(critical_geometry.eta)) if hasattr(critical_geometry, 'eta') else "N/A"
            
            print(f"\nGEOMETRIA SUPERFICIE CRITICA:")
            print(f"Punto ingresso (x_in): {x_in:.2f} m, quota: {y_in:.2f} m")
            print(f"Punto uscita (x_out): {x_out:.2f} m, quota: {y_out:.2f} m")
            print(f"Lunghezza superficie: {x_in - x_out:.2f} m")
            if isinstance(eta_deg, (int, float)):
                print(f"Angolo eta: {eta_deg:.2f}°")
            else:
                print(f"Angolo eta: {eta_deg}")
            
            # Sample the surface for the graph
            try:
                x_start, x_end = critical_geometry.landslide_interval
                slip_x = np.linspace(x_start, x_end, 200)
                slip_y = critical_geometry.slip_surface(slip_x)

                # Save the critical surface with metadata
                self._store_surface('grid', method_label, slip_x, slip_y, factor_of_safety)

            except Exception as e:
                print(f"⚠️ Unable to sample the critical slip surface for the graph (Grid): {e}")
                pass
            
            # Update the graph with all available surfaces
            self._update_profile_with_all_surfaces()
            
            # Prepare output
            eta_str = f"{eta_deg:.2f}°" if isinstance(eta_deg, (int, float)) else str(eta_deg)
            
            # Stratigraphy info for output
            strat_info = self._format_stratigraphy_info(params)
            
            results_text = grid_overlap_warning + f"""STABILITY ANALYSIS - {method_label.upper()} (GRID)

Parameters used:
- Unit weight (γ): {params['gamma']:.1f} kN/m³
- Cohesion (c): {params['cohesion']:.1f} kPa
- Friction angle (φ): {params['friction_angle']:.1f}°
- Number of slices: {params['num_slices']}
- Computation time: {computation_time:.2f} s

{strat_info}

GRID PARAMETERS:
- In points: {params['num_in_pts']}
- Out points: {params['num_out_pts']}
- Min η increment: {params['min_eta_inc']:.1f}°
- In interval: [{in_interval_min:.1f}, {in_interval_max:.1f}]
- Out interval: [{out_interval_min:.1f}, {out_interval_max:.1f}]

CRITICAL SLIP SURFACE:
- Entry point (upstream): x = {x_in:.2f} m, z = {y_in:.2f} m
- Exit point (downstream): x = {x_out:.2f} m, z = {y_out:.2f} m
- Surface length: {x_in - x_out:.2f} m
- Eta angle: {eta_str}

RESULTS:
Factor of Safety (FS): {factor_of_safety:.3f} ({method_label})

Condition: {'STABLE (FS ≥ 1.5)' if factor_of_safety >= 1.5 else 'UNSTABLE (FS < 1.0)' if factor_of_safety < 1.0 else 'MARGINALLY STABLE (1.0 ≤ FS < 1.5)'}

Total surfaces analyzed: {len(all_results)}
"""
            
            self.dlg.updateStabilityResults(results_text, 'grid')
            self.dlg.setStatus(f"Grid analysis complete. Min FS = {factor_of_safety:.3f}")
            
            print(f"\nSUMMARY:")
            print(f"Critical FS: {factor_of_safety:.4f}")
            print(f"Total surfaces: {len(all_results)}")
            print("=" * 60)
            print("GRID STABILITY ANALYSIS - END")
            print("=" * 60)
            
        except Exception as e:
            import traceback
            error_msg = f"Error in grid analysis:\n{str(e)}\n\n{traceback.format_exc()}"
            self.dlg.setStatus(f"Error: {str(e)}")
            self.dlg.updateStabilityResults(error_msg, 'grid')
            print(f"\nERROR IN GRID ANALYSIS:\n{traceback.format_exc()}")

    def _analyze_simplex_stability(self, params):
        """Execute stability analysis with simplex optimization."""
        if not self.profile_distances or not self.profile_elevations:
            self.dlg.setStatus("Compute the profile first.")
            return
        
        if not LIMIT_EQUILIBRIUM_AVAILABLE:
            error_msg = "Limit-equilibrium modules not available. Please check installation."
            self.dlg.setStatus(error_msg)
            self.dlg.updateStabilityResults(error_msg, 'simplex')
            return
        
        try:
            # --- LOG HEADER ---
            print("=" * 60)
            print("SIMPLEX STABILITY ANALYSIS - START")
            print("=" * 60)
            print(f"Soil parameters layer 1: γ={params['gamma']:.1f} kN/m³, c={params['cohesion']:.1f} kPa, φ={params['friction_angle']:.1f}°, n={params['porosity']:.2f}")
            
            # Stratigraphy log
            if params.get('enable_layer2', False):
                print(f"Second layer active: γ₂={params['gamma_2']:.1f} kN/m³, c₂={params['cohesion_2']:.1f} kPa, φ₂={params['friction_angle_2']:.1f}°")
            if params.get('enable_water', False):
                print("Water table active")

            # 1. Create ground_surface function
            ground_surface = self._create_ground_surface_function(
                self.profile_distances, 
                self.profile_elevations
            )
            
            # 2. Create function for second layer interface (if enabled)
            layer2_interface = None
            if params.get('enable_layer2', False):
                layer2_interface = self._create_layer2_interface_function(params, ground_surface)
                print(f"Second layer interface created: mode {params.get('layer2_definition_mode', 'unknown')}")
                # Test the interface on a sample point
                x_min_temp = self.profile_distances[0]
                x_max_temp = self.profile_distances[-1]
                test_x = (x_min_temp + x_max_temp) / 2
                test_interface_y = layer2_interface(test_x)
                test_ground_y = ground_surface(test_x)
                # Ensure scalar floats for formatted printing (handle numpy arrays)
                try:
                    ti_val = float(np.asarray(test_interface_y).item())
                    tg_val = float(np.asarray(test_ground_y).item())
                    thickness = tg_val - ti_val
                    print(f"  Test punto x={test_x:.1f}: terreno={tg_val:.1f}m, interfaccia={ti_val:.1f}m, spessore={thickness:.1f}m")
                except Exception:
                    # Fallback: print arrays/repr without numeric formatting
                    ti_arr = np.asarray(test_interface_y)
                    tg_arr = np.asarray(test_ground_y)
                    thickness_arr = tg_arr - ti_arr
                    print(f"  Test point x={test_x:.1f}: ground={tg_arr}, interface={ti_arr}, thickness={thickness_arr}")
            # 3. Create function for water table (if enabled)
            water_table = None
            if params.get('enable_water', False):
                water_table = self._create_water_table_function(params, ground_surface)
            
            # 2. Calculate search bounds
            valid_elevations = [e for e in self.profile_elevations if e is not None]
            if not valid_elevations:
                raise ValueError("No valid data in the profile")
            
            x_min = self.profile_distances[0]
            x_max = self.profile_distances[-1]

            # Bounds per ottimizzazione simplex (usando i parametri dall'interfaccia)
            x_in_min = params['x_in_min'] * x_max
            x_in_max = params['x_in_max'] * x_max
            x_out_min = params['x_out_min'] * x_max
            x_out_max = params['x_out_max'] * x_max
            eta_min = float(params['eta_min'])
            eta_max = float(params['eta_max'])

            # Normalizza bounds SENZA modificarli “di nascosto”: solo ordinamento e clipping nel dominio
            if x_in_min > x_in_max:
                x_in_min, x_in_max = x_in_max, x_in_min
            if x_out_min > x_out_max:
                x_out_min, x_out_max = x_out_max, x_out_min
            x_in_min = max(x_min, min(x_in_min, x_max))
            x_in_max = max(x_min, min(x_in_max, x_max))
            x_out_min = max(x_min, min(x_out_min, x_max))
            x_out_max = max(x_min, min(x_out_max, x_max))
            if eta_min > eta_max:
                eta_min, eta_max = eta_max, eta_min
            eta_min = max(0.0, min(eta_min, 90.0))
            eta_max = max(0.0, min(eta_max, 90.0))

            in_range = (float(x_in_min), float(x_in_max))
            out_range = (float(x_out_min), float(x_out_max))

            # Warn when ranges overlap: the optimizer could generate backward surfaces.
            if x_in_min < x_out_max:
                overlap_warning = (
                    "⚠️ ATTENZIONE: i range x_in e x_out si sovrappongono "
                    f"(x_in_min={x_in_min:.1f} < x_out_max={x_out_max:.1f}).\n"
                    "Le superfici con x_in ≤ x_out verranno scartate automaticamente.\n"
                    "Per risultati migliori imposta x_in nella parte alta del pendio "
                    "(es. 50–100%) e x_out nella parte bassa (es. 0–50%).\n\n"
                )
                print(overlap_warning)
            else:
                overlap_warning = ""

            simplex_grid_pts = 8
            grid_options = {
                'num_in_points': simplex_grid_pts,
                'num_out_points': simplex_grid_pts,
                'min_eta_inc': np.radians(10.0),
            }

            print(f"Search ranges: in={in_range}, out={out_range}")
            print(f"Bounds simplex:")
            print(f"  x_in: [{x_in_min:.1f}, {x_in_max:.1f}] m (interval: {x_in_max-x_in_min:.1f} m)")
            print(f"  x_out: [{x_out_min:.1f}, {x_out_max:.1f}] m (interval: {x_out_max-x_out_min:.1f} m)")
            print(f"  η: [{eta_min:.1f}, {eta_max:.1f}]°")
            print(f"Initial simplex grid:")
            print(f"  num_in_pts: {simplex_grid_pts}")
            print(f"  num_out_pts: {simplex_grid_pts}")
            print(f"  min_eta_inc: 10.0° (increment for initial grid)")
            print(f"  Total combinations: {simplex_grid_pts * simplex_grid_pts} pairs (in,out)")

            # Stability method selector
            method_label, solver = self._get_solver(params.get('stability_method', 'Bishop'))
            print(f"Stability calculation method: {method_label}")
            
            # Soil and method setup
            soil = self._create_soil_properties_and_state(
                params, ground_surface, layer2_interface, water_table
            )
            method_options = lemOptions(
                max_iteration=params.get('max_iterations', 300),
                tolerance=1e-4,
                subdivision_method=lambda interval: uniform_subdivision(interval, int(params['num_slices']))
            )
            method = lemMethod(solver, soil, method_options)

            domain = circularSlipSearchDomain(
                ground_surface=ground_surface,
                in_range=in_range,
                out_range=out_range,
                eta_min_shift=np.radians(1.0),
            )
            
            # Run calculation
            self.dlg.setStatus("Calculation in progress... (simplex optimization)")
            
            print("Calling searchInterface.simplex...")
            initial_geometries = domain.sample_grid(grid_options)
            # Filter reversed geometries (x_in <= x_out produce backward surfaces with negative FoS)
            n_before = len(initial_geometries)
            initial_geometries = [g for g in initial_geometries
                                   if hasattr(g, 'in_pt') and hasattr(g, 'out_pt')
                                   and float(g.in_pt[0]) > float(g.out_pt[0])]
            n_filtered = n_before - len(initial_geometries)
            if n_filtered > 0:
                print(f"  Filtered {n_filtered} reversed geometries (x_in <= x_out); {len(initial_geometries)} remain.")
            if not initial_geometries:
                raise ValueError(
                    "Nessuna geometria iniziale valida dopo il filtraggio.\n"
                    "Assicurati che il range x_in (corona, alta frazione) "
                    "sia nella parte alta del pendio rispetto a x_out (piede, bassa frazione).\n"
                    "Esempio: x_in=[0.5, 1.0], x_out=[0.0, 0.5]"
                )
            grid_result, grid_time = find_critical(method, initial_geometries, num_geometries=3)
            simplex_start_geo = [g[0] for g in grid_result]
            simplex_options = {
                'disp': False,
                'xatol': 1e-3,
                'fatol': 1e-4,
                'maxiter': int(params.get('max_iterations', 300)),
                'return_all': True,
            }
            optimized, simplex_time, calls = simplex(
                domain=domain,
                method=method,
                initialGeometries=simplex_start_geo,
                num_geometries=1,
                options=simplex_options,
            )
            computation_time = grid_time + simplex_time

            print(f"\nCalcolo completato in {computation_time:.2f} secondi")
            
            if not optimized:
                raise ValueError("Nessuna superficie di scivolamento valida trovata")

            best_geometry, best_result = optimized[0]
            factor_of_safety = float(best_result.factor_of_safety)
            
            print(f"\nRISULTATO CRITICO:")
            print(f"Fattore di Sicurezza (FS): {factor_of_safety:.4f}")
            print(f"   Numero chiamate metodo: {calls + len(initial_geometries)}")

            # Informazioni superficie critica
            if hasattr(best_geometry, 'in_pt') and hasattr(best_geometry, 'out_pt'):
                x_in = float(best_geometry.in_pt[0])
                x_out = float(best_geometry.out_pt[0])
                eta_deg = float(np.degrees(best_geometry.eta)) if hasattr(best_geometry, 'eta') else "N/A"
                
                print(f"\nGEOMETRIA SUPERFICIE CRITICA:")
                print(f"Punto ingresso (x_in): {x_in:.2f} m")
                print(f"Punto uscita (x_out): {x_out:.2f} m") 
                print(f"Lunghezza superficie: {x_in - x_out:.2f} m")
                if isinstance(eta_deg, (int, float)):
                    print(f"Angolo eta: {eta_deg:.2f}°")
                else:
                    print(f"Angolo eta: {eta_deg}")
                
                try:
                    x_start, x_end = best_geometry.landslide_interval
                    slip_x = np.linspace(x_start, x_end, 200)
                    slip_y = best_geometry.slip_surface(slip_x)

                    self._store_surface('simplex', method_label, slip_x, slip_y, factor_of_safety)
                    self._update_profile_with_all_surfaces()
                except Exception as e:
                    print(f"⚠️ Errore nel campionamento della superficie circolare: {e}")
                    self._update_profile_with_all_surfaces()
            else:
                print("⚠️ Parametri geometrici non disponibili")
                x_in = x_out = eta_deg = "N/A"
                self._update_profile_with_all_surfaces()
                self.dlg.updateProfile(self.profile_distances, self.profile_elevations)
            
            # Prepara output
            # Gestisci il caso in cui i parametri geometrici non siano disponibili
            if isinstance(x_in, (int, float)) and isinstance(x_out, (int, float)):
                x_in_str = f"{x_in:.2f}"
                x_out_str = f"{x_out:.2f}"
                length_str = f"{x_in - x_out:.2f}"
            else:
                x_in_str = str(x_in)
                x_out_str = str(x_out)
                length_str = "N/A"
            
            eta_str = f"{eta_deg:.2f}" if isinstance(eta_deg, (int, float)) else str(eta_deg)
            
            convergence_status = "N/A (handled by searchInterface.simplex)"
            opt_message = "N/A"
            opt_nit = str(params.get('max_iterations', 300))
            
            # Info stratigrafia per output
            strat_info = self._format_stratigraphy_info(params)
            
            results_text = overlap_warning + f"""STABILITY ANALYSIS - {method_label.upper()} (SIMPLEX)

Parameters used:
- Unit weight (γ): {float(params['gamma']):.1f} kN/m³
- Cohesion (c): {float(params['cohesion']):.1f} kPa
- Friction angle (φ): {float(params['friction_angle']):.1f}°
- Number of slices: {int(params['num_slices'])}
- Computation time: {float(computation_time):.2f} s

{strat_info}

BOUNDS SIMPLEX (absolute values):
- x_in: [{float(x_in_min):.1f}, {float(x_in_max):.1f}] m
- x_out: [{float(x_out_min):.1f}, {float(x_out_max):.1f}] m
- η: [{float(eta_min):.1f}, {float(eta_max):.1f}]°

BOUNDS SIMPLEX (percentages relative to L_max={float(x_max):.1f}m):
- x_in: [{float(params['x_in_min'])*100:.1f}%, {float(params['x_in_max'])*100:.1f}%]
- x_out: [{float(params['x_out_min'])*100:.1f}%, {float(params['x_out_max'])*100:.1f}%]

CRITICAL SLIP SURFACE:
- Entry point (upstream): x = {x_in_str} m
- Exit point (downstream): x = {x_out_str} m
- Surface length: {length_str} m
- Eta angle: {eta_str}°

RESULTS:
Factor of Safety (FS): {float(factor_of_safety):.3f} ({method_label})

Condition: {'STABLE (FS ≥ 1.5)' if factor_of_safety >= 1.5 else 'UNSTABLE (FS < 1.0)' if factor_of_safety < 1.0 else 'MARGINALLY STABLE (1.0 ≤ FS < 1.5)'}

Optimization:
- Convergence: {convergence_status}
- Message: {opt_message}
- Iterations: {opt_nit}
"""
            
            self.dlg.updateStabilityResults(results_text, 'simplex')
            self.dlg.setStatus(f"Simplex analysis complete. FS = {factor_of_safety:.3f}")
            
            print(f"\nSUMMARY:")
            print(f"Critical FS: {factor_of_safety:.4f}")
            print("=" * 60)
            print("SIMPLEX STABILITY ANALYSIS - END")
            print("=" * 60)
            
        except Exception as e:
            import traceback
            error_msg = f"Error in simplex analysis:\n{str(e)}\n\n{traceback.format_exc()}"
            self.dlg.setStatus(f"Error: {str(e)}")
            self.dlg.updateStabilityResults(error_msg, 'simplex')
            print(f"\nERROR IN SIMPLEX ANALYSIS:\n{traceback.format_exc()}")

    def _create_ground_surface_function(self, distances, elevations):
        """Crea una funzione lineare a tratti per la superficie del terreno."""
        valid_points = [(d, e) for d, e in zip(distances, elevations) if e is not None]
        if len(valid_points) < 2:
            raise ValueError("Dati insufficienti per creare la funzione del terreno")

        valid_distances, valid_elevations = zip(*valid_points)
        x_data = np.asarray(valid_distances, dtype=float)
        y_data = np.asarray(valid_elevations, dtype=float)

        # Ensure monotonic x for interpolation and preserve linear extrapolation at boundaries.
        sort_idx = np.argsort(x_data)
        x_data = x_data[sort_idx]
        y_data = y_data[sort_idx]

        x0, x1 = x_data[0], x_data[1]
        y0, y1 = y_data[0], y_data[1]
        left_slope = (y1 - y0) / (x1 - x0)

        xn_1, xn = x_data[-2], x_data[-1]
        yn_1, yn = y_data[-2], y_data[-1]
        right_slope = (yn - yn_1) / (xn - xn_1)

        def ground_surface(x):
            x_arr = np.asarray(x, dtype=float)
            is_scalar = x_arr.ndim == 0
            x_eval = np.atleast_1d(x_arr)

            y_eval = np.interp(x_eval, x_data, y_data)

            left_mask = x_eval < x_data[0]
            if np.any(left_mask):
                y_eval[left_mask] = y0 + left_slope * (x_eval[left_mask] - x0)

            right_mask = x_eval > x_data[-1]
            if np.any(right_mask):
                y_eval[right_mask] = yn + right_slope * (x_eval[right_mask] - xn)

            if is_scalar:
                return float(y_eval[0])
            return y_eval
        
        return ground_surface
    
    def _create_layer2_interface_function(self, params, ground_surface):
        """Create a function for the second layer interface."""
        mode = params.get('layer2_definition_mode', 0)
        
        if mode == 0:  # Constant depth from ground surface
            depth = params.get('layer2_const_depth', 5.0)
            return lambda x: ground_surface(x) - depth
        
        elif mode == 1:  # Da raster
            raster_layer = params.get('layer2_raster_layer')
            if raster_layer:
                # Crea funzione che campiona il raster (profondità) e sottrae dalla superficie
                def layer2_from_raster(x):
                    depths = self._sample_raster_at_x_coordinates(x, raster_layer, default_value=5.0)
                    return ground_surface(x) - depths
                return layer2_from_raster
            else:
                # Fallback
                depth = params.get('layer2_const_depth', 5.0)
                return lambda x: ground_surface(x) - depth
        
        elif mode == 2:  # Quota assoluta
            elevation = params.get('layer2_elevation', 0.0)
            return lambda x: np.full_like(x, elevation, dtype=float)
        
        # Fallback
        depth = params.get('layer2_const_depth', 5.0)
        return lambda x: ground_surface(x) - depth
    
    def _create_water_table_function(self, params, ground_surface):
        """Create a function for the water table."""
        mode = params.get('water_definition_mode', 0)
        
        if mode == 0:  # Constant depth from ground surface
            depth = params.get('water_const_depth', 2.0)
            return lambda x: ground_surface(x) - depth
        
        elif mode == 1:  # From raster
            raster_layer = params.get('water_raster_layer')
            if raster_layer:
                # Create function that samples the raster (depth) and subtracts from surface
                def water_from_raster(x):
                    depths = self._sample_raster_at_x_coordinates(x, raster_layer, default_value=2.0)
                    return ground_surface(x) - depths
                return water_from_raster
            else:
                # Fallback
                depth = params.get('water_const_depth', 2.0)
                return lambda x: ground_surface(x) - depth
        
        elif mode == 2:  # Quota assoluta
            elevation = params.get('water_elevation', 0.0)
            return lambda x: np.full_like(x, elevation, dtype=float)
        
        # Fallback
        depth = params.get('water_const_depth', 2.0)
        return lambda x: ground_surface(x) - depth
    
    def compute_layer2_profile_for_display(self, params):
        """Calculate the values of the second layer interface along the profile for visualization.
        
        Returns:
            List of elevations (float) or None if it cannot be calculated
        """
        if not self.profile_distances or not self.profile_elevations:
            return None
        
        try:
            # Create ground_surface function
            ground_surface = self._create_ground_surface_function(
                self.profile_distances, 
                self.profile_elevations
            )
            
            mode = params.get('layer2_definition_mode', 0)
            
            if mode == 0:  # Constant depth
                depth = params.get('layer2_const_depth', 5.0)
                return [ground_surface(x) - depth for x in self.profile_distances]
            
            elif mode == 1:  # From raster
                raster_layer = params.get('layer2_raster_layer')
                if not raster_layer or not self.profile_p1 or not self.profile_p2:
                    return None
                
                # Sample raster along profile
                depths = []
                provider = raster_layer.dataProvider()
                band = 1
                no_data = provider.sourceNoDataValue(band)
                extent_length = self.profile_p1.distance(self.profile_p2)
                
                raster_crs = raster_layer.crs()
                project_crs = QgsProject.instance().crs()
                need_transform = project_crs.isValid() and raster_crs.isValid() and (project_crs != raster_crs)
                transformer = None
                if need_transform:
                    transformer = QgsCoordinateTransform(project_crs, raster_crs, QgsProject.instance())
                
                for d in self.profile_distances:
                    t = d / extent_length if extent_length > 0 else 0
                    x = self.profile_p1.x() + (self.profile_p2.x() - self.profile_p1.x()) * t
                    y = self.profile_p1.y() + (self.profile_p2.y() - self.profile_p1.y()) * t
                    pt = QgsPointXY(x, y)
                    
                    if transformer is not None:
                        try:
                            pt = transformer.transform(pt)
                        except Exception:
                            depths.append(5.0)  # Default
                            continue
                    
                    val = self._sample_with_bilinear(provider, pt, band, no_data, raster_layer)
                    depths.append(val if val is not None else 5.0)
                
                # Calculate interface elevations
                return [ground_surface(x) - depth for x, depth in zip(self.profile_distances, depths)]
            
            elif mode == 2:  # Absolute elevation
                elevation = params.get('layer2_elevation', 0.0)
                return [elevation] * len(self.profile_distances)
            
        except Exception as e:
            print(f"Layer2 profile calculation error for visualization: {e}")
            return None
    
    def compute_water_profile_for_display(self, params):
        """Calculate water table values along the profile for visualization.
        
        Returns:
            List of elevations (float) or None if it cannot be calculated
        """
        if not self.profile_distances or not self.profile_elevations:
            return None
        
        try:
            # Create ground_surface function
            ground_surface = self._create_ground_surface_function(
                self.profile_distances, 
                self.profile_elevations
            )
            
            mode = params.get('water_definition_mode', 0)
            
            if mode == 0:  # Constant depth
                depth = params.get('water_const_depth', 2.0)
                return [ground_surface(x) - depth for x in self.profile_distances]
            
            elif mode == 1:  # From raster
                raster_layer = params.get('water_raster_layer')
                if not raster_layer or not self.profile_p1 or not self.profile_p2:
                    return None
                
                # Sample raster along profile
                depths = []
                provider = raster_layer.dataProvider()
                band = 1
                no_data = provider.sourceNoDataValue(band)
                extent_length = self.profile_p1.distance(self.profile_p2)
                
                raster_crs = raster_layer.crs()
                project_crs = QgsProject.instance().crs()
                need_transform = project_crs.isValid() and raster_crs.isValid() and (project_crs != raster_crs)
                transformer = None
                if need_transform:
                    transformer = QgsCoordinateTransform(project_crs, raster_crs, QgsProject.instance())
                
                for d in self.profile_distances:
                    t = d / extent_length if extent_length > 0 else 0
                    x = self.profile_p1.x() + (self.profile_p2.x() - self.profile_p1.x()) * t
                    y = self.profile_p1.y() + (self.profile_p2.y() - self.profile_p1.y()) * t
                    pt = QgsPointXY(x, y)
                    
                    if transformer is not None:
                        try:
                            pt = transformer.transform(pt)
                        except Exception:
                            depths.append(2.0)  # Default
                            continue
                    
                    val = self._sample_with_bilinear(provider, pt, band, no_data, raster_layer)
                    depths.append(val if val is not None else 2.0)
                
                # Calculate water table elevations
                return [ground_surface(x) - depth for x, depth in zip(self.profile_distances, depths)]
            
            elif mode == 2:  # Absolute elevation
                elevation = params.get('water_elevation', 0.0)
                return [elevation] * len(self.profile_distances)
            
        except Exception as e:
            print(f"Water table profile calculation error for visualization: {e}")
            return None
    
    def _create_soil_properties_and_state(self, params, ground_surface, layer2_interface=None, water_table=None):
        """Create Soil for the updated LEM interface."""

        gamma_w = 9.81

        def _arr(v):
            return np.asarray(v, dtype=float)

        def saturation(x, y):
            x_arr = _arr(x)
            y_arr = _arr(y)
            if params.get('enable_water', False) and water_table is not None:
                water_y = _arr(water_table(x_arr))
                return np.where(y_arr <= water_y, 1.0, 0.0)
            return np.zeros_like(y_arr, dtype=float)

        def pore_pressure(x, y):
            x_arr = _arr(x)
            y_arr = _arr(y)
            if params.get('enable_water', False) and water_table is not None:
                water_y = _arr(water_table(x_arr))
                return np.maximum(gamma_w * (water_y - y_arr), 0.0)
            return np.zeros_like(y_arr, dtype=float)

        def cohesion(x, y):
            x_arr = _arr(x)
            y_arr = _arr(y)
            if params.get('enable_layer2', False) and layer2_interface is not None:
                interface_y = _arr(layer2_interface(x_arr))
                return np.where(y_arr > interface_y, params['cohesion'], params.get('cohesion_2', 50.0))
            return params['cohesion'] * np.ones_like(y_arr, dtype=float)

        def friction_angle(x, y):
            x_arr = _arr(x)
            y_arr = _arr(y)
            if params.get('enable_layer2', False) and layer2_interface is not None:
                interface_y = _arr(layer2_interface(x_arr))
                return np.where(y_arr > interface_y, params['friction_angle'], params.get('friction_angle_2', 30.0))
            return params['friction_angle'] * np.ones_like(y_arr, dtype=float)

        def vertical_cohesion(x, y_bot, y_top):
            return cohesion(x, y_bot)

        def vertical_friction_angle(x, y_bot, y_top):
            return friction_angle(x, y_bot)

        def column_weight(x, y):
            x_arr = _arr(x)
            y_arr = _arr(y)
            gnd = _arr(ground_surface(x_arr))

            if params.get('enable_layer2', False) and layer2_interface is not None:
                interface_y = _arr(layer2_interface(x_arr))
                depth_1 = np.maximum(0.0, gnd - np.maximum(y_arr, interface_y))
                depth_2 = np.maximum(0.0, np.minimum(gnd, interface_y) - y_arr)

                sat_mid_1 = saturation(x_arr, gnd - 0.5 * depth_1)
                sat_mid_2 = saturation(x_arr, interface_y - 0.5 * depth_2)
                gamma_1 = params['gamma'] + params['porosity'] * sat_mid_1 * gamma_w
                gamma_2 = params.get('gamma_2', 22.0) + params.get('porosity_2', 0.25) * sat_mid_2 * gamma_w
                return gamma_1 * depth_1 + gamma_2 * depth_2

            depth = np.maximum(0.0, gnd - y_arr)
            sat_mid = saturation(x_arr, gnd - 0.5 * depth)
            gamma = params['gamma'] + params['porosity'] * sat_mid * gamma_w
            return gamma * depth

        return Soil(
            cohesion=cohesion,
            vertical_cohesion=vertical_cohesion,
            friction_angle=friction_angle,
            vertical_friction_angle=vertical_friction_angle,
            pore_pressure=pore_pressure,
            saturation=saturation,
            column_weight=column_weight,
        )
    
    def _format_stratigraphy_info(self, params):
        """Formatta le informazioni di stratigrafia per l'output."""
        info = []
        
        if params.get('enable_layer2', False):
            info.append("STRATIGRAPHY:")
            info.append(f"- Second layer active")
            mode = params.get('layer2_definition_mode', 0)
            if mode == 0:
                info.append(f"  Depth: {params.get('layer2_const_depth', 5.0):.2f} m from ground surface")
            elif mode == 1:
                info.append(f"  Depth from raster")
            elif mode == 2:
                info.append(f"  Absolute elevation: {params.get('layer2_elevation', 0.0):.2f} m")
            info.append(f"  Layer 2 parameters: γ₂={params.get('gamma_2', 22.0):.1f} kN/m³, c₂={params.get('cohesion_2', 50.0):.1f} kPa, φ₂={params.get('friction_angle_2', 30.0):.1f}°, n₂={params.get('porosity_2', 0.25):.2f}")
        
        if params.get('enable_water', False):
            if not info:
                info.append("")
            info.append("WATER TABLE:")
            info.append(f"- Water table active")
            mode = params.get('water_definition_mode', 0)
            if mode == 0:
                info.append(f"  Depth: {params.get('water_const_depth', 2.0):.2f} m from ground surface")
            elif mode == 1:
                info.append(f"  Depth from raster")
            elif mode == 2:
                info.append(f"  Absolute elevation: {params.get('water_elevation', 0.0):.2f} m")
        
        return '\n'.join(info) if info else ""

    def _update_profile_with_all_surfaces(self):
        """Update profile graph with all calculated critical surfaces."""
        if not self.profile_distances or not self.profile_elevations:
            return
        
        surfaces_list = []
        for idx, s in enumerate(self.slip_surfaces):
            search_type = s.get('search')
            method_label = s.get('method')
            color = s.get('color') or self._get_color_for_surface(idx, search_type, method_label)
            label = s.get('label') or self._format_surface_label(search_type, method_label, s.get('fs'), color)
            surfaces_list.append({'x': s.get('x', []), 'y': s.get('y', []), 'color': color, 'label': label})

        if surfaces_list:
            self.dlg.updateProfile(self.profile_distances, self.profile_elevations, slip_surfaces_list=surfaces_list)
        else:
            self.dlg.updateProfile(self.profile_distances, self.profile_elevations)

    def _get_solver(self, method_name):
        """Return (label, solver_function) based on UI selection."""
        name = (method_name or '').strip().lower()
        if 'morgen' in name or 'morger' in name:
            return ('Morgenstern-Price', morgerstern_price)
        if 'spencer' in name:
            return ('Spencer', spencer)
        return ('Bishop', bishop)

    def _get_color_for_surface(self, index, search_type, method_label):
        """Generate unique color for each surface maintaining cool/warm distinction.
        
        Color palette:
        - Grid (cool): blue, cyan, green, azure, turquoise...
        - Simplex (warm): red, orange, pink, magenta, coral, crimson...
        """
        # Extended palette for grid (cool colors)
        grid_palette = [
            '#1f77b4',  # blu
            '#17becf',  # ciano
            '#2ca02c',  # verde
            '#00CED1',  # turchese scuro
            '#4682B4',  # blu acciaio
            '#20B2AA',  # verde acqua chiaro
            '#5F9EA0',  # blu cadetto
            '#008B8B',  # ciano scuro
            '#00BFFF',  # azzurro intenso
            '#4169E1',  # blu reale
            '#6495ED',  # blu fiordaliso
            '#87CEEB',  # celeste
        ]
        
        # Extended palette for simplex (warm colors)
        simplex_palette = [
            '#d62728',  # rosso
            '#ff7f0e',  # arancione
            '#e377c2',  # rosa
            '#DC143C',  # cremisi
            '#FF6347',  # pomodoro
            '#FF4500',  # arancione rosso
            '#FF69B4',  # rosa caldo
            '#DB7093',  # viola pallido
            '#CD5C5C',  # rosso indiano
            '#F08080',  # corallo chiaro
            '#FA8072',  # salmone
            '#FFA07A',  # salmone chiaro
        ]
        
        # Select appropriate palette
        palette = simplex_palette if search_type == 'simplex' else grid_palette
        
        # Use index to select color (with wrapping if necessary)
        color = palette[index % len(palette)]
        
        return color

    def _surface_search_label(self, search_type):
        st = (search_type or '').strip().lower()
        return 'Simplex' if st == 'simplex' else 'Grid'

    def _format_surface_label(self, search_type, method_label, fos, color):
        search_lbl = self._surface_search_label(search_type)
        method_lbl = str(method_label) if method_label is not None else 'Unknown'
        try:
            fos_val = float(fos)
            fos_part = f"FoS={fos_val:.3f}"
        except Exception:
            fos_part = f"FoS={fos}"
        return f"{search_lbl} – {method_lbl} ({fos_part})"

    def _draw_plan_surface(self, x_in, x_out, fs, color='#ff0000'):
        """Draw segment corresponding to critical surface on plan and add FS label.
        x_in/x_out are distances along profile (from P1 to P2).
        """
        try:
            if self.profile_p1 is None or self.profile_p2 is None:
                return
            # Ensure float
            x_in = float(x_in)
            x_out = float(x_out)
            # Profile length and t parameter
            p1 = self.profile_p1
            p2 = self.profile_p2
            extent_length = p1.distance(p2)
            if extent_length == 0:
                return
            t_in = min(max(x_in / extent_length, 0.0), 1.0)
            t_out = min(max(x_out / extent_length, 0.0), 1.0)
            pin = QgsPointXY(p1.x() + (p2.x() - p1.x()) * t_in, p1.y() + (p2.y() - p1.y()) * t_in)
            pout = QgsPointXY(p1.x() + (p2.x() - p1.x()) * t_out, p1.y() + (p2.y() - p1.y()) * t_out)

            # Plan line
            try:
                rb = QgsRubberBand(self.iface.mapCanvas(), QgsWkbTypes.LineGeometry)
                rb.setColor(QColor(color))
                rb.setWidth(3)
                rb.addPoint(pin)
                rb.addPoint(pout)
                self.surface_rubber_bands.append(rb)
            except Exception:
                pass

            # Label: add a QGraphicsSimpleTextItem to map canvas scene
            try:
                from qgis.PyQt.QtWidgets import QGraphicsSimpleTextItem
                from qgis.PyQt.QtGui import QFont
                midx = (pin.x() + pout.x()) / 2.0
                midy = (pin.y() + pout.y()) / 2.0
                # Convert map coordinates to screen coordinates using getCoordinateTransform
                canvas = self.iface.mapCanvas()
                scene_x = None
                scene_y = None
                try:
                    ct = canvas.getCoordinateTransform()
                    pt_px = ct.transform(QgsPointXY(midx, midy))
                    scene_x = pt_px.x()
                    scene_y = pt_px.y()
                except Exception:
                    # Fallback: try mapToPixel (if available)
                    try:
                        p = canvas.mapToPixel(QgsPointXY(midx, midy))
                        scene_x = p.x()
                        scene_y = p.y()
                    except Exception:
                        pass

                if scene_x is not None and scene_y is not None:
                    txt = QGraphicsSimpleTextItem(f"FoS={fs:.3f}")
                    font = QFont()
                    font.setPointSize(10)
                    font.setBold(True)
                    txt.setFont(font)
                    txt.setBrush(QColor(color))
                    
                    # Calculate position with overlap avoidance
                    pos_x = scene_x + 6
                    pos_y = scene_y - 12
                    step_y = 15
                    
                    # Simple collision detection
                    max_iter = 50
                    for _ in range(max_iter):
                        collision = False
                        for item in self.surface_label_items:
                            try:
                                # Skip items not in the same scene or invalid
                                if item.scene() != canvas.scene():
                                    continue
                                    
                                ipos = item.pos()
                                # Check logic: close in X (<40px) and close in Y (<12px)
                                if abs(ipos.x() - pos_x) < 40 and abs(ipos.y() - pos_y) < 12:
                                    collision = True
                                    break
                            except Exception:
                                continue
                        
                        if collision:
                            pos_y += step_y
                        else:
                            break
                            
                    txt.setPos(pos_x, pos_y)
                    canvas.scene().addItem(txt)
                    self.surface_label_items.append(txt)
                else:
                    # If conversion fails, write to status
                    self.dlg.setStatus(f"FoS={fs:.3f} (label not placed)")
            except Exception:
                try:
                    self.dlg.setStatus(f"FoS={fs:.3f}")
                except Exception:
                    pass
        except Exception as e:
            print(f"_draw_plan_surface error: {e}")

    def _store_surface(self, search, method_label, x, y, fs):
        """Add calculated surface to list, maintaining history.
        Also draw representative segment on plan and FS label."""
        try:
            surface_index = len(self.slip_surfaces)
            color = self._get_color_for_surface(surface_index, search, method_label)
            label = self._format_surface_label(search, method_label, fs, color)
            self.slip_surfaces.append({
                'search': search,
                'method': method_label,
                'x': x,
                'y': y,
                'fs': float(fs),
                'color': color,
                'label': label,
            })
            # Try to draw on plan (segment from x_out to x_in along profile)
            try:
                # Calculate x_in/x_out: x values refer to distances along profile (usually in meters)
                if hasattr(x, '__len__') and len(x) >= 2:
                    x_start = float(x[0])
                    x_end = float(x[-1])
                    # Determine entry/exit: take min/max
                    x_out = min(x_start, x_end)
                    x_in = max(x_start, x_end)
                else:
                    # If no geometry, no drawing
                    return

                fs_val = float(fs)
                self._draw_plan_surface(x_in, x_out, fs_val, color)
            except Exception as e:
                print(f"Warning: cannot draw surface on plan: {e}")
        except Exception:
            pass

