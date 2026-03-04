#!/usr/bin/env python3
import argparse
import json
import subprocess
from pathlib import Path

from common import create_issue_comment, ensure_labels, set_output


parser = argparse.ArgumentParser()
parser.add_argument("--issue-number", required=True)
parser.add_argument("--branch", required=True)
parser.add_argument("--base", default="main")
parser.add_argument("--run-id", required=True)
args = parser.parse_args()

issue_number = int(args.issue_number)
plan = json.loads(Path("ops-state/ops-plan.json").read_text()) if Path("ops-state/ops-plan.json").exists() else {}
state = json.loads(Path("ops-state/ops-state.json").read_text()) if Path("ops-state/ops-state.json").exists() else {}
summary = json.loads(Path("ops-state/test-summary.json").read_text()) if Path("ops-state/test-summary.json").exists() else {}

body = f"""## Problem and Context
- Issue: #{issue_number}
- Objective: {plan.get('objective', 'n/a')}

## Acceptance Criteria Mapping
{chr(10).join(f"- {x}" for x in plan.get('definition_of_done', [])) or '- n/a'}

## Implementation Summary
{chr(10).join(f"- {x}" for x in plan.get('implementation_steps', [])) or '- n/a'}

## Changed Files and Rationale
- See commit diff on branch `{args.branch}`.

## Test Matrix and Results
- Install: `{summary.get('install_cmd', 'n/a')}` -> `{summary.get('install_status', 'n/a')}`
- Lint: `{summary.get('lint_cmd', 'n/a')}` -> `{summary.get('lint_status', 'n/a')}`
- Tests: `{summary.get('test_cmd', 'n/a')}` -> `{summary.get('test_status', 'n/a')}`

## Risk and Rollback
- Risk flags: {', '.join(plan.get('risk_flags', [])) or 'n/a'}
- Rollback: revert PR merge commit.

## Iteration and Reviewer Fix Log
- Iteration count: {state.get('iteration', 1)}

## Ledger and Budget Snapshot
- Ledger artifact: `ledger-{args.run_id}.jsonl`
- Used tokens: {state.get('used_tokens', 0)}
- Elapsed minutes: {state.get('elapsed_minutes', 0)}
"""

cmd = [
    "gh", "pr", "create",
    "--base", args.base,
    "--head", args.branch,
    "--title", f"ops: issue #{issue_number} implementation",
    "--body", body,
]
result = subprocess.check_output(cmd, text=True).strip()
pr_number = result.rstrip("/").split("/")[-1]

for label in ["ops:auto", "ops:review", "needs-approval"]:
    subprocess.run(["gh", "pr", "edit", pr_number, "--add-label", label], check=True)

create_issue_comment(issue_number, f"PR created: {result}")
ensure_labels(issue_number, ["ops:review"])

set_output("pr_url", result)
set_output("pr_number", pr_number)
print(result)
