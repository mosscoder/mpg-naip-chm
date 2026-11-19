"""catalog/manifest.csv: every file under data/ (gitignored), so a clone knows what belongs there and can check it.

Columns: path (relative to data/), group (products, inputs, predictions, validation, reference), bytes, md5,
public (products are published to the public bucket; everything else is archived privately).
Run after anything in data/ changes: python scripts/manifest.py [--check]
  --check compares data/ with the committed manifest instead of rewriting it, and exits 1 on any difference.
"""

import argparse
import hashlib
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from paths import CATALOG, DATA

MANIFEST = CATALOG / "manifest.csv"
SKIP = {".DS_Store"}


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan():
    files = sorted(p for p in DATA.rglob("*") if p.is_file() and p.name not in SKIP and not p.name.endswith(".aux.xml"))
    with ThreadPoolExecutor(8) as pool:
        sums = list(pool.map(md5, files))
    rows = [dict(path=p.relative_to(DATA).as_posix(), group=p.relative_to(DATA).parts[0], bytes=p.stat().st_size,
                 md5=s, public=p.relative_to(DATA).parts[0] == "products") for p, s in zip(files, sums)]
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    now = scan()
    if not ap.parse_args().check:
        now.to_csv(MANIFEST, index=False)
        print(now.groupby("group").agg(files=("path", "size"), gb=("bytes", lambda b: round(b.sum() / 1e9, 2))))
        print(f"{MANIFEST.relative_to(CATALOG.parent)}: {len(now)} files, {now.bytes.sum() / 1e9:.1f} GB")
        return
    old = pd.read_csv(MANIFEST)
    both = old.merge(now, on="path", how="outer", suffixes=("_manifest", "_disk"), indicator=True)
    missing = both[both._merge == "left_only"].path
    extra = both[both._merge == "right_only"].path
    changed = both[(both._merge == "both") & (both.md5_manifest != both.md5_disk)].path
    for name, paths in (("missing", missing), ("not in manifest", extra), ("changed", changed)):
        for p in paths:
            print(f"{name}: {p}")
    print(f"{len(old)} in manifest; {len(missing)} missing, {len(extra)} extra, {len(changed)} changed")
    sys.exit(1 if len(missing) + len(extra) + len(changed) else 0)


if __name__ == "__main__":
    main()
