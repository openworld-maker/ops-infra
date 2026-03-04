#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from common import die, set_output


def call_model(models: list[str], system_prompt: str, user_prompt: str):
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        die("OPENAI_API_KEY is required")
    last_error = ""
    for model in models:
        payload = {
            "model": model,
            "input": [
                {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "text", "text": user_prompt}]},
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
            return data.get("output_text", "").strip(), data.get("usage", {}), model
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", errors="ignore")
            last_error = f"{err.code} {body}"
            if err.code in (400, 401, 403, 404):
                continue
            raise
    die(f"Executor request failed for all candidate models: {models}. Last error: {last_error}")


parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=["plan", "impl"], required=True)
parser.add_argument("--plan-file", default="ops-state/ops-plan.json")
parser.add_argument("--delta-file", default="")
parser.add_argument("--executor-version", required=True)
parser.add_argument("--ops-infra-path", default="_ops_infra")
args = parser.parse_args()
model_candidates = [
    m.strip()
    for m in os.getenv("EXECUTOR_MODEL_CANDIDATES", "gpt-5.3,gpt-5,gpt-4o-mini").split(",")
    if m.strip()
]

plan = json.loads(Path(args.plan_file).read_text())
delta = ""
if args.delta_file and Path(args.delta_file).exists():
    delta = Path(args.delta_file).read_text()
state_path = Path("ops-state/ops-state.json")
state = json.loads(state_path.read_text()) if state_path.exists() else {}

if args.mode == "plan":
    system_prompt = "Return JSON only."
    user_prompt = (Path(args.ops_infra_path) / "prompts" / "executor" / args.executor_version / "plan-mode.md").read_text()
    user_prompt += "\n\n# Task Plan\n" + json.dumps(plan, indent=2)
    if delta:
        user_prompt += "\n\n# Delta Context\n" + delta
    text, usage, used_model = call_model(model_candidates, system_prompt, user_prompt)
    try:
        output = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            die("Executor plan mode did not return JSON")
        output = json.loads(text[start : end + 1])
    out_path = Path("ops-state/executor-plan.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2) + "\n")
    set_output("executor_plan_file", str(out_path))
    set_output("executor_tokens", str(usage.get("total_tokens", 0)))
    state["executor_tokens_last"] = usage.get("total_tokens", 0)
    state["executor_tokens"] = int(state.get("executor_tokens", 0)) + int(usage.get("total_tokens", 0))
    state["executor_model"] = used_model
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    print(f"Wrote {out_path}")
    raise SystemExit(0)

system_prompt = "Return patch text only."
user_prompt = (Path(args.ops_infra_path) / "prompts" / "executor" / args.executor_version / "impl-mode.md").read_text()
user_prompt += "\n\n# Task Plan\n" + json.dumps(plan, indent=2)
if Path("ops-state/executor-plan.json").exists():
    user_prompt += "\n\n# Execution Plan\n" + Path("ops-state/executor-plan.json").read_text()
if delta:
    user_prompt += "\n\n# Delta Context\n" + delta

text, usage, used_model = call_model(model_candidates, system_prompt, user_prompt)
patch_path = Path("ops-state/generated.patch")
patch_path.parent.mkdir(parents=True, exist_ok=True)
patch_path.write_text(text + "\n")

if text:
    subprocess.run(["git", "apply", "--whitespace=nowarn", str(patch_path)], check=True)

set_output("patch_file", str(patch_path))
set_output("executor_tokens", str(usage.get("total_tokens", 0)))
state["executor_tokens_last"] = usage.get("total_tokens", 0)
state["executor_tokens"] = int(state.get("executor_tokens", 0)) + int(usage.get("total_tokens", 0))
state["executor_model"] = used_model
state_path.parent.mkdir(parents=True, exist_ok=True)
state_path.write_text(json.dumps(state, indent=2) + "\n")
print(f"Applied patch from {patch_path}")
