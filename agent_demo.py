#!/usr/bin/env python3
"""A small, real coding-agent loop against the bypassed.sh API.

The model gets four tools over a throwaway copy of sample/: list the files,
read a file, replace text in pagination.py, and run the unit tests. It must
find and fix an off-by-one bug. The harness runs the tests itself before and
after, so the result does not depend on what the model says.

Standard library only. Python 3.9 or newer.

    export BYPASSED_API_KEY=...        # never pass the key as an argument
    python3 agent_demo.py

Bounds: at most --max-requests chat requests (default 8, hard limit 12), each
with max_tokens --max-tokens (default 1000, hard limit 2000). No request is
retried. Any HTTP error, timeout or unreadable response stops the run, because
the request may already have been billed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "1"
HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample"
DEFAULT_BASE = "https://api.bypassed.sh/v1"
DEFAULT_MODEL = "glm-5.3-flash-unlocked"
READABLE = ("pagination.py", "test_pagination.py")
EDITABLE = ("pagination.py",)
MAX_FILE_BYTES = 20_000
MAX_EDIT_CHARS = 2_000
TEST_TIMEOUT = 20
HTTP_TIMEOUT = 120

# Published prices in USD per million tokens (input, cached input, output), from
# https://bypassed.sh/#models on 8 November 2026. Only used for an estimate;
# the usage page in the dashboard is the billing record.
PRICES = {
    "glm-5.3-flash-unlocked": (0.15, 0.015, 0.55),
    "glm-5.3-unlocked": (1.25, 0.20, 3.50),
    "deepseek-v4.1-flash-unlocked": (0.20, 0.04, 0.75),
}

# The patch runs as Python when the tests import it. This guard rejects edits
# that reach outside plain list arithmetic. It is a guard, not a sandbox.
FORBIDDEN = re.compile(r"\bimport\b|__|\b(?:open|exec|eval|compile|globals|locals|getattr|setattr|delattr|vars|input|breakpoint|exit|quit)\b|\b(?:os|sys|subprocess|socket|shutil|pathlib|builtins)\b")

SYSTEM = (
    "You are a careful coding agent working in a small Python project. "
    "Use the tools to read the code and tests, make the smallest fix in pagination.py, "
    "then run the tests. Do not edit the tests. When the tests pass, reply with one or two "
    "sentences describing the fix."
)
TASK = "The unit tests in test_pagination.py fail. Find the bug in pagination.py, fix it, and run the tests."

TOOLS = [
    {"type": "function", "function": {"name": "list_files", "description": "List the files in the project.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read one project file.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "enum": list(READABLE)}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "replace_in_file", "description": "Replace one exact, unique snippet of text in pagination.py.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "enum": list(EDITABLE)}, "old": {"type": "string", "description": "Exact text that appears once in the file."}, "new": {"type": "string"}}, "required": ["path", "old", "new"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "run_tests", "description": "Run the project's unit tests and return the output.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
]


class Stop(Exception):
    """Ends the run. `uncertain` means a request may have been billed."""

    def __init__(self, reason: str, uncertain: bool = False):
        super().__init__(reason)
        self.reason, self.uncertain = reason, uncertain


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


class Workspace:
    """A throwaway copy of sample/. Tools only touch the files listed above."""

    def __init__(self, root: Path):
        self.root = root
        shutil.copytree(SAMPLE, root, ignore=shutil.ignore_patterns("__pycache__"))
        self.original = (root / "pagination.py").read_text()

    def _file(self, path: object, allowed: tuple[str, ...]) -> Path:
        if not isinstance(path, str) or path not in allowed:
            raise ValueError(f"path must be one of {', '.join(allowed)}")
        return self.root / path

    def list_files(self) -> str:
        return "\n".join(READABLE)

    def read_file(self, path: object) -> str:
        return self._file(path, READABLE).read_text()[:MAX_FILE_BYTES]

    def replace_in_file(self, path: object, old: object, new: object) -> str:
        target = self._file(path, EDITABLE)
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            raise ValueError("old and new must be text, and old must not be empty")
        if len(old) > MAX_EDIT_CHARS or len(new) > MAX_EDIT_CHARS:
            raise ValueError("edit is too large")
        if FORBIDDEN.search(new):
            raise ValueError("the replacement uses a name this demo does not allow")
        text = target.read_text()
        count = text.count(old)
        if count != 1:
            raise ValueError(f"old text must appear exactly once, found {count}")
        updated = text.replace(old, new, 1)
        if len(updated) > MAX_FILE_BYTES:
            raise ValueError("file would be too large")
        target.write_text(updated)
        return "Replaced 1 occurrence."

    def run_tests(self) -> dict:
        # Isolated interpreter, no bytecode files, and an environment without the API key.
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            proc = subprocess.run([sys.executable, "-I", "-B", "-m", "unittest", "discover", "-s", ".", "-t", ".", "-p", "test_*.py"], cwd=self.root, env=env, capture_output=True, text=True, timeout=TEST_TIMEOUT)
        except subprocess.TimeoutExpired:
            return {"passed": False, "exit_code": None, "summary": f"timed out after {TEST_TIMEOUT}s", "output": ""}
        output = (proc.stdout + proc.stderr)[-4000:]
        lines = [line for line in output.strip().splitlines() if line.startswith(("Ran ", "OK", "FAILED"))]
        return {"passed": proc.returncode == 0, "exit_code": proc.returncode, "summary": " · ".join(lines), "output": output}

    def diff(self) -> str:
        after = (self.root / "pagination.py").read_text()
        return "".join(difflib.unified_diff(self.original.splitlines(True), after.splitlines(True), "a/pagination.py", "b/pagination.py"))


class Client:
    def __init__(self, base: str, key: str):
        parsed = urllib.parse.urlparse(base)
        local = parsed.hostname in ("127.0.0.1", "localhost", "::1")
        if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
            raise Stop("base URL must use HTTPS")
        self.url = base.rstrip("/") + "/chat/completions"
        self._key = key

    def redact(self, text: str) -> str:
        return text.replace(self._key, "[redacted]") if self._key else text

    def chat(self, body: dict) -> tuple[dict, dict]:
        data = json.dumps(body).encode()
        req = urllib.request.Request(self.url, data=data, method="POST", headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json", "User-Agent": f"bypassed-quickstart-agent-demo/{VERSION}"})
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read(8 << 20)
                meta = {"http_status": resp.status, "request_id": resp.headers.get("X-Request-ID", ""), "seconds": round(time.monotonic() - started, 2)}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                err = json.loads(e.read(65536) or b"{}").get("error", {})
                detail = f"{err.get('code', '')}: {err.get('message', '')}".strip(": ")
            except (ValueError, AttributeError):
                pass
            finally:
                e.close()
            # A 4xx from validation was not charged; anything else may have been.
            raise Stop(self.redact(f"HTTP {e.code} {detail} (request {e.headers.get('X-Request-ID', 'unknown')})"), uncertain=e.code >= 500 or e.code == 408)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise Stop(self.redact(f"network error or timeout: {getattr(e, 'reason', e)}"), uncertain=True)
        try:
            return json.loads(raw), meta
        except ValueError:
            raise Stop("unreadable response body", uncertain=True)


def run(args: argparse.Namespace, key: str, out_dir: Path) -> dict:
    client = Client(args.base_url, key)
    report: dict = {
        "harness": f"bypassed-quickstart agent_demo.py v{VERSION}",
        "fixture": args.base_url != DEFAULT_BASE,
        "settings": {"model": args.model, "base_url": args.base_url, "reasoning_effort": args.reasoning, "max_tokens": args.max_tokens, "max_requests": args.max_requests, "http_timeout_s": HTTP_TIMEOUT, "test_timeout_s": TEST_TIMEOUT},
        "started_at": now(), "events": [], "usage": {"requests": 0, "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0},
    }
    events = report["events"]

    def log(kind: str, text: str, **fields):
        events.append({"at": now(), "type": kind, **fields})
        print(client.redact(f"[{kind}] {text}"), flush=True)

    with tempfile.TemporaryDirectory(prefix="bypassed-demo-") as tmp:
        ws = Workspace(Path(tmp) / "workspace")
        before = ws.run_tests()
        report["tests_before"] = {k: before[k] for k in ("passed", "exit_code", "summary")}
        log("harness", f"tests before: {before['summary']}", summary=before["summary"])
        if before["passed"]:
            raise SystemExit("sample tests already pass; nothing to fix")
        messages: list[dict] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": TASK}]
        outcome, final_text = "max_requests_reached", ""
        try:
            for n in range(1, args.max_requests + 1):
                body = {"model": args.model, "messages": messages, "tools": TOOLS, "tool_choice": "auto", "max_tokens": args.max_tokens, "reasoning_effort": args.reasoning}
                resp, meta = client.chat(body)
                report["usage"]["requests"] += 1
                usage = resp.get("usage") or {}
                u = report["usage"]
                u["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
                u["completion_tokens"] += int(usage.get("completion_tokens", 0))
                u["cached_tokens"] += int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
                u["reasoning_tokens"] += int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0)
                choice = (resp.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                calls = msg.get("tool_calls") or []
                content = msg.get("content") or ""
                log("model", f"request {n}: {len(calls)} tool call(s), finish={choice.get('finish_reason')}, {usage.get('prompt_tokens', 0)} in / {usage.get('completion_tokens', 0)} out" + (f"\n  {content.strip()[:300]}" if content.strip() else ""),
                    request=n, finish_reason=choice.get("finish_reason"), usage=usage, content=content[:2000], reasoning_chars=len(msg.get("reasoning_content") or ""), tool_calls=[{"name": (c.get("function") or {}).get("name"), "arguments": ((c.get("function") or {}).get("arguments") or "")[:2000]} for c in calls], **meta)
                echo = {"role": "assistant", "content": msg.get("content")}
                if msg.get("reasoning_content") is not None:
                    echo["reasoning_content"] = msg["reasoning_content"]
                if calls:
                    if not all(isinstance(c, dict) and c.get("id") and isinstance(c.get("function"), dict) and c["function"].get("name") for c in calls):
                        raise Stop("response contained a malformed tool call")
                    echo["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["function"]["name"], "arguments": c["function"].get("arguments") or "{}"}} for c in calls]
                elif echo["content"] is None:
                    echo["content"] = ""
                messages.append(echo)
                if not calls:
                    outcome, final_text = "model_finished", content.strip()
                    break
                if choice.get("finish_reason") == "length":
                    raise Stop("model hit max_tokens in the middle of a tool call")
                for call in calls:
                    name = (call.get("function") or {}).get("name")
                    params = {}
                    try:
                        params = json.loads(call["function"].get("arguments") or "{}")
                        if not isinstance(params, dict):
                            raise ValueError("arguments must be a JSON object")
                        if name == "list_files":
                            result = ws.list_files()
                        elif name == "read_file":
                            result = ws.read_file(params.get("path"))
                        elif name == "replace_in_file":
                            result = ws.replace_in_file(params.get("path"), params.get("old"), params.get("new"))
                        elif name == "run_tests":
                            t = ws.run_tests()
                            result = ("PASSED\n" if t["passed"] else "FAILED\n") + t["output"]
                        else:
                            raise ValueError(f"unknown tool {name!r}")
                        ok = True
                    except (ValueError, KeyError, TypeError) as e:
                        result, ok = f"error: {e}", False
                    shown = result if name != "read_file" else f"{len(result)} characters"
                    log("tool", f"{name}({', '.join(f'{k}={v!r}'[:80] for k, v in (params.items() if isinstance(params, dict) else []) if k != 'new')}) → {'ok' if ok else 'rejected'}: {shown.splitlines()[0][:120] if shown else ''}",
                        name=name, ok=ok, path=params.get("path") if isinstance(params, dict) else None, result=result[:2000] if name != "read_file" else f"{len(result)} characters")
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        except Stop as e:
            outcome = "stopped_uncertain_billing" if e.uncertain else "stopped"
            report["stop_reason"] = e.reason
            log("stop", e.reason + (" · not retried; check the usage page for this request" if e.uncertain else ""))
        after = ws.run_tests()
        report["tests_after"] = {k: after[k] for k in ("passed", "exit_code", "summary")}
        report["diff"] = ws.diff()
        log("harness", f"tests after: {after['summary']}", summary=after["summary"])
    report["outcome"] = outcome
    report["final_message"] = final_text[:2000]
    report["fixed"] = bool(not before["passed"] and after["passed"])
    rates = PRICES.get(args.model)
    if rates:
        u = report["usage"]
        cost = ((u["prompt_tokens"] - u["cached_tokens"]) * rates[0] + u["cached_tokens"] * rates[1] + u["completion_tokens"] * rates[2]) / 1e6
        report["estimated_cost_usd"] = round(cost, 6)
        report["estimate_note"] = "From reported usage and published prices on 8 November 2026. Reasoning tokens are billed as output. The dashboard usage page is the billing record."
    report["finished_at"] = now()
    text = client.redact(json.dumps(report, indent=2))
    report = json.loads(text)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"run-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(text + "\n")
    print(f"\n{'FIXED' if report['fixed'] else 'NOT FIXED'} · {outcome} · {report['usage']['requests']} request(s)" + (f" · about ${report['estimated_cost_usd']:.4f}" if rates else "") + f"\nreport: {path}")
    if report["diff"]:
        print(report["diff"])
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--model", default=os.environ.get("BYPASSED_MODEL", DEFAULT_MODEL))
    p.add_argument("--base-url", default=os.environ.get("BYPASSED_BASE_URL", DEFAULT_BASE))
    p.add_argument("--reasoning", default="low", help="reasoning_effort supported by the selected model; see /v1/models")
    p.add_argument("--max-tokens", type=int, default=1000)
    p.add_argument("--max-requests", type=int, default=8)
    p.add_argument("--out", type=Path, default=HERE / "runs")
    args = p.parse_args(argv)
    if not 1 <= args.max_requests <= 12 or not 64 <= args.max_tokens <= 2000:
        p.error("--max-requests must be 1-12 and --max-tokens 64-2000")
    key = os.environ.get("BYPASSED_API_KEY", "")
    if not key:
        p.error("set BYPASSED_API_KEY in the environment (it is never read from arguments)")
    try:
        report = run(args, key, args.out)
    except Stop as e:
        print(f"stopped: {e.reason}", file=sys.stderr)
        return 2
    return 0 if report["fixed"] else 1


if __name__ == "__main__":
    sys.exit(main())
