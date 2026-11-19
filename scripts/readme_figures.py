"""README and docs figures. No titles: panels carry short labels ("LiDAR 2019"), and explanations live in README captions.

1. Acquisition timeline (docs/img/acquisition_timeline.png, shown in docs/products.md), from catalog/acquisitions.csv: NAIP, fixed-wing and
   LiDAR dates, each labeled with month-day and the share of the map extent covered. Circles are 1 m NAIP, squares
   0.6 m NAIP, diamonds fixed-wing, triangles LiDAR. Leaf-off NAIP (on or after October 1) is gray; 2023 NAIP is
   hollow because it wasn't run here. A gold outline marks the year's best image.

2. Best-image timeline (fig_best_timeline): no longer drawn; the coverage figure replaced it in the README
   (2026-11-19). It showed the best image per year with LiDAR flights as dashed lines.

4. Coverage (docs/img/coverage.png, the README overview figure): where each map has data. Rows NAIP and
   fixed-wing, one column per year, wrapped into two blocks of eight years (2009-2016, 2017-2024) so it stays legible
   at README width; each panel is the map extent with covered area filled (NAIP green, fixed-wing blue, leaf-off NAIP
   gray, no data light gray), the Ranch outlined and the flight's month-day(s) at the top. The year's best image is
   framed in gold. Under each panel: % of the extent and % of the Ranch covered. A legend
   below explains the grays, the outline and the frame. Read from the maps at every 10th pixel (6 m).

Outputs: docs/img/acquisition_timeline.png,
         docs/img/coverage.png
Run: python scripts/readme_figures.py [--data DIR]
"""

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
import rasterio.warp
from affine import Affine
from matplotlib.colors import LogNorm
from rasterio.enums import Resampling
from rasterio.warp import reproject
from rasterio.windows import from_bounds

from paths import DATA, DERIVED, FIXED_WING_MAPS, NAIP_MAPS, RANCH_IN_AOI

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "docs" / "img"
# Until the data move into data/ (phase 3), read them where they were made.
RES = 0.6
NORM = LogNorm(0.1, 30)
TICKS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30)
ROWS = {"NAIP": 2, "fixed-wing": 1, "LiDAR": 0}
COLORS = {"NAIP": "#1b7837", "fixed-wing": "#2166ac", "LiDAR": "#b2182b"}
GOLD = "#e3a600"                                        # marks the best image (the recommended series)
LABEL_FS = 8.5                                          # marker labels on the timelines


def time_axis(ax, start):
    ax.set_xlim(pd.Timestamp(start), pd.Timestamp("2025-06-30"))
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.tick_params(axis="x", labelsize=11)
    ax.grid(axis="x", color="0.92", lw=0.8)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)


def fig_timeline():
    df = pd.read_csv(ROOT / "catalog" / "acquisitions.csv", parse_dates=["start", "end"])
    df["leaf_off"] = df.leaf_off.astype(bool)
    df["best"] = df.best.astype(bool)
    fig, ax = plt.subplots(figsize=(13, 5.6))
    for src, y in ROWS.items():
        ax.axhline(y, color="0.88", lw=6, zorder=0, solid_capstyle="butt")
    for r in df.itertuples():
        y, x, col = ROWS[r.source], r.start, COLORS[r.source]
        if r.source == "LiDAR":
            ax.plot(x, y, marker="v", ms=10, color=col, mec="k", mew=0.5, ls="none", zorder=3)
            continue
        marker = "D" if r.source == "fixed-wing" else ("o" if r.gsd_m == 1.0 else "s")
        made = r.product in (True, "True")
        face = "white" if not made else ("0.6" if r.leaf_off else col)
        edge = GOLD if r.best else ("0.6" if r.leaf_off else col)
        ax.plot(x, y, marker=marker, ms=10, mfc=face, mec=edge, mew=2.6 if r.best else 1.4, ls="none", zorder=3)
    # one label per source and year (two-date NAIP years combined), staggered where labels would collide
    for src, g in df.groupby("source", sort=False):
        g = g.assign(year=g.start.dt.year).sort_values("start")
        last_x, level = None, 0
        for year, gy in g.groupby("year", sort=True):
            x = gy.start.mean()
            if src == "LiDAR":
                r = gy.iloc[0]
                when = f"{r.start:%m-%d}" + (f" to {r.end:%m-%d}" if r.end != r.start else "")
            else:
                when = " + ".join(f"{d:%m-%d}" for d in gy.start)
            pct = gy.extent_pct.sum()
            cover = "<1%" if pct < 0.5 else f"{pct:.0f}%"      # every listed collection touches the extent
            if src == "NAIP" and not (gy["product"].astype(str) == "True").all():
                cover += ", not run"
            level = 1 - level if last_x is not None and (x - last_x).days < 420 and level == 0 else 0
            last_x = x
            grey = bool(gy.leaf_off.all())
            y_text = ROWS[src] + 0.2 + 0.34 * level
            ax.text(x, y_text, f"{when}\n{cover}", ha="center", va="bottom", fontsize=LABEL_FS,
                    color="0.55" if grey else "k")
            for d in gy.start:                                  # leader line from the label to each of its marks
                ax.plot([d, x], [ROWS[src] + 0.07, y_text - 0.02], color="0.6", lw=0.6, zorder=1)
            if grey:
                ax.text(x, ROWS[src] - 0.17, "Leaf off", ha="center", va="top", fontsize=LABEL_FS + 0.5,
                        color="0.45", style="italic")
                for d in gy.start:
                    ax.plot([d, x], [ROWS[src] - 0.07, ROWS[src] - 0.15], color="0.6", lw=0.6, zorder=1)
    ax.set_yticks(list(ROWS.values()), list(ROWS), fontsize=14)
    ax.tick_params(axis="y", length=0)
    ax.set_ylim(-0.4, 3.0)
    time_axis(ax, "2007-07-01")
    fig.tight_layout()
    fig.savefig(IMG / "acquisition_timeline.png", dpi=150)
    plt.close(fig)


LIDAR_MIN_PCT = 5                                       # LiDAR flights drawn on the best-image timeline cover >= 5%


def fig_best_timeline():
    """One row: the best image for each year, colored and shaped by source, with years lacking imagery marked.
    LiDAR flights covering at least LIDAR_MIN_PCT of the extent are dashed vertical lines, labeled with that share."""
    allrows = pd.read_csv(ROOT / "catalog" / "acquisitions.csv", parse_dates=["start"])
    lidar = allrows[(allrows.source == "LiDAR") & (allrows.extent_pct >= LIDAR_MIN_PCT)]
    df = allrows[allrows.best.astype(bool)].assign(year=lambda d: d.start.dt.year).sort_values("start")
    kinds = {("NAIP", 1.0): ("o", COLORS["NAIP"], "NAIP 1 m"),
             ("NAIP", 0.6): ("s", COLORS["NAIP"], "NAIP 0.6 m"),
             ("fixed-wing", 0.152): ("D", COLORS["fixed-wing"], "fixed-wing")}
    fig, ax = plt.subplots(figsize=(13, 3.0))
    ax.axhline(0, color="0.88", lw=6, zorder=0, solid_capstyle="butt")
    for r in lidar.itertuples():
        ax.axvline(r.start, color=COLORS["LiDAR"], ls="--", lw=1.4, zorder=0.5)
        ax.text(r.start + pd.Timedelta(days=25), -0.62, f"{r.extent_pct:.0f}%", ha="left", va="center",
                fontsize=LABEL_FS + 0.5, color=COLORS["LiDAR"])
    for r in df.itertuples():
        m, col, _ = kinds[(r.source, round(r.gsd_m, 3))]
        ax.plot(r.start, 0, marker=m, ms=11, mfc=col, mec="k", mew=1.2, ls="none", zorder=3)
    for year, gy in df.groupby("year"):
        x = gy.start.mean()
        when = " + ".join(f"{d:%m-%d}" for d in gy.start)
        ax.text(x, 0.22, f"{when}\n{gy.extent_pct.sum():.0f}%", ha="center", va="bottom", fontsize=LABEL_FS,
                bbox=dict(facecolor="white", edgecolor="none", pad=1.0), zorder=4)
        for d in gy.start:
            ax.plot([d, x], [0.07, 0.2], color="0.6", lw=0.6, zorder=1)
    for year in sorted(set(range(df.year.min(), df.year.max() + 1)) - set(df.year)):
        ax.text(pd.Timestamp(f"{year}-07-01"), -0.2, "no\nimagery", ha="center", va="top", fontsize=LABEL_FS + 0.5,
                color="0.45", style="italic")
    handles = [plt.Line2D([], [], marker=m, ms=11, mfc=c, mec="k", mew=1.2, ls="none", label=lab)
               for m, c, lab in kinds.values()]
    handles.append(plt.Line2D([], [], color=COLORS["LiDAR"], ls="--", lw=1.4, label="LiDAR flight (% of extent)"))
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=4, fontsize=12, frameon=False)
    ax.set_yticks([])
    ax.set_ylim(-0.8, 0.75)
    time_axis(ax, "2008-07-01")
    fig.tight_layout()
    fig.savefig(IMG / "best_image_timeline.png", dpi=150)
    plt.close(fig)


def grid(win):
    w, h = int(round((win[2] - win[0]) / RES)), int(round((win[3] - win[1]) / RES))
    return Affine(RES, 0, win[0], 0, -RES, win[3]), (h, w)


def read_height(path, scale, win):
    with rasterio.open(path) as d:
        a = d.read(1, window=from_bounds(*win, d.transform), masked=True, boundless=True)
    a = np.ma.masked_invalid(a.astype("float32") * scale)
    a = np.ma.masked_less(a, -1)                         # LiDAR nodata is -3.4e38
    return np.ma.clip(a, NORM.vmin, None)


def read_rgb(path, win, bands=(1, 2, 3)):
    """NAIP bands warped onto the product grid (bilinear), stretched 2-98% per band; (rows, cols, bands)."""
    t, shape = grid(win)
    out = np.zeros((len(bands),) + shape, dtype="float32")
    with rasterio.open(path) as d:
        for i, b in enumerate(bands):
            reproject(rasterio.band(d, b), out[i], dst_transform=t, dst_crs="EPSG:6514",
                      resampling=Resampling.bilinear)
    lo, hi = np.percentile(out, (2, 98), axis=(1, 2))
    return np.clip((out - lo[:, None, None]) / (hi - lo)[:, None, None], 0, 1).transpose(1, 2, 0)


INFO_WIN = (252960, 282160, 253140, 282340)             # 180 m square where a draw meets the valley floor (x0, y0, x1, y1)
BLUE, GREEN, GRAY, AMBER = "#2166ac", "#1b7837", "#4d4d4d", "#b35806"


def fig_infographic(data):
    """What NAIP-CHM is: a NAIP image and site context go into a U-Net trained on LiDAR; out comes canopy height.
    Real thumbnails from the 2019 window; no title, big labels."""
    naip = next(data.glob("inputs/naip/2019/naip_20190915_4611424_ne.tif"))
    chm = next(data.glob("products/naip/naip_chm_2019_*.tif"))
    rgb = read_rgb(naip, INFO_WIN)
    nir = read_rgb(naip, INFO_WIN, bands=(4,))[..., 0]
    height = read_height(chm, 0.01, INFO_WIN)

    fig = plt.figure(figsize=(18, 5.35), dpi=120)
    canvas = fig.add_axes([0, 0, 1, 1])
    canvas.set_xlim(0, 18), canvas.set_ylim(0, 5.35), canvas.set_axis_off()

    def image(x, y, s, a, **kw):                        # an image tile with its lower-left corner at (x, y), side s
        ax = fig.add_axes([x / 18, y / 5.35, s / 18, s / 5.35])
        ax.imshow(a, interpolation="nearest", **kw)
        ax.set_xticks([]), ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_color("white"), sp.set_linewidth(2)
        return ax

    def arrow(x0, y0, x1, y1, color=GRAY):
        canvas.annotate("", xy=(x1, y1), xytext=(x0, y0),
                        arrowprops=dict(arrowstyle="-|>,head_width=0.5,head_length=0.9", lw=4, color=color))

    def text(x, y, s, size=20, **kw):
        canvas.text(x, y, s, ha="center", va="center", fontsize=size, **kw)

    # 1. NAIP image: near-infrared behind, true color in front
    image(1.05, 2.05, 3.0, nir, cmap="gray")
    image(0.55, 1.55, 3.0, rgb)
    text(2.3, 0.95, "NAIP image", 22, weight="bold")
    text(2.3, 0.45, "red, green, blue, near-infrared · 0.6 m", 13, color=GRAY)

    # 2. Site context chips (2 x 3, below the image arrow); flight date highlighted because season changes output
    chips = ["climate", "soil", "elevation", "land cover", "ecoregion", "flight date"]
    for i, c in enumerate(chips):
        x, y = 5.45 + (i % 2) * 1.75, 3.15 - (i // 2) * 0.62
        col = AMBER if c == "flight date" else "#878787"
        canvas.add_patch(matplotlib.patches.FancyBboxPatch((x - 0.78, y - 0.23), 1.56, 0.46, boxstyle="round,pad=0.04",
                                                           fc=col, ec="none"))
        text(x, y, c, 15, color="white", weight="bold")
    text(6.33, 0.95, "Site context", 22, weight="bold")

    # 3. The model: a U of blocks
    xs = np.linspace(9.0, 12.4, 7)
    ys = [3.9, 3.2, 2.5, 1.95, 2.5, 3.2, 3.9]
    hs = [1.5, 1.15, 0.8, 0.55, 0.8, 1.15, 1.5]
    for x, y, h in zip(xs, ys, hs):
        canvas.add_patch(matplotlib.patches.FancyBboxPatch((x - 0.17, y - h / 2), 0.34, h, boxstyle="round,pad=0.03",
                                                           fc=BLUE, ec="none"))
    for (x0, y0), (x1, y1) in zip(zip(xs[:-1], ys[:-1]), zip(xs[1:], ys[1:])):
        canvas.plot([x0 + 0.2, x1 - 0.2], [y0, y1], color=BLUE, lw=2.5, alpha=0.6)
    for i in range(3):                                  # skip connections
        canvas.plot([xs[i] + 0.2, xs[6 - i] - 0.2], [ys[i] + hs[i] / 2 + 0.12] * 2, color=BLUE, lw=1.5, ls="--",
                     alpha=0.6)
    text(10.7, 0.95, "U-Net model", 22, weight="bold")
    text(10.7, 0.5, "22 M parameters · trained on 22 M", 13, color=GRAY)
    text(10.7, 0.18, "NAIP–LiDAR image pairs across the US", 13, color=GRAY)

    # 4. Canopy height out
    image(14.1, 1.55, 3.0, height, cmap="viridis", norm=NORM)
    text(15.6, 0.95, "Canopy height", 22, weight="bold")
    text(15.6, 0.45, "meters, every 0.6 m pixel", 13, color=GRAY)

    arrow(4.2, 3.9, 8.65, 3.9)                          # image -> model, above the chips
    arrow(8.1, 2.55, 8.75, 3.25)                        # context -> model
    arrow(12.85, 3.9, 13.95, 3.9)                       # model -> canopy height
    fig.savefig(IMG / "naip_chm_infographic.png")
    plt.close(fig)


def label(ax, text):
    ax.text(0.015, 0.975, text, transform=ax.transAxes, ha="left", va="top", fontsize=22, weight="bold",
            color="white", bbox=dict(facecolor="black", alpha=0.55, edgecolor="none", pad=5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DATA)
    args = ap.parse_args()
    IMG.mkdir(parents=True, exist_ok=True)
    fig_infographic(args.data)
    fig_timeline()
    fig_derived((2011, 2020), args.data)
    fig_coverage()


DERIVED_WIN = (253700, 280700, 255700, 282200)          # 2 x 1.5 km of draws, imaged in every year shown


def best_rgb(year, data, win):
    """True color of the year's best image over `win`, as the model saw it: (label, rows x cols x 3 array)."""
    acq = pd.read_csv(ROOT / "catalog" / "acquisitions.csv")
    row = acq[acq.best.astype(bool) & (acq.start.str[:4] == str(year))].iloc[0]
    day = row.start.replace("-", "")
    if row.source == "NAIP":
        for p in sorted(data.glob(f"inputs/naip/{year}/naip_{day}_*.tif")):
            with rasterio.open(p) as d:
                b = rasterio.warp.transform_bounds(d.crs, "EPSG:6514", *d.bounds)
            if b[0] <= win[0] and b[1] <= win[1] and b[2] >= win[2] and b[3] >= win[3]:
                return f"NAIP {year}", read_rgb(p, win)
        raise SystemExit(f"no single {year} DOQQ covers the window")
    return f"fixed-wing {year}", read_rgb(data / "inputs" / "fixed_wing" / f"fixedwing_{day}.tif", win)


def fig_derived(years, data, win=DERIVED_WIN):
    """Derived 10 m products (scripts/08_derive.py) zoomed to `win`: one column per year, headed by the year; rows are
    the best image's true color, the CHM median and the CHM 95th percentile. Height panels show only cells with
    values in every year shown; other cells are light gray."""
    files = {y: next(DERIVED.glob(f"*_chm_{y}_*_10m.tif")) for y in years}
    common = None
    for f in files.values():
        with rasterio.open(f) as d:
            ok = d.read(1, window=from_bounds(*win, d.transform)) != d.nodata
        common = ok if common is None else common & ok
    cmap = plt.get_cmap("viridis").copy()
    cmap.set_bad("0.88")
    ext = (win[0], win[2], win[1], win[3])
    fig, axes = plt.subplots(3, len(years), figsize=(6.0 * len(years) + 1.4, 13.6), dpi=120, constrained_layout=True)
    for col, y in zip(axes.T, years):
        name, rgb = best_rgb(y, data, win)
        col[0].imshow(rgb, extent=ext, interpolation="nearest")
        label(col[0], name.rsplit(" ", 1)[0])                       # source only; the year heads the column
        col[0].set_title(str(y), fontsize=28, weight="bold", pad=10)
        for ax, (b, what) in zip(col[1:], ((1, "CHM median"), (2, "CHM 95th percentile"))):
            with rasterio.open(files[y]) as d:
                a = np.ma.masked_array(d.read(b, window=from_bounds(*win, d.transform)) / 100, mask=~common)
            im = ax.imshow(np.ma.clip(a, NORM.vmin, None), extent=ext, norm=NORM, cmap=cmap, interpolation="nearest")
            label(ax, what)
        for ax in col:
            ax.set_xticks([]), ax.set_yticks([])
    # attach to the whole grid so the columns stay aligned; shrink and anchor it beside the two height rows
    cb = fig.colorbar(im, ax=axes, shrink=0.6, anchor=(0.0, 0.1), pad=0.01)
    cb.set_label("height (m)", fontsize=14)
    cb.set_ticks(TICKS, labels=[f"{t:g}" for t in TICKS])
    cb.ax.tick_params(labelsize=12)
    fig.savefig(IMG / f"derived_{'_'.join(map(str, years))}.png")
    plt.close(fig)


def fig_coverage(step=10, per_row=8):
    """Where each map has data: NAIP and fixed-wing rows, one column per year, month-day(s) in each panel."""
    import geopandas as gpd
    from matplotlib.colors import ListedColormap
    from rasterio.features import rasterize
    acq = pd.read_csv(ROOT / "catalog" / "acquisitions.csv")
    best = acq[acq.best.astype(bool)]
    best_source = dict(zip(best.start.str[:4].astype(int), best.source))
    ranch = gpd.read_file(RANCH_IN_AOI).geometry.iloc[0]
    maps = {("NAIP", int(p.stem.split("_")[2])): p for p in NAIP_MAPS.glob("naip_chm_*.tif")}
    maps |= {("fixed-wing", int(p.stem.split("_")[2])): p for p in FIXED_WING_MAPS.glob("fixedwing_chm_*.tif")}
    leaf_off = set(acq[(acq.source == "NAIP") & acq.leaf_off.astype(bool)].start.str[:4].astype(int))
    colors = {"NAIP": COLORS["NAIP"], "fixed-wing": COLORS["fixed-wing"], "leaf-off": "#9e9e9e"}
    years = range(2009, 2025)

    def read(path):
        with rasterio.open(path) as d:
            h, w = d.height // step, d.width // step
            ok = d.read(1, out_shape=(h, w), resampling=Resampling.nearest) != d.nodata
            t = d.transform * d.transform.scale(d.width / w, d.height / h)
            b = d.bounds
        return ok, t, (b.left, b.right, b.bottom, b.top)

    def month_days(path):      # naip_chm_2016_20160730-20160816 -> "07-30\n08-16"
        return "\n".join(f"{d[4:6]}-{d[6:]}" for d in path.stem.split("_")[-1].split("-"))

    _, _, ext = read(next(iter(maps.values())))
    blocks = [list(years)[i:i + per_row] for i in range(0, len(years), per_row)]
    fig = plt.figure(figsize=(11, 8.7))
    gs = fig.add_gridspec(3 * len(blocks) - 1, per_row, height_ratios=([1, 1, 0.1] * len(blocks))[:-1], wspace=0.1,
                          hspace=0.42)
    ranch_px = None
    for bi, block in enumerate(blocks):
        for col, year in enumerate(block):
            for row, source in enumerate(("NAIP", "fixed-wing")):
                ax = fig.add_subplot(gs[3 * bi + row, col])
                ax.set_xticks([]), ax.set_yticks([])
                ax.set_xlim(ext[:2]), ax.set_ylim(ext[2:]), ax.set_aspect("equal")
                for sp in ax.spines.values():
                    sp.set_visible(False)
                if row == 0:
                    ax.set_title(str(year), fontsize=15, weight="bold", pad=6)
                if col == 0:
                    ax.text(-0.12, 0.5, source, transform=ax.transAxes, ha="right", va="center", fontsize=14,
                            weight="bold", color=colors[source])
                path = maps.get((source, year))
                if path is None:
                    ax.add_patch(plt.Rectangle((0, 0), 1, 1, transform=ax.transAxes, fc="none", ec="0.8", lw=0.8,
                                               ls=":"))
                    if (source, year) == ("NAIP", 2023):
                        ax.text(0.5, 0.5, "not run", transform=ax.transAxes, ha="center", va="center", fontsize=10,
                                color="0.5", style="italic")
                    continue
                ok, t, _ = read(path)
                if ranch_px is None:
                    ranch_px = rasterize([(ranch, 1)], out_shape=ok.shape, transform=t).astype(bool)
                color = colors["leaf-off"] if source == "NAIP" and year in leaf_off else colors[source]
                ax.imshow(ok, cmap=ListedColormap(["#ececec", color]), vmin=0, vmax=1, extent=ext,
                          interpolation="nearest")
                gpd.GeoSeries([ranch]).boundary.plot(ax=ax, color="k", lw=0.7)
                ax.set_xlim(ext[:2]), ax.set_ylim(ext[2:])
                ax.text(0.5, 0.97, month_days(path), transform=ax.transAxes, ha="center", va="top",
                        fontsize=11, weight="bold", linespacing=1.0,
                        bbox=dict(boxstyle="square,pad=0.15", fc="white", ec="none", alpha=0.85))
                if best_source.get(year) == source:
                    for sp in ax.spines.values():
                        sp.set_visible(True), sp.set_color(GOLD), sp.set_linewidth(3.5)
                ax.text(0.5, -0.05, f"{100 * ok.mean():.0f}%", transform=ax.transAxes, ha="center", va="top",
                        fontsize=12.5, weight="bold")
                ax.text(0.5, -0.2, f"Ranch {100 * ok[ranch_px].mean():.0f}%", transform=ax.transAxes, ha="center",
                        va="top", fontsize=9.5, color="0.3")
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    handles = [Patch(fc="white", ec=GOLD, lw=3, label="best image (the recommended series)"),
               Patch(fc=colors["leaf-off"], label="leaf-off NAIP (October-November)"),
               Patch(fc="#ececec", ec="0.75", lw=0.6, label="no data"),
               Line2D([], [], color="k", lw=0.9, label="Ranch boundary")]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.045), ncol=2, fontsize=12.5,
               frameon=False, handlelength=1.6, columnspacing=2.5)
    fig.savefig(IMG / "coverage.png", dpi=150, bbox_inches="tight", facecolor="w")
    plt.close(fig)


if __name__ == "__main__":
    main()
