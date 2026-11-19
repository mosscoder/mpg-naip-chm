"""Per-year maps on the 0.6 m product grid (EPSG:6514) from the model's per-tile predictions.

NAIP: each DOQQ prediction is warped to the grid (nearest). Where DOQQs overlap, a pixel takes the DOQQ whose footprint
centroid is nearest, so seams fall near the nominal quarter-quad lines and away from DOQQ edges. The file name carries
the date range of the DOQQs that supply pixels; tags list each date's DOQQs. Some DOQQs only touch the 150 m buffer
(2011-08-20, 2016-08-25, 2021-08-24), so 2016 (07-30 east, 08-16 west) and 2017 (10-15 west, 10-24 east) are the
mixed-date years. 2015 (east of about 114 W) and 2019 (west of it) cover part of the extent; the rest is nodata.
Fixed-wing: the prediction is on the product grid already (03 pads it by 150 m); it is cropped to the extent.
Both: band 1 canopy height (cm), band 2 acquisition day of year; uint16, nodata 65535; overviews 2-32 (nearest).

Outputs: data/products/naip/naip_chm_{year}_{first date}[-{last date}].tif,
         data/products/fixed_wing/fixedwing_chm_{year}_{YYYYMMDD}.tif
Run: python scripts/06_mosaic.py [naip|fixed_wing] [--years Y ...] [--out DIR]
"""

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

import common as c
from paths import FIXED_WING_MAPS, NAIP_MAPS, PREDICTIONS, REFERENCE

PAD_PX = int(round(c.BUFFER_M / c.RES))


def naip_mosaic(year, doqqs, t, shape):
    xs = (t.c + (np.arange(shape[1]) + 0.5) * c.RES).astype(np.float64)
    ys = (t.f - (np.arange(shape[0]) + 0.5) * c.RES).astype(np.float64)
    best = np.full(shape, np.inf, np.float32)
    chm = np.full(shape, c.NODATA, np.uint16)
    doy = np.full(shape, c.NODATA, np.uint16)
    source = np.full(shape, 255, np.uint8)
    for i, r in enumerate(doqqs.itertuples()):
        path = PREDICTIONS / "naip" / str(year) / f"{Path(r.input).stem}_chm.tif"
        with rasterio.open(path) as src, WarpedVRT(src, crs="EPSG:6514", transform=t, width=shape[1], height=shape[0],
                                                   resampling=Resampling.nearest, src_nodata=c.NODATA,
                                                   nodata=c.NODATA) as v:
            a = v.read(1)
        d = ((xs[None, :] - r.centroid_x) ** 2).astype(np.float32) + ((ys[:, None] - r.centroid_y) ** 2).astype(np.float32)
        take = (a != c.NODATA) & (d < best)
        best[take], chm[take], doy[take], source[take] = d[take], a[take], r.doy, i
    return chm, doy, source


def write_naip(year, chm, doy, doqqs, t, out_dir):
    dates = sorted(set(doqqs.acq_date))
    out = out_dir / (f"naip_chm_{year}_{dates[0]}" + (f"-{dates[-1]}" if len(dates) > 1 else "") + ".tif")
    with rasterio.open(out, "w", **c.MAP_PROFILE, width=chm.shape[1], height=chm.shape[0], transform=t) as dst:
        dst.write(chm, 1)
        dst.write(doy, 2)
        dst.set_band_description(1, "canopy height (cm)")
        dst.set_band_description(2, f"NAIP acquisition day of year ({year})")
        dst.update_tags(NAIP_YEAR=str(year),
                        ACQUISITION_DATES="; ".join(f"{d}: " + ", ".join(doqqs[doqqs.acq_date == d].quad) for d in dates),
                        NAIP_GSD_M=f"{doqqs.gsd_m.iloc[0]:g}", MODEL=c.MODEL_TAG, SEAMS="nearest DOQQ footprint centroid")
        dst.build_overviews(c.OVERVIEWS, Resampling.nearest)
    return out


def naip(years, out_dir):
    doqqs = gpd.read_file(REFERENCE / "doqq_footprints.gpkg")
    t, shape = c.map_grid()
    for year, g in doqqs.groupby("year"):
        year = int(year)
        if years and year not in years:
            continue
        chm, doy, src = naip_mosaic(year, g, t, shape)
        g = g.iloc[[i for i in np.unique(src) if i != 255]]          # DOQQs that supply pixels
        for old in out_dir.glob(f"naip_chm_{year}_*.tif"):
            old.unlink()
        out = write_naip(year, chm, doy, g, t, out_dir)
        print(f"{year}: {out.name}, {100 * (chm != c.NODATA).mean():.1f}% of the extent covered")


def fixed_wing(years, out_dir):
    t, (h, w) = c.map_grid()
    for pred in sorted((PREDICTIONS / "fixed_wing").glob("fixedwing_*_chm.tif")):
        d = pred.stem.split("_")[1]
        year = int(d[:4])
        if years and year not in years:
            continue
        out = out_dir / f"fixedwing_chm_{year}_{d}.tif"
        with rasterio.open(pred) as s:
            assert abs(s.transform.c - (t.c - PAD_PX * c.RES)) < 1e-6 and abs(s.res[0] - c.RES) < 1e-9
            chm = s.read(1, window=Window(PAD_PX, PAD_PX, w, h))
        doy = np.where(chm != c.NODATA, c.day_of_year(d), c.NODATA).astype(np.uint16)
        with rasterio.open(out, "w", **c.MAP_PROFILE, width=w, height=h, transform=t) as dst:
            dst.write(chm, 1)
            dst.write(doy, 2)
            dst.set_band_description(1, "canopy height (cm)")
            dst.set_band_description(2, f"fixed-wing acquisition day of year ({year})")
            dst.update_tags(SOURCE="MPG Ranch fixed-wing 4-band mosaic, area-averaged to 0.6 m",
                            SOURCE_GSD_M=f"{c.FIXED_WING_GSD_M:g}", ACQUISITION_DATE=f"{d[:4]}-{d[4:6]}-{d[6:]}",
                            MODEL=c.MODEL_TAG)
            dst.build_overviews(c.OVERVIEWS, Resampling.nearest)
        print(f"{year}: {out.name}, {100 * (chm != c.NODATA).mean():.1f}% of the extent covered")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("series", nargs="?", choices=("naip", "fixed_wing"))
    ap.add_argument("--years", type=int, nargs="+")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    if args.series in (None, "naip"):
        out = args.out / "naip" if args.out else NAIP_MAPS
        out.mkdir(parents=True, exist_ok=True)
        naip(set(args.years or ()), out)
    if args.series in (None, "fixed_wing"):
        out = args.out / "fixed_wing" if args.out else FIXED_WING_MAPS
        out.mkdir(parents=True, exist_ok=True)
        fixed_wing(set(args.years or ()), out)


if __name__ == "__main__":
    main()
