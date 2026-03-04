#!/usr/bin/env python3
import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from common import (
    create_issue_comment,
    die,
    gh_api,
    load_config,
    repo_owner_name,
    set_output,
)


def call_model(models: list[str], system_prompt: str, user_prompt: str):
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        die("OPENAI_API_KEY is required")
    last_error = ""
    for model in models:
        for attempt in range(1, 5):
            payload = {
                "model": model,
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                    {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
                ],
            }
            req = urllib.request.Request(
                "https://api.openai.com/v1/responses",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                text = data.get("output_text", "").strip()
                if not text:
                    die("Planner model returned empty output")
                try:
                    plan = json.loads(text)
                except json.JSONDecodeError:
                    start = text.find("{")
                    end = text.rfind("}")
                    if start == -1 or end == -1:
                        die("Planner output was not JSON")
                    plan = json.loads(text[start : end + 1])
                usage = data.get("usage", {})
                return plan, usage, model
            except urllib.error.HTTPError as err:
                body = err.read().decode("utf-8", errors="ignore")
                last_error = f"{err.code} {body}"
                if err.code in (429, 500, 502, 503, 504):
                    retry_after = err.headers.get("Retry-After")
                    sleep_s = int(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 20)
                    print(
                        f"Planner transient API error {err.code} on model {model}, retrying in {sleep_s}s "
                        f"(attempt {attempt}/4)"
                    )
                    time.sleep(sleep_s)
                    continue
                if err.code in (400, 401, 403, 404):
                    break
                raise
    die(f"Planner request failed for all candidate models: {models}. Last error: {last_error}")


parser = argparse.ArgumentParser()
parser.add_argument("--issue-number", default="")
parser.add_argument("--objective", default="")
parser.add_argument("--planner-version", required=True)
parser.add_argument("--ops-infra-path", default="_ops_infra")
parser.add_argument("--run-id", required=True)
parser.add_argument("--delta-file", default="")
args = parser.parse_args()

cfg = load_config()
context_version = cfg.get("base_context_version", "v1")
context_path = Path(".ops") / "context" / f"{context_version}.md"
if not context_path.exists():
    issue_num = int(args.issue_number) if args.issue_number else None
    if issue_num:
        create_issue_comment(
            issue_num,
            "Automation paused: missing bootstrap context file `.ops/context/"
            f"{context_version}.md`. Applying `ops:needs-human`."
        )
    die(f"Missing mandatory context file: {context_path}", code=10)

owner, repo = repo_owner_name()
issue_title = ""
issue_body = ""
if args.issue_number:
    issue = gh_api("GET", f"/repos/{owner}/{repo}/issues/{int(args.issue_number)}")
    issue_title = issue["title"]
    issue_body = issue.get("body") or ""

state_path = Path("ops-state/ops-state.json")
state = {}
if state_path.exists():
    state = json.loads(state_path.read_text())

system_prompt = (Path(args.ops_infra_path) / "prompts" / "planner" / args.planner_version / "system.md").read_text()
user_template = (Path(args.ops_infra_path) / "prompts" / "planner" / args.planner_version / "user.md").read_text()

user_prompt = "\n\n".join([
    user_template,
    "# Base Context",
    context_path.read_text(),
    "# Task",
    f"Issue: #{args.issue_number} {issue_title}" if args.issue_number else f"Objective: {args.objective}",
    issue_body if issue_body else "",
    "# Repo State",
    json.dumps(state, indent=2),
])
if args.delta_file and Path(args.delta_file).exists():
    user_prompt += "\n\n# Delta Context\n" + Path(args.delta_file).read_text()

model_candidates = [
    m.strip()
    for m in os.getenv("PLANNER_MODEL_CANDIDATES", "gpt-5.3,gpt-5,gpt-4o-mini").split(",")
    if m.strip()
]
plan, usage, used_model = call_model(model_candidates, system_prompt, user_prompt)
out_dir = Path("ops-state")
out_dir.mkdir(parents=True, exist_ok=True)
plan_path = out_dir / "ops-plan.json"
plan_path.write_text(json.dumps(plan, indent=2) + "\n")

state["run_id"] = args.run_id
state["issue_number"] = int(args.issue_number) if args.issue_number else None
state["planner_model"] = used_model
state["planner_tokens_last"] = usage.get("total_tokens", 0)
state["planner_tokens"] = int(state.get("planner_tokens", 0)) + int(usage.get("total_tokens", 0))
state_path.write_text(json.dumps(state, indent=2) + "\n")

set_output("plan_file", str(plan_path))
set_output("planner_tokens", str(usage.get("total_tokens", 0)))
print(f"Wrote plan to {plan_path}")
