# reference/

Small vector layers, in git. Rasters for figures and site searches, and the Ranch boundaries, are in
`data/reference/` (gitignored; listed in `catalog/manifest.csv`).

| File | What | From |
|---|---|---|
| `aoi.gpkg` | the map extent | `scripts/01_aoi.py` |
| `doqq_footprints.gpkg` | every NAIP DOQQ over the extent: year, quad, acquisition date, day of year, resolution, centroid | `scripts/02_naip_inputs.py` |
| `draws.gpkg`, `draw_networks_aoi.gpkg` | draw channels (catchment >= 5 ha, perennial creeks and floodplain removed) and draw networks | the earlier draw analysis (LiDAR DEM, 5 m) |
| `wire_corridors.gpkg` | wire corridors over the map extent, left out of statistics | traced on the LiDAR in the earlier draw analysis |

The Ranch boundaries are private and stay out of git, in `data/reference/`: `ranch_boundary_planner.geojson` set the
extent's south edge (01_aoi.py, which falls back to the recorded edge without it), `ranch_boundary.geojson` defines
"on the Ranch" in the 2021 validation (07_validate.py), and `ranch_in_aoi.gpkg` is the Ranch part of the extent. The two
boundaries differ by 0.8 km² but give the same south edge to within 0.3 m; each is kept where it was used, so the
stored numbers reproduce exactly.
