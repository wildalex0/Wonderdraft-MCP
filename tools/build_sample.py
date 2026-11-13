"""Build examples/sample_region.json into a map: build_sample.py OUT [--spec SPEC]."""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wdmcp import region  # noqa: E402
from wdmcp.assets.catalog import Catalog  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--spec", default=str(ROOT / "examples" / "sample_region.json"))
    a = ap.parse_args()
    spec = region.RegionSpec.model_validate_json(Path(a.spec).read_text(encoding="utf-8"))
    cat = Catalog.load()
    t = time.perf_counter()
    res = region.build(spec, cat)
    dt = time.perf_counter() - t
    res.doc.save(Path(a.out))
    print(f"{a.out} built in {dt:.2f}s counts={res.counts}")
    for w in res.warnings:
        print("warning:", w)


if __name__ == "__main__":
    main()
