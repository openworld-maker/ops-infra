# Secrets Governance

## Policy

- Secrets are owned at org scope and rotated every 90 days.
- Workflows consume secrets via environment-scoped protections.
- Least-privilege token model is required.

## Environments

- `ops-plan`: planning-only keys.
- `ops-exec`: execution keys.
- `ops-pre-pr-approval`: manual approval checkpoint before PR creation.
- `ops-merge`: manual approval checkpoint before merge.

## Controls

- Rotation runbook execution every quarter.
- Rotation proof retained in workflow artifacts.
