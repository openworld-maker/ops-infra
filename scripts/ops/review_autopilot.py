#!/usr/bin/env python3
import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from common import create_pr_comment, ensure_labels, gh_api, load_config, repo_owner_name, run_cmd


parser = argparse.ArgumentParser()
parser.add_argument("--pr-number", type=int, default=0)
parser.add_argument("--ops-infra-path", default="_ops_infra")
args = parser.parse_args()

owner, repo = repo_owner_name()
cfg = load_config()
executor_version = cfg.get("executor_prompt_version", "v1.0.0")

if args.pr_number:
    prs = [gh_api("GET", f"/repos/{owner}/{repo}/pulls/{args.pr_number}")]
else:
    prs = gh_api("GET", f"/repos/{owner}/{repo}/pulls", query={"state": "open", "per_page": 50})

for pr in prs:
    number = pr["number"]
    labels = {l["name"] for l in pr.get("labels", [])}
    if args.pr_number == 0 and "ops:auto" not in labels:
        continue

    comments = gh_api("GET", f"/repos/{owner}/{repo}/pulls/{number}/comments", query={"per_page": 50})
    actionable = []
    needs_human = False
    for c in comments:
        body = (c.get("body") or "").lower()
        user = c.get("user", {}).get("login", "")
        if user.endswith("[bot]"):
            continue
        if "[ops-bot-handled]" in body:
            continue
        if "codeowner" in body or "/needs-human" in body:
            needs_human = True
        actionable.append(c)

    if not actionable:
        continue

    if needs_human:
        issue_number = pr["number"]
        ensure_labels(issue_number, ["ops:needs-human"])
        create_pr_comment(number, "Escalating to human due to CODEOWNERS/protected feedback.")
        continue

    head_ref = pr["head"]["ref"]
    run_cmd(f"git fetch origin {head_ref}")
    run_cmd(f"git checkout {head_ref}")

    delta_items = []
    for c in actionable:
        delta_items.append({"path": c.get("path"), "body": c.get("body"), "id": c.get("id")})
    Path("ops-state").mkdir(exist_ok=True)
    delta_file = Path("ops-state/review-delta.json")
    delta_file.write_text(json.dumps({"comments": delta_items}, indent=2) + "\n")

    subprocess.run([
        "python3", f"{args.ops_infra_path}/scripts/ops/executor_run.py",
        "--mode", "impl",
        "--plan-file", "ops-state/ops-plan.json",
        "--delta-file", str(delta_file),
        "--executor-version", executor_version,
        "--ops-infra-path", args.ops_infra_path,
    ], check=True)

    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        run_cmd("git add -A")
        run_cmd(f"git commit -m 'ops: address review feedback on PR #{number}'")
        run_cmd(f"git push origin {head_ref}")
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        create_pr_comment(number, f"[ops-bot-handled] Applied review feedback in commit `{sha}` and pushed updates.")
    else:
        create_pr_comment(number, "[ops-bot-handled] Reviewed comments; no code changes were required.")
