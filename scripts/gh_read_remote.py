#!/usr/bin/env python3
"""Read remote GitHub state via REST API.
Fetches: repo main tip, PR #10 head, PR #14, Issues #5/#8 last comments,
PR #10 / PR #14 review state, last commit status."""

from __future__ import annotations

import json

# Token is deliberately NOT hardcoded (repo went public).
# Provide a fresh token via environment: set GITHUB_TOKEN=ghp_...
import os
import sys
import time
import urllib.error
import urllib.request

TOKEN = os.environ.get("GITHUB_TOKEN", "")
API = "https://api.github.com"
H = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "User-Agent": "freshlink-b-agent",
}


def fetch(url: str, max_retry: int = 3) -> dict | list | None:
    last_err = None
    for attempt in range(max_retry):
        try:
            req = urllib.request.Request(url, headers=H)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            return {"_error": e.code, "_body": body[:400], "_url": url}
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 + attempt)
    return {"_error": f"network: {last_err!r}", "_url": url}


def main() -> int:
    out: dict[str, object] = {}

    # repo meta + default branch tip
    out["repo"] = fetch(f"{API}/repos/Cynthia060913/freshlink-zhikong")

    # branches (tip of main)
    out["branch_main"] = fetch(
        f"{API}/repos/Cynthia060913/freshlink-zhikong/branches/main"
    )

    # PRs #10, #14
    for n in (10, 14):
        out[f"pr_{n}"] = fetch(f"{API}/repos/Cynthia060913/freshlink-zhikong/pulls/{n}")
        out[f"pr_{n}_commits"] = fetch(
            f"{API}/repos/Cynthia060913/freshlink-zhikong/pulls/{n}/commits?per_page=5"
        )
        out[f"pr_{n}_reviews"] = fetch(
            f"{API}/repos/Cynthia060913/freshlink-zhikong/pulls/{n}/reviews?per_page=10"
        )

    # issues #5, #8 + last 5 comments each
    for n in (5, 8):
        out[f"issue_{n}"] = fetch(
            f"{API}/repos/Cynthia060913/freshlink-zhikong/issues/{n}"
        )
        out[f"issue_{n}_comments"] = fetch(
            f"{API}/repos/Cynthia060913/freshlink-zhikong/issues/{n}/comments?per_page=5"
        )

    # check runs on PR #10 head
    pr10 = out.get("pr_10") or {}
    sha = pr10.get("head", {}).get("sha") if isinstance(pr10, dict) else None
    if sha:
        out["pr_10_checks"] = fetch(
            f"{API}/repos/Cynthia060913/freshlink-zhikong/commits/{sha}/check-runs"
        )

    # also check PR #4 (merged) for main tip comparison
    out["pr_4"] = fetch(f"{API}/repos/Cynthia060913/freshlink-zhikong/pulls/4")

    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
