"""Best-image time-lapse GIF for one window: true color of the best image on the left, its NAIP-CHM on the right,
one frame per best-series year (14 frames, 2009-2024; no imagery in 2010 or 2012).

Default window: candidate 3 of scripts/gif_sites.py, the N21 draw where it leaves the bench above the valley floor
(x 252,950-253,250, y 282,050-282,350, EPSG:6514; 300 m square, 0.6 m pixels).
True color is the image the model saw (NAIP tiles matched per pixel by the mosaic's day-of-year band; fixed-wing
averaged to 0.6 m), stretched 2-98% per band in each frame. Height uses a log scale, 0.1-10 m. Light grid lines
every 50 m (EPSG:6514 multiples) on both panels help match places between them; they are drawn 1 px wide without
antialiasing, so their color is exact in the GIF palette.
The GIF has one 256-color palette for all frames: true color gets colors fitted to it (dithered), the height map\n60 steps of its colormap (not dithered).
A strip under the true color marks the series' years (green NAIP, blue fixed-wing) and the current one.

Output: docs/img/best_image_timelapse.gif
Run: python scripts/timelapse_gif.py [--win x0 y0 x1 y1] [--ms 900]
"""

import argparse
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

import gif_sites as gs

WIN = (252950, 282050, 253250, 282350)
COLORS = {"NAIP": "#1b7837", "fixed-wing": "#2166ac"}
GRID_M = 50                                            # light grid on both panels, on EPSG:6514 multiples of 50 m
GRID_COLOR = {"rgb": "#d9d9d9", "chm": "#999999"}      # mid gray on the dark height map, so it stays light there
OUT = gs.ROOT / "docs" / "img" / "best_image_timelapse.gif"


def label(ax, text):
    ax.text(0.025, 0.975, text, transform=ax.transAxes, ha="left", va="top", fontsize=20, weight="bold", color="w",
            bbox=dict(boxstyle="square,pad=0.25", fc="k", ec="none", alpha=0.65))


def frame(year, maps, win, cmap):
    source, dates, mosaic, rgb_paths = maps[year]
    h, doy = gs.window_chm(mosaic, win)
    rgb = gs.window_rgb(source, rgb_paths, doy, win)
    fig = plt.figure(figsize=(10.3, 5.95), dpi=100)
    ax_rgb = fig.add_axes([0.01, 0.145, 0.485, 0.84])
    ax_chm = fig.add_axes([0.505, 0.145, 0.485, 0.84])
    ext = (win[0], win[2], win[1], win[3])
    ax_rgb.imshow(rgb, extent=ext, interpolation="nearest")
    ax_chm.imshow(np.ma.clip(h, gs.NORM.vmin, None), extent=ext, norm=gs.NORM, cmap=cmap, interpolation="nearest")
    label(ax_rgb, f"{source} {dates.replace(' / ' + str(year) + '-', ' / ')}")
    label(ax_chm, f"NAIP-CHM {year}")
    for ax, grid_color in ((ax_rgb, GRID_COLOR["rgb"]), (ax_chm, GRID_COLOR["chm"])):
        for x in np.arange(np.ceil(win[0] / GRID_M + 1e-9), win[2] / GRID_M) * GRID_M:   # interior lines only
            ax.axvline(x, color=grid_color, lw=0.72, antialiased=False)
        for y in np.arange(np.ceil(win[1] / GRID_M + 1e-9), win[3] / GRID_M) * GRID_M:
            ax.axhline(y, color=grid_color, lw=0.72, antialiased=False)
        ax.set_xlim(ext[:2]), ax.set_ylim(ext[2:])
        ax.set_xticks([]), ax.set_yticks([])
    # years of the series, current one ringed
    ax_t = fig.add_axes([0.04, 0.035, 0.425, 0.07])
    for y, (s, *_) in maps.items():
        ax_t.scatter(y, 0, s=150 if y == year else 40, color=COLORS[s], ec="k" if y == year else "none", lw=2, zorder=3)
    ax_t.axhline(0, color="0.6", lw=1, zorder=1)
    ax_t.set_xlim(2008.4, 2024.6), ax_t.set_ylim(-1, 1)
    ax_t.set_xticks([2010, 2015, 2020], labels=["2010", "2015", "2020"], fontsize=11)
    ax_t.tick_params(length=0, pad=2)
    ax_t.set_yticks([])
    for side in ("top", "right", "left", "bottom"):
        ax_t.spines[side].set_visible(False)
    ax_c = fig.add_axes([0.53, 0.075, 0.435, 0.03])
    cb = fig.colorbar(plt.cm.ScalarMappable(gs.NORM, cmap), cax=ax_c, orientation="horizontal")
    cb.set_ticks([0.1, 0.25, 0.5, 1, 2, 5, 10], labels=["0.1", "0.25", "0.5", "1", "2", "5", "10 m"])
    cb.ax.tick_params(labelsize=11)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="w")
    plt.close(fig)
    return Image.open(buf).convert("RGB")


def to_gif_frames(frames, cmap, n_ramp=60):
    """Index every frame into one shared 256-color palette. Left half (true color, year strip): 189 colors fitted to
    all frames' left halves plus white, black and gray, dithered. Right half (NAIP-CHM, colorbar): n_ramp steps of
    the height colormap plus white, black and two grays, not dithered, so the photo can't borrow ramp colors and the
    height map stays exact."""
    split = frames[0].size[0] // 2
    neutrals = [[255, 255, 255], [0, 0, 0], [153, 153, 153]]
    n_fit = 192 - len(neutrals)
    left = Image.fromarray(np.vstack([np.asarray(f)[:, :split] for f in frames]))
    fitted = np.array(left.quantize(colors=n_fit, method=Image.Quantize.MEDIANCUT).getpalette()[:3 * n_fit]).reshape(-1, 3)
    pal_left = np.vstack([fitted, neutrals])                                      # 192 colors, indices 0-191
    ramp = (np.array([cmap(v)[:3] for v in np.linspace(0, 1, n_ramp)]) * 255).round()
    pal_right = np.vstack([ramp, neutrals, [[217, 217, 217]]])                    # 64 colors, indices 192-255
    def palette_image(colors):
        im = Image.new("P", (1, 1))
        im.putpalette([int(v) for v in colors.ravel()] + [0, 0, 0] * (256 - len(colors)))   # pads with black
        return im
    p_left, p_right = palette_image(pal_left), palette_image(pal_right)
    black_left, black_right = len(fitted) + 1, n_ramp + 1
    out = []
    for f in frames:
        a = np.asarray(f)
        il = np.asarray(Image.fromarray(a[:, :split]).quantize(palette=p_left, dither=Image.Dither.FLOYDSTEINBERG)).copy()
        ir = np.asarray(Image.fromarray(a[:, split:]).quantize(palette=p_right, dither=Image.Dither.NONE)).copy()
        il[il >= len(pal_left)] = black_left                                     # padding entries are black too
        ir[ir >= len(pal_right)] = black_right
        g = Image.fromarray(np.hstack([il, ir.astype(np.uint16) + len(pal_left)]).astype(np.uint8), mode="P")
        g.putpalette([int(v) for v in np.vstack([pal_left, pal_right]).ravel()])
        out.append(g)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--win", type=float, nargs=4, default=WIN)
    ap.add_argument("--ms", type=int, default=900)
    args = ap.parse_args()
    maps = gs.best_maps()
    cmap = plt.get_cmap("viridis").copy()
    cmap.set_bad("0.85")
    frames = [frame(y, maps, tuple(args.win), cmap) for y in maps]
    frames = to_gif_frames(frames, cmap)
    durations = [args.ms] * (len(frames) - 1) + [args.ms * 2]
    frames[0].save(OUT, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)
    print(f"{OUT.relative_to(gs.ROOT)}: {len(frames)} frames, {frames[0].size[0]} x {frames[0].size[1]} px, "
          f"{OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
