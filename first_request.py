#!/usr/bin/env python3
"""One Chat Completions request to bypassed.sh, using only the standard library.

    export BYPASSED_API_KEY=...
    python3 first_request.py                 # one JSON response
    python3 first_request.py --stream        # tokens as they arrive

The key is read from the environment, never from arguments. One request,
max_tokens 200, no retry.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

p = argparse.ArgumentParser()
p.add_argument("--model", default=os.environ.get("BYPASSED_MODEL", "glm-5.3-flash-unlocked"))
p.add_argument("--stream", action="store_true")
p.add_argument("prompt", nargs="?", default="Write a Python one-liner that reverses a string, then explain it in one sentence.")
args = p.parse_args()
key = os.environ.get("BYPASSED_API_KEY")
if not key:
    sys.exit("set BYPASSED_API_KEY first")
base = os.environ.get("BYPASSED_BASE_URL", "https://api.bypassed.sh/v1").rstrip("/")
body = {"model": args.model, "reasoning_effort": "low", "max_tokens": 200, "stream": args.stream, "messages": [{"role": "user", "content": args.prompt}]}
req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(body).encode(), method="POST", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "bypassed-quickstart/1"})
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        if not args.stream:
            out = json.load(resp)
            print(out["choices"][0]["message"].get("content") or "")
            print("\nusage:", json.dumps(out.get("usage")), file=sys.stderr)
        else:
            usage = None
            for line in resp:
                line = line.decode().strip()
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                chunk = json.loads(line[6:])
                for choice in chunk.get("choices") or []:
                    print((choice.get("delta") or {}).get("content") or "", end="", flush=True)
                usage = chunk.get("usage") or usage
            print("\n\nusage:", json.dumps(usage), file=sys.stderr)
except urllib.error.HTTPError as e:
    sys.exit(f"HTTP {e.code}: {e.read(4096).decode(errors='replace')}")
