#!/usr/bin/env bash
# setup-branch-rulesets.sh — apply a solo-dev branch ruleset to one or more repos.
#
# Usage:
#   ./setup-branch-rulesets.sh OWNER repo [repo ...]            # dry-run (default)
#   ./setup-branch-rulesets.sh --apply OWNER repo [repo ...]    # execute
#
# The ruleset on the default branch requires: a pull request (0 approvals — solo-friendly),
# the `ci-passed` status check green, the branch up to date (strict), a merge queue, and no
# force-push. The repository-admin role is a bypass actor so a solo maintainer is never
# self-locked-out.
#
# GOTCHA: GitHub branch rulesets DO NOT enforce on PRIVATE repos of a personal (Free) account.
# Gated repos must be PUBLIC or live in an organization. And the `ci-passed` check name must
# have run at least once (open one PR) before a ruleset can require it.
#
# Requires: gh (authenticated — `gh auth status`).
set -euo pipefail

APPLY=0
if [[ "${1:-}" == "--apply" ]]; then APPLY=1; shift; fi
OWNER="${1:?usage: $0 [--apply] OWNER repo [repo ...]}"; shift
REPOS=("$@")
[[ ${#REPOS[@]} -gt 0 ]] || { echo "no repos given"; exit 1; }

payload() {
  cat <<'JSON'
{
  "name": "main-protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "bypass_actors": [
    { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" }
  ],
  "rules": [
    { "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
      } },
    { "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [ { "context": "ci-passed" } ]
      } },
    { "type": "non_fast_forward" },
    { "type": "merge_queue",
      "parameters": {
        "merge_method": "SQUASH",
        "grouping_strategy": "ALLGREEN",
        "max_entries_to_build": 5,
        "min_entries_to_merge": 1,
        "max_entries_to_merge": 5,
        "min_entries_to_merge_wait_minutes": 5,
        "check_response_timeout_minutes": 60
      } }
  ]
}
JSON
}

command -v gh >/dev/null || { echo "gh not found — install the GitHub CLI"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "run 'gh auth login' first"; exit 1; }

for repo in "${REPOS[@]}"; do
  echo "=== ${OWNER}/${repo} ==="
  vis=$(gh repo view "${OWNER}/${repo}" --json visibility --jq .visibility 2>/dev/null || echo UNKNOWN)
  if [[ "$vis" == "PRIVATE" ]]; then
    echo "  WARNING: ${repo} is PRIVATE on a personal account — a ruleset here enforces NOTHING."
  fi
  if [[ $APPLY -eq 1 ]]; then
    payload | gh api -X POST "repos/${OWNER}/${repo}/rulesets" --input - >/dev/null \
      && echo "  ruleset applied (require ci-passed + up-to-date + merge queue)"
  else
    echo "  [dry-run] would POST repos/${OWNER}/${repo}/rulesets (visibility: $vis)"
  fi
done

[[ $APPLY -eq 0 ]] && echo "Dry run. Re-run with --apply to execute."
exit 0
