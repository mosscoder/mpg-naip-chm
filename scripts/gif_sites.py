"""Candidate sites for a best-image time-lapse GIF (true color left, NAIP-CHM right): 300 m windows on low-elevation
draws, off the Bitterroot floodplain, where canopy height changed most across the series.

Cells: 10 m, on the derived-products grid (x 252,000, y 286,000; scripts/08_derive.py). Each 0.6 m pixel counts
toward the cell holding its center. Per best-series year: share of the cell's pixels with data, and the share of
valid pixels at least 0.5 m tall (cover >= 0.5 m).
Change, judged within each source so a switch between NAIP and fixed-wing doesn't count as change:
  - NAIP:       2021-08-29 minus the mean of 2009-07-20, 2011-09-10 and 2013-07-09;
  - fixed-wing: mean of 2022-07-03, 2023-07-06 and 2024-08-01 minus the mean of 2014-07-25 and 2015-06-26.
  A cell changed if both sources moved the same way by at least MIN_CHANGE (share of the cell).
Masks, from the draw mapping of the earlier draw analysis (data/reference/terrain, reference/):
  - draw zone: within 25 m of a draw channel (catchment >= 5 ha; perennial creeks and floodplain channels removed);
  - floodplain: less than 10 m above the Bitterroot River (relative elevation);
  - wire corridors (reference/wire_corridors.gpkg): left out of every statistic.
Windows: 300 m squares on a 50 m step, imaged in every best-series year (every cell >= 50% valid in all 14 years),
with a draw within 50 m of the center, the changed draw-zone cells centered (centroid within 40 m of the center),
the center itself off the floodplain, at most MAX_FLOODPLAIN of the window
on the floodplain, and the draw zone's median elevation at most MAX_ELEV_M.
Score: share of the window's draw-zone cells that changed (gains plus losses). Candidates are the best-scoring windows
that don't overlap, at most two per draw network.

Outputs (scratch/gif_sites/): cells.npz (cache), candidates.csv, overview.png (candidates over the base map) and
candidate_N.png (true color and NAIP-CHM for all 14 years).
Run: python scripts/gif_sites.py [--n 8] [--max-elev 1150]
"""

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.colors import LogNorm
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.warp import reproject
from rasterio.windows import from_bounds
from scipy import ndimage
from shapely.geometry import box

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "scratch" / "gif_sites"
from paths import BASEMAP, FIXED_WING_INPUTS, FIXED_WING_MAPS, NAIP_INPUTS, NAIP_MAPS, REFERENCE, TERRAIN
X0, YTOP, CELL, RES = 252000.0, 286000.0, 10.0, 0.6
NCOL, NROW = 600, 791
WIN_M, STEP_M = 300.0, 50.0
DRAW_BUFFER_M, FLOODPLAIN_REM_M = 25.0, 10.0
MIN_CHANGE = 0.2
MAX_FLOODPLAIN = 0.2
MAX_OFFCENTER_M = 40.0                                 # changed cells' centroid to window center
NAIP_EARLY, NAIP_LATE = (2009, 2011, 2013), (2021,)
FW_EARLY, FW_LATE = (2014, 2015), (2022, 2023, 2024)
NORM = LogNorm(0.1, 10)                               # most change in the draws is under 3 m


def best_maps():
    """{year: (source label, date text, mosaic path, RGB input paths)} for the best-image series."""
    acq = pd.read_csv(ROOT / "catalog" / "acquisitions.csv")
    best = acq[acq.best.astype(bool)]
    out = {}
    for year in sorted(best.start.str[:4].astype(int).unique()):
        rows = best[best.start.str[:4] == str(year)]
        dates = " / ".join(rows.start)
        if rows.source.iloc[0] == "NAIP":
            mosaic = next(NAIP_MAPS.glob(f"naip_chm_{year}_*.tif"))
            rgb = sorted((NAIP_INPUTS / str(year)).glob("naip_*.tif"))
            out[year] = ("NAIP", dates, mosaic, rgb)
        else:
            mosaic = next(FIXED_WING_MAPS.glob(f"fixedwing_chm_{year}_*.tif"))
            day = rows.start.iloc[0].replace("-", "")
            out[year] = ("fixed-wing", dates, mosaic, [FIXED_WING_INPUTS / f"fixedwing_{day}.tif"])
    return out


def cell_stats(maps):
    """valid share and cover >= 0.5 m share per 10 m cell and year: two (years, NROW, NCOL) float32 arrays."""
    cache = OUT / "cells.npz"
    if cache.exists():
        z = np.load(cache)
        if list(z["years"]) == list(maps):
            return z["valid"], z["cover"]
    valid = np.zeros((len(maps), NROW, NCOL), "float32")
    cover = np.zeros_like(valid)
    for i, (year, (_, _, path, _)) in enumerate(maps.items()):
        with rasterio.open(path) as d:
            t, nod, (h, w) = d.transform, d.nodata, d.shape
            ccol = np.floor((t.c + (np.arange(w) + 0.5) * t.a - X0) / CELL).astype(int)
            crow = np.floor((YTOP - (t.f + (np.arange(h) + 0.5) * t.e)) / CELL).astype(int)
            n_all, n_ok, n_tall = (np.zeros(NROW * NCOL) for _ in range(3))
            for r0 in range(0, h, 1000):
                a = d.read(1, window=((r0, min(r0 + 1000, h)), (0, w)))
                idx = (crow[r0:r0 + a.shape[0], None] * NCOL + ccol[None, :]).ravel()
                ok = (a != nod).ravel()
                n_all += np.bincount(idx, minlength=NROW * NCOL)
                n_ok += np.bincount(idx[ok], minlength=NROW * NCOL)
                n_tall += np.bincount(idx[ok & (a.ravel() >= 50)], minlength=NROW * NCOL)
        valid[i] = (n_ok / np.maximum(n_all, 1)).reshape(NROW, NCOL)
        cover[i] = (n_tall / np.maximum(n_ok, 1)).reshape(NROW, NCOL)
        print(f"  {year}: {100 * (valid[i] >= 0.5).mean():.0f}% of cells imaged")
    np.savez_compressed(cache, years=list(maps), valid=valid, cover=cover)
    return valid, cover


def site_layers():
    """10 m draw zone, floodplain, wire, elevation and draw-network layers on the cell grid."""
    with rasterio.open(TERRAIN / "draws_5m.tif") as d:
        t5 = d.transform
        r0, c0 = round((t5.f - YTOP) / CELL * 2), round((X0 - t5.c) / CELL * 2)
        sl = (slice(r0, r0 + 2 * NROW), slice(c0, c0 + 2 * NCOL))
        draws5 = d.read(1) > 0
    zone5 = ndimage.distance_transform_edt(~draws5) * t5.a <= DRAW_BUFFER_M
    def to10(a):
        return a[sl].reshape(NROW, 2, NCOL, 2).mean(axis=(1, 3))
    with rasterio.open(TERRAIN / "dem_5m.tif") as d:
        dem = to10(d.read(1).astype("float64"))
    with rasterio.open(TERRAIN / "rem_river_5m.tif") as d:
        rem = to10(d.read(1).astype("float64"))
    t10 = rasterio.Affine(CELL, 0, X0, 0, -CELL, YTOP)
    corridors = gpd.read_file(REFERENCE / "wire_corridors.gpkg")
    wires = rasterize([(g, 1) for g in corridors.geometry], out_shape=(NROW, NCOL),
                      transform=t10, fill=0, dtype="uint8", all_touched=True).astype(bool)
    nets = gpd.read_file(REFERENCE / "draw_networks_aoi.gpkg")
    network = rasterize([(g.buffer(DRAW_BUFFER_M), i + 1) for i, g in enumerate(nets.geometry)], out_shape=(NROW, NCOL),
                        transform=t10, fill=0, dtype="int16")
    names = np.array([""] + list(nets.network))
    return dict(zone=to10(zone5) >= 0.5, draw=to10(draws5) > 0, floodplain=rem < FLOODPLAIN_REM_M, dem=dem,
                rem=rem, wires=wires, network=names[network], t10=t10)


def score_windows(maps, valid, cover, lay, max_elev):
    years = list(maps)
    mean_of = lambda ys: cover[[years.index(y) for y in ys]].mean(axis=0)
    d_naip = mean_of(NAIP_LATE) - mean_of(NAIP_EARLY)
    d_fw = mean_of(FW_LATE) - mean_of(FW_EARLY)
    same = np.sign(d_naip) == np.sign(d_fw)
    agreed = np.where(same, np.sign(d_naip) * np.minimum(abs(d_naip), abs(d_fw)), 0.0)
    imaged_all = (valid >= 0.5).all(axis=0)
    zone = lay["zone"] & ~lay["wires"] & imaged_all
    n, step = int(WIN_M / CELL), int(STEP_M / CELL)
    rows = []
    for r in range(0, NROW - n + 1, step):
        for c in range(0, NCOL - n + 1, step):
            s = (slice(r, r + n), slice(c, c + n))
            if not imaged_all[s].all():
                continue
            mid = (slice(r + n // 2 - 5, r + n // 2 + 5), slice(c + n // 2 - 5, c + n // 2 + 5))
            if not lay["draw"][mid].any() or lay["floodplain"][r + n // 2, c + n // 2]:
                continue
            z = zone[s]
            if z.sum() < 0.1 * n * n or lay["floodplain"][s].mean() > MAX_FLOODPLAIN:
                continue
            elev = np.median(lay["dem"][s][z])
            if elev > max_elev:
                continue
            a = agreed[s][z]
            rr, cc = np.nonzero(z & (abs(agreed[s]) >= MIN_CHANGE))
            if len(rr) == 0 or np.hypot(rr.mean() + 0.5 - n / 2, cc.mean() + 0.5 - n / 2) * CELL > MAX_OFFCENTER_M:
                continue
            nets, counts = np.unique(lay["network"][s][z], return_counts=True)
            keep = nets != ""
            rows.append(dict(x0=X0 + c * CELL, y0=YTOP - (r + n) * CELL, x1=X0 + (c + n) * CELL, y1=YTOP - r * CELL,
                             network=nets[keep][np.argmax(counts[keep])] if keep.any() else "",
                             elev_m=elev, floodplain_pct=100 * lay["floodplain"][s].mean(),
                             draw_zone_pct=100 * z.mean(),
                             changed_pct=100 * (abs(a) >= MIN_CHANGE).mean(),
                             gain_pct=100 * (a >= MIN_CHANGE).mean(), loss_pct=100 * (a <= -MIN_CHANGE).mean(),
                             naip_change_pts=100 * d_naip[s][z].mean(), fw_change_pts=100 * d_fw[s][z].mean()))
    return pd.DataFrame(rows).sort_values("changed_pct", ascending=False)


def pick(windows, n, per_network=2):
    chosen = []
    for _, w in windows.iterrows():
        b = box(w.x0, w.y0, w.x1, w.y1)
        if any(b.intersects(box(o.x0, o.y0, o.x1, o.y1)) and not b.touches(box(o.x0, o.y0, o.x1, o.y1))
               for o in chosen):
            continue
        if w.network and sum(o.network == w.network for o in chosen) >= per_network:
            continue
        chosen.append(w)
        if len(chosen) == n:
            break
    out = pd.DataFrame(chosen).reset_index(drop=True)
    out.insert(0, "candidate", np.arange(1, len(out) + 1))
    return out


def grid(win):
    return rasterio.Affine(RES, 0, win[0], 0, -RES, win[3]), (round((win[3] - win[1]) / RES), round((win[2] - win[0]) / RES))


def window_chm(path, win):
    """Height (m) and day-of-year band over `win`; height masked where there is no data."""
    with rasterio.open(path) as d:
        a = d.read(window=from_bounds(*win, d.transform))
        nod = d.nodata
    return np.ma.masked_equal(a[0], nod).astype("float32") / 100, a[1]


def window_rgb(source, paths, doy, win):
    """True color of the image the model saw over `win`, stretched 2-98% per band. NAIP tiles are matched to each
    pixel by the mosaic's day-of-year band."""
    t, shape = grid(win)
    rgb = np.zeros((3,) + shape, "float32")
    for p in paths:
        tile = np.zeros((3,) + shape, "float32")
        with rasterio.open(p) as d:
            if not rasterio.coords.disjoint_bounds(rasterio.warp.transform_bounds(d.crs, "EPSG:6514", *d.bounds), win):
                for i in range(3):
                    reproject(rasterio.band(d, i + 1), tile[i], dst_transform=t, dst_crs="EPSG:6514",
                              resampling=Resampling.bilinear, src_nodata=0 if source == "NAIP" else None)
        if source == "NAIP":
            day = pd.Timestamp(p.stem.split("_")[1]).dayofyear
            use = (doy == day) & (tile.sum(0) > 0)
            rgb[:, use] = tile[:, use]
        else:
            rgb = tile
    v = rgb[:, rgb.sum(0) > 0]
    lo, hi = np.percentile(v, (2, 98), axis=1)
    return np.clip((rgb - lo[:, None, None]) / (hi - lo)[:, None, None], 0, 1).transpose(1, 2, 0)


def label(ax, text, size=10.5):
    ax.text(0.03, 0.97, text, transform=ax.transAxes, ha="left", va="top", fontsize=size, weight="bold", color="w",
            bbox=dict(boxstyle="square,pad=0.2", fc="k", ec="none", alpha=0.6))


def fig_candidate(cand, maps):
    win = (cand.x0, cand.y0, cand.x1, cand.y1)
    years = list(maps)
    cmap = plt.get_cmap("viridis").copy()
    cmap.set_bad("0.85")
    fig, axes = plt.subplots(4, 7, figsize=(21, 12.9), dpi=100)
    fig.subplots_adjust(left=0.01, right=0.93, top=0.92, bottom=0.01, wspace=0.025, hspace=0.025)
    for k, year in enumerate(years):
        source, dates, mosaic, rgb_paths = maps[year]
        h, doy = window_chm(mosaic, win)
        rgb = window_rgb(source, rgb_paths, doy, win)
        ax_rgb, ax_chm = axes[2 * (k // 7), k % 7], axes[2 * (k // 7) + 1, k % 7]
        ax_rgb.imshow(rgb, interpolation="nearest")
        ax_chm.imshow(np.ma.clip(h, NORM.vmin, None), norm=NORM, cmap=cmap, interpolation="nearest")
        label(ax_rgb, f"{source} {dates.replace(' / ' + str(year) + '-', ' / ')}")
        label(ax_chm, f"NAIP-CHM {year}")
    for ax in axes.flat:
        ax.set_xticks([]), ax.set_yticks([])
    cb = fig.colorbar(plt.cm.ScalarMappable(NORM, cmap), cax=fig.add_axes([0.94, 0.03, 0.012, 0.4]))
    cb.set_ticks([0.1, 0.25, 0.5, 1, 2, 5, 10], labels=["0.1", "0.25", "0.5", "1", "2", "5", "10"])
    cb.set_label("canopy height (m)", fontsize=12)
    fig.text(0.01, 0.965, f"Candidate {cand.candidate}: x {cand.x0:,.0f}-{cand.x1:,.0f}, y {cand.y0:,.0f}-{cand.y1:,.0f} "
             f"(EPSG:6514), network {cand.network}, draws at {cand.elev_m:,.0f} m", fontsize=14, weight="bold")
    fig.text(0.01, 0.94, f"Draw zone: {cand.changed_pct:.0f}% of 10 m cells changed by >= {MIN_CHANGE:.0%} cover in "
             f"both sources ({cand.gain_pct:.0f}% gained, {cand.loss_pct:.0f}% lost). Cover >= 0.5 m: "
             f"{cand.naip_change_pts:+.0f} pts NAIP 2009-13 to 2021, {cand.fw_change_pts:+.0f} pts fixed-wing "
             f"2014-15 to 2022-24.", fontsize=12)
    fig.savefig(OUT / f"candidate_{cand.candidate}.png", bbox_inches="tight")
    plt.close(fig)


def fig_overview(cands, lay, valid):
    with rasterio.open(BASEMAP) as d:
        bm = d.read(window=from_bounds(X0, YTOP - NROW * CELL, X0 + NCOL * CELL, YTOP, d.transform)).transpose(1, 2, 0)
    ext = (X0, X0 + NCOL * CELL, YTOP - NROW * CELL, YTOP)
    fig, ax = plt.subplots(figsize=(10, 13), dpi=110)
    ax.imshow(bm, extent=ext)
    ax.imshow(np.ma.masked_where(~lay["floodplain"], np.ones(lay["floodplain"].shape)), extent=ext, cmap="Blues",
              vmin=0, vmax=1.5, alpha=0.45, interpolation="nearest")
    ax.contour(np.flipud((valid >= 0.5).all(axis=0)).astype(float), levels=[0.5], colors="w", linewidths=1.2,
               linestyles="--", extent=ext, origin="lower")
    gpd.read_file(REFERENCE / "draws.gpkg").plot(ax=ax, color="#ffd92f", linewidth=0.6)
    for _, c in cands.iterrows():
        color = "#d73027" if c.loss_pct > c.gain_pct else "#1a9850"
        ax.add_patch(plt.Rectangle((c.x0, c.y0), WIN_M, WIN_M, fill=False, ec=color, lw=2.5))
        ax.text(c.x1 + 30, c.y1, str(c.candidate), color="w", fontsize=16, weight="bold", va="top",
                path_effects=[pe.withStroke(linewidth=3, foreground="k")])
    ax.set_xlim(ext[:2]), ax.set_ylim(ext[2:])
    ax.set_xticks([]), ax.set_yticks([])
    fig.savefig(OUT / "overview.png", bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--max-elev", type=float, default=1150)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    maps = best_maps()
    valid, cover = cell_stats(maps)
    lay = site_layers()
    imaged = (valid >= 0.5).all(axis=0)
    z = lay["dem"][lay["zone"] & imaged & ~lay["wires"]]
    print("draw-zone elevation in the all-years footprint (m), 5/25/50/75/95%:", np.percentile(z, (5, 25, 50, 75, 95)).round())
    windows = score_windows(maps, valid, cover, lay, args.max_elev)
    print(f"{len(windows)} windows pass the filters")
    cands = pick(windows, args.n)
    cands.round(1).to_csv(OUT / "candidates.csv", index=False)
    print(cands.round(1).to_string(index=False))
    fig_overview(cands, lay, valid)
    for _, c in cands.iterrows():
        fig_candidate(c, maps)


if __name__ == "__main__":
    main()
