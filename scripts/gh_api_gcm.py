#!/usr/bin/env python3
"""Secure GitHub API helper for B — authenticates via Git Credential Manager.

Usage:
    python gh_api_gcm.py check
        Verify GCM credential identity, repo access, and Issue #8 readability.
    python gh_api_gcm.py comment <issue-or-pr-number> <path-to-body-md>
        POST a comment; prints the resulting html_url.
    python gh_api_gcm.py get <api-path> [query]
        GET an API path (e.g. "repos/OWNER/REPO/issues/8/comments"); print JSON.
    python gh_api_gcm.py recent <issue-or-pr-number> [count]
        Print the last N comments (default 3), user + time + truncated body.

No credential is ever written to source, disk, output, or logs: the token is
fetched from `git credential fill` into process memory only and never printed.

Note for HTTPS-with-GCM runs: git itself may use a *different* stored
credential than api.github.com requests; this script only reads what the
credential manager hands back for host github.com.
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):  # Windows console default is GBK
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import json
import subprocess
import sys
import urllib.error
import urllib.request

REPO_API = "https://api.github.com/repos/Cynthia060913/freshlink-zhikong"
OWNER_LOGIN = "xixidamei"  # expected B account, read-only check target


def _credential() -> tuple[str, str]:
    """Fetch username/password from the configured git credential helper.

    The password (an OAuth token) is returned to the caller only; it is never
    printed, logged, or written anywhere.
    """
    import os

    env = dict(os.environ)
    env.update(GCM_INTERACTIVE="never", GIT_TERMINAL_PROMPTS="0")
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "ERROR: no stored GitHub credential (git credential fill failed). "
            "Fix: run `git ls-remote https://github.com/Cynthia060913/"
            "freshlink-zhikong.git` once in a terminal and complete the "
            "browser login yourself, then re-run."
        )
    fields: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key] = value
    if not fields.get("password"):
        raise SystemExit("ERROR: credential helper returned no password.")
    return fields.get("username", ""), fields["password"]


def _request(
    token: str, method: str, url: str, payload: dict[str, object] | None = None
) -> tuple[int, dict[str, object]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "freshlink-b-agent",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body: dict[str, object] = json.loads(resp.read().decode("utf-8"))
            return resp.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, {"_error": exc.read().decode("utf-8", "replace")[:300]}


def cmd_check(token: str) -> int:
    status, user = _request(token, "GET", "https://api.github.com/user")
    login = user.get("login") if isinstance(user, dict) else None
    print(f"identity: HTTP {status} login={login!r}")
    if status != 200:
        print("=> FAIL: credential cannot authenticate to the GitHub API.")
        return 1
    if login != OWNER_LOGIN:
        print(f"=> FAIL: authenticated account is not {OWNER_LOGIN}.")
        return 1
    print("=> OK: authenticated as the expected B account.")

    status, repo = _request(token, "GET", REPO_API)
    if status != 200 or not isinstance(repo, dict):
        print(f"repo: HTTP {status} — cannot read repository metadata.")
        return 1
    print(
        f"repo: {repo.get('full_name')} private={repo.get('private')} "
        f"push={bool((repo.get('permissions') or {}).get('push'))}"
    )
    status, issue = _request(token, "GET", f"{REPO_API}/issues/8")
    print(f"issue #8: HTTP {status} title={issue.get('title')!r}")
    if status == 200:
        print("=> ALL CHECKS PASSED (identity, repo read, issue read).")
        return 0
    return 1


def cmd_comment(token: str, number: str, body_path: str) -> int:
    with open(body_path, encoding="utf-8") as fh:
        body = fh.read()
    if not body.strip():
        raise SystemExit("ERROR: comment body is empty.")
    status, resp = _request(
        token,
        "POST",
        f"{REPO_API}/issues/{number}/comments",
        {"body": body},
    )
    if status == 201:
        print(f"OK url={resp.get('html_url')}")
        return 0
    print(f"FAIL HTTP {status}: {resp}")
    return 1


def cmd_get(token: str, path: str, query: str = "") -> int:
    url = (
        f"https://api.github.com/{path.lstrip('/')}"
        if not path.startswith("https://")
        else path
    )
    if query:
        url += f"?{query}"
    status, resp = _request(token, "GET", url)
    print(f"HTTP {status}")
    print(json.dumps(resp, ensure_ascii=False, indent=1))
    return 0 if status == 200 else 1


def cmd_recent(token: str, number: str, count: str = "3") -> int:
    """Print the last N comments on an issue/PR, compactly (body truncated)."""
    import datetime as _dt

    n = max(1, int(count))
    status, resp = _request(
        token,
        "GET",
        f"{REPO_API}/issues/{number}/comments?per_page=100",
    )
    if status != 200 or not isinstance(resp, list):
        print(f"issue #{number}: HTTP {status} — cannot list comments.")
        return 1
    total = len(resp)
    print(f"issue #{number}: {total} comments total; last {min(n, total)}:")
    for item in resp[-n:]:
        user = (item.get("user") or {}).get("login", "?")
        created = item.get("created_at", "?")
        body = str(item.get("body", "")).replace("\r\n", "\n")
        flat = " ".join(body.split())
        print(f"\n--- {user} @ {created} (id {item.get('id')})")
        print(flat[:700] + (" …[truncated]" if len(flat) > 700 else ""))
    _ = _dt.datetime  # keep import used
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in {"check", "comment", "get", "recent"}:
        print(__doc__)
        return 2
    _, password = _credential()
    if argv[1] == "check":
        return cmd_check(password)
    if argv[1] == "recent":
        return cmd_recent(password, *argv[2:])
    if argv[1] == "comment":
        if len(argv) != 4:
            print("usage: gh_api_gcm.py comment <number> <body.md>")
            return 2
        return cmd_comment(password, argv[2], argv[3])
    return cmd_get(password, *argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
