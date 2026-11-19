"""Derived products: median and 95th percentile canopy height in 10 m cells, for best-series maps.

Grid: 10 m cells aligned to the extent's top-left corner (x 252,000, y 286,000), EPSG:6514. 10 m cells don't nest
the 0.6 m pixels (16.7 per side), so each 0.6 m pixel goes to the cell that contains its center (16 or 17 pixels
per side). Percentiles use every valid pixel in the cell, zeros included. A cell gets values only where at least
MIN_VALID of its pixels have data; otherwise it is nodata.

The best map for a year comes from catalog/acquisitions.csv (best = True).

Output: data/products/derived/{naip|fixedwing}_chm_{year}_{dates}_10m.tif, uint16 centimeters, nodata 65535;
        band 1 median height, band 2 95th percentile height; tags carry the source and acquisition date(s).
Run: python scripts/08_derive.py YEAR [YEAR ...]
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from affine import Affine

from paths import CATALOG, DERIVED as OUT, FIXED_WING_MAPS, NAIP_MAPS

X0, YTOP, CELL = 252000.0, 286000.0, 10.0
MIN_VALID = 0.5
NODATA = 65535
PCTS = (50, 95)


def best_source(year):
    """Path of the year's best-image mosaic and its public file stem."""
    acq = pd.read_csv(CATALOG / "acquisitions.csv")
    rows = acq[(acq.best.astype(bool)) & (acq.start.str[:4] == str(year))]
    if rows.empty:
        raise SystemExit(f"no best image for {year}")
    src = rows.source.iloc[0]
    if src == "NAIP":
        path = next(NAIP_MAPS.glob(f"naip_chm_{year}_*.tif"))
    else:
        path = next(FIXED_WING_MAPS.glob(f"fixedwing_chm_{year}_*.tif"))
    return path, path.stem


def derive(path):
    """(2, rows, cols) uint16 array of median and 95th percentile per 10 m cell, and its transform."""
    with rasterio.open(path) as d:
        a = d.read(1)
        t, nod = d.transform, d.nodata
        tags = d.tags()
    h, w = a.shape
    cx = t.c + (np.arange(w) + 0.5) * t.a
    cy = t.f + (np.arange(h) + 0.5) * t.e
    ccol = np.floor((cx - X0) / CELL).astype(int)
    crow = np.floor((YTOP - cy) / CELL).astype(int)
    ncol, nrow = ccol.max() + 1, crow.max() + 1
    k = np.bincount(ccol).max()
    idx = np.full((ncol, k), -1)
    for c in range(ncol):
        cols = np.nonzero(ccol == c)[0]
        idx[c, :len(cols)] = cols
    pad = idx < 0
    out = np.full((len(PCTS), nrow, ncol), NODATA, dtype=np.uint16)
    for r in range(nrow):
        rows = np.nonzero(crow == r)[0]
        s = a[rows][:, np.maximum(idx, 0)].astype(np.float32)        # (pixel rows, cells, k)
        s[a[rows][:, np.maximum(idx, 0)] == nod] = np.nan
        s[:, pad] = np.nan
        v = s.transpose(1, 0, 2).reshape(ncol, -1)
        n_total = len(rows) * (~pad).sum(1)
        ok = np.isfinite(v).sum(1) >= MIN_VALID * n_total
        if ok.any():
            out[:, r, ok] = np.round(np.nanpercentile(v[ok], PCTS, axis=1)).astype(np.uint16)
    return out, Affine(CELL, 0, X0, 0, -CELL, YTOP), tags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("years", type=int, nargs="+")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for year in args.years:
        path, stem = best_source(year)
        out, t, tags = derive(path)
        dst = OUT / f"{stem}_10m.tif"
        profile = dict(driver="GTiff", width=out.shape[2], height=out.shape[1], count=len(PCTS), dtype="uint16",
                       crs="EPSG:6514", transform=t, nodata=NODATA, tiled=True, blockxsize=256, blockysize=256,
                       compress="deflate")
        keep = {k: v for k, v in tags.items() if k != "AREA_OR_POINT"}
        with rasterio.open(dst, "w", **profile) as d:
            d.write(out)
            d.set_band_description(1, "median canopy height (cm)")
            d.set_band_description(2, "95th percentile canopy height (cm)")
            d.update_tags(**keep, DERIVED_FROM=path.name, CELL_M="10",
                          METHOD=f"0.6 m pixels assigned to 10 m cells by center; cells need >= {MIN_VALID:.0%} "
                                 "valid pixels; percentiles include zeros")
        valid = out[0] != NODATA
        print(f"{year}: {dst.name}  {out.shape[2]} x {out.shape[1]} cells, {100 * valid.mean():.0f}% with values, "
              f"median of medians {np.median(out[0][valid]) / 100:.2f} m, "
              f"median of 95th percentiles {np.median(out[1][valid]) / 100:.2f} m")


if __name__ == "__main__":
    main()
