#!/usr/bin/env python3
"""
check_claims.py — mechanical enforcement of PROTOCOL.md / CLAIMS.md.

PROTOCOL.md states the reproducibility rules as gates ("a CSV without a valid
sidecar is not citable"). This script turns them into a CI check. It is repo-
generic: it discovers the paper as catt*.tex (falls back to any *.tex) and reads
CLAIMS.md from the repo root, so the same file drops into netadv-ccs, netadv, ...

Schema-aware about provenance sidecars — validates both:
  * RunMeta.write_sidecar(): {"output_file": "x.csv", "output_sha256": "..."}
  * curated runs:            {"output_files": [...], "output_sha256_by_file": {name: hex}}

  HARD FAIL (exit 1)
    R1. A CLAIMS.md ✅/✅+ claim cites a *.csv that resolves NOWHERE in the repo.
        (A gap marker may excuse a missing *number*; it never excuses a verified
        claim whose evidence file has vanished.)
    R2. A recorded SHA-256 (singular or per-file map) != the CSV's real digest.
    R5. A "NN.N pp" number in the paper appears nowhere in CLAIMS.md (drift).
        Matching is on the bare decimal, so "(std 22.6)" in CLAIMS covers a
        "22.6 pp" in the paper. Every pp-number the paper states must trace to
        the claims record — this is the rule that stops a stale headline from
        shipping while the certified record disagrees.
    R7. The same output CSV is certified by two sidecars with different run
        configs — one physical file cannot be two different runs.

  WARN (exit 0; promoted to FAIL under --strict / --camera-ready)
    R3. A sidecar lists a missing output, or records no SHA-256 at all.
    R4. A sidecar has git_dirty=true (PROTOCOL.md §1: must be flagged ⚠️ in CLAIMS).

  CAMERA-READY (--camera-ready promotes WARNs to HARD FAIL)
    R6. CLAIMS.md still contains ⚠️ / "Unarchived" / "transitional" markers.

Usage:
    python tools/check_claims.py                 # per-PR
    python tools/check_claims.py --strict         # nightly: warnings also block
    python tools/check_claims.py --camera-ready   # release: no open gaps tolerated
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

VERIFIED = ("✅", "✅+")
GAP_MARKERS = ("⚠️", "Unarchived", "transitional")

CSV_PATH_RE = re.compile(r"`([^`]+?\.csv)`")          # any backticked CSV (path or bare name)
HEADING_RE = re.compile(r"^#{2,3}\s")                 # ## Claim / ### subsection
PP_NUMBER_RE = re.compile(r"[-+]?\d+\.\d+\s*pp")      # "+70.5 pp", "36.6 pp"
DECIMAL_RE = re.compile(r"\d+\.\d+")                  # bare decimal for R5 matching


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_csv(root: Path, rel: str) -> Path | None:
    """Resolve a CITED csv to a real file. Tries the literal repo-relative path,
    then a repo-wide search by basename so a bare `results_ccs.csv` citation finds
    benchmarks/*/results/results_ccs.csv. Returns None if it exists nowhere."""
    p = root / rel
    if p.exists():
        return p
    return next(iter(root.rglob(Path(rel).name)), None)


def _split_blocks(text: str) -> list[str]:
    """Split CLAIMS.md into heading-delimited blocks so a CSV path can be tied to
    the ✅/⚠️ status of the claim it belongs to."""
    blocks, current = [], []
    for line in text.splitlines():
        if HEADING_RE.match(line) and current:
            blocks.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _provenance_sig(meta: dict) -> tuple:
    """A coarse fingerprint of the run a sidecar describes. Two sidecars that
    certify the same output but disagree here are describing different runs."""
    seeds = meta.get("seeds")
    return (meta.get("row_count"), meta.get("n_seeds"), tuple(seeds) if isinstance(seeds, list) else seeds)


def check(root: Path, strict: bool, camera_ready: bool) -> int:
    claims_path = root / "CLAIMS.md"
    tex_candidates = sorted(root.glob("catt*.tex")) or sorted(root.glob("*.tex"))
    tex_path = tex_candidates[0] if tex_candidates else None

    hard: list[str] = []
    warn: list[str] = []

    if not claims_path.exists():
        print(f"FAIL: CLAIMS.md not found at {claims_path}")
        return 1
    claims = claims_path.read_text(encoding="utf-8")

    # ---- R1: every CSV cited by a VERIFIED claim must resolve somewhere --------
    for block in _split_blocks(claims):
        is_gap = any(m in block for m in GAP_MARKERS)
        is_verified = any(m in block for m in VERIFIED)
        for rel in CSV_PATH_RE.findall(block):
            if _resolve_csv(root, rel) is not None:
                continue
            if is_verified:
                # vanished evidence behind a ✅ claim — a gap marker does NOT excuse this
                hard.append(f"[R1] verified (✅) claim cites a CSV that exists nowhere in the repo: {rel}")
            else:
                # 🔁 re-runnable / ⚠️ gap / unmarked — the CSV may legitimately not be
                # committed yet; flag it, but only a ✅ claim hard-fails on missing evidence.
                warn.append(f"[R1] cited CSV absent (claim not ✅-verified): {rel}")

    # ---- R2-R4: validate every sidecar that DOES exist ------------------------
    sidecars = sorted(root.glob("benchmarks/**/*_meta.json"))
    owners: dict[str, list[tuple[str, tuple]]] = {}   # output name -> [(sidecar, prov-sig)]
    for side in sidecars:
        rel = side.relative_to(root)
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            hard.append(f"[R2] malformed sidecar {rel}: {exc}")
            continue

        outputs = meta.get("output_files")
        if isinstance(outputs, str):
            outputs = [outputs]
        elif outputs is None:
            single = meta.get("output_file")
            outputs = [single] if single else []
        if not outputs:
            warn.append(f"[R3] sidecar {rel} lists no output file(s) — cannot tie it to a result")
            continue

        sig = _provenance_sig(meta)
        for name in outputs:
            # Key ownership on the repo-relative PATH, not the bare basename:
            # benchmarks/a/results.csv and benchmarks/b/results.csv are different
            # physical files and may legitimately be certified by different runs.
            owner_key = str((side.parent / name).relative_to(root))
            owners.setdefault(owner_key, []).append((str(rel), sig))
            if not (side.parent / name).exists():
                warn.append(f"[R3] sidecar {rel} references a missing output: {name}")

        # SHA-256 integrity — mismatch is a hard fail; total absence is a warn.
        digest_map = meta.get("output_sha256_by_file")
        single_digest = meta.get("output_sha256")
        single_file = meta.get("output_file")
        verified_any = False
        if isinstance(digest_map, dict) and digest_map:
            for name, hexd in digest_map.items():
                csv = side.parent / name
                if csv.exists() and hexd != _sha256(csv):
                    hard.append(f"[R2] SHA-256 mismatch — {csv.relative_to(root)} changed "
                                f"since its sidecar certified it")
                verified_any = True
        if single_digest and single_file:
            csv = side.parent / single_file
            if csv.exists() and single_digest != _sha256(csv):
                hard.append(f"[R2] SHA-256 mismatch — {csv.relative_to(root)} changed "
                            f"since its sidecar certified it")
            verified_any = True
        if not verified_any:
            warn.append(f"[R3] sidecar {rel} records no SHA-256 digest — integrity not "
                        f"cryptographically verifiable (run tools/add_sidecar_digests.py)")

        if meta.get("git_dirty"):
            warn.append(f"[R4] {rel}: git_dirty=true must be flagged ⚠️ in CLAIMS.md")

    # ---- R7: one output CSV certified by sidecars describing different runs ----
    reported: set[tuple] = set()
    for name, lst in owners.items():
        if len({sig for _, sig in lst}) > 1:
            srcs = tuple(sorted({s for s, _ in lst}))
            if srcs not in reported:
                reported.add(srcs)
                hard.append(f"[R7] conflicting provenance — sidecars {' & '.join(srcs)} certify "
                            f"the same output(s) (e.g. {name}) with different run configs; "
                            f"one CSV cannot be two runs")

    if not sidecars:
        warn.append("[info] no *_meta.json sidecars under benchmarks/ — sidecar checks idle")

    # ---- R5: paper numbers that have drifted away from CLAIMS.md ---------------
    # Compare on the bare decimal (sign/'pp' stripped): CLAIMS.md legitimately
    # writes variance as "(std 22.6)" and ranges as "12.7--69.5" without the pp
    # suffix, and those must not read as drift. A pp-number whose digits appear
    # nowhere in CLAIMS.md is a HARD failure — the 2026-06 UNSW headline (79.1 pp
    # in the tex, absent from the certified CLAIMS.md) shipped through the old
    # warn-only version of this rule.
    if tex_path and tex_path.exists():
        claims_decimals = set(DECIMAL_RE.findall(claims))
        seen: set[str] = set()
        for raw in PP_NUMBER_RE.findall(tex_path.read_text(encoding="utf-8")):
            bare = DECIMAL_RE.search(raw).group(0)
            if bare not in claims_decimals and bare not in seen:
                seen.add(bare)
                hard.append(f"[R5] {tex_path.name} cites '{raw.strip()}' but the number "
                            f"appears nowhere in CLAIMS.md (drift)")

    # ---- R6: camera-ready tolerates no open gaps ------------------------------
    if camera_ready:
        # The "Status key" legend defines the markers themselves; it is documentation,
        # not an open gap, so it must not self-trip R6. Scan only the real content.
        scan = "\n".join(line for line in claims.splitlines()
                         if not line.lstrip().startswith("**Status key:**"))
        for marker in GAP_MARKERS:
            if marker in scan:
                hard.append(f"[R6] camera-ready blocked: CLAIMS.md still contains '{marker}' entries")
                break

    # ---- Report ---------------------------------------------------------------
    promote = strict or camera_ready
    for w in warn:
        print(f"WARN: {w}")
    for h in hard:
        print(f"FAIL: {h}")
    if promote and warn:
        print(f"FAIL: --{'camera-ready' if camera_ready else 'strict'} promotes "
              f"{len(warn)} warning(s) to failures")

    failed = bool(hard) or (promote and bool(warn))
    if failed:
        print(f"\nclaims gate: FAILED ({len(hard)} hard, {len(warn)} warn)")
        return 1
    print(f"\nclaims gate: PASS ({len(warn)} warning(s), {len(sidecars)} sidecar(s) checked)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Enforce PROTOCOL.md / CLAIMS.md reproducibility gates.")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                    help="repo root (default: parent of tools/)")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
    ap.add_argument("--camera-ready", action="store_true",
                    help="block if any ⚠️/Unarchived/transitional claim remains")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # emoji markers print safely on CI runners
    except Exception:
        pass
    return check(args.root.resolve(), args.strict, args.camera_ready)


if __name__ == "__main__":
    raise SystemExit(main())
