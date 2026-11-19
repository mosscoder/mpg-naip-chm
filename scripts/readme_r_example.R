# The README's "Load in R" chunk as a script; writes docs/img/r_example_2019.png.
# Usage: Rscript scripts/readme_r_example.R [BASE]
#   BASE defaults to the public bucket. Before publication, pass a local folder laid out like the bucket
#   (catalog.csv, naip/, fixed_wing/). The committed PNG was rendered from such a local copy on 2026-11-18.
library(terra)
args <- commandArgs(trailingOnly = TRUE)
bucket_url <- if (length(args)) args[1] else "https://storage.googleapis.com/mpg-naip-chm/"
vsi <- if (grepl("^https?://", bucket_url)) "/vsicurl/" else ""

# The catalog lists every map; best == TRUE marks the one best map per year
catalog   <- read.csv(paste0(bucket_url, "catalog.csv"))
best_maps <- catalog[catalog$best, ]

# Retrieve one year from the best-image series
map_2019 <- best_maps[best_maps$year == 2019, ]
map_2019$source; map_2019$acquired                                   # "naip", "2019-09-15"
chm_cm <- rast(paste0(vsi, bucket_url, map_2019$path))[[1]]  # band 1: height in cm

# Helper: a lat/lon box (WGS84 degrees) as an extent in the products' CRS (EPSG:6514)
latlon_extent <- function(west, south, east, north, crs_to = "EPSG:6514") {
  box <- densify(vect(ext(west, east, south, north), crs = "EPSG:4326"), 10)
  ext(project(box, crs_to))
}

# Read only an area of interest and convert to meters
area_of_interest <- latlon_extent(-114.0439, 46.6985, -114.0399, 46.7004)  # or ext() in EPSG:6514 meters
height_m <- crop(chm_cm, area_of_interest) / 100
names(height_m) <- "height_m"
height_m

png("docs/img/r_example_2019.png", width = 900, height = 680, res = 120)
plot(height_m, breaks = c(0, 0.25, 0.5, 1, 2, 5, 10, 25), col = hcl.colors(7, "viridis"),
     main = "NAIP-CHM 2019", plg = list(title = "m"))
invisible(dev.off())
