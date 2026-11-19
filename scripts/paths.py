"""Where everything lives. data/ is gitignored and listed in catalog/manifest.csv; reference/ is small and in git.

data/products/    the published maps; mirrors the public bucket (naip/, fixed_wing/, derived/)
data/inputs/      model inputs: NAIP DOQQ clips by year, fixed-wing at 0.6 m, conditioning rasters
data/predictions/ per-tile model output, before mosaicking
data/validation/  the LiDAR check: model runs on the validation inputs, the 2023 official tile, LiDAR CHM clips
data/reference/   base map, 5 m terrain and draw rasters, and the Ranch boundaries (kept out of git)
reference/        small vectors: map extent, DOQQ footprints, draws, wire corridors
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

PRODUCTS = DATA / "products"
NAIP_MAPS, FIXED_WING_MAPS, DERIVED = PRODUCTS / "naip", PRODUCTS / "fixed_wing", PRODUCTS / "derived"

INPUTS = DATA / "inputs"
NAIP_INPUTS, FIXED_WING_INPUTS, CONDITIONING = INPUTS / "naip", INPUTS / "fixed_wing", INPUTS / "conditioning"

PREDICTIONS = DATA / "predictions"
VALIDATION = DATA / "validation"
LIDAR = VALIDATION / "lidar"

TERRAIN = DATA / "reference" / "terrain"
BASEMAP = DATA / "reference" / "basemap_usgs_imagerytopo_6m.tif"
# Ranch boundaries are private: in data/ (gitignored, archived privately), not in reference/
RANCH_BOUNDARY = DATA / "reference" / "ranch_boundary.geojson"                  # "on the Ranch" in validation
RANCH_BOUNDARY_PLANNER = DATA / "reference" / "ranch_boundary_planner.geojson"  # set the extent's south edge
RANCH_IN_AOI = DATA / "reference" / "ranch_in_aoi.gpkg"

REFERENCE = ROOT / "reference"
CATALOG = ROOT / "catalog"
