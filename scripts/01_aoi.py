"""The map extent and its base map.

Extent: a rectangle from the Bitterroot River to Baldy Mountain. West edge x 252,000, east edge x 258,000 (247 m east
of the summit, GNIS 779302 at x 257,753, y 282,896), north edge y 286,000; the south edge is the Ranch's south limit
west of the summit (y 278,098.9, from the Ranch boundary in data/reference/, which is kept out of git; without it
the recorded value is used). The extent takes in the river, its floodplain forest and some land outside the Ranch;
the Ranch part is stored with the boundaries, out of git.
Grid: 0.6 m from the extent's top-left corner (scripts/common.py map_grid), 10,000 x 13,169 pixels.
Base map: USGS National Map Imagery Topo tiles (zoom 14), warped to EPSG:6514 at 6 m, for figures.

Outputs: reference/aoi.gpkg (the extent), data/reference/ranch_in_aoi.gpkg, data/reference/basemap_usgs_imagerytopo_6m.tif
Run: python scripts/01_aoi.py [--out DIR]   (DIR replaces reference/ and data/reference/, for checking)
"""

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from shapely.geometry import box

from paths import BASEMAP, RANCH_BOUNDARY_PLANNER, RANCH_IN_AOI, REFERENCE

SUMMIT = (257753.0, 282896.0)           # Baldy Mountain, GNIS, EPSG:6514
WEST, EAST, NORTH = 252000.0, 258000.0, 286000.0
SOUTH_RECORDED = 278098.9105534577      # the Ranch's south limit west of the summit, as computed from the boundary
BASEMAP_RES, BASEMAP_MARGIN_M = 6.0, 1000.0   # matches the stored base map (x 251,000-258,998, y 277,004-287,000)
TILES = ("<GDAL_WMS><Service name='TMS'><ServerUrl>https://basemap.nationalmap.gov/arcgis/rest/services/"
         "USGSImageryTopo/MapServer/tile/${z}/${y}/${x}</ServerUrl></Service><DataWindow>"
         "<UpperLeftX>-20037508.34</UpperLeftX><UpperLeftY>20037508.34</UpperLeftY>"
         "<LowerRightX>20037508.34</LowerRightX><LowerRightY>-20037508.34</LowerRightY>"
         "<TileLevel>14</TileLevel><TileCountX>1</TileCountX><TileCountY>1</TileCountY>"
         "<YOrigin>top</YOrigin></DataWindow><Projection>EPSG:3857</Projection><BlockSizeX>256</BlockSizeX>"
         "<BlockSizeY>256</BlockSizeY><BandsCount>3</BandsCount><ZeroBlockHttpCodes>404</ZeroBlockHttpCodes>"
         "</GDAL_WMS>")


def extent():
    """The extent, and its Ranch part (None without the private boundary file)."""
    if not RANCH_BOUNDARY_PLANNER.exists():
        return box(WEST, SOUTH_RECORDED, EAST, NORTH), None
    ranch = gpd.read_file(RANCH_BOUNDARY_PLANNER).to_crs(6514).geometry.iloc[0]
    south = ranch.intersection(box(0, 0, SUMMIT[0], NORTH)).bounds[1]
    poly = box(WEST, south, EAST, NORTH)
    return poly, ranch.intersection(poly)


def basemap(bounds, path):
    x0, y0 = np.floor(bounds[0] / 100) * 100, np.floor(bounds[1] / 100) * 100
    x1, y1 = np.ceil(bounds[2] / 100) * 100, np.ceil(bounds[3] / 100) * 100
    t = Affine(BASEMAP_RES, 0, x0, 0, -BASEMAP_RES, y1)
    w, h = int((x1 - x0) / BASEMAP_RES), int((y1 - y0) / BASEMAP_RES)
    with rasterio.open(TILES) as src, WarpedVRT(src, crs="EPSG:6514", transform=t, width=w, height=h,
                                                resampling=Resampling.bilinear) as v:
        img = v.read()
    if not img.any():   # failed tiles come back as zeros (ZeroBlockHttpCodes); as of 2026-11-19 this service 404s
        raise SystemExit("base map download came back blank: the USGS Imagery Topo tile service returned no tiles. "
                         "Keep the stored base map, or point TILES at USGSImageryOnly.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=3, dtype="uint8", crs="EPSG:6514",
                       transform=t, compress="deflate") as d:
        d.write(img)
    print(f"base map {w} x {h} px -> {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    aoi_path = (args.out or REFERENCE) / "aoi.gpkg"
    ranch_path = args.out / RANCH_IN_AOI.name if args.out else RANCH_IN_AOI
    basemap_path = args.out / BASEMAP.name if args.out else BASEMAP
    poly, ranch_in = extent()
    aoi_path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame({"name": ["aoi"]}, geometry=[poly], crs=6514).to_file(aoi_path)
    print(f"extent {poly.area / 1e6:.1f} km², bounds {np.round(poly.bounds, 1)}")
    if ranch_in is not None:
        ranch_path.parent.mkdir(parents=True, exist_ok=True)
        gpd.GeoDataFrame({"name": ["ranch_in_aoi"]}, geometry=[ranch_in], crs=6514).to_file(ranch_path)
        print(f"Ranch part {ranch_in.area / 1e6:.1f} km² -> {ranch_path}")
    if args.out or not basemap_path.exists():
        basemap(poly.buffer(BASEMAP_MARGIN_M).bounds, basemap_path)


if __name__ == "__main__":
    main()
