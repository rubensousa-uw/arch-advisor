"""Provider routing/preferences/response checks in disposable, inference-free profiles."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "codex/scripts/advisor.py"
spec = importlib.util.spec_from_file_location("codex_advisor", RUNNER)
advisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(advisor)


class CodexPluginTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="codex-plugin-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.workspace = self.base / "workspace with spaces 'quoted'"
        self.workspace.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.calls_path = self.base / "calls.jsonl"
        mock = """#!{python}
import json, os, pathlib, sys, time
args = sys.argv[1:]
prompt = sys.stdin.read()
with open(os.environ['MOCK_CALLS'], 'a') as stream:
    stream.write(json.dumps({{'provider': pathlib.Path(sys.argv[0]).name, 'args': args, 'cwd': os.getcwd(), 'prompt': prompt}}) + '\\n')
time.sleep(float(os.environ.get('MOCK_SLEEP', '0')))
value = os.environ.get('MOCK_RESPONSE', 'ship: evidence reviewed')
if os.environ.get('MOCK_AUTO_OK') and 'Respond with exactly OK' in prompt:
    value = 'OK'
if pathlib.Path(sys.argv[0]).name == 'codex':
    if not os.environ.get('MOCK_NO_FINAL'):
        pathlib.Path(args[args.index('--output-last-message') + 1]).write_text(value)
    print('OK')
else:
    if os.environ.get('MOCK_INVALID_JSON'):
        print('OK')
    else:
        print(json.dumps({{'type': 'result', 'subtype': 'success', 'is_error': bool(os.environ.get('MOCK_IS_ERROR')), 'result': value, 'modelUsage': {{'claude-opus-observed': {{}}}}}}))
sys.exit(int(os.environ.get('MOCK_EXIT', '0')))
""".format(python=sys.executable)
        for name in ("codex", "claude"):
            path = self.bin / name
            path.write_text(mock)
            path.chmod(0o755)
        self.env = os.environ.copy()
        self.env.pop("ARCH_ADVISOR_CODEX_CONFIG", None)
        self.env.update(CODEX_HOME=str(self.base / "profile"),
                        PATH=str(self.bin) + os.pathsep + self.env.get("PATH", ""),
                        MOCK_CALLS=str(self.calls_path), PYTHONDONTWRITEBYTECODE="1")
        self.settings = self.base / "profile/arch-advisor/config.json"

    def cli(self, *args, prompt="Review local evidence."):
        return subprocess.run([sys.executable, str(RUNNER), *args], cwd=self.base,
                              env=self.env, input=prompt, capture_output=True, text=True)

    def calls(self):
        return [json.loads(line) for line in self.calls_path.read_text().splitlines()] if self.calls_path.exists() else []

    def patch(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def show(self):
        result = self.cli("show", "--json", "--cd", str(self.workspace))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def opinion(self, *args):
        return self.cli("opinion", "--cd", str(self.workspace), *args)

    def test_preferences_survive_package_copy_and_no_inference_for_configuration(self):
        result = self.cli("set", "routine", "--model", "future-model-id", "--effort", "max")
        self.assertEqual(result.returncode, 0, result.stderr)
        updated = self.base / "updated-plugin"
        shutil.copytree(ROOT / "codex", updated)
        result = subprocess.run([sys.executable, str(updated / "scripts/advisor.py"), "show", "--json", "--cd", str(self.workspace)], env=self.env, capture_output=True, text=True)
        lane = json.loads(result.stdout)["lanes"]["routine"]
        self.assertEqual((lane["model"], lane["effort"]), ("future-model-id", "max"))
        self.assertEqual(self.calls(), [])

    def test_set_preserves_omitted_lanes_and_provider_specific_choices(self):
        self.assertEqual(self.cli("set", "second-opinion", "--provider", "claude", "--model", "sonnet", "--effort", "high").returncode, 0)
        self.assertEqual(self.cli("set", "second-opinion", "--provider", "codex", "--model", "gpt-6-astra", "--effort", "medium").returncode, 0)
        data = self.show()["lanes"]
        self.assertEqual(data["routine"]["model"], "gpt-6-luna")
        self.assertEqual(data["second-opinion"]["claude"], {"model": "sonnet", "effort": "high"})
        self.assertEqual(data["second-opinion"]["codex"]["effort"], "medium")

    def test_target_workspace_overrides_user_and_caller_and_explicit_overrides_project(self):
        self.cli("set", "routine", "--model", "user-model")
        self.patch(self.base / ".arch-advisor/codex.json", {"lanes": {"routine": {"model": "wrong-caller"}}})
        self.patch(self.workspace / ".arch-advisor/codex.json", {"lanes": {"routine": {"model": "project-model"}}})
        result = self.cli("run", "routine", "--cd", str(self.workspace))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("project-model", self.calls()[-1]["args"])
        self.assertEqual(Path(self.calls()[-1]["cwd"]).resolve(), self.workspace.resolve())
        explicit = self.workspace / "explicit.json"
        self.patch(explicit, {"lanes": {"routine": {"model": "explicit-model"}}})
        self.env["ARCH_ADVISOR_CODEX_CONFIG"] = "explicit.json"
        self.assertEqual(self.show()["lanes"]["routine"]["model"], "explicit-model")

    def test_claude_codex_and_effort_overrides_do_not_rewrite_preferences(self):
        self.cli("set", "second-opinion", "--provider", "claude", "--effort", "high")
        saved = self.settings.read_bytes()
        result = self.opinion("--provider", "codex", "--effort", "max")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1]["provider"], "codex")
        self.assertIn("model_reasoning_effort=max", self.calls()[-1]["args"])
        self.assertIn("read-only", self.calls()[-1]["args"])
        result = self.opinion()
        self.assertEqual(result.returncode, 0, result.stderr)
        call = self.calls()[-1]
        self.assertEqual(call["provider"], "claude")
        for flag in ("--safe-mode", "--restricted", "--strict-mcp-config", "--disable-slash-commands", "--no-chrome"):
            self.assertIn(flag, call["args"])
        for flag in ("--tools", "--allowedTools"):
            self.assertEqual(call["args"][call["args"].index(flag) + 1], "Read,Grep,Glob")
        self.assertEqual(call["args"][call["args"].index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(call["args"][call["args"].index("--permission-prompts") + 1], "none")
        self.assertIn("MODELS_REPORTED: claude-opus-observed", result.stdout)
        self.assertIn("ORIGINAL RESPONSE:\nship: evidence reviewed", result.stdout)
        self.assertEqual(self.settings.read_bytes(), saved)

    def test_inherited_effort_omits_flag_for_each_provider(self):
        for provider in ("codex", "claude"):
            self.cli("set", "second-opinion", "--provider", provider, "--effort", "inherit")
            self.assertEqual(self.opinion().returncode, 0)
            args = self.calls()[-1]["args"]
            self.assertNotIn("--effort", args)
            self.assertFalse(any(arg.startswith("model_reasoning_effort=") for arg in args))

    def test_implementers_are_codex_only_and_workspace_write(self):
        for name in ("routine", "complex"):
            result = self.cli("run", name, "--cd", str(self.workspace))
            self.assertEqual(result.returncode, 0, result.stderr)
            call = self.calls()[-1]
            self.assertEqual(call["provider"], "codex")
            self.assertIn("workspace-write", call["args"])
            self.assertIn('approval_policy="never"', call["args"])
        self.assertNotEqual(self.cli("set", "routine", "--provider", "claude").returncode, 0)

    def test_invalid_settings_fail_before_provider_without_exposing_unrelated_values(self):
        for data in ("{broken", json.dumps({"unexpected": "PRIVATE_VALUE"}), json.dumps({"lanes": {"routine": {"model": "--shell injection"}}}), json.dumps({"lanes": {"complex": {"effort": "ultra"}}}), json.dumps({"lanes": {"routine": {"timeout_seconds": True}}})):
            self.settings.parent.mkdir(parents=True, exist_ok=True)
            self.settings.write_text(data)
            result = self.opinion()
            self.assertEqual(result.returncode, 4)
            self.assertNotIn("PRIVATE_VALUE", result.stdout + result.stderr)
            self.assertEqual(self.calls(), [])

    def test_missing_explicit_override_and_missing_workspace_are_errors(self):
        self.env["ARCH_ADVISOR_CODEX_CONFIG"] = "missing.json"
        self.assertEqual(self.opinion().returncode, 4)
        self.assertNotEqual(self.cli("opinion").returncode, 0)
        self.assertNotEqual(self.cli("opinion", "--cd", str(self.base / "missing")).returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_dry_run_and_empty_prompt_never_call_provider(self):
        self.assertEqual(self.opinion("--dry-run").returncode, 0)
        self.assertEqual(self.opinion("--provider", "claude", "--dry-run").returncode, 0)
        self.assertEqual(self.cli("opinion", "--cd", str(self.workspace), prompt="").returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_exact_exit_code_and_no_advice_from_failure(self):
        self.env["MOCK_EXIT"] = "9"
        result = self.opinion()
        self.assertEqual(result.returncode, 9)
        self.assertIn("EXIT_CODE: 9", result.stdout)
        self.assertNotIn("STATUS: complete", result.stdout)
        self.assertNotIn("ORIGINAL RESPONSE:", result.stdout)

    def test_codex_stdout_ok_without_final_message_is_not_success(self):
        self.env["MOCK_NO_FINAL"] = "1"
        result = self.opinion()
        self.assertEqual(result.returncode, 1)
        self.assertIn("STATUS: refused", result.stdout)

    def test_claude_error_result_or_invalid_json_is_not_success(self):
        for key in ("MOCK_IS_ERROR", "MOCK_INVALID_JSON"):
            self.env[key] = "1"
            self.env["MOCK_RESPONSE"] = "OK"
            result = self.opinion("--provider", "claude")
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("STATUS: complete", result.stdout)
            self.env.pop(key)

    def test_smoke_checks_final_exact_ok_and_exit_for_both_providers(self):
        for provider in ("codex", "claude"):
            args = ("smoke", "second-opinion", "--cd", str(self.workspace), "--provider", provider)
            self.env["MOCK_RESPONSE"] = "OK"
            self.assertEqual(self.cli(*args).returncode, 0)
            self.env["MOCK_RESPONSE"] = "OK but not an exact probe"
            self.assertEqual(self.cli(*args).returncode, 1)
            self.env["MOCK_RESPONSE"] = "OK"
            self.env["MOCK_EXIT"] = "9"
            self.assertEqual(self.cli(*args).returncode, 1)
            self.env.pop("MOCK_EXIT")

    def test_smoke_all_lanes_are_read_only_and_have_independent_results(self):
        self.env["MOCK_AUTO_OK"] = "1"
        result = self.cli("smoke", "--cd", str(self.workspace))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls()), 3)
        for call in self.calls():
            self.assertIn("read-only", call["args"])
        self.assertEqual(len({call["args"][call["args"].index("--output-last-message") + 1] for call in self.calls()}), 3)

    def test_timeout_is_enforced_without_external_coreutils(self):
        self.patch(self.settings, {"lanes": {"second-opinion": {"timeout_seconds": 1}}})
        self.env["MOCK_SLEEP"] = "8"
        start = time.monotonic()
        result = self.opinion()
        self.assertEqual(result.returncode, 124)
        self.assertIn("STATUS: timeout", result.stdout)
        self.assertLess(time.monotonic() - start, 5)

    def test_configure_requires_real_terminal_and_does_not_write_or_call_model(self):
        result = self.cli("configure")
        self.assertEqual(result.returncode, 4)
        self.assertIn("interactive terminal", result.stderr)
        self.assertFalse(self.settings.exists())
        self.assertEqual(self.calls(), [])

    @unittest.skipUnless(os.name == "posix", "Unix terminal required")
    def test_arrow_key_menu_saves_user_choices_without_inference(self):
        import pty
        import select
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        env = dict(self.env, TERM="xterm")
        process = subprocess.Popen([sys.executable, str(RUNNER), "configure"],
                                   stdin=slave, stdout=slave, stderr=slave, env=env)
        os.close(slave)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)

        def receive(marker):
            received = b""
            deadline = time.monotonic() + 3
            while marker not in received and time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        received += os.read(master, 8192)
                    except OSError:
                        break
            self.assertIn(marker, received, received.decode(errors="replace"))

        receive(b"Cancel")
        for keys, marker in (
            (b"\r", b"custom"),                         # Routine
            (b"\x1b[B\x1b[B\r", b"max"),             # Astra model
            (b"\x1b[A\r", b"Cancel"),                 # Medium effort
            (b"\x1b[B\x1b[B\r", b"claude"),          # Second opinion
            (b"\x1b[B\r", b"custom"),                 # Claude provider
            (b"\r", b"max"),                          # Opus alias
            (b"\x1b[B\x1b[B\r", b"Cancel"),          # Claude max
            (b"\x1b[B\x1b[B\x1b[B\r", b"Saved"),    # Save
        ):
            os.write(master, keys)
            receive(marker)
        self.assertEqual(process.wait(timeout=3), 0)
        config = json.loads(self.settings.read_text())["lanes"]
        self.assertEqual((config["routine"]["model"], config["routine"]["effort"]), ("gpt-6-astra", "medium"))
        self.assertEqual(config["second-opinion"]["provider"], "claude")
        self.assertEqual(config["second-opinion"]["claude"]["effort"], "max")
        self.assertEqual(config["second-opinion"]["codex"]["effort"], "medium")
        self.assertEqual(self.calls(), [])

    def test_invalid_set_does_not_overwrite_valid_preferences(self):
        self.cli("set", "routine", "--effort", "low")
        before = self.settings.read_bytes()
        self.assertEqual(self.cli("set", "routine", "--model", "invalid model").returncode, 4)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_package_is_self_contained_and_compatibility_metadata_matches(self):
        portable = json.loads((ROOT / "codex/plugin.json").read_text())
        legacy = json.loads((ROOT / "codex/.codex-plugin/plugin.json").read_text())
        for key in ("name", "version", "description", "author", "repository", "license"):
            self.assertEqual(portable[key], legacy[key])
        self.assertEqual(portable["extensions"]["com.openai"]["interface"], legacy["interface"])
        self.assertTrue((ROOT / "codex" / legacy["skills"]).is_dir())
        marketplace = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
        self.assertEqual((ROOT / marketplace["plugins"][0]["source"]["path"]).resolve(), (ROOT / "codex").resolve())


if __name__ == "__main__":
    unittest.main()
