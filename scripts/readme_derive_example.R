# The README's "Derived canopy metrics" chunk as a script, plus a check against the Python files from 08_derive.py.
# Usage: Rscript scripts/readme_derive_example.R [BASE]   (BASE as in readme_r_example.R)
library(terra)
args <- commandArgs(trailingOnly = TRUE)
bucket_url <- if (length(args)) args[1] else "https://storage.googleapis.com/mpg-naip-chm/"
vsi <- if (grepl("^https?://", bucket_url)) "/vsicurl/" else ""
catalog   <- read.csv(paste0(bucket_url, "catalog.csv"))
best_maps <- catalog[catalog$best, ]

# Continues from "Load in R" (uses bucket_url and best_maps).

# Settings: the published 10 m files use these same values (scripts/08_derive.py)
cell_size_m     <- 10                                      # side of each summary cell (m)
min_valid_share <- 0.5                                     # a cell needs data in at least this share of its pixels
grid_origin     <- c(x = 252000, y = 286000)               # top-left corner of the products' grid (EPSG:6514 m);
                                                           #   keeps cells aligned across years and with the files
upper_quantile  <- 0.95                                    # the upper height percentile reported
figure_area     <- ext(253700, 255700, 280700, 282200)     # area to summarize (EPSG:6514 m): the figure below

# Summarize a 0.6 m canopy height map (cm) in square cells with any function of the heights (m).
# Each 0.6 m pixel counts toward the cell holding its center; cells with data in less than min_valid_share
# of their pixels are left NA. summary_fun gets only pixels with data and returns one or more named values;
# each becomes a layer.
summarize_cells <- function(chm_cm, area, summary_fun, cell_size, min_valid_share, grid_origin) {
  # the grid, snapped outward from the origin to cover the area
  snap  <- function(value, origin, round_fun) origin + cell_size * round_fun((value - origin) / cell_size)
  cells <- rast(ext(snap(xmin(area), grid_origin[["x"]], floor), snap(xmax(area), grid_origin[["x"]], ceiling),
                    snap(ymin(area), grid_origin[["y"]], floor), snap(ymax(area), grid_origin[["y"]], ceiling)),
                resolution = cell_size, crs = crs(chm_cm))

  pixels        <- crop(chm_cm, cells, snap = "out")
  height_m      <- values(pixels, mat = FALSE) / 100
  cell_of_pixel <- cellFromXY(cells, xyFromCell(pixels, seq_len(ncell(pixels))))
  in_area       <- !is.na(cell_of_pixel)
  has_height    <- in_area & !is.na(height_m)

  heights_by_cell <- split(height_m[has_height],
                           factor(cell_of_pixel[has_height], levels = seq_len(ncell(cells))))
  example <- summary_fun(heights_by_cell[[which(lengths(heights_by_cell) > 0)[1]]])
  n_stats <- length(example)
  stats <- vapply(heights_by_cell,
                  \(h) if (length(h)) as.numeric(summary_fun(h)) else rep(NA_real_, n_stats),
                  numeric(n_stats))
  stats <- matrix(stats, ncol = n_stats, byrow = TRUE)                  # one row per cell

  pixels_per_cell <- tabulate(cell_of_pixel[in_area], ncell(cells))
  valid_per_cell  <- tabulate(cell_of_pixel[has_height], ncell(cells))
  stats[valid_per_cell < min_valid_share * pixels_per_cell, ] <- NA

  layer_names <- if (is.null(names(example))) paste0("stat_", seq_len(n_stats)) else names(example)
  summary_map <- rast(cells, nlyrs = n_stats, names = layer_names)
  values(summary_map) <- stats
  summary_map
}

load_best_map <- function(year) rast(paste0(vsi, bucket_url, best_maps$path[best_maps$year == year]))[[1]]
summarize     <- function(year, summary_fun) {
  summarize_cells(load_best_map(year), figure_area, summary_fun,
                  cell_size = cell_size_m, min_valid_share = min_valid_share, grid_origin = grid_origin)
}
height_stats  <- function(h) {                             # zeros (bare ground) included
  setNames(c(median(h), quantile(h, upper_quantile, names = FALSE)),
           c("median_m", sprintf("p%g_m", 100 * upper_quantile)))                 # layers median_m, p95_m
}

summaries <- lapply(c("2011" = 2011, "2020" = 2020), summarize, summary_fun = height_stats)
pdf(NULL); plot(summaries[["2020"]], breaks = c(0, 0.25, 0.5, 1, 2, 5, 10, 35), col = hcl.colors(7, "viridis"))

# Any function of the heights works, e.g. the share of each cell at least 2 m tall:
cover_2020 <- summarize(2020, \(h) c(cover_ge_2m = mean(h >= 2)))

# Check: the Python files round to whole centimeters, so differences should be <= 0.005 m
for (year in names(summaries)) {
  python_file <- list.files("data/products/derived", pattern = paste0("_chm_", year, "_.*_10m[.]tif$"), full.names = TRUE)
  if (length(python_file)) {
    difference <- abs(summaries[[year]] - crop(rast(python_file), figure_area) / 100)
    cat(year, "max |R - Python| (m):", round(global(difference, "max", na.rm = TRUE)[, 1], 3), "\n")
  }
}
