#!/usr/bin/env python3
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml


PRIORITY_ORDER = {"priority:P0": 0, "priority:P1": 1, "priority:P2": 2}


def die(msg: str, code: int = 1):
    print(msg)
    raise SystemExit(code)


def repo_slug() -> str:
    slug = os.getenv("GITHUB_REPOSITORY", "")
    if not slug:
        die("GITHUB_REPOSITORY is required")
    return slug


def repo_owner_name() -> tuple[str, str]:
    owner, name = repo_slug().split("/", 1)
    return owner, name


def gh_token() -> str:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        die("GITHUB_TOKEN is required")
    return token


def gh_api(method: str, path: str, payload=None, query=None):
    base = "https://api.github.com"
    url = f"{base}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None
    headers = {
        "Authorization": f"Bearer {gh_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="ignore")
        die(f"GitHub API error {err.code} for {path}: {body}")


def gh_graphql(query: str, variables: dict):
    payload = {"query": query, "variables": variables}
    return gh_api("POST", "/graphql", payload=payload)


def load_yaml(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def load_config(path: str = ".ops/config.yml") -> dict:
    cfg = load_yaml(path)
    defaults = {
        "install_cmd": "python3 -m pip install -e .",
        "lint_cmd": "echo 'no lint configured'",
        "test_cmd": "echo 'no tests configured'",
        "requires_docker": False,
        "docker_compose_file": "docker-compose.yml",
        "max_total_tokens": 120000,
        "max_runtime_minutes": 60,
        "max_iterations": 3,
        "comment_poll_interval": 30,
        "planner_prompt_version": "v1.0.0",
        "executor_prompt_version": "v1.0.0",
        "base_context_version": "v1",
    }
    defaults.update(cfg)
    return defaults


def set_output(name: str, value: str):
    output_file = os.getenv("GITHUB_OUTPUT")
    if not output_file:
        print(f"{name}={value}")
        return
    with open(output_file, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


def ensure_labels(issue_number: int, labels: list[str]):
    owner, repo = repo_owner_name()
    gh_api(
        "POST",
        f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
        payload={"labels": labels},
    )


def create_issue_comment(issue_number: int, body: str):
    owner, repo = repo_owner_name()
    gh_api(
        "POST",
        f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
        payload={"body": body},
    )


def create_pr_comment(pr_number: int, body: str):
    owner, repo = repo_owner_name()
    gh_api(
        "POST",
        f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
        payload={"body": body},
    )


def run_cmd(cmd: str):
    print(f"+ {cmd}")
    subprocess.run(cmd, shell=True, check=True)
