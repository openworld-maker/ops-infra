#!/usr/bin/env python3
import argparse
import json
import os
import time
import urllib.parse
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
        for m in os.getenv("PLANNER_MODEL_CANDIDATES", DEFAULT_MODEL_CANDIDATES).split(",")
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
            print(f"Planner model discovery skipped for {api_version}: {err.code} {body[:240]}")
        except Exception as err:  # pylint: disable=broad-except
            print(f"Planner model discovery skipped for {api_version}: {err}")

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
                    usage_meta = data.get("usageMetadata", {})
                    usage = {"total_tokens": int(usage_meta.get("totalTokenCount", 0))}
                    return plan, usage, model
                except urllib.error.HTTPError as err:
                    body = err.read().decode("utf-8", errors="ignore")
                    last_error = f"{err.code} {body}"
                    if err.code in (429, 500, 502, 503, 504):
                        retry_after = err.headers.get("Retry-After")
                        sleep_s = int(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 20)
                        print(
                            f"Planner transient API error {err.code} on model {model} ({api_version}), "
                            f"retrying in {sleep_s}s (attempt {attempt}/4): {body[:240]}"
                        )
                        time.sleep(sleep_s)
                        continue
                    if err.code == 404:
                        break
                    if err.code in (400, 401, 403):
                        break
                    raise
    die(f"Planner request failed for all candidate models: {models}. Last error: {last_error}")


def truncate_text(text: str, max_chars: int, label: str) -> str:
    if len(text) <= max_chars:
        return text
    print(f"Planner prompt {label} truncated from {len(text)} to {max_chars} chars")
    return text[:max_chars]


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
max_prompt_chars = int(os.getenv("OPS_MAX_PROMPT_CHARS", "30000"))
base_context_text = truncate_text(context_path.read_text(), max_prompt_chars, "base_context")
issue_body = truncate_text(issue_body, max_prompt_chars, "issue_body")
repo_state_text = truncate_text(json.dumps(state, indent=2), max_prompt_chars, "repo_state")

user_prompt = "\n\n".join([
    user_template,
    "# Base Context",
    base_context_text,
    "# Task",
    f"Issue: #{args.issue_number} {issue_title}" if args.issue_number else f"Objective: {args.objective}",
    issue_body if issue_body else "",
    "# Repo State",
    repo_state_text,
])
if args.delta_file and Path(args.delta_file).exists():
    user_prompt += "\n\n# Delta Context\n" + truncate_text(Path(args.delta_file).read_text(), max_prompt_chars, "delta")

model_candidates = resolve_model_candidates(gemini_api_key())
print(f"Planner model candidates: {', '.join(model_candidates)}")
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
