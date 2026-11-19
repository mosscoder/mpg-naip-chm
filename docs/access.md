# Access

[← README](../README.md)

Every product is a public, cloud-optimized GeoTIFF. Reading one needs no credentials, and reading a window downloads only that window. Band 1 is canopy height in centimeters (nodata 65535); band 2 is the acquisition day of year. Coordinates are EPSG:6514 meters.

Base URL: `https://storage.googleapis.com/mpg-naip-chm/`

## R (terra)

See the [README](../README.md#load-in-r) for one map and the best-image series. To skip off-season maps but keep both sources in years that have both:

```r
catalog <- read.csv("https://storage.googleapis.com/mpg-naip-chm/catalog.csv")
in_season_maps <- catalog[catalog$season == "in", ]
```

## Python (rasterio)

```python
import pandas as pd
import rasterio
from rasterio.windows import from_bounds

base = "https://storage.googleapis.com/mpg-naip-chm/"
bounds = (252880, 282150, 253180, 282350)  # x0, y0, x1, y1

catalog = pd.read_csv(base + "catalog.csv")
series = {}
for row in catalog[catalog.best].itertuples():
    with rasterio.open(base + row.path) as src:
        a = src.read(1, window=from_bounds(*bounds, src.transform), masked=True)
    series[f"{row.year} {row.source} {row.acquired}"] = a / 100  # meters
```

## GDAL command line

```sh
gdal_translate -b 1 -projwin 252880 282350 253180 282150 \
  /vsicurl/https://storage.googleapis.com/mpg-naip-chm/naip/naip_chm_2021_20210829.tif \
  chm_2021_window.tif
```

## QGIS

Layer → Add Layer → Add Raster Layer → Source type *Protocol: HTTP(S), cloud, etc.*, then paste a file's HTTPS URL. Style band 1 with a logarithmic color ramp to see short vegetation next to trees.

## Whole files

```sh
curl -O https://storage.googleapis.com/mpg-naip-chm/naip/naip_chm_2021_20210829.tif
gcloud storage cp "gs://mpg-naip-chm/fixed_wing/*.tif" .
```

Each file is 60–150 MB; the NAIP series totals about 1.0 GB and the fixed-wing series 1.4 GB.
