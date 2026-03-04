#!/usr/bin/env python3
import argparse
import json
import os
import urllib.request

from common import create_issue_comment


parser = argparse.ArgumentParser()
parser.add_argument("--issue-number", required=True)
parser.add_argument("--message", required=True)
parser.add_argument("--slack", action="store_true")
args = parser.parse_args()

issue_number = int(args.issue_number)
create_issue_comment(issue_number, args.message)

if args.slack:
    hook = os.getenv("SLACK_WEBHOOK_URL", "")
    if hook:
        payload = {"text": args.message}
        req = urllib.request.Request(
            hook,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req).read()

print("notification sent")
