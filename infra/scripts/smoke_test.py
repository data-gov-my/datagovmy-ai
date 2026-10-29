#!/usr/bin/env python3
"""Check the serving contract the frontend and the token-roll Lambda rely on.

Without a token it makes no model calls: it checks /health and that every
protected route exists and rejects unauthenticated requests. With
SMOKE_CHAT_TOKEN set it also sends one short chat message.

    python infra/scripts/smoke_test.py https://staging.example.org:8443
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request


def request(method: str, url: str, body=None, token=None, timeout=60):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main(base: str) -> int:
    base = base.rstrip("/")
    chat_body = {"input": {"messages": [{"role": "user", "content": "hello"}]}}
    checks = [
        ("GET", "/health", None, None, 200),
        ("POST", "/chat/invoke", chat_body, None, 401),
        ("POST", "/chat/invoke", chat_body, "not-the-token", 401),
        ("POST", "/chat/stream", chat_body, None, 401),
        ("POST", "/auth-token", {"ROLLING_TOKEN": "x"}, None, 401),
        ("POST", "/auth-token", {"ROLLING_TOKEN": "x"}, "not-the-key", 401),
    ]
    token = os.environ.get("SMOKE_CHAT_TOKEN")
    if token:
        checks.append(("POST", "/chat/invoke", chat_body, token, 200))

    failed = 0
    for method, path, body, tok, expected in checks:
        start = time.monotonic()
        status, text = request(method, base + path, body, tok)
        ok = status == expected
        failed += not ok
        label = "auth" if tok == token and tok else ("bad-auth" if tok else "no-auth")
        print(
            f"{'ok ' if ok else 'FAIL'} {method} {path} [{label}] -> {status} "
            f"(want {expected}, {time.monotonic() - start:.1f}s)"
        )
        if not ok:
            print(f"     {text[:300]}")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
