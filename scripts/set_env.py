#!/usr/bin/env python3
"""set_env.py — safely set KEY=VALUE pairs in .env without corrupting existing lines.

Writes each value on its own line (this python-dotenv parses inline '# ...' as the value,
so we never append inline comments). Updates the key in place if present, else appends.
Leaves all other lines — including secrets — byte-for-byte untouched.

Usage:
    python3 scripts/set_env.py BILLING_WEBHOOK_SECRET=whsec_xxx RESEND_API_KEY=re_xxx
Values may contain '=' (only the first '=' splits key/value).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main(pairs: list[str]) -> int:
    if not pairs:
        print("usage: set_env.py KEY=VALUE [KEY=VALUE ...]", file=sys.stderr)
        return 2
    updates: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            print(f"ERROR: '{p}' is not KEY=VALUE", file=sys.stderr)
            return 2
        k, v = p.split("=", 1)
        updates[k.strip()] = v
    env = Path(".env")
    lines = env.read_text().splitlines(keepends=True) if env.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in updates:
            out.append(f"{key}={updates[key]}\n")
            seen.add(key)
        else:
            out.append(line)
    if out and not out[-1].endswith("\n"):
        out[-1] += "\n"
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}\n")
    contents = "".join(out)
    try:
        fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        env.write_text(contents)
    else:
        with os.fdopen(fd, "w") as file:
            file.write(contents)
    print("set:", ", ".join(sorted(updates)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
