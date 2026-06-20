#!/usr/bin/env python3
"""
add_sidecar_digests.py — make curated provenance sidecars cryptographically real.

The hand-authored sidecars use `output_files: [...]` with NO per-file digest, so
results are not verifiable (integrity rests on an MD5 in a free-text note). This
walks every benchmarks/**/*_meta.json, computes SHA-256 for each listed output
that exists, and writes an `output_sha256_by_file` map back into the sidecar.
check_claims.py then verifies those digests on every run — any later mutation of a
certified CSV becomes a hard failure.

This certifies the CSVs *as they are now*. Run it once results are final for a run;
re-running re-certifies (and would mask a change), so commit the sidecar and let the
gate guard it thereafter.

Usage:
    python tools/add_sidecar_digests.py --dry-run    # show what would change
    python tools/add_sidecar_digests.py              # write digests into sidecars
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = args.root.resolve()

    processed = 0
    for side in sorted(root.glob("benchmarks/**/*_meta.json")):
        rel = side.relative_to(root)
        meta = json.loads(side.read_text(encoding="utf-8"))

        outputs = meta.get("output_files")
        if isinstance(outputs, str):
            outputs = [outputs]
        elif not outputs and meta.get("output_file"):
            outputs = [meta["output_file"]]
        if not outputs:
            print(f"skip (no outputs listed): {rel}")
            continue

        digests: dict[str, str] = {}
        missing: list[str] = []
        for name in outputs:
            p = side.parent / name
            if p.exists():
                digests[name] = _sha256(p)
            else:
                missing.append(name)

        meta["output_sha256_by_file"] = digests
        if missing:
            meta["output_sha256_missing"] = missing

        action = "[dry] would update" if args.dry_run else "updated"
        print(f"{action} {rel}: {len(digests)} digest(s)"
              + (f", {len(missing)} missing output(s): {missing}" if missing else ""))
        if not args.dry_run:
            side.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        processed += 1

    print(f"\n{processed} sidecar(s) processed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
