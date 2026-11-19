# Quality assessment with LiDAR validation

[← README](../README.md)

LiDAR is a reference here, not ground truth. Each check pairs an image with the nearest LiDAR flight, sometimes months apart, and most checks fall in areas next to the product extent rather than inside it. Every number here comes from `scripts/07_validate.py`; its tables and figures are in [`validation/`](../validation/).

## Does local inference match the official product?

We ran one 2023 NAIP DOQQ through the model on Apple MPS and compared it with the authors' official tile. The result was r 0.997 with a bias of −0.005 m, and cover ≥ 2 m was 52.02% against 52.04%. The remaining differences are small and unbiased, consistent with GPU numerics.

## Height in 1 m cells

LiDAR is nearest-resampled to 1 m and predictions are area-averaged. Each row uses only cells where the LiDAR and both predictions exist. Power lines are masked, because LiDAR records the wires as 40–85 m "canopy".

| LiDAR flown | Area (cells; mean LiDAR height) | Fixed-wing | NAIP | r² fixed-wing / NAIP | RMSE fixed-wing / NAIP |
|---|---|---|---|---|---|
| 2016-09-01 | Bitterroot River corridor (7.4 M; 3.4 m) | 2016-06-22 | 2016-08-16, 1 m | 0.67 / 0.82 | 4.24 / 2.98 m |
| 2020-05-09 / 06-02 | Ranch south-boundary strip (0.8 M; 0.2 m) | 2020-06-22 | 2019-09-15, 0.6 m | 0.74 / 0.78 | 0.81 / 0.78 m |
| 2021-10-16 | Eastern forest tile (55.2 M; 5.4 m) | 2021-06-29 | 2021-08-29, 0.6 m | 0.65 / 0.70 | 4.18 / 3.97 m |

- **NAIP fits better than fixed-wing against every LiDAR flight.** That includes 2016, when NAIP was 1 m.
- **The 2021 fixed-wing run levels off near 18 m in tall conifers**, where NAIP keeps rising. In 2016 the fixed-wing run tracked LiDAR to about 20 m, close to NAIP, so the plateau is specific to the 2021 flight.
- **Below 10 m of LiDAR height, r² falls to 0.30–0.48.** Medians stay near 1:1, but crown height spills into gaps and crown edges.

## Open-country draws

These are 25 m buffers along draws through open, low vegetation, in 1 m cells with LiDAR ≤ 10 m.

| LiDAR flown | r² fixed-wing / NAIP |
|---|---|
| 2016-09-01 | 0.48 / 0.62 |
| 2021-10-16 | 0.50 / 0.40 |

Cover ≥ 0.5 m per draw segment ranks the same as LiDAR (Spearman 0.94–0.97). Its level agrees in 2016 (bias under 1 point) but runs 7–10 points high in 2021.

## Short vegetation

Results are in 3 m cells on open Ranch ground, against the 2021-10-16 LiDAR.

| LiDAR height | Fixed-wing 2021-06-29 | NAIP 2021-08-29 | NAIP 2023-10-19 (official) |
|---|---|---|---|
| 0.5–1 m (median 0.79) | 0.35 m | 0.44 m | 0.26 m |
| 1–1.5 m (median 1.23) | 0.48 m | 0.57 m | 0.31 m |
| 1.5–2 m (median 1.67) | 0.62 m | 0.73 m | 0.37 m |
| Presence ≥ 0.5 m, AUC | 0.88 | 0.91 | 0.87 |
| ≥ 1 m among short plants, AUC | 0.66 | 0.66 | 0.59 |

- **Short vegetation is found but compressed.** Presence is detected well, while heights come out at about half (August NAIP), 40% (June fixed-wing) or a quarter (October NAIP) of LiDAR's.
- **Height classes within 0.5–2 m can't be separated** (AUC 0.59–0.66).
- **Season alone halves short-vegetation cover**: the same model on the same ground gives about half the 0.5–2 m cover from October imagery as from August imagery. This is why off-season maps are left out of the best-image series.

## Tall vegetation (30 m cells)

Against the 2021-10-16 LiDAR, cover ≥ 2 m reaches ρ 0.97 from both NAIP 2021-08-29 and fixed-wing 2021-06-29. Both overstate cover at 2 m by about 10 points, because crown edges blur. At 5 m they are close to LiDAR.

## Caveats

- **Fixed-wing radiometry isn't matched to NAIP.** Fixed-wing values enter the model as raw values scaled to 0–1 (DN/255), as for NAIP. Camera, flight date and phenology can't be separated.
- **LiDAR processing differs between flights.** The 2016 CHM is a lidR pit-free build; 2020 and 2021 are Montana State Library products.
- **Heights are model estimates.** Report heights or cover above fixed heights (≥ 0.5 m, ≥ 2 m). Height alone doesn't identify plant type.
