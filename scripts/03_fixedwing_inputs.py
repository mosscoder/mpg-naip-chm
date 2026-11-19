"""Fixed-wing model inputs: the Ranch's 4-band mosaics area-averaged to 0.6 m, for the maps and the validation areas.

Source: gs://mpg-aerial-survey/surveys/fixed_wing/cogs/clean/{year}_4band.tif (0.152 m, EPSG:6514, public), read over
HTTPS. Band 5 of each mosaic marks imaged pixels; an output cell is valid only if imagery covers all of it.
Values stay raw DN (the model scales them by 1/255, as for NAIP); nothing is matched to NAIP.
Flight dates come from catalog/acquisitions.csv and go into each input's name, fixedwing_{YYYYMMDD}.tif, so the
model's day-of-year conditioning matches the flight.
  Maps: every survey year (2011, 2013-2018, 2020-2024), on the product grid buffered by 150 m (250 pixels).
  Validation: the 2016, 2020 and 2021 areas buffered by 150 m.

Outputs: data/inputs/fixed_wing/fixedwing_{YYYYMMDD}.tif, data/validation/inputs_{year}/fixedwing_{YYYYMMDD}.tif
Run: python scripts/03_fixedwing_inputs.py [maps|validation] [--years Y ...] [--out DIR]
"""

import argparse
from pathlib import Path

import pandas as pd

import common as c
from paths import CATALOG, FIXED_WING_INPUTS, VALIDATION

PAD_PX = int(round(c.BUFFER_M / c.RES))


def flights():
    acq = pd.read_csv(CATALOG / "acquisitions.csv")
    fw = acq[acq.source == "fixed-wing"]
    return {int(d[:4]): d.replace("-", "") for d in fw.start}


def map_input_bounds():
    t, (h, w) = c.map_grid()
    pad = PAD_PX * c.RES
    return t.c - pad, t.f - h * c.RES - pad, t.c + w * c.RES + pad, t.f + pad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("set", nargs="?", choices=("maps", "validation"))
    ap.add_argument("--years", type=int, nargs="+")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    if args.set in (None, "maps"):
        out_dir = args.out / "fixed_wing" if args.out else FIXED_WING_INPUTS
        for year, d in sorted(flights().items()):
            if args.years and year not in args.years:
                continue
            c.average_fixed_wing(c.FIXED_WING_URL.format(year=year), map_input_bounds(), out_dir / f"fixedwing_{d}.tif")
    if args.set in (None, "validation"):
        root = args.out / "validation" if args.out else VALIDATION
        for year, v in c.VALIDATION.items():
            if args.years and int(year) not in args.years:
                continue
            c.average_fixed_wing(c.FIXED_WING_URL.format(year=year), c.buffered(v["area"]),
                                 root / f"inputs_{year}" / f"fixedwing_{v['fixed_wing']}.tif")


if __name__ == "__main__":
    main()
