"""Run the published NAIP-CHM model on every input.

The authors' own code and weights (external/naip-chm at 3a474f4, model_20251016.pt, process_naip_quad), with their
settings: 432-pixel chips, 0.3 overlap, 0.6 m (1 m NAIP is resampled to 0.6 m by their code, bilinear). Device: Apple
MPS if available, else CUDA, else CPU. Day of year comes from the date in each input's file name; conditioning
from 04. Outputs are uint16 centimeters, nodata 65535. Finished outputs are skipped, so a run can resume.
  Maps: data/inputs/naip/{year}/*.tif -> data/predictions/naip/{year}/; data/inputs/fixed_wing/*.tif ->
        data/predictions/fixed_wing/.
  Validation: the 2023 DOQQ, the 2021 NAIP and fixed-wing inputs (conditioning_2021) and the 2016 and 2020 inputs
        (conditioning_2016_2020) -> data/validation/predictions/.

Run: python scripts/05_run_model.py [maps|validation] [--only NAME ...] [--out DIR]
     --only runs inputs whose file stem or year folder is listed; --out writes predictions under DIR, for checking.
"""

import argparse
from pathlib import Path

import common as c
from paths import CONDITIONING, FIXED_WING_INPUTS, NAIP_INPUTS, PREDICTIONS, VALIDATION


def map_jobs(out_root):
    jobs = [(p, CONDITIONING, out_root / "naip" / p.parent.name) for p in sorted(NAIP_INPUTS.glob("*/naip_*.tif"))]
    jobs += [(p, CONDITIONING, out_root / "fixed_wing") for p in sorted(FIXED_WING_INPUTS.glob("fixedwing_*.tif"))]
    return jobs


def validation_jobs(out_dir):
    cond_2021, cond_2016_2020 = VALIDATION / "conditioning_2021", VALIDATION / "conditioning_2016_2020"
    jobs = [(VALIDATION / "official_2023" / f"{c.VALID_DOQQ}.tif", cond_2021, out_dir),
            (c.validation_inputs(2021) / "naip_20210829.tif", cond_2021, out_dir),
            (c.validation_inputs(2021) / "fixedwing_20210629.tif", cond_2021, out_dir)]
    jobs += [(p, cond_2016_2020, out_dir) for year in (2016, 2020) for p in sorted(c.validation_inputs(year).glob("*.tif"))]
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("set", nargs="?", choices=("maps", "validation"))
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    jobs = []
    if args.set in (None, "maps"):
        jobs += map_jobs(args.out / "predictions" if args.out else PREDICTIONS)
    if args.set in (None, "validation"):
        jobs += validation_jobs(args.out / "validation_predictions" if args.out else VALIDATION / "predictions")
    if args.only:
        jobs = [j for j in jobs if j[0].stem in args.only or j[0].parent.name in args.only]
    c.run_model(jobs)


if __name__ == "__main__":
    main()
