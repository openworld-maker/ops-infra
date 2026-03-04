#!/usr/bin/env python3
import argparse
import subprocess
from pathlib import Path

from common import load_config, die


parser = argparse.ArgumentParser()
parser.add_argument("--ops-infra-path", default="_ops_infra")
args = parser.parse_args()

cfg = load_config()
planner_version = cfg.get("planner_prompt_version")
executor_version = cfg.get("executor_prompt_version")
planner_sha = cfg.get("planner_prompt_sha", "")
executor_sha = cfg.get("executor_prompt_sha", "")
for label, version in (("planner", planner_version), ("executor", executor_version)):
    if version in {"main", "master", "latest", "HEAD", ""}:
        die(f"{label}_prompt_version must be pinned tag, got: {version}")

planner_dir = Path(args.ops_infra_path) / "prompts" / "planner" / planner_version
executor_dir = Path(args.ops_infra_path) / "prompts" / "executor" / executor_version
if not planner_dir.exists():
    die(f"Missing pinned planner prompt directory: {planner_dir}")
if not executor_dir.exists():
    die(f"Missing pinned executor prompt directory: {executor_dir}")

head_sha = subprocess.check_output([
    "git", "-C", args.ops_infra_path, "rev-parse", "HEAD"
], text=True).strip()

if planner_sha and planner_sha != head_sha:
    die(f"planner_prompt_sha mismatch: expected {planner_sha}, got {head_sha}")
if executor_sha and executor_sha != head_sha:
    die(f"executor_prompt_sha mismatch: expected {executor_sha}, got {head_sha}")

print("Prompt pinning check passed")
