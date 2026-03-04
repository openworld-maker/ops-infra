#!/usr/bin/env python3
from datetime import datetime, timedelta, timezone

from common import create_pr_comment, ensure_labels, gh_api, repo_owner_name

owner, repo = repo_owner_name()
prs = gh_api("GET", f"/repos/{owner}/{repo}/pulls", query={"state": "open", "per_page": 50})
cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

for pr in prs:
    labels = {l["name"] for l in pr.get("labels", [])}
    if "ops:auto" not in labels:
        continue
    updated = datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00"))
    if updated < cutoff:
        create_pr_comment(pr["number"], "Watchdog: this automated PR has been stale for >24h. Please review or mark `/needs-human`.")

issues = gh_api("GET", f"/repos/{owner}/{repo}/issues", query={"state": "open", "labels": "ops:blocked", "per_page": 100})
for issue in issues:
    ensure_labels(issue["number"], ["ops:needs-human"])
print("watchdog complete")
