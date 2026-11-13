"""Spike A helper: re-encode a Wonderdraft map to a NEW path.

Usage:
    python tools/roundtrip.py IN OUT [--label TEXT]

With --label, the first label's text is replaced before writing.
The input file is never modified; OUT must not already exist.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wdmcp.codec import dump_map_bytes, load_map_bytes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("inp", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--label", help="replace the first label's text")
    args = ap.parse_args(argv)

    if args.out.exists():
        print(f"refusing to overwrite existing {args.out}", file=sys.stderr)
        return 2
    if args.out.resolve() == args.inp.resolve():
        print("OUT must differ from IN", file=sys.stderr)
        return 2

    root = load_map_bytes(args.inp)
    if args.label is not None:
        labels = root.get("labels") or []
        if not labels:
            print("map has no labels", file=sys.stderr)
            return 1
        print(f"label 0: {labels[0]['text']!r} -> {args.label!r}")
        labels[0]["text"] = args.label

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(dump_map_bytes(root))
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
