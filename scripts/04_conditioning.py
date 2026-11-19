"""The model's conditioning rasters, clipped once per set so inference doesn't fetch them for every chip.

Source: the authors' server (inference-resources/conditioning-data): climate PCA (6 bands) and soil PCA (6), 928 m;
elevation, 240 m; NLCD land cover and ecoregion, 386 m; all EPSG:5070. The model samples them once per chip at its
center. Each clip covers the area buffered by 150 m, plus 3 km so every chip center near the edge is inside.
  Maps: the map extent.
  Validation: the 2021 area (conditioning_2021); the 2016 and 2020 areas together (conditioning_2016_2020).

Outputs: data/inputs/conditioning/, data/validation/conditioning_{2021,2016_2020}/
Run: python scripts/04_conditioning.py [maps|validation] [--out DIR]
"""

import argparse
from pathlib import Path

import numpy as np

import common as c
from paths import CONDITIONING, VALIDATION


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("set", nargs="?", choices=("maps", "validation"))
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    if args.set in (None, "maps"):
        region = c.map_extent().buffer(c.BUFFER_M, join_style="mitre")
        c.clip_conditioning(region.bounds, args.out / "conditioning" if args.out else CONDITIONING)
    if args.set in (None, "validation"):
        root = args.out / "validation" if args.out else VALIDATION
        groups = {}
        for v in c.VALIDATION.values():
            groups.setdefault(v["conditioning"], []).append(c.buffered(v["area"]))
        for name, boxes in groups.items():
            b = np.array(boxes)
            c.clip_conditioning((*b[:, :2].min(0), *b[:, 2:].max(0)), root / name)


if __name__ == "__main__":
    main()
