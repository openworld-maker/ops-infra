# ops-infra

Central reusable GitHub Actions workflows and shared scripts for the `openworld-maker` autonomous engineering pipeline.

## What this repo provides

- Reusable workflows for task orchestration, review autopilot, merge gating, and watchdog monitoring.
- Shared scripts for issue selection, planning, execution, notification, budget enforcement, and audit ledger writing.
- Versioned prompt packs and JSON schemas.

## Runtime model

- Product repositories call reusable workflows via `workflow_call`.
- This repository is checked out into `_ops_infra` inside caller workflow runs.
- Git-visible names use `ops/*` conventions.

## Required secrets in caller repositories

- `OPENAI_API_KEY`
- `SLACK_WEBHOOK_URL` (optional but recommended)

## Model selection behavior

- Workflow defaults try `gpt-5`, then `gpt-5-mini`, then `gpt-4o-mini`.
- Planner/executor scripts retry transient API failures and fall back across model candidates.
- Override candidates with `PLANNER_MODEL_CANDIDATES` and `EXECUTOR_MODEL_CANDIDATES` if needed.
- Use `OPS_MAX_PROMPT_CHARS` to cap per-section prompt size when issue descriptions are very large.

## Recommended environments in caller repositories

- `ops-plan`
- `ops-exec`
- `ops-pre-pr-approval`
- `ops-merge`

## Prompt pinning

Caller repository `.ops/config.yml` must pin:

- `planner_prompt_version`
- `planner_prompt_sha`
- `executor_prompt_version`
- `executor_prompt_sha`

The workflow fails if these are missing or floating refs are used.
