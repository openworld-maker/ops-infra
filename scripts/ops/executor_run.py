#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from common import die, set_output


DEFAULT_MODEL_CANDIDATES = "gpt-5,gpt-5-mini,gpt-4o-mini"


def openai_api_key() -> str:
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        die("OPENAI_API_KEY is required")
    return key


def extract_response_text(data: dict) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    snippets: list[str] = []
    for item in data.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") in ("output_text", "text") and content.get("text"):
                snippets.append(content.get("text", ""))
    return "\n".join(snippets).strip()


def call_model(models: list[str], system_prompt: str, user_prompt: str):
    key = openai_api_key()
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
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                text = extract_response_text(data)
                usage_obj = data.get("usage") or {}
                usage = {"total_tokens": int(usage_obj.get("total_tokens", 0) or 0)}
                return text, usage, model
            except urllib.error.HTTPError as err:
                body = err.read().decode("utf-8", errors="ignore")
                last_error = f"{err.code} {body}"
                if err.code in (429, 500, 502, 503, 504):
                    retry_after = err.headers.get("Retry-After")
                    sleep_s = int(retry_after) if retry_after and retry_after.isdigit() else min(2**attempt, 20)
                    print(
                        f"Executor transient API error {err.code} on model {model}, "
                        f"retrying in {sleep_s}s (attempt {attempt}/4): {body[:240]}"
                    )
                    time.sleep(sleep_s)
                    continue
                if err.code in (400, 401, 403, 404, 422):
                    break
                raise
    die(f"Executor request failed for all candidate models: {models}. Last error: {last_error}")


def truncate_text(text: str, max_chars: int, label: str) -> str:
    if len(text) <= max_chars:
        return text
    print(f"Executor prompt {label} truncated from {len(text)} to {max_chars} chars")
    return text[:max_chars]


parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=["plan", "impl"], required=True)
parser.add_argument("--plan-file", default="ops-state/ops-plan.json")
parser.add_argument("--delta-file", default="")
parser.add_argument("--executor-version", required=True)
parser.add_argument("--ops-infra-path", default="_ops_infra")
args = parser.parse_args()
model_candidates = [m.strip() for m in os.getenv("EXECUTOR_MODEL_CANDIDATES", DEFAULT_MODEL_CANDIDATES).split(",") if m.strip()]
max_prompt_chars = int(os.getenv("OPS_MAX_PROMPT_CHARS", "30000"))
print(f"Executor model candidates: {', '.join(model_candidates)}")

plan = json.loads(Path(args.plan_file).read_text())
delta = ""
if args.delta_file and Path(args.delta_file).exists():
    delta = truncate_text(Path(args.delta_file).read_text(), max_prompt_chars, "delta")
state_path = Path("ops-state/ops-state.json")
state = json.loads(state_path.read_text()) if state_path.exists() else {}

if args.mode == "plan":
    system_prompt = "Return JSON only."
    user_prompt = (Path(args.ops_infra_path) / "prompts" / "executor" / args.executor_version / "plan-mode.md").read_text()
    user_prompt += "\n\n# Task Plan\n" + truncate_text(json.dumps(plan, indent=2), max_prompt_chars, "task_plan")
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
user_prompt += "\n\n# Task Plan\n" + truncate_text(json.dumps(plan, indent=2), max_prompt_chars, "task_plan")
if Path("ops-state/executor-plan.json").exists():
    user_prompt += "\n\n# Execution Plan\n" + truncate_text(
        Path("ops-state/executor-plan.json").read_text(), max_prompt_chars, "execution_plan"
    )
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
