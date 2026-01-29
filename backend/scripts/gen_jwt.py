#!/usr/bin/env python3

"""Generate a dev HS256 JWT for platform v1.

This is intentionally minimal and prints a token to stdout.

Env:
- PLATFORM_JWT_SECRET (required)

Args:
- --sub <user_id>
- --project-id <uuid>
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import jwt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sub", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--exp-seconds", type=int, default=3600)
    args = parser.parse_args()

    secret = os.getenv("PLATFORM_JWT_SECRET")
    if not secret:
        print("ERROR: PLATFORM_JWT_SECRET is required", file=sys.stderr)
        return 2

    now = int(time.time())
    payload = {
        "sub": args.sub,
        "project_id": args.project_id,
        "iat": now,
        "exp": now + max(60, args.exp_seconds),
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    # pyjwt may return str or bytes depending on version.
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
