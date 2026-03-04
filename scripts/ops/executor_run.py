#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import time
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

from common import die, set_output


DEFAULT_MODEL_CANDIDATES = (
    "gemini-2.0-flash-lite,gemini-2.0-flash,gemini-2.5-flash-lite,gemini-2.5-flash,gemini-2.5-pro"
)


def gemini_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
    if not key:
        die("GEMINI_API_KEY (or GOOGLE_API_KEY) is required")
    return key


def normalize_model_name(name: str) -> str:
    return name.strip().removeprefix("models/").strip()


def resolve_model_candidates(key: str) -> list[str]:
    configured = [
        normalize_model_name(m)
        for m in os.getenv("EXECUTOR_MODEL_CANDIDATES", DEFAULT_MODEL_CANDIDATES).split(",")
        if normalize_model_name(m)
    ]
    discovered: list[str] = []
    for api_version in ("v1", "v1beta"):
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/{api_version}/models?key={key}",
            headers={"Content-Type": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for model in data.get("models", []):
                methods = model.get("supportedGenerationMethods") or []
                if "generateContent" not in methods:
                    continue
                name = normalize_model_name(model.get("name", ""))
                if name and name not in discovered:
                    discovered.append(name)
            if discovered:
                break
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", errors="ignore")
            print(f"Executor model discovery skipped for {api_version}: {err.code} {body[:240]}")
        except Exception as err:  # pylint: disable=broad-except
            print(f"Executor model discovery skipped for {api_version}: {err}")

    if not discovered:
        return configured

    resolved: list[str] = []
    for candidate in configured:
        if candidate in discovered and candidate not in resolved:
            resolved.append(candidate)
            continue
        prefix = f"{candidate}-"
        match = next((m for m in discovered if m.startswith(prefix)), "")
        if match and match not in resolved:
            resolved.append(match)

    preferred_prefixes = [
        "gemini-2.0-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-1.5-flash",
    ]
    for prefix in preferred_prefixes:
        if prefix in discovered and prefix not in resolved:
            resolved.append(prefix)
            continue
        prefix_match = next((m for m in discovered if m.startswith(f"{prefix}-")), "")
        if prefix_match and prefix_match not in resolved:
            resolved.append(prefix_match)

    for discovered_model in discovered:
        if discovered_model not in resolved:
            resolved.append(discovered_model)

    return resolved or configured


def call_model(models: list[str], system_prompt: str, user_prompt: str):
    key = gemini_api_key()
    last_error = ""
    for model in models:
        for api_version in ("v1", "v1beta"):
            for attempt in range(1, 5):
                payload = {
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                    "generationConfig": {"temperature": 0.1},
                }
                model_ref = urllib.parse.quote(model, safe="")
                req = urllib.request.Request(
                    f"https://generativelanguage.googleapis.com/{api_version}/models/{model_ref}:generateContent?key={key}",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    text = ""
                    for candidate in data.get("candidates", []):
                        content = candidate.get("content", {})
                        parts = content.get("parts", [])
                        snippets = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
                        if snippets:
                            text = "\n".join(snippets).strip()
                            break
                    usage_meta = data.get("usageMetadata", {})
                    usage = {"total_tokens": int(usage_meta.get("totalTokenCount", 0))}
                    return text, usage, model
                except urllib.error.HTTPError as err:
                    body = err.read().decode("utf-8", errors="ignore")
                    last_error = f"{err.code} {body}"
                    if err.code in (429, 500, 502, 503, 504):
                        retry_after = err.headers.get("Retry-After")
                        sleep_s = int(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 20)
                        print(
                            f"Executor transient API error {err.code} on model {model} ({api_version}), "
                            f"retrying in {sleep_s}s (attempt {attempt}/4): {body[:240]}"
                        )
                        time.sleep(sleep_s)
                        continue
                    if err.code == 404:
                        break
                    if err.code in (400, 401, 403):
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
key = gemini_api_key()
model_candidates = resolve_model_candidates(key)
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
