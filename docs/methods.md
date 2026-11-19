# Methods and reproduction

[← README](../README.md)

## How the model was run

- **Model**: the authors' weights (`model_20251016.pt`) and their own inference code (`process_naip_quad`), from [smorf-ntsg/naip-chm](https://github.com/smorf-ntsg/naip-chm) pinned at commit `3a474f4` (described in [Morford et al. 2026](https://doi.org/10.1038/s41597-026-07549-w); version of record on [Zenodo](https://doi.org/10.5281/zenodo.17664995)) as a git submodule.
- **Settings**: the authors' own: 0.6 m, 432-pixel chips, 0.3 overlap, and their conditioning rasters (climate and soil, elevation, land cover, ecoregion), clipped once for the extent.
- **Hardware**: Apple MPS. The code also runs on CUDA or CPU.
- **Flight date**: the model conditions on day of year, read from each input's file name, so every input is named with its own acquisition date.
- **NAIP**: each DOQQ is clipped on its own grid and CRS and passed as 4-band RGBN. The authors' resampling path fails on 5-band input that isn't 0.6 m, so no mask band is added. 1 m years are resampled to 0.6 m by the model's own code (bilinear).
- **Fixed-wing**: the 0.152 m 4-band mosaic is area-averaged to the 0.6 m product grid, with a mask band for cells fully covered by imagery. Values are scaled the same way as NAIP (DN/255), with no matching to NAIP.
- **Mosaics**: where DOQQ predictions overlap, each pixel takes the DOQQ whose footprint centroid is nearest. Band 2 records each pixel's acquisition day of year.

## Reproduce

Each step handles two sets: `maps` (the products) and `validation` (the LiDAR check areas and one 2023 DOQQ). With no
argument a step does both; finished files are skipped, so a run can resume. Steps 02 and 03 accept `--years`, and
01-06 accept `--out DIR` to write elsewhere for checking. All of 01-07 were checked against the stored files
(2026-11-19): the extent, conditioning, sampled NAIP and fixed-wing inputs, a model run and all 20 maps came out
identical, and every validation table matched. The USGS Imagery Topo tile service behind the base map no longer serves
tiles, so 01 keeps the stored base map.

```sh
git clone --recurse-submodules https://github.com/mosscoder/mpg-naip-chm
cd mpg-naip-chm
conda env create -f environment.yml && conda activate mpg-naip-chm
python scripts/01_aoi.py               # extent (reference/aoi.gpkg) and base map
python scripts/02_naip_inputs.py       # DOQQ clips (Planetary Computer; Earth Engine for 2009) and validation NAIP
python scripts/03_fixedwing_inputs.py  # fixed-wing mosaics area-averaged to 0.6 m, maps and validation areas
python scripts/04_conditioning.py      # the authors' conditioning rasters, clipped once per set
python scripts/05_run_model.py         # NAIP-CHM on MPS / CUDA / CPU
python scripts/06_mosaic.py            # per-year maps with a day-of-year band
python scripts/07_validate.py          # official-tile check and LiDAR checks -> validation/
python scripts/08_derive.py 2011 2020  # 10 m median and 95th percentile of the best map per year
python scripts/09_publish.py cog      # convert maps to cloud-optimized GeoTIFFs (pixels checked unchanged)
python scripts/09_publish.py catalog  # catalog.csv: one row per map
python scripts/09_publish.py upload   # to gs://mpg-naip-chm; needs credentials (--dry-run lists only)
python scripts/manifest.py             # catalog/manifest.csv after anything in data/ changes
python scripts/readme_figures.py       # docs/img/: infographic, acquisition timeline, coverage, derived figure
Rscript scripts/readme_r_example.R     # docs/img/r_example_2019.png (the README R chunk)
Rscript scripts/readme_derive_example.R  # README derived-products chunk; checks it against 08_derive.py
python scripts/gif_sites.py            # candidate time-lapse windows on low draws (scratch/gif_sites/)
python scripts/timelapse_gif.py        # docs/img/best_image_timelapse.gif (candidate 3)
```

Shared settings (grid, model settings, sources, validation areas) are in `scripts/common.py`; folder names in
`scripts/paths.py`. Only publishing needs credentials: a service-account key named by `AERIAL_SURVEY_CREDENTIALS` in
`.env`.

## Layout

Code, docs and small vectors are in git. Everything large is under `data/`, which is gitignored; `catalog/manifest.csv`
lists every file there with its size and checksum (`python scripts/manifest.py --check` compares a copy against it).
Folder names are defined once, in `scripts/paths.py`.

```
mpg-naip-chm/
├── README.md
├── environment.yml
├── external/naip-chm/      git submodule: the authors' code and weights at 3a474f4 (MIT)
├── scripts/                pipeline 01-09, common.py, paths.py, manifest.py, README examples, GIF tools
├── catalog/                acquisitions.csv (timeline), catalog.csv (copy of the bucket's), manifest.csv (data/)
├── docs/                   these pages and img/
├── validation/             tables and figures from 07_validate.py
├── reference/              small vectors: map extent, DOQQ footprints, draws, wire corridors
└── data/                   gitignored
    ├── products/           naip/, fixed_wing/, derived/: the published maps
    ├── inputs/             naip/{year}/ DOQQ clips, fixed_wing/ at 0.6 m, conditioning/
    ├── predictions/        per-tile model output before mosaicking (naip/{year}/, fixed_wing/)
    ├── validation/         validation runs (inputs_2016, inputs_2020, inputs_2021, predictions/),
    │                       official_2023/, conditioning_*, lidar/ (LiDAR CHM clips named by flight date)
    └── reference/          base map, terrain/ (5 m DEM, height above river, draw rasters), Ranch boundaries
```

Products are published to the public bucket `gs://mpg-naip-chm`; the rest of `data/` stays local, and the files that can't be rebuilt from public sources should be backed up privately.
