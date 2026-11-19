"""NAIP model inputs: DOQQ clips for the maps, and the NAIP inputs of the validation areas.

Maps: every 4-band NAIP year over the map extent (2009-2021; 2023 not run). Every DOQQ that touches the extent
buffered by 150 m is clipped on its own grid and CRS, as the authors run the model, and written as plain 4-band RGBN:
the authors' resampling path fails on 5-band input that isn't 0.6 m, so no mask band is added. Clips keep the DOQQ's
own nodata (all bands 0) where the buffer reaches past it; 06_mosaic masks it.
  2011-2021: Planetary Computer (STAC "naip"; COGs read directly from the public blob).
  2009: Earth Engine only (USDA/NAIP/DOQQ), read with getPixels on each asset's native UTM grid; needs `ee` auth.
Acquisition date: each clip is named naip_{YYYYMMDD}_{quad}.tif with its DOQQ's acquisition date (the first date in
the NAIP name; the second is the processing date). The model reads day of year from that name. Some years mix dates
across DOQQs; reference/doqq_footprints.gpkg records each one.

Validation (areas in common.VALIDATION):
  2016, 2020: DOQQ clips, as for the maps (2016-08-16 1 m ne/se; 2019-09-15 0.6 m se; Montana has no 2020 NAIP).
  2021: the 8 NAIP 2021-08-29 DOQQs share one 0.6 m grid (EPSG:26912), so they are merged without resampling;
        band 5 is a mask (any band > 0). The official NAIP-CHM 2023 tiles over the same area are merged likewise.
  2023: one DOQQ and its official NAIP-CHM tile, to check that local inference reproduces the published product.

Outputs: data/inputs/naip/{year}/naip_{YYYYMMDD}_{quad}.tif, reference/doqq_footprints.gpkg,
         data/validation/inputs_{2016,2020,2021}/, data/validation/official_2023/
Run: python scripts/02_naip_inputs.py [maps|validation] [--years Y ...] [--out DIR]
     (--out writes clips under DIR instead, for checking; existing clips are skipped)
"""

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from rasterio.merge import merge
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds
from shapely.geometry import mapping, shape

import common as c
from paths import NAIP_INPUTS, REFERENCE, VALIDATION

YEARS = (2009, 2011, 2013, 2015, 2016, 2017, 2019, 2021)
EE_COLLECTION, EE_TILE = "USDA/NAIP/DOQQ", 2048     # px per getPixels request (4 bands: 16 MB)


def parse(name):
    """m_4611424_ne_11_1_20110910[_20111026] -> quad, UTM zone, acquisition date."""
    p = name.removeprefix("mt_").split("_")
    return f"{p[1]}_{p[2]}", int(p[3]), p[5]


# ---- maps -----------------------------------------------------------------------------------------------------------
def find_doqqs(region, years, inputs):
    ll = gpd.GeoSeries([region], crs=6514).to_crs(4326).iloc[0]
    rows, body = [], {"collections": ["naip"], "intersects": mapping(ll), "limit": 250}
    while True:
        r = requests.post(c.STAC, json=body, timeout=120).json()
        for f in r["features"]:
            year = int(f["properties"]["naip:year"])
            if year in years and year != 2009:
                rows.append({"year": year, "id": f["id"], "source": "planetary computer", "gsd_m": f["properties"]["gsd"],
                             "href": f["assets"]["image"]["href"], "footprint": shape(f["geometry"])})
        nxt = [link for link in r.get("links", []) if link["rel"] == "next"]
        if not nxt:
            break
        body = nxt[0]["body"]
    if 2009 in years:
        import ee
        ee.Initialize()
        col = ee.ImageCollection(EE_COLLECTION).filterBounds(ee.Geometry(mapping(ll))).filterDate("2009-01-01", "2010-01-01")
        for f in col.map(lambda i: ee.Feature(i.geometry(), {"id": i.id()})).getInfo()["features"]:
            rows.append({"year": 2009, "id": f["properties"]["id"], "source": "earth engine", "gsd_m": 1.0,
                         "href": f"{EE_COLLECTION}/{f['properties']['id']}", "footprint": shape(f["geometry"])})
    df = gpd.GeoDataFrame(rows, geometry="footprint", crs=4326).to_crs(6514)
    df[["quad", "utm_zone", "acq_date"]] = pd.DataFrame([parse(i) for i in df["id"]], index=df.index)
    df["doy"] = [c.day_of_year(d) for d in df["acq_date"]]
    df["input"] = [(inputs / str(y) / f"naip_{d}_{q}.tif").as_posix() for y, d, q in zip(df.year, df.acq_date, df.quad)]
    return df.sort_values(["year", "quad"]).reset_index(drop=True)


def clip_pc(href, bounds):
    with rasterio.open(f"/vsicurl/{href}") as src:
        win = c.clip_window(src, bounds)
        return src.read([1, 2, 3, 4], window=win), src.crs, src.window_transform(win)


def clip_ee(asset, bounds):
    import ee
    info = ee.Image(asset).getInfo()["bands"][0]
    crs, t = info["crs"], rasterio.Affine(*info["crs_transform"])
    w, h = info["dimensions"]
    b = transform_bounds("EPSG:6514", crs, *bounds, densify_pts=21)
    win = from_bounds(*b, transform=t).round_offsets().round_lengths().intersection(Window(0, 0, w, h))
    c0, r0, cw, rh = int(win.col_off), int(win.row_off), int(win.width), int(win.height)
    arr = np.zeros((4, rh, cw), np.uint8)
    for r in range(0, rh, EE_TILE):
        for col in range(0, cw, EE_TILE):
            tw, th = min(EE_TILE, cw - col), min(EE_TILE, rh - r)
            x, y = t * (c0 + col, r0 + r)
            px = ee.data.getPixels({"assetId": asset, "fileFormat": "NUMPY_NDARRAY", "bandIds": ["R", "G", "B", "N"],
                                    "grid": {"dimensions": {"width": tw, "height": th}, "crsCode": crs,
                                             "affineTransform": {"scaleX": t.a, "shearX": 0, "translateX": x,
                                                                 "shearY": 0, "scaleY": t.e, "translateY": y}}})
            for i, band in enumerate("RGBN"):
                arr[i, r:r + th, col:col + tw] = px[band]
    return arr, rasterio.crs.CRS.from_string(crs), t * rasterio.Affine.translation(c0, r0)


def maps(years, inputs, footprints):
    extent = c.map_extent()
    region = extent.buffer(c.BUFFER_M, join_style="mitre")
    df = find_doqqs(region, years, inputs)
    df["aoi_share_pct"] = [100 * f.intersection(extent).area / extent.area for f in df.footprint]
    df["centroid_x"], df["centroid_y"] = df.footprint.centroid.x, df.footprint.centroid.y
    for row in df.itertuples():
        out = Path(row.input)
        if out.exists():
            continue
        clip = clip_ee if row.source == "earth engine" else clip_pc
        arr, crs, transform = clip(row.href, region.bounds)
        c.write_input(out, arr, crs, transform)
        print(f"{row.year} {row.quad} {row.acq_date} ({row.gsd_m:g} m, {row.source}): {arr.shape[2]}x{arr.shape[1]} px, "
              f"{100 * (arr == 0).all(0).mean():.1f}% nodata -> {out}")
    if footprints:
        df.drop(columns="href").to_file(footprints)
    print(df.groupby("year").agg(n_doqq=("quad", "size"), dates=("acq_date", lambda s: ", ".join(sorted(set(s)))),
                                 gsd_m=("gsd_m", "first")).to_string())


# ---- validation -----------------------------------------------------------------------------------------------------
def validation_clip(url, area, out_dir):
    name = Path(url).stem                                   # m_4611424_se_11_1_20160816
    quad, acq = "_".join(name.split("_")[1:3]), name.split("_")[-1]
    out = out_dir / f"naip_{acq}_{quad}.tif"
    if out.exists():
        return
    arr, crs, transform = clip_pc(url, c.buffered(area))
    assert (arr > 0).any(0).all(), f"{name}: clip has nodata"
    c.write_input(out, arr, crs, transform)
    print(f"NAIP {quad} {acq}: {arr.shape[2]}x{arr.shape[1]} px -> {out}")


def merged(urls, area, nodata):
    bounds = c.snap_out(transform_bounds("EPSG:6514", "EPSG:26912", *c.buffered(area), densify_pts=21))
    srcs = [rasterio.open(f"/vsicurl/{u}") for u in urls]
    arr, transform = merge(srcs, bounds=bounds, res=c.RES, nodata=nodata, method="first")
    crs = srcs[0].crs
    for s in srcs:
        s.close()
    return arr, crs, transform


def download(url, out):
    if out.exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 22):
                f.write(chunk)
    tmp.rename(out)
    print(f"downloaded {out.name} ({out.stat().st_size / 1e6:.0f} MB)")


def validation(root, years):
    for year, v in c.VALIDATION.items():
        if years and int(year) not in years:
            continue
        out_dir = root / f"inputs_{year}"
        for url in v.get("naip", []):
            validation_clip(url, v["area"], out_dir)
        if "naip_mosaic" in v:
            out = out_dir / "naip_20210829.tif"
            if not out.exists():
                arr, crs, t = merged(v["naip_mosaic"], v["area"], 0)
                mask = np.where((arr > 0).any(0), 255, 0).astype(np.uint8)
                c.write_input(out, np.concatenate([arr, mask[None]]), crs, t)
                print(f"NAIP 2021: {arr.shape[2]}x{arr.shape[1]} px, {100 * (mask > 0).mean():.1f}% valid -> {out}")
            out = out_dir / "naipchm_official_20231019.tif"
            if not out.exists():
                arr, crs, t = merged(v["official_2023"], v["area"], c.NODATA)
                with rasterio.open(out, "w", **c.INPUT_PROFILE, width=arr.shape[2], height=arr.shape[1], count=1,
                                   dtype="uint16", nodata=c.NODATA, crs=crs, transform=t) as dst:
                    dst.write(arr)
                print(f"official NAIP-CHM 2023: {arr.shape[2]}x{arr.shape[1]} px -> {out}")
    if not years or 2023 in years:
        download(f"{c.NAIP_BLOB}/2023/mt_060cm_2023/46113/{c.VALID_DOQQ}.tif", root / "official_2023" / f"{c.VALID_DOQQ}.tif")
        download(c.OFFICIAL_2023.format(name=c.VALID_DOQQ), root / "official_2023" / f"{c.VALID_DOQQ}_chm_official.tif")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("set", nargs="?", choices=("maps", "validation"))
    ap.add_argument("--years", type=int, nargs="+")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    if args.set in (None, "maps"):
        inputs = args.out / "naip" if args.out else NAIP_INPUTS
        footprints = None if args.out else REFERENCE / "doqq_footprints.gpkg"
        maps(tuple(args.years or YEARS), inputs, footprints)
    if args.set in (None, "validation"):
        validation(args.out / "validation" if args.out else VALIDATION, set(args.years or ()))


if __name__ == "__main__":
    main()
