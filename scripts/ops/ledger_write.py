#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--event", required=True)
parser.add_argument("--run-id", required=True)
parser.add_argument("--data", default="{}")
parser.add_argument("--ledger", default="ops-state/ledger.jsonl")
args = parser.parse_args()

ledger_path = Path(args.ledger)
ledger_path.parent.mkdir(parents=True, exist_ok=True)
prev_hash = "0" * 64
if ledger_path.exists():
    lines = [x for x in ledger_path.read_text().splitlines() if x.strip()]
    if lines:
        prev_hash = json.loads(lines[-1]).get("event_hash", prev_hash)

data_obj = json.loads(args.data)
event = {
    "ts": datetime.now(timezone.utc).isoformat(),
    "event": args.event,
    "run_id": args.run_id,
    "repository": os.getenv("GITHUB_REPOSITORY", ""),
    "data": data_obj,
    "prev_event_hash": prev_hash,
}
canonical = json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
event_hash = hashlib.sha256(canonical).hexdigest()
event["event_hash"] = event_hash
with open(ledger_path, "a", encoding="utf-8") as f:
    f.write(json.dumps(event, sort_keys=True) + "\n")
print(f"ledger={ledger_path}")
