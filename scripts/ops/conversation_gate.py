#!/usr/bin/env python3
import argparse

from common import gh_graphql, repo_owner_name, set_output


QUERY = """
query($owner: String!, $repo: String!, $pr: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100) {
        nodes {
          isResolved
          isOutdated
        }
      }
    }
  }
}
"""

parser = argparse.ArgumentParser()
parser.add_argument("--pr-number", required=True, type=int)
args = parser.parse_args()

owner, repo = repo_owner_name()
resp = gh_graphql(QUERY, {"owner": owner, "repo": repo, "pr": args.pr_number})
threads = (
    resp.get("data", {})
    .get("repository", {})
    .get("pullRequest", {})
    .get("reviewThreads", {})
    .get("nodes", [])
)
unresolved = [t for t in threads if not t.get("isResolved", False) and not t.get("isOutdated", False)]
count = len(unresolved)
set_output("unresolved_count", str(count))
print(f"unresolved_threads={count}")
if count > 0:
    raise SystemExit(30)
