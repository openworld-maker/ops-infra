#!/usr/bin/env python3
import argparse
import json
import time
from pathlib import Path

from common import create_issue_comment, ensure_labels, load_config


parser = argparse.ArgumentParser()
parser.add_argument("--state-file", default="ops-state/ops-state.json")
parser.add_argument("--issue-number", required=True)
args = parser.parse_args()

cfg = load_config()
max_tokens = int(cfg.get("max_total_tokens", 120000))
max_minutes = int(cfg.get("max_runtime_minutes", 60))

state_path = Path(args.state_file)
state = json.loads(state_path.read_text()) if state_path.exists() else {}
start_ts = state.get("started_at")
if not start_ts:
    start_ts = int(time.time())
    state["started_at"] = start_ts

used_tokens = int(state.get("planner_tokens", 0)) + int(state.get("executor_tokens", 0))
elapsed_minutes = (int(time.time()) - int(start_ts)) / 60.0
state["used_tokens"] = used_tokens
state["elapsed_minutes"] = elapsed_minutes
state_path.parent.mkdir(parents=True, exist_ok=True)
state_path.write_text(json.dumps(state, indent=2) + "\n")

if used_tokens > max_tokens or elapsed_minutes > max_minutes:
    issue_number = int(args.issue_number)
    ensure_labels(issue_number, ["ops:budget-exceeded", "ops:needs-human"])
    create_issue_comment(
        issue_number,
        "Automation paused due to budget guard. "
        f"Tokens={used_tokens}/{max_tokens}, runtime_minutes={elapsed_minutes:.1f}/{max_minutes}."
    )
    raise SystemExit(20)

print("Budget checks passed")
