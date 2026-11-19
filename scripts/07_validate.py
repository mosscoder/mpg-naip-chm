"""Validation: does local inference reproduce the official product, and how well do predictions fit LiDAR?

Inputs come from 02-05 (validation set) and the LiDAR clips in data/validation/lidar/. LiDAR is a reference, not
ground truth: each check pairs imagery with the nearest LiDAR flight, sometimes months apart.

1. Official tile. One 2023 DOQQ run locally vs the authors' official tile for it (same grid).
2. LiDAR fit in 1 m cells, three areas (common.VALIDATION):
     2016  LiDAR 2016-09-01 (Ranch lidR pit-free CHM)    fixed-wing 2016-06-22   NAIP 2016-08-16 (1 m)
     2020  LiDAR 2020-05-09 / 06-02 (MSL RAVALLI_2020)  fixed-wing 2020-06-22   NAIP 2019-09-15 (no 2020 NAIP)
     2021  LiDAR 2021-10-16 (MSL MISSOULA_2021)         fixed-wing 2021-06-29   NAIP 2021-08-29
   LiDAR dates are from point GPS times. LiDAR is nearest-resampled to 1 m and predictions area-averaged; where two
   NAIP DOQQ predictions overlap they are averaged, and the 2020 LiDAR quads are combined (first valid). Each area uses
   only cells valid in LiDAR and both predictions, minus wire corridors (WIRES): LiDAR keeps conductors as tall
   "canopy" where both models see ground. Everything is done for all cells and for cells with LiDAR <= 10 m (chosen
   on the reference only, so the regression of prediction on LiDAR isn't biased by selecting on the prediction).
3. Short vegetation, 2021 area, 3 m cells (max height; LiDAR 1 m in 3 x 3, predictions 0.6 m in 5 x 5; no wire mask):
   predicted height by LiDAR height class, and detection of cells >= 0.5 m and >= 1 m among cells < 2 m in both
   (AUC; threshold by Youden's J). For the whole area, the Ranch part (data/reference/ranch_boundary.geojson), and "open"
   ground: 3 m cells in 30 m cells with < 1% of LiDAR >= 2 m. Sources include the official 2023 NAIP-CHM
   (NAIP 2023-10-19, off-season).
4. Cover in 30 m cells, 2021 area: % of each 30 m cell in height bands, from LiDAR (1 m) and each prediction (0.6 m),
   cells >= 95% valid in all. Bands starting at >= 2 m on every cell; lower bands on open cells only. Spearman
   matrix of cover >= 2 m.
5. Draws: 1 m cells within 25 m of a draw channel (data/reference/terrain/draw_links_5m.tif, 5 m), in the three
   areas. A draw link is "open country" if under 10% of the 25-100 m ring beside it is >= 2 m tall in the LiDAR
   (needs >= 2,000 m2 of ring). LiDAR fit as in 2, for all draws and open-country draws; and per draw link with
   >= 1,000 m2 of valid buffer, % of buffer cells >= 0.5 m and >= 2 m, LiDAR vs each prediction.
Height classes are heights only: height cannot tell woody from herbaceous plants.

Outputs: validation/*.csv and validation/*.png (in git)
Run: python scripts/07_validate.py [--out DIR]
"""

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from affine import Affine
from matplotlib.colors import LogNorm
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.vrt import WarpedVRT
from rasterio.windows import from_bounds
from scipy import ndimage
from scipy.stats import pearsonr, spearmanr
from shapely.geometry import LineString
from sklearn.metrics import roc_auc_score

import common as c
from paths import LIDAR, RANCH_BOUNDARY, ROOT, TERRAIN, VALIDATION

PRED = VALIDATION / "predictions"
LIDAR_FILES = {"2016": ["lidar_chm_2016_20160901.tif"],
               "2020": ["lidar_chm_2020_20200509-20200602_46113f8.tif", "lidar_chm_2020_20200509-20200602_46114f1.tif"],
               "2021": ["lidar_chm_2021_20211016.tif"]}
EPOCHS = {
    "2016": {"lidar_label": "LiDAR 2016-09-01",
             "sources": {"fw2016": (["fixedwing_20160622"], "fixed-wing 2016-06-22"),
                         "naip2016": (["naip_20160816_4611424_ne", "naip_20160816_4611424_se"], "NAIP 2016-08-16 (1 m)")}},
    "2020": {"lidar_label": "LiDAR 2020-05-09 / 06-02",
             "sources": {"fw2020": (["fixedwing_20200622"], "fixed-wing 2020-06-22"),
                         "naip2019": (["naip_20190915_4611424_se"], "NAIP 2019-09-15")}},
    "2021": {"lidar_label": "LiDAR 2021-10-16",
             "sources": {"fw2021": (["fixedwing_20210629"], "fixed-wing 2021-06-29"),
                         "naip2021": (["naip_20210829"], "NAIP 2021-08-29")}},
}
# Wire corridors in the validation LiDAR (EPSG:6514 vertices, buffer m). 2016: a line crossing the bluff draws, traced
# in 100 m steps from cells with LiDAR > 1.5 m where both predictions are < 0.5 m. 2021: the high-voltage line
# crossing the eastern tile NW-SE (conductors at 40-85 m), two straight segments fitted to LiDAR > 40 m with
# predictions < 5 m.
WIRES = {"2016": ([(253116, 284760), (253144, 284650), (253194, 284450), (253264, 284150), (253336, 283850),
                   (253402, 283550), (253452, 283150), (253514, 282650), (253572, 282150), (253590, 282000)], 15.0),
         "2021": ([(256800, 287927), (264642, 281891), (265830, 281853)], 25.0)}
SUBSETS = {"all": {"max_h": None, "hist_m": 0.25, "median_m": 1.0, "suffix": ""},
           "lidar <= 10 m": {"max_h": 10.0, "hist_m": 0.1, "median_m": 0.5, "suffix": "_le10m"}}
MIN_BIN_N = 100                         # LiDAR bins with fewer cells are left off the median line

# 2021 area: 30 m cells (the RAP grid the area was drawn on), and the official 2023 product as a third source
NCOL, NROW = 301, 342
OFFICIAL_2023 = c.validation_inputs(2021) / "naipchm_official_20231019.tif"
LABEL_2023 = "NAIP 2023-10-19 (official)"
HIGH_M = 2.0
OPEN_MAX_PCT = 1.0                      # open 30 m cell: < 1% of LiDAR >= 2 m
VALID_MIN = 0.95
CLASSES = [0, 0.25, 0.5, 1.0, 1.5, 2.0, 5.0, 10.0, 20.0, np.inf]
BANDS = [(0.1, 0.5), (0.25, 1.0), (0.5, 2.0), (1.0, 2.0), (2.0, None), (5.0, None)]

DRAW_BUFFER_M, RING_M = 25.0, 100.0
OPEN_MAX_GE2, MIN_RING_M2, MIN_SEG_M2 = 10.0, 2000, 1000
LOW_M = 0.5


# ---- reading --------------------------------------------------------------------------------------------------------
def warp(path, aoi, resampling, scale=1.0, res=1.0):
    """path on the res grid over aoi (EPSG:6514); NaN outside data, heights clipped at 0."""
    t = Affine(res, 0, aoi[0], 0, -res, aoi[3])
    w, h = int(round((aoi[2] - aoi[0]) / res)), int(round((aoi[3] - aoi[1]) / res))
    with rasterio.open(path) as src, WarpedVRT(src, src_crs=src.crs or "EPSG:6514", crs="EPSG:6514", transform=t,
                                               width=w, height=h, resampling=resampling, src_nodata=src.nodata,
                                               nodata=np.nan, dtype="float32") as vrt:
        arr = vrt.read(1) / scale
    arr[~np.isfinite(arr) | (arr < -1) | (arr > 120)] = np.nan
    return np.clip(arr, 0, None)


def combine(arrs, how):
    stack = np.stack(arrs)
    with np.errstate(all="ignore"):
        return np.nanmean(stack, 0) if how == "mean" else np.where(np.isfinite(stack[0]), stack[0], np.nanmax(stack, 0))


def wire_mask(epoch, aoi):
    w, h = int(round(aoi[2] - aoi[0])), int(round(aoi[3] - aoi[1]))
    if epoch not in WIRES:
        return np.zeros((h, w), bool)
    line, buf = WIRES[epoch]
    return rasterize([(LineString(line).buffer(buf), 1)], out_shape=(h, w), fill=0, dtype="uint8",
                     transform=Affine(1.0, 0, aoi[0], 0, -1.0, aoi[3])).astype(bool)


def grids(epoch):
    """1 m LiDAR and predictions over the epoch's area, the cells valid in all three (minus wires), and the area."""
    e, aoi = EPOCHS[epoch], c.VALIDATION[epoch]["area"]
    lid = combine([warp(LIDAR / f, aoi, Resampling.nearest) for f in LIDAR_FILES[epoch]], "first")
    pred = {n: combine([warp(PRED / f"{s}_chm.tif", aoi, Resampling.average, 100.0) for s in stems], "mean")
            for n, (stems, _) in e["sources"].items()}
    ok = np.isfinite(lid) & np.logical_and.reduce([np.isfinite(p) for p in pred.values()]) & ~wire_mask(epoch, aoi)
    return lid, pred, ok, aoi


# ---- 1. official tile -----------------------------------------------------------------------------------------------
def official():
    with rasterio.open(PRED / f"{c.VALID_DOQQ}_chm.tif") as a, \
            rasterio.open(VALIDATION / "official_2023" / f"{c.VALID_DOQQ}_chm_official.tif") as b:
        same_grid = a.transform == b.transform and a.shape == b.shape and a.crs == b.crs
        local = a.read(1).astype(np.float32)
        off = b.read(1, window=from_bounds(*a.bounds, transform=b.transform), boundless=True,
                     fill_value=c.NODATA).astype(np.float32)
    ok = (local != c.NODATA) & (off != c.NODATA)
    m, o = local[ok] / 100, off[ok] / 100
    d = m - o
    return pd.DataFrame([{"same_grid": same_grid, "n_pixels": int(ok.sum()), "r": pearsonr(m, o)[0],
                          "mean_diff_m": d.mean(), "mae_m": np.abs(d).mean(), "p99_abs_diff_m": np.percentile(np.abs(d), 99),
                          "pct_within_5cm": 100 * (np.abs(d) <= 0.05).mean(),
                          "pct_within_25cm": 100 * (np.abs(d) <= 0.25).mean(),
                          "cover_ge_2m_local_pct": 100 * (m >= HIGH_M).mean(),
                          "cover_ge_2m_official_pct": 100 * (o >= HIGH_M).mean()}])


# ---- 2 and 5. LiDAR fit ---------------------------------------------------------------------------------------------
def stats(t, v):
    t, v = t.astype(np.float64), v.astype(np.float64)
    return {"n": len(t), "mean_lidar_m": t.mean(), "mean_pred_m": v.mean(), "bias_m": (v - t).mean(),
            "mae_m": np.abs(v - t).mean(), "rmse_m": np.sqrt(((v - t) ** 2).mean()),
            "r2": pearsonr(v, t)[0] ** 2, "slope": np.cov(t, v)[0, 1] / t.var(ddof=1)}


def binned(t, v, width):
    g = pd.Series(v).groupby(np.floor(t / width) * width)
    b = pd.DataFrame({"n": g.size(), "p25_m": g.quantile(0.25), "median_m": g.median(), "p75_m": g.quantile(0.75)})
    return b[b["n"] >= MIN_BIN_N].rename_axis("lidar_bin_lo_m").reset_index()


def subset(t, preds, cfg):
    keep = np.ones(t.shape, bool) if cfg["max_h"] is None else t <= cfg["max_h"]
    lim = cfg["max_h"] or 5 * np.ceil(np.percentile(t, 99.99) / 5)
    return t[keep], {n: v[keep] for n, v in preds.items()}, lim


def draw(ax, t, v, b, s, lim, vmax, legend, hist_m, median_m):
    """2-D histogram of LiDAR (x) vs prediction (y) with 1:1, binned median and IQR, and the statistics."""
    edges = np.arange(0, lim + hist_m, hist_m)
    h, _, _ = np.histogram2d(t, v, bins=[edges, edges])
    m = ax.pcolormesh(edges, edges, np.ma.masked_equal(h.T, 0), cmap="viridis", norm=LogNorm(1, vmax), rasterized=True)
    ax.plot([0, lim], [0, lim], "k--", lw=1, label="1:1")
    mid = b["lidar_bin_lo_m"] + median_m / 2
    ax.plot(mid, b["median_m"], color="tab:red", lw=1.8, label=f"median per {median_m:g} m LiDAR bin")
    ax.fill_between(mid, b["p25_m"], b["p75_m"], color="tab:red", alpha=0.2, lw=0, label="IQR")
    ax.set_xlim(0, lim), ax.set_ylim(0, lim), ax.set_aspect("equal")
    ax.text(0.03, 0.97, f"n = {s['n']:,}\nr² = {s['r2']:.2f}\nRMSE = {s['rmse_m']:.2f} m\n"
                        f"bias = {s['bias_m']:+.2f} m\nOLS slope = {s['slope']:.2f}",
            transform=ax.transAxes, va="top", fontsize=9, bbox=dict(boxstyle="round", fc="white", ec="0.7", alpha=0.9))
    if legend:
        ax.legend(loc="lower right", fontsize=8)
    return m


def fit(data, name_s, cfg, out_png, title, srows, brows, extra):
    """One 3 x 2 figure (areas in rows; fixed-wing, NAIP in columns) and the statistics behind it."""
    lim = max(subset(t, p, cfg)[2] for t, p in data.values())
    edges = np.arange(0, lim + cfg["hist_m"], cfg["hist_m"])
    fig, axes = plt.subplots(3, 2, figsize=(11.5, 16), constrained_layout=True)
    for i, (epoch, (t_all, p_all)) in enumerate(data.items()):
        e = EPOCHS[epoch]
        t, pred, _ = subset(t_all, p_all, cfg)
        if t.size < 100:
            for ax in axes[i]:
                ax.set_axis_off()
                ax.text(0.5, 0.5, f"{epoch}: too few cells ({t.size})", ha="center", transform=ax.transAxes)
            continue
        vmax = max(np.histogram2d(t, v, bins=[edges, edges])[0].max() for v in pred.values())
        for j, (name, (_, label)) in enumerate(e["sources"].items()):
            v = pred[name]
            s, b = stats(t, v), binned(t, v, cfg["median_m"])
            srows.append(extra | {"subset": name_s, "epoch": epoch, "source": name, "label": label,
                                  "lidar": e["lidar_label"]} | s |
                         {"n_beyond_axis": int(((t > lim) | (v > lim)).sum()), "axis_max_m": lim})
            brows.append(b.assign(**extra, subset=name_s, epoch=epoch, source=name))
            ax = axes[i, j]
            m = draw(ax, t, v, b, s, lim, vmax, legend=(i == 0 and j == 0), hist_m=cfg["hist_m"],
                     median_m=cfg["median_m"])
            ax.set_title(f"{epoch}: {label}\nvs {e['lidar_label']}", fontsize=10)
            ax.set_xlabel("LiDAR canopy height (m)")
            ax.set_ylabel("NAIP-CHM canopy height (m)")
        fig.colorbar(m, ax=axes[i, :], label=f"1 m cells per {cfg['hist_m']:g} m bin", shrink=0.8)
    for ax, head in zip(axes[0], ("Fixed-wing", "NAIP")):
        ax.annotate(head, (0.5, 1.22), xycoords="axes fraction", ha="center", fontsize=13, weight="bold")
    if title:
        fig.suptitle(title, fontsize=13)
    fig.savefig(out_png, dpi=130)
    plt.close(fig)


# ---- 3 and 4. short vegetation and 30 m cover (2021 area) -----------------------------------------------------------
def block_max(arr, k):
    h, w = arr.shape
    return arr.reshape(h // k, k, w // k, k).max(axis=(1, 3))


def ranch_mask(aoi, res):
    geom = gpd.read_file(RANCH_BOUNDARY).to_crs("EPSG:6514").union_all()
    shape = (int(round((aoi[3] - aoi[1]) / res)), int(round((aoi[2] - aoi[0]) / res)))
    return rasterize([(geom, 1)], out_shape=shape, transform=Affine(res, 0, aoi[0], 0, -res, aoi[3]), fill=0,
                     dtype="uint8").astype(bool)


def compression(lid3, pred3, area_mask):
    ok = area_mask & np.isfinite(lid3) & np.logical_and.reduce([np.isfinite(p) for p in pred3.values()])
    lv = lid3[ok]
    rows = []
    for lo, hi in zip(CLASSES[:-1], CLASSES[1:]):
        m = (lv >= lo) & (lv < hi)
        if m.sum() < 100:
            continue
        row = {"lidar_max_class_m": f"{lo:g}-{hi:g}", "n_cells": int(m.sum()), "lidar_median_m": np.median(lv[m])}
        for name, p in pred3.items():
            v = p[ok][m]
            row[f"{name}_median_m"] = np.median(v)
            row[f"{name}_p25_m"], row[f"{name}_p75_m"] = np.percentile(v, [25, 75])
            row[f"{name}_ratio"] = np.median(v) / np.median(lv[m]) if lo > 0 else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def detection(lid3, pred3, area_mask):
    rows = []
    thresholds = np.arange(0.05, 1.51, 0.01)
    for name, p in pred3.items():
        ok = area_mask & np.isfinite(lid3) & np.isfinite(p) & (lid3 < HIGH_M) & (p < HIGH_M)
        lv, n = lid3[ok], p[ok]
        for low in (0.5, 1.0):
            y = lv >= low
            youden = [((n >= t) & y).sum() / y.sum() - ((n >= t) & ~y).sum() / (~y).sum() for t in thresholds]
            t_best = thresholds[int(np.argmax(youden))]
            hit = n >= t_best
            rows.append({"source": name, "test": f"LiDAR max >= {low:g} m", "n_cells": int(ok.sum()),
                         "prevalence": y.mean(), "auc": roc_auc_score(y, n), "best_threshold_m": t_best,
                         "accuracy": (hit == y).mean(), "recall": (hit & y).sum() / y.sum(),
                         "precision": (hit & y).sum() / max(hit.sum(), 1)})
        present = lv >= 0.5
        for taller in (1.0, 1.5):
            y = lv[present] >= taller
            rows.append({"source": name, "test": f"among cells >= 0.5 m: >= {taller:g} m vs shorter",
                         "n_cells": int(present.sum()), "prevalence": y.mean(), "auc": roc_auc_score(y, n[present])})
    return pd.DataFrame(rows)


def band_key(lo, hi):
    return f"{lo:g}_{'up' if hi is None else f'{hi:g}'}"


def fractions(arr, sub, prefix):
    """% of each 30 m cell in each height band, from a 1 m (sub 30) or 0.6 m (sub 50) grid, and its valid share."""
    cells = arr.reshape(NROW, sub, NCOL, sub).transpose(0, 2, 1, 3).reshape(NROW, NCOL, -1)
    valid = np.isfinite(cells)
    nv = np.maximum(valid.sum(-1), 1)
    out = {f"{prefix}_valid": valid.mean(-1)}
    for lo, hi in BANDS:
        hi_v = np.inf if hi is None else hi
        out[f"{prefix}_{band_key(lo, hi)}"] = 100 * ((cells >= lo) & (cells < hi_v)).sum(-1) / nv
    return {k: v.ravel() for k, v in out.items()}


def agreement(x, y):
    return {"spearman": spearmanr(x, y).correlation, "pearson": pearsonr(x, y)[0], "mean_x": x.mean(),
            "mean_y": y.mean(), "mae": np.abs(x - y).mean()}


def vegetation_2021(lid1, aoi, out):
    sources = {"fw2021": PRED / "fixedwing_20210629_chm.tif", "naip2021": PRED / "naip_20210829_chm.tif",
               "naip2023": OFFICIAL_2023}
    pred06 = {n: warp(p, aoi, Resampling.nearest, 100.0, res=c.RES) for n, p in sources.items()}
    lid3 = block_max(lid1, 3)
    pred3 = {n: block_max(p, 5) for n, p in pred06.items()}
    ranch3 = ranch_mask(aoi, 3.0)
    with np.errstate(all="ignore"):
        tall30 = 100 * np.nanmean(np.where(np.isfinite(lid1), lid1 >= HIGH_M, np.nan).reshape(NROW, 30, NCOL, 30),
                                  axis=(1, 3))
    open3 = np.kron(tall30 < OPEN_MAX_PCT, np.ones((10, 10), bool))
    comps, dets = [], []
    for area, m in (("area", np.ones_like(ranch3)), ("ranch", ranch3), ("area open", open3), ("ranch open", open3 & ranch3)):
        comps.append(compression(lid3, pred3, m).assign(area=area))
        dets.append(detection(lid3, pred3, m).assign(area=area))
    pd.concat(comps).to_csv(out / "short_vegetation_height_3m.csv", index=False)
    pd.concat(dets).to_csv(out / "short_vegetation_detection_3m.csv", index=False)

    cols = fractions(lid1, 30, "lidar")
    for n, p in pred06.items():
        cols |= fractions(p, 50, n)
    cols["ranch"] = ranch_mask(aoi, 30.0).ravel()
    df = pd.DataFrame(cols)
    df = df[(df["lidar_valid"] >= VALID_MIN) & np.logical_and.reduce([df[f"{n}_valid"] >= VALID_MIN for n in sources])]
    rows = []
    for area, sub in (("area", df), ("ranch", df[df["ranch"]])):
        is_open = (sub["lidar_2_up"] < OPEN_MAX_PCT).to_numpy()
        for lo, hi in BANDS:
            k = band_key(lo, hi)
            m = np.ones(len(sub), bool) if lo >= HIGH_M else is_open
            for n in sources:
                rows.append({"area": area, "band_m": k, "cells": "all" if lo >= HIGH_M else "open", "source": n,
                             "n": int(m.sum()), **agreement(sub[f"{n}_{k}"].to_numpy()[m], sub[f"lidar_{k}"].to_numpy()[m])})
        mat = pd.DataFrame({"lidar": sub["lidar_2_up"]} | {n: sub[f"{n}_2_up"] for n in sources}).corr(method="spearman")
        mat.to_csv(out / f"cover_ge_2m_spearman_30m_{area}.csv")
    pd.DataFrame(rows).to_csv(out / "cover_30m_agreement.csv", index=False)


# ---- 5. draws -------------------------------------------------------------------------------------------------------
def draw_geometry():
    """Distance (m) to the nearest draw cell and that cell's link id, on the 5 m draw grid."""
    with rasterio.open(TERRAIN / "draw_links_5m.tif") as s:
        links, t5 = s.read(1), s.transform
        if s.nodata is not None:
            links = np.where(links == s.nodata, 0, links)
    links = np.where(links > 0, links, 0).astype(np.int32)
    dist, (ri, ci) = ndimage.distance_transform_edt(links == 0, return_indices=True)
    return (dist * t5.a).astype(np.float32), links[ri, ci], t5


def to_area(arr5, t5, aoi):
    """Sample a 5 m array at the centers of the 1 m grid over aoi."""
    w, h = int(round(aoi[2] - aoi[0])), int(round(aoi[3] - aoi[1]))
    cols = np.floor((aoi[0] + np.arange(w) + 0.5 - t5.c) / t5.a).astype(int)
    rows = np.floor((t5.f - (aoi[3] - np.arange(h) - 0.5)) / -t5.e).astype(int)
    return arr5[np.ix_(rows, cols)]


def link_setting(lid, ok, dist, link):
    """Per link: LiDAR cover >= 2 m in the 25-100 m ring, and 0 unclassified / 1 open country / 2 not open."""
    ring = ok & (dist > DRAW_BUFFER_M) & (dist <= RING_M)
    n = np.bincount(link[ring], minlength=link.max() + 1)
    tall = np.bincount(link[ring], weights=(lid[ring] >= HIGH_M), minlength=link.max() + 1)
    with np.errstate(all="ignore"):
        cover = 100 * tall / n
    return np.where(n < MIN_RING_M2, 0, np.where(cover < OPEN_MAX_GE2, 1, 2)).astype(np.uint8)


def segment_cover(epoch, lid, pred, inbuf, link, setting):
    rows = []
    ids = link[inbuf]
    n = np.bincount(ids, minlength=link.max() + 1)
    keep = np.flatnonzero(n >= MIN_SEG_M2)
    for thr, kind in ((LOW_M, ">= 0.5 m"), (HIGH_M, ">= 2 m")):
        lc = 100 * np.bincount(ids, weights=lid[inbuf] >= thr, minlength=link.max() + 1)[keep] / n[keep]
        for name, p in pred.items():
            pc = 100 * np.bincount(ids, weights=p[inbuf] >= thr, minlength=link.max() + 1)[keep] / n[keep]
            for stratum, m in (("all", np.ones(keep.size, bool)), ("open", setting[keep] == 1)):
                if m.sum() < 5:
                    continue
                rows.append({"epoch": epoch, "draws": stratum, "cover": kind, "source": name, "n_segments": int(m.sum()),
                             "lidar_mean_pct": lc[m].mean(), "pred_mean_pct": pc[m].mean(),
                             "bias_pct": (pc[m] - lc[m]).mean(), "mae_pct": np.abs(pc[m] - lc[m]).mean(),
                             "spearman": spearmanr(lc[m], pc[m]).correlation, "r2": np.corrcoef(lc[m], pc[m])[0, 1] ** 2})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "validation")
    out = ap.parse_args().out
    out.mkdir(parents=True, exist_ok=True)

    off = official()
    off.to_csv(out / "official_tile_check.csv", index=False)
    print("== 1. local inference vs the official tile,", c.VALID_DOQQ)
    print(off.round(3).to_string(index=False))

    dist5, link5, t5 = draw_geometry()
    fit_data, draw_data, seg_rows = {}, {"all": {}, "open": {}}, []
    for epoch in EPOCHS:
        lid, pred, ok, aoi = grids(epoch)
        fit_data[epoch] = (lid[ok], {n: p[ok] for n, p in pred.items()})
        dist, link = to_area(dist5, t5, aoi), to_area(link5, t5, aoi)
        setting = link_setting(lid, ok, dist, link)
        inbuf = dist <= DRAW_BUFFER_M
        for stratum, m in (("all", inbuf & ok), ("open", inbuf & ok & (setting[link] == 1))):
            draw_data[stratum][epoch] = (lid[m], {n: p[m] for n, p in pred.items()})
        seg_rows += segment_cover(epoch, lid, pred, inbuf & ok, link, setting)
        print(f"== {epoch}: {ok.sum():,} 1 m cells in all three; {(inbuf & ok).sum():,} within {DRAW_BUFFER_M:g} m of draws")
        if epoch == "2021":
            vegetation_2021(lid, aoi, out)
        del lid, pred, ok, dist, link

    srows, brows = [], []
    for name_s, cfg in SUBSETS.items():
        sub = "" if cfg["max_h"] is None else f"Cells with LiDAR height <= {cfg['max_h']:g} m"
        fit(fit_data, name_s, cfg, out / f"lidar_fit{cfg['suffix']}.png", sub, srows, brows, {})
    pd.DataFrame(srows).to_csv(out / "lidar_fit_stats.csv", index=False)
    pd.concat(brows).to_csv(out / "lidar_fit_bins.csv", index=False)
    print(pd.DataFrame(srows).drop(columns=["label", "lidar"]).round(3).to_string(index=False))

    srows, brows = [], []
    for stratum, d in draw_data.items():
        what = "all draws" if stratum == "all" else "open-country draws"
        for name_s, cfg in SUBSETS.items():
            sub = "" if cfg["max_h"] is None else f", LiDAR <= {cfg['max_h']:g} m"
            fit(d, name_s, cfg, out / f"draws_fit_{stratum}{cfg['suffix']}.png",
                f"Within {DRAW_BUFFER_M:g} m of {what}{sub}", srows, brows, {"draws": stratum})
    pd.DataFrame(srows).to_csv(out / "draws_fit_stats.csv", index=False)
    pd.concat(brows).to_csv(out / "draws_fit_bins.csv", index=False)
    pd.DataFrame(seg_rows).to_csv(out / "draws_segment_cover.csv", index=False)
    print(f"tables and figures -> {out}")


if __name__ == "__main__":
    main()
