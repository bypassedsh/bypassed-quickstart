"""Fixture tests for agent_demo.py.

These use a scripted local server, not the real API, so they check the
harness only: tool limits, stopping rules, the report and key handling. They
are not evidence that any model can fix the bug. Run from the quickstart
directory:

    python3 -m unittest discover -s tests
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import unittest.mock
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent_demo  # noqa: E402

KEY = "fixture-key-0123456789abcdef"
FIX_OLD = "start = page * per_page"
FIX_NEW = "start = (page - 1) * per_page"


def call(name, args, n=1):
    return {"id": f"call_{name}_{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def reply(calls=None, content=None):
    return {"status": 200, "body": {"choices": [{"index": 0, "finish_reason": "tool_calls" if calls else "stop", "message": {"role": "assistant", "content": content, "tool_calls": calls or None}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050, "prompt_tokens_details": {"cached_tokens": 400}}}}


class Fixture:
    def __init__(self, script):
        self.script, self.requests = list(script), []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fixture.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                step = fixture.script.pop(0) if fixture.script else reply([call("list_files", {}, len(fixture.requests))])
                data = json.dumps(step["body"]).encode()
                self.send_response(step["status"])
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Request-ID", f"fixture_{len(fixture.requests)}")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class AgentDemoFixtureTest(unittest.TestCase):
    def run_demo(self, script, *extra):
        fixture = Fixture(script)
        self.addCleanup(fixture.close)
        out = Path(tempfile.mkdtemp())
        args = agent_demo.argparse.Namespace(model="glm-5.3-flash-unlocked", base_url=fixture.base, reasoning="low", max_tokens=1000, max_requests=8, out=out)
        for k, v in extra:
            setattr(args, k, v)
        with redirect_stdout(io.StringIO()) as printed:
            report = agent_demo.run(args, KEY, out)
        files = list(out.glob("run-*.json"))
        self.assertEqual(len(files), 1)
        saved = files[0].read_text()
        self.assertNotIn(KEY, saved)
        self.assertNotIn(KEY, printed.getvalue())
        self.assertEqual(json.loads(saved), report)
        return report, fixture

    def test_tool_loop_fixes_the_bug_and_harness_verifies_it(self):
        report, fixture = self.run_demo([
            reply([call("read_file", {"path": "pagination.py"}), call("read_file", {"path": "test_pagination.py"})]),
            reply([call("replace_in_file", {"path": "pagination.py", "old": FIX_OLD, "new": FIX_NEW})]),
            reply([call("run_tests", {})]),
            reply(content="Pages are 1-based, so the slice now starts at (page - 1) * per_page."),
        ])
        self.assertTrue(report["fixture"])
        self.assertTrue(report["fixed"])
        self.assertFalse(report["tests_before"]["passed"])
        self.assertTrue(report["tests_after"]["passed"])
        self.assertEqual(report["outcome"], "model_finished")
        self.assertIn("+    start = (page - 1) * per_page", report["diff"])
        self.assertEqual(report["usage"]["requests"], 4)
        self.assertEqual(report["usage"]["cached_tokens"], 1600)
        self.assertAlmostEqual(report["estimated_cost_usd"], (2400 * 0.15 + 1600 * 0.015 + 200 * 0.55) / 1e6)
        first = fixture.requests[0]
        self.assertEqual(first["auth"], f"Bearer {KEY}")
        self.assertEqual(first["path"], "/v1/chat/completions")
        self.assertEqual({k: first["body"][k] for k in ("model", "reasoning_effort", "max_tokens", "tool_choice")}, {"model": "glm-5.3-flash-unlocked", "reasoning_effort": "low", "max_tokens": 1000, "tool_choice": "auto"})
        # Tool results are sent back with their call IDs, after the assistant turn.
        roles = [m["role"] for m in fixture.requests[1]["body"]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "tool"])
        self.assertEqual(fixture.requests[1]["body"]["messages"][3]["tool_call_id"], "call_read_file_1")
        self.assertNotIn(KEY, json.dumps([r["body"] for r in fixture.requests]))

    def test_tools_stay_inside_the_demo_files(self):
        report, fixture = self.run_demo([
            reply([
                call("read_file", {"path": "../agent_demo.py"}, 1),
                call("read_file", {"path": "/etc/passwd"}, 2),
                call("replace_in_file", {"path": "test_pagination.py", "old": "[1, 2]", "new": "[3, 4]"}, 3),
                call("replace_in_file", {"path": "pagination.py", "old": FIX_OLD, "new": "import os; os.system('id')"}, 4),
                call("replace_in_file", {"path": "pagination.py", "old": FIX_OLD, "new": "start = __import__('os')"}, 5),
                call("replace_in_file", {"path": "pagination.py", "old": "per_page", "new": "x"}, 6),
                call("run_shell", {"cmd": "id"}, 7),
            ]),
            reply(content="Giving up."),
        ])
        results = [e for e in report["events"] if e["type"] == "tool"]
        self.assertEqual(len(results), 7)
        self.assertTrue(all(not e["ok"] for e in results), results)
        self.assertIn("exactly once", results[5]["result"])
        self.assertEqual(report["diff"], "")
        self.assertFalse(report["fixed"])

    def test_server_error_stops_without_retry(self):
        report, fixture = self.run_demo([{"status": 502, "body": {"error": {"code": "service_unavailable", "message": f"bad gateway {KEY}"}}}])
        self.assertEqual(len(fixture.requests), 1)
        self.assertEqual(report["outcome"], "stopped_uncertain_billing")
        self.assertIn("HTTP 502", report["stop_reason"])
        self.assertIn("fixture_1", report["stop_reason"])
        self.assertIn("[redacted]", report["stop_reason"])

    def test_validation_error_is_not_marked_uncertain(self):
        report, fixture = self.run_demo([{"status": 400, "body": {"error": {"code": "invalid_request", "message": "Unsupported reasoning effort."}}}])
        self.assertEqual(len(fixture.requests), 1)
        self.assertEqual(report["outcome"], "stopped")

    def test_request_budget_is_enforced(self):
        report, fixture = self.run_demo([], ("max_requests", 3))
        self.assertEqual(len(fixture.requests), 3)
        self.assertEqual(report["outcome"], "max_requests_reached")

    def test_malformed_tool_arguments_are_rejected_without_crashing(self):
        malformed = call("read_file", {})
        malformed["function"]["arguments"] = "{not json"
        report, fixture = self.run_demo([reply([malformed]), reply(content="Stopping.")])
        results = [e for e in report["events"] if e["type"] == "tool"]
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["ok"])
        self.assertEqual(report["outcome"], "model_finished")
        self.assertEqual(len(fixture.requests), 2)

    def test_model_echo_is_redacted_from_terminal_and_returned_report(self):
        report, _ = self.run_demo([reply(content=f"An accidental echo: {KEY}")])
        self.assertNotIn(KEY, json.dumps(report))
        self.assertIn("[redacted]", report["final_message"])

    def test_key_comes_only_from_the_environment(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
            saved = os.environ.pop("BYPASSED_API_KEY", None)
            try:
                with unittest.mock.patch("sys.stderr", io.StringIO()):
                    agent_demo.main(["--base-url", "http://127.0.0.1:9/v1"])
            finally:
                if saved is not None:
                    os.environ["BYPASSED_API_KEY"] = saved

    def test_plain_http_is_refused_for_remote_hosts(self):
        with self.assertRaises(agent_demo.Stop):
            agent_demo.Client("http://api.bypassed.sh/v1", KEY)


if __name__ == "__main__":
    unittest.main()
