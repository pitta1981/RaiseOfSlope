# Raise of Slopes – QGIS Plugin

A QGIS plugin for slope stability analysis using limit equilibrium methods (Bishop, Morgenstern-Price, Spencer).

## Features

### Elevation Profile
- Select two points on a DEM
- Profile sampling with CRS and nodata handling
- Graphical display and export to CSV / image / DXF

### Stability Analysis
- Grid search and simplex optimisation
- Geotechnical parameters for 1 or 2 soil layers
- Optional water table (constant depth, raster, or absolute elevation)
- Project save/load (`.rslope` format)

### Hazard Map
- Factor of Safety (FoS) raster accumulation across the slope
- Depth raster styling for visualisation

### Profile Report
- Legend and table of geotechnical parameters
- Export-ready graphical output

## Architecture

The plugin relies exclusively on the external framework:

`external/gwf-le/src/LEM`

and the critical surface search modules in:

`external/gwf-le/src/searchCriticalF`

No local fallbacks or copies of the LE modules are present.

## Installation (development)

1. Clone the repository including the `external/gwf-le` submodule.
2. Copy the plugin folder to the QGIS plugins directory or use the provided install script.

macOS example – manual copy:

```bash
cp -r /path/to/RaiseOfSlope ~/Library/Application\ Support/QGIS/QGIS3/profiles/default/python/plugins/RaiseOfSlopes
```

For QGIS 4 use the `QGIS4` profile folder instead of `QGIS3`.

Or, from the plugin root:

```bash
python install_plugin.py            # copy to default directory (QGIS 3 profile)
python install_plugin.py --qgis 4   # copy to the QGIS 4 profile
python install_plugin.py --dest /path/to/plugins
python install_plugin.py --zip      # generate RaiseOfSlopes.zip
```

Then enable the plugin in QGIS via `Plugins > Manage and Install Plugins`.

## Quick Start

1. Load a DEM layer.
2. Open the plugin and select two points on the map.
3. The elevation profile is computed automatically.
4. Run the analysis from `Grid Analysis` or `Simplex Analysis`.

## Dependencies

- QGIS 3.34+ (Qt5) or QGIS 4.x (Qt6)
- NumPy, SciPy, Matplotlib (QGIS Python environment)
- LE modules in `external/gwf-le/src/LEM` and `external/gwf-le/src/searchCriticalF`

## Troubleshooting

### LE module import error

Verify that the following directories exist and contain the expected modules (`lemInterface.py`, `gleMethods.py`, `searchInterface.py`, `circularSlipSurfaces.py`):

- `external/gwf-le/src/LEM`
- `external/gwf-le/src/searchCriticalF`

### Analysis does not start

- Confirm the elevation profile has been computed
- Check bounds and geotechnical parameter values
- Read the QGIS Python console for detailed error output

## Key Files

| File | Purpose |
|------|---------|
| `raise_of_slopes_plugin.py` | Main plugin logic |
| `ui/profile_dialog.py` | User interface |
| `debug_stability_standalone.py` | Standalone debug outside QGIS |
| `INTEGRATION_NOTES.md` | Technical integration notes |

## Citation

If you use this plugin in your research, please cite the following papers on which the present work is based:

>[1] Pittaluga, S., Lalicata, L.M., Bressan, A., Tamellini, L., Gallipoli, D.,
> **Large-Scale Slope Equilibrium Analysis Within a GIS Framework.**
> In: Monaco, P., Chiaradonna, A. (eds) Prediction and Performance in Geotechnical Engineering -  Proceedings of the 9th Italian National Conference of the Researchers of Geotechnical Engineering CNRIG 2026. Springer Series in Geomechanics and Geoengineering. Springer, Cham. 
> https://doi.org/10.1007/978-3-032-30669-2_14
> [2] Lalicata, L. M., Bressan, A., Pittaluga, S., Tamellini, L., & Gallipoli, D. (2025).
> **An Efficient Slope Stability Algorithm with Physically Consistent Parametrisation of Slip Surfaces.**
> *International Journal of Civil Engineering*, 23, 671–682.
> https://doi.org/10.1007/s40999-024-01053-1