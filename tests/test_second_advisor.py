import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/second-advisor.sh"


class SecondAdvisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="second-advisor-tests-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "project with spaces"
        self.workspace.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.record = self.base / "invocation.json"
        self.config = self.base / "lanes.json"
        self.config.write_text((ROOT / "config/lanes.json").read_text())
        mock = self.bin / "codex"
        mock.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
pathlib.Path(os.environ['MOCK_CODEX_RECORD']).write_text(json.dumps({'args': args, 'prompt': prompt}))
mode = os.environ.get('MOCK_CODEX_MODE', 'success')
if mode in ('failure', 'timeout'):
    print('model unavailable' if mode == 'failure' else 'timed out')
    sys.exit(7 if mode == 'failure' else 124)
if mode != 'empty':
    pathlib.Path(args[args.index('--output-last-message') + 1]).write_text('VERDICT: ship\\nEvidence checked; no changes needed.\\n')
""")
        mock.chmod(0o755)
        # Avoid real waiting while retaining the helper's timeout invocation path.
        timer = self.bin / "gtimeout"
        timer.write_text('#!/bin/sh\nshift\nexec "$@"\n')
        timer.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(
            PATH=str(self.bin) + os.pathsep + self.env.get("PATH", ""),
            ARCH_ADVISOR_CONFIG=str(self.config),
            CLAUDE_CONFIG_DIR=str(self.base / "claude"),
            MOCK_CODEX_RECORD=str(self.record),
        )

    def run_helper(self, *args, question="Check the plan; do not edit files."):
        return subprocess.run(
            [str(HELPER), "--cd", str(self.workspace), *args],
            input=question, text=True, capture_output=True, env=self.env,
            cwd=self.base,
        )

    def invocation(self):
        return json.loads(self.record.read_text())

    def test_success_uses_astra_read_only_and_preserves_prompt(self):
        question = "Review `literal` $(never execute) and quoted 'paths'."
        before = list(self.workspace.iterdir())
        result = self.run_helper("--effort", "high", question=question)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("STATUS: complete", result.stdout)
        call = self.invocation()
        args = call["args"]
        self.assertEqual(args[args.index("--model") + 1], "gpt-6-astra")
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertIn('approval_policy="never"', args)
        self.assertIn("model_reasoning_effort=high", args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
        self.assertEqual(args[args.index("--cd") + 1], str(self.workspace))
        self.assertIn(question, call["prompt"])
        self.assertEqual(before, list(self.workspace.iterdir()))
        self.assertFalse(Path(args[args.index("--output-last-message") + 1]).parent.exists())

    def test_omitted_effort_is_not_pinned(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(a.startswith("model_reasoning_effort=") for a in self.invocation()["args"]))
        self.assertIn("codex default", result.stdout)

    def test_disallowed_effort_never_calls_codex(self):
        result = self.run_helper("--effort", "ultra")
        self.assertEqual(result.returncode, 4)
        self.assertIn("STATUS: unavailable", result.stdout)
        self.assertFalse(self.record.exists())

    def test_configured_model_override_reaches_cli(self):
        config = json.loads(self.config.read_text())
        config["lanes"]["2nd-advisor"]["model"] = "gpt-6.1-sol"
        self.config.write_text(json.dumps(config))
        result = self.run_helper("--effort", "medium")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("gpt-6.1-sol", self.invocation()["args"])

    def test_target_project_config_wins_over_plugin_default(self):
        self.env.pop("ARCH_ADVISOR_CONFIG")
        folder = self.workspace / ".arch-advisor"
        folder.mkdir()
        config = json.loads(self.config.read_text())
        config["lanes"]["2nd-advisor"]["model"] = "gpt-6.1-sol"
        (folder / "lanes.json").write_text(json.dumps(config))
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("gpt-6.1-sol", self.invocation()["args"])

    def test_missing_advisor_lane_never_calls_codex(self):
        config = json.loads(self.config.read_text())
        del config["lanes"]["2nd-advisor"]
        self.config.write_text(json.dumps(config))
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("STATUS: unavailable", result.stdout)
        self.assertFalse(self.record.exists())

    def test_cli_failure_and_timeout_are_not_success(self):
        for mode, code, status in (("failure", 7, "unavailable"), ("timeout", 124, "timeout")):
            with self.subTest(mode=mode):
                self.env["MOCK_CODEX_MODE"] = mode
                result = self.run_helper()
                self.assertEqual(result.returncode, code)
                self.assertIn("STATUS: " + status, result.stdout)
                self.assertNotIn("STATUS: complete", result.stdout)

    def test_empty_result_is_refusal(self):
        self.env["MOCK_CODEX_MODE"] = "empty"
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("STATUS: refused", result.stdout)

    def test_empty_question_does_not_call_codex(self):
        result = self.run_helper(question="")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.record.exists())

    def test_missing_option_value_does_not_call_codex(self):
        result = self.run_helper("--effort")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.record.exists())


if __name__ == "__main__":
    unittest.main()
