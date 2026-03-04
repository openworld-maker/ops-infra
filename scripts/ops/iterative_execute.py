#!/usr/bin/env python3
import argparse
import json
import subprocess
import time
from pathlib import Path

from common import load_config, run_cmd


def run_checked(cmd: str):
    proc = subprocess.run(cmd, shell=True)
    return proc.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue-number", required=True)
    parser.add_argument("--planner-version", required=True)
    parser.add_argument("--executor-version", required=True)
    parser.add_argument("--ops-infra-path", default="_ops_infra")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    cfg = load_config()
    max_iterations = int(cfg.get("max_iterations", 3))
    install_cmd = cfg.get("install_cmd", "true")
    lint_cmd = cfg.get("lint_cmd", "true")
    test_cmd = cfg.get("test_cmd", "true")

    state_dir = Path("ops-state")
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "ops-state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    state.setdefault("run_id", args.run_id)
    state.setdefault("issue_number", int(args.issue_number))
    state.setdefault("started_at", int(time.time()))

    passed = False
    for attempt in range(1, max_iterations + 1):
        state["iteration"] = attempt
        state_file.write_text(json.dumps(state, indent=2) + "\n")

        delta_file = state_dir / "delta-context.json"
        if attempt > 1:
            run_cmd(
                " ".join([
                    "python3", f"{args.ops_infra_path}/scripts/ops/planner_request.py",
                    f"--issue-number {args.issue_number}",
                    f"--planner-version {args.planner_version}",
                    f"--ops-infra-path {args.ops_infra_path}",
                    f"--run-id {args.run_id}",
                    f"--delta-file {delta_file}",
                ])
            )

        exec_parts = [
            "python3", f"{args.ops_infra_path}/scripts/ops/executor_run.py",
            "--mode impl",
            "--plan-file ops-state/ops-plan.json",
            f"--executor-version {args.executor_version}",
            f"--ops-infra-path {args.ops_infra_path}",
        ]
        if delta_file.exists():
            exec_parts.append(f"--delta-file {delta_file}")
        exec_cmd = " ".join(exec_parts)
        run_cmd(exec_cmd)

        result = {
            "attempt": attempt,
            "install_cmd": install_cmd,
            "lint_cmd": lint_cmd,
            "test_cmd": test_cmd,
        }
        rc_install = run_checked(install_cmd)
        rc_lint = run_checked(lint_cmd)
        rc_test = run_checked(test_cmd)
        result["install_status"] = "pass" if rc_install == 0 else "fail"
        result["lint_status"] = "pass" if rc_lint == 0 else "fail"
        result["test_status"] = "pass" if rc_test == 0 else "fail"

        (state_dir / "test-summary.json").write_text(json.dumps(result, indent=2) + "\n")

        run_cmd(
            f"python3 {args.ops_infra_path}/scripts/ops/enforce_budgets.py "
            f"--issue-number {args.issue_number} --state-file ops-state/ops-state.json"
        )

        if rc_install == 0 and rc_lint == 0 and rc_test == 0:
            passed = True
            break

        delta = {
            "attempt": attempt,
            "failure_summary": result,
            "changed_files": subprocess.check_output(["git", "status", "--porcelain"], text=True),
        }
        delta_file.write_text(json.dumps(delta, indent=2) + "\n")

    state["status"] = "passed" if passed else "failed"
    state_file.write_text(json.dumps(state, indent=2) + "\n")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
