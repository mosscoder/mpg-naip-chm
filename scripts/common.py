"""Settings and helpers shared by the pipeline scripts (01-07).

Two sets of runs share every step:
  maps        the products: every NAIP and fixed-wing year over the map extent (reference/aoi.gpkg)
  validation  three areas where LiDAR, fixed-wing and NAIP overlap (VALIDATION below), plus one 2023 DOQQ to
              compare local inference with the authors' official tile
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window, from_bounds

from paths import REFERENCE, ROOT, VALIDATION as VALIDATION_DIR

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MULTIRANGE", "YES")

# ---- grid and files -------------------------------------------------------------------------------------------------
RES = 0.6                               # the model's inference resolution, and every product's pixel
NODATA = 65535
BUFFER_M = 150.0                        # inputs reach this far past an area, so its pixels are interior to model chips
INPUT_PROFILE = dict(driver="GTiff", tiled=True, blockxsize=512, blockysize=512, compress="DEFLATE", predictor=2,
                     BIGTIFF="IF_SAFER")
MAP_PROFILE = dict(INPUT_PROFILE, count=2, dtype="uint16", nodata=NODATA, crs="EPSG:6514")
OVERVIEWS = [2, 4, 8, 16, 32]

# ---- the model ------------------------------------------------------------------------------------------------------
MODEL_REPO = ROOT / "external" / "naip-chm"     # git submodule, smorf-ntsg/naip-chm at 3a474f4
MODEL_WEIGHTS = MODEL_REPO / "model" / "model_20251016.pt"
MODEL_CONFIG = MODEL_REPO / "configs" / "config.yaml"
MODEL_TAG = "NAIP-CHM model_20251016.pt (Morford et al. 2026)"
CHIP_SIZE, CHIP_OVERLAP = 432, 0.3      # as in the official 2023 tiles' manifests

# ---- sources --------------------------------------------------------------------------------------------------------
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
NAIP_BLOB = "https://naipeuwest.blob.core.windows.net/naip/v002/mt"
FIXED_WING_URL = "https://storage.googleapis.com/mpg-aerial-survey/surveys/fixed_wing/cogs/clean/{year}_4band.tif"
FIXED_WING_GSD_M = 0.152
CONDITIONING_URL = "https://rangeland.ntsg.umt.edu/data/naip-chm/inference-resources/conditioning-data/{name}.tif"
CONDITIONING_LAYERS = ("climate_pca", "soil_pca", "elevation", "nlcd", "ecoregion")
CONDITIONING_PAD_M = 3000.0             # conditioning rasters are 240-928 m, so clip well past the area
OFFICIAL_2023 = "https://rangeland.ntsg.umt.edu/data/naip-chm/2023/12/{name}_chm.tif"

# ---- validation areas (EPSG:6514; each nested in the 30 m RAP grid used when they were drawn) ------------------------
# 2016 and 2020: LiDAR x fixed-wing overlaps. 2021: the 46113f8 LiDAR tile, clipped to the 2021 fixed-wing survey.
QUADS_2021 = ["4611309_sw", "4611309_se", "4611317_nw", "4611317_ne", "4611317_sw", "4611317_se", "4611318_nw",
              "4611318_sw"]
VALID_DOQQ = "m_4611317_nw_12_060_20231019_20240103"   # the 2023 DOQQ run locally and compared with its official tile
VALIDATION = {
    "2016": dict(area=(252090.0, 278160.0, 253650.0, 284760.0), fixed_wing="20160622",
                 naip=[f"{NAIP_BLOB}/2016/mt_100cm_2015/46114/m_4611424_{q}_11_1_20160816.tif" for q in ("ne", "se")],
                 conditioning="conditioning_2016_2020"),
    "2020": dict(area=(253710.0, 277980.0, 260250.0, 279150.0), fixed_wing="20200622",
                 naip=[f"{NAIP_BLOB}/2019/mt_60cm_2019/46114/m_4611424_se_11_060_20190915.tif"],
                 conditioning="conditioning_2016_2020"),
    "2021": dict(area=(256800.0, 277380.0, 265830.0, 287640.0), fixed_wing="20210629",
                 naip_mosaic=[f"{NAIP_BLOB}/2021/mt_060cm_2021/46113/m_{q}_12_060_20210829.tif" for q in QUADS_2021],
                 official_2023=[OFFICIAL_2023.format(name=f"m_{q}_12_060_20231019_20240103") for q in QUADS_2021],
                 conditioning="conditioning_2021"),
}


def validation_inputs(year):
    return VALIDATION_DIR / f"inputs_{year}"


def map_extent():
    """The map extent polygon (EPSG:6514)."""
    return gpd.read_file(REFERENCE / "aoi.gpkg").set_index("name").loc["aoi", "geometry"]


def map_grid():
    """Transform and (rows, cols) of the 0.6 m product grid: top-left at the extent's corner."""
    x0, y0, x1, y1 = map_extent().bounds
    return Affine(RES, 0, x0, 0, -RES, y1), (int(np.ceil((y1 - y0) / RES)), int(round((x1 - x0) / RES)))


def buffered(bounds, m=BUFFER_M):
    x0, y0, x1, y1 = bounds
    return x0 - m, y0 - m, x1 + m, y1 + m


def snap_out(bounds, res=RES):
    x0, y0, x1, y1 = bounds
    return (np.floor(x0 / res) * res, np.floor(y0 / res) * res, np.ceil(x1 / res) * res, np.ceil(y1 / res) * res)


def day_of_year(yyyymmdd):
    return date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:])).timetuple().tm_yday


def clip_window(src, bounds_6514):
    """Window of src covering bounds (EPSG:6514), on src's own grid, inside src."""
    b = transform_bounds("EPSG:6514", src.crs, *bounds_6514, densify_pts=21)
    win = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
    return win.intersection(Window(0, 0, src.width, src.height))


def write_input(out, arr, crs, transform):
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", **INPUT_PROFILE, width=arr.shape[2], height=arr.shape[1], count=arr.shape[0],
                       dtype="uint8", crs=crs, transform=transform) as dst:
        dst.write(arr)


# ---- conditioning ---------------------------------------------------------------------------------------------------
def clip_conditioning(bounds, out_dir):
    """Clip the authors' conditioning rasters (EPSG:5070) to bounds (EPSG:6514) plus CONDITIONING_PAD_M."""
    out_dir.mkdir(parents=True, exist_ok=True)
    b = transform_bounds("EPSG:6514", "EPSG:5070", *bounds, densify_pts=21)
    b = (b[0] - CONDITIONING_PAD_M, b[1] - CONDITIONING_PAD_M, b[2] + CONDITIONING_PAD_M, b[3] + CONDITIONING_PAD_M)
    for name in CONDITIONING_LAYERS:
        out = out_dir / f"{name}.tif"
        if out.exists():
            continue
        with rasterio.open(f"/vsicurl/{CONDITIONING_URL.format(name=name)}") as src:
            win = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
            data = src.read(window=win)
            prof = {k: v for k, v in src.profile.items() if k not in ("blockxsize", "blockysize", "tiled")}
            prof.update(width=data.shape[2], height=data.shape[1], transform=src.window_transform(win))
        with rasterio.open(out, "w", **prof) as dst:
            dst.write(data)
        print(f"conditioning {name}: {data.shape} -> {out}")


# ---- fixed-wing: 0.152 m mosaic area-averaged to 0.6 m --------------------------------------------------------------
def _fixed_wing_block(args):
    url, row0, col0, h, w, transform = args
    dst_t = transform * Affine.translation(col0, row0)
    left, top = dst_t.c, dst_t.f
    bounds = (left - 1, top - h * RES - 1, left + w * RES + 1, top + 1)
    with rasterio.open(f"/vsicurl/{url}") as src:
        win = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
        data = src.read(window=win, boundless=True, fill_value=0)
        src_t, crs = src.window_transform(win), src.crs
    rgbn = np.zeros((4, h, w), np.float32)
    for b in range(4):
        reproject(data[b].astype(np.float32), rgbn[b], src_transform=src_t, src_crs=crs, dst_transform=dst_t,
                  dst_crs=crs, resampling=Resampling.average)
    cover = np.zeros((h, w), np.float32)
    reproject((data[4] > 0).astype(np.float32), cover, src_transform=src_t, src_crs=crs, dst_transform=dst_t,
              dst_crs=crs, resampling=Resampling.average)
    valid = cover >= 0.999                                  # band 5 of the mosaic marks imaged pixels
    rgbn = np.where(valid, np.clip(np.round(rgbn), 0, 255), 0).astype(np.uint8)
    return row0, col0, rgbn, np.where(valid, 255, 0).astype(np.uint8)


def average_fixed_wing(url, bounds, out, block=2000, workers=8):
    """Fixed-wing mosaic at url, area-averaged to the 0.6 m grid over bounds (EPSG:6514): bands 1-4 RGBN, band 5 a
    mask of cells fully covered by imagery."""
    if out.exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    x0, y0, x1, y1 = bounds
    width, height = int(round((x1 - x0) / RES)), int(round((y1 - y0) / RES))
    transform = Affine(RES, 0, x0, 0, -RES, y1)
    jobs = [(url, r, c, min(block, height - r), min(block, width - c), transform)
            for r in range(0, height, block) for c in range(0, width, block)]
    n_valid = 0
    with rasterio.open(out, "w", **INPUT_PROFILE, width=width, height=height, count=5, dtype="uint8",
                       crs="EPSG:6514", transform=transform) as dst, ThreadPoolExecutor(workers) as pool:
        for i, (r, c, rgbn, mask) in enumerate(pool.map(_fixed_wing_block, jobs), 1):
            win = Window(c, r, mask.shape[1], mask.shape[0])
            dst.write(rgbn, [1, 2, 3, 4], window=win)
            dst.write(mask, 5, window=win)
            n_valid += int((mask > 0).sum())
            if i % 10 == 0 or i == len(jobs):
                print(f"  fixed-wing block {i}/{len(jobs)}")
    print(f"fixed-wing: {width}x{height} px, {100 * n_valid / (width * height):.1f}% valid -> {out}")


# ---- running the model ----------------------------------------------------------------------------------------------
def device():
    import torch
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def run_model(jobs):
    """Run the published model on each (input, conditioning dir, output dir) with the authors' process_naip_quad.
    Day of year comes from the date in each input's file name. Outputs that exist are skipped."""
    import logging
    sys.path.append(str(MODEL_REPO))
    os.environ["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"   # rio cogeo
    from src.inference_utils import StaticRasterHandler, load_model, process_naip_quad
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    dev = device()
    model = None
    for path, cond, out_dir in jobs:
        if (out_dir / f"{path.stem}_chm.tif").exists():
            logging.info(f"skipping {path.name} (done)")
            continue
        model = model or load_model(MODEL_WEIGHTS, MODEL_CONFIG, dev)
        out_dir.mkdir(parents=True, exist_ok=True)
        with StaticRasterHandler(elevation_path=cond / "elevation.tif", climate_pca_path=cond / "climate_pca.tif",
                                 soil_pca_path=cond / "soil_pca.tif", nlcd_path=cond / "nlcd.tif",
                                 ecoregion_path=cond / "ecoregion.tif") as handler:
            result = process_naip_quad(naip_quad_path=path, output_dir=out_dir, model=model, device=dev,
                                       raster_handler=handler, chip_size=CHIP_SIZE, chip_overlap=CHIP_OVERLAP)
        logging.info(f"{path.name}: {result}")
        if result["status"] != "success":
            raise RuntimeError(f"inference failed for {path.name}")
