#!/usr/bin/env python3
import argparse

from common import PRIORITY_ORDER, gh_api, repo_owner_name, set_output, ensure_labels, die


parser = argparse.ArgumentParser()
parser.add_argument("--issue-number", default="")
args = parser.parse_args()

owner, repo = repo_owner_name()
selected = None

if args.issue_number:
    num = int(args.issue_number)
    selected = gh_api("GET", f"/repos/{owner}/{repo}/issues/{num}")
else:
    issues = gh_api(
        "GET",
        f"/repos/{owner}/{repo}/issues",
        query={"state": "open", "labels": "ops:ready", "per_page": 100},
    )
    only_issues = [i for i in issues if "pull_request" not in i]
    if only_issues:
        def score(issue):
            labels = [l["name"] for l in issue.get("labels", [])]
            p = min((PRIORITY_ORDER.get(l, 9) for l in labels), default=9)
            return (p, issue["created_at"])
        selected = sorted(only_issues, key=score)[0]

if not selected:
    set_output("found", "false")
    print("No eligible issue found")
    raise SystemExit(0)

issue_number = selected["number"]
ensure_labels(issue_number, ["ops:in-progress"])
set_output("found", "true")
set_output("issue_number", str(issue_number))
set_output("issue_title", selected["title"])
set_output("issue_body", (selected.get("body") or "").replace("\n", " "))
print(f"Selected issue #{issue_number}: {selected['title']}")
