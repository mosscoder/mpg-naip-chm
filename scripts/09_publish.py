"""Publish the products to the public bucket gs://mpg-naip-chm (us-central1, hierarchical namespace, public read).

Stages (run in order; each can be run alone):
  cog      convert every file in data/products/ to a valid cloud-optimized GeoTIFF in place (GDAL COG driver,
           deflate, predictor 2, 512 px blocks, existing overviews reused), after checking the copy has the same
           pixels, grid, nodata, band descriptions and tags. Files that already validate are skipped.
  catalog  write data/products/catalog.csv (one row per 0.6 m map; a copy goes to catalog/catalog.csv in git):
           year, source, acquired, input, season, best, coverage_pct, path, bytes, md5.
  upload   copy data/products/ to the bucket root (naip/, fixed_wing/, derived/, catalog.csv), skipping objects whose
           MD5 already matches, then check every object's MD5. Needs a service-account key: the environment
           variable AERIAL_SURVEY_CREDENTIALS (or a .env file in the repo root defining it) gives its path.
           --dry-run lists what would be uploaded without touching the bucket.

Run: python scripts/09_publish.py cog | catalog | upload [--dry-run]
"""

import argparse
import base64
import hashlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import rasterio.shutil
from rio_cogeo.cogeo import cog_validate

from paths import CATALOG, PRODUCTS, ROOT

BUCKET = "mpg-naip-chm"
PROJECT = "mpg-aerial-survey-375719"
COG_OPTIONS = dict(driver="COG", compress="DEFLATE", predictor=2, blocksize=512, overviews="AUTO",
                   resampling="NEAREST", BIGTIFF="IF_SAFER")
TESTED_NAIP_GSD_M = 0.6                 # the model was trained and tested on 0.6 m NAIP
CHUNK_BYTES = 8 << 20                   # 8 MB upload chunks: 64 MB chunks timed out on a home uplink
WORKERS, ATTEMPTS, REQUEST_TIMEOUT_S = 2, 3, 300


def tifs():
    return sorted(PRODUCTS.glob("*/*.tif"))


# ---- cog ------------------------------------------------------------------------------------------------------------
def same(a_path, b_path):
    with rasterio.open(a_path) as a, rasterio.open(b_path) as b:
        meta = (a.transform == b.transform and a.crs == b.crs and a.count == b.count and a.nodata == b.nodata
                and a.descriptions == b.descriptions and a.tags() == b.tags() and a.dtypes == b.dtypes)
        return meta and all(np.array_equal(a.read(i), b.read(i)) for i in range(1, a.count + 1))


def to_cog(path):
    if cog_validate(str(path), quiet=True)[0]:
        return f"already a COG: {path.name}"
    tmp = path.with_suffix(".cog.tmp")
    rasterio.shutil.copy(path, tmp, **COG_OPTIONS)
    ok = cog_validate(str(tmp), quiet=True)[0] and same(path, tmp)
    if not ok:
        tmp.unlink()
        raise RuntimeError(f"COG copy of {path.name} failed validation or differs from the original")
    size = (path.stat().st_size, tmp.stat().st_size)
    os.replace(tmp, path)
    return f"converted {path.relative_to(PRODUCTS)}: {size[0] / 1e6:.1f} -> {size[1] / 1e6:.1f} MB, pixels and tags unchanged"


def cog():
    with ThreadPoolExecutor(3) as pool:
        for line in pool.map(to_cog, tifs()):
            print(line, flush=True)


# ---- catalog --------------------------------------------------------------------------------------------------------
def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def catalog():
    acq = pd.read_csv(CATALOG / "acquisitions.csv")
    best = {(int(s[:4]), "naip" if src == "NAIP" else "fixed_wing") for s, src in
            zip(acq[acq.best.astype(bool)].start, acq[acq.best.astype(bool)].source)}
    leaf_off = set(acq[(acq.source == "NAIP") & acq.leaf_off.astype(bool)].start.str[:4].astype(int))
    rows = []
    for path in tifs():
        if path.parent.name == "derived":
            continue
        source = path.parent.name
        year = int(path.stem.split("_")[2])
        dates = path.stem.split("_")[-1].split("-")
        with rasterio.open(path) as d:
            coverage = 100 * (d.read(1) != d.nodata).mean()
            gsd = float(d.tags().get("NAIP_GSD_M", d.tags().get("SOURCE_GSD_M")))
        rows.append({"year": year, "source": source,
                     "acquired": "/".join(f"{x[:4]}-{x[4:6]}-{x[6:]}" for x in dates),
                     "input": "tested" if source == "naip" and gsd == TESTED_NAIP_GSD_M else "untested",
                     "season": "off" if source == "naip" and year in leaf_off else "in",
                     "best": "TRUE" if (year, source) in best else "FALSE",
                     "coverage_pct": round(coverage, 1), "path": path.relative_to(PRODUCTS).as_posix(),
                     "bytes": path.stat().st_size, "md5": md5(path)})
    df = pd.DataFrame(rows).sort_values(["year", "source"])
    assert df[df.best == "TRUE"].year.is_unique, "more than one best map in a year"
    for out in (PRODUCTS / "catalog.csv", CATALOG / "catalog.csv"):
        df.to_csv(out, index=False)
    print(df.drop(columns=["md5"]).to_string(index=False))
    print(f"-> {PRODUCTS / 'catalog.csv'} and {CATALOG / 'catalog.csv'}")


# ---- upload ---------------------------------------------------------------------------------------------------------
def credentials():
    key = os.environ.get("AERIAL_SURVEY_CREDENTIALS")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("AERIAL_SURVEY_CREDENTIALS="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key or not Path(key).exists():
        raise SystemExit("set AERIAL_SURVEY_CREDENTIALS to the service-account key path (environment or .env)")
    return key


def upload(dry_run):
    files = tifs() + [PRODUCTS / "catalog.csv"]
    assert (PRODUCTS / "catalog.csv").exists(), "run the catalog stage first"
    local = {p.relative_to(PRODUCTS).as_posix(): (p, md5(p)) for p in files}
    if dry_run:
        for name, (p, _) in local.items():
            print(f"would upload gs://{BUCKET}/{name}  ({p.stat().st_size / 1e6:.1f} MB)")
        print(f"{len(local)} files, {sum(p.stat().st_size for p, _ in local.values()) / 1e9:.2f} GB")
        return
    from google.cloud import storage
    from google.oauth2 import service_account
    client = storage.Client(project=PROJECT,
                            credentials=service_account.Credentials.from_service_account_file(credentials()))
    bucket = client.bucket(BUCKET)
    remote = {b.name: base64.b64decode(b.md5_hash).hex() for b in client.list_blobs(BUCKET)}

    def put(item):
        name, (path, digest) = item
        if remote.get(name) == digest:
            return f"unchanged: {name}"
        ctype = "text/csv" if name.endswith(".csv") else "image/tiff; application=geotiff; profile=cloud-optimized"
        for attempt in range(1, ATTEMPTS + 1):
            try:
                blob = bucket.blob(name, chunk_size=CHUNK_BYTES)      # resumable upload in small chunks
                blob.upload_from_filename(path, content_type=ctype, checksum="md5", timeout=REQUEST_TIMEOUT_S)
                return f"uploaded: {name} ({path.stat().st_size / 1e6:.1f} MB)"
            except Exception as e:                                   # network errors: retry, then report
                error = f"{type(e).__name__}: {str(e)[:120]}"
        return f"FAILED after {ATTEMPTS} attempts: {name} ({error})"

    with ThreadPoolExecutor(WORKERS) as pool:
        for line in pool.map(put, local.items()):
            print(line, flush=True)
    remote = {b.name: base64.b64decode(b.md5_hash).hex() for b in client.list_blobs(BUCKET)}
    bad = [n for n, (_, d) in local.items() if remote.get(n) != d]
    print(f"checked {len(local)} objects against local MD5: {len(bad)} mismatched" + (f": {bad}" if bad else ""))
    if bad:
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("cog", "catalog", "upload"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    {"cog": cog, "catalog": catalog, "upload": lambda: upload(args.dry_run)}[args.stage]()


if __name__ == "__main__":
    main()
