# research-harness

A reusable reproducibility + CI harness for research code. One source of truth, consumed by
many repos — so the gate is fixed once and propagated by a pinned SHA, not by copy-paste.

## What's here

| Path | Purpose |
|---|---|
| `tools/check_claims.py` | Claims/provenance gate — enforces a `PROTOCOL.md`/`CLAIMS.md` contract: every cited number traces to a hash-matched data file; no missing evidence; no conflicting provenance. |
| `tools/add_sidecar_digests.py` | Attaches per-file SHA-256 to provenance sidecars so results are cryptographically verifiable (a later mutation of a certified file becomes a hard failure). |
| `.github/workflows/ci-reusable.yml` | Reusable CI workflow (claims gate + tests behind one `ci-passed` aggregator). Consumed by other repos. |
| `scripts/setup-branch-rulesets.sh` | Applies a solo-dev-friendly branch ruleset (require `ci-passed` + up-to-date + merge queue; 0 approvals + admin bypass). |

## The idea: data audits constraints; the gate audits claims

The methodology this harness mechanizes:

- **Constraints come from documentation/protocol, never from observed data.** Reading a bound off the
  data ("I saw values up to 4, so bound it [0,4]") lets dirty data define the constraint and defeats the
  point. Source every bound; audit the data *against* it; out-of-spec rows are a finding to disclose,
  not a reason to widen the bound.
- **Claims are verified on a layer independent of the one that produced them** — code (tests), provenance
  (SHA-256 sidecars), paper-vs-record (drift check), independent reproduction (determinism), and reality
  (real-data validity + a human reading the spec). A claim is confirmed only when an independent layer agrees.
- **Green CI is a proxy, not correctness.** The gate hard-fails on enforceable facts (missing evidence, a
  SHA mismatch, conflicting provenance); it cannot certify that the science is right. Three moments still
  need a human: a new headline number's first appearance, any change to a constraint spec or the gate
  itself, and the camera-ready tag.

## Use it from another repo

`.github/workflows/pr-ci.yml` in the consuming repo:

```yaml
name: pr-ci
on: { pull_request: {}, merge_group: {} }
jobs:
  ci:
    uses: OWNER/research-harness/.github/workflows/ci-reusable.yml@<40-char-commit-sha>
```

**Pin to a commit SHA, never `@main`** — a branch ref silently changes the gate everywhere, and a gate
that runs from `research-harness@<sha>` is immune to the PR that edits the calling repo.

## The claims gate

```bash
python tools/check_claims.py                # per-PR: hard-fail on missing/ mismatched/ conflicting evidence
python tools/check_claims.py --strict       # nightly: drift warnings also block
python tools/check_claims.py --camera-ready # release: no open gaps tolerated
```

## Branch rulesets

```bash
./scripts/setup-branch-rulesets.sh OWNER repo1 repo2          # dry-run
./scripts/setup-branch-rulesets.sh --apply OWNER repo1 repo2  # execute
```

> **Gotcha:** GitHub branch rulesets **do not enforce on private repos of a personal (Free) account** —
> they show green and gate nothing. Gated repos must be **public** or live in an **organization**.
> Verify enforcement with a `doctor` check that pushes a known-bad commit and asserts the PR is blocked.
