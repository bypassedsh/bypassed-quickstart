#!/bin/sh
# One Chat Completions request with curl. Reads BYPASSED_API_KEY from the environment.
set -eu
: "${BYPASSED_API_KEY:?set BYPASSED_API_KEY first}"
curl -sS "${BYPASSED_BASE_URL:-https://api.bypassed.sh/v1}/chat/completions" \
  -H "Authorization: Bearer $BYPASSED_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "glm-5.3-flash-unlocked",
    "reasoning_effort": "low",
    "max_tokens": 200,
    "messages": [{"role": "user", "content": "Write a Python one-liner that reverses a string."}]
  }'
echo
