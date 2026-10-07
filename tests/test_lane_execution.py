import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run-lane.sh"
SMOKE = ROOT / "scripts/smoke.sh"
ADVISOR = ROOT / "scripts/second-advisor.sh"


class LaneExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lane-regressions-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "project with spaces 'quoted'"
        self.workspace.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.record = self.base / "calls.jsonl"
        self.config = self.base / "lanes.json"
        self.data = json.loads((ROOT / "config/lanes.json").read_text())
        self.save_config()
        mock = self.bin / "codex"
        mock.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['MOCK_CODEX_RECORD'], 'a') as record:
    record.write(json.dumps({'args': args, 'cwd': os.getcwd(), 'prompt': sys.stdin.read()}) + '\\n')
mode = os.environ.get('MOCK_CODEX_MODE', 'success')
model = args[args.index('--model') + 1]
if model == os.environ.get('MOCK_NO_RESULT_MODEL'):
    mode = 'stdout_only'
if mode != 'stdout_only' and '--output-last-message' in args:
    pathlib.Path(args[args.index('--output-last-message') + 1]).write_text(os.environ.get('MOCK_FINAL', 'OK'))
print('OK')
if mode == 'failure_ok':
    sys.exit(9)
if mode == 'timeout':
    sys.exit(124)
""")
        mock.chmod(0o755)
        timer = self.bin / "gtimeout"
        timer.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$MOCK_TIMER_RECORD"\nshift\nexec "$@"\n')
        timer.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(
            PATH=str(self.bin) + os.pathsep + self.env.get("PATH", ""),
            ARCH_ADVISOR_CONFIG=str(self.config),
            MOCK_CODEX_RECORD=str(self.record),
            MOCK_TIMER_RECORD=str(self.base / "timeouts"),
        )

    def save_config(self):
        self.config.write_text(json.dumps(self.data))

    def run_command(self, script, *args):
        return subprocess.run(
            [str(script), *args], cwd=self.base, env=self.env,
            input="Inspect the workspace.\n", text=True, capture_output=True,
        )

    def run_lane(self, lane, *args):
        return self.run_command(
            RUNNER, lane, "--cd", str(self.workspace), "--sandbox", "read-only" if lane == '2nd-advisor' else "workspace-write",
            "--output-last-message", str(self.base / "final"), *args,
        )

    def calls(self):
        return [json.loads(line) for line in self.record.read_text().splitlines()] if self.record.exists() else []

    def test_failed_resolution_stops_before_codex_despite_old_model(self):
        self.env['LANE_MODEL'] = 'old-model'
        result = self.run_lane('missing-lane')
        self.assertEqual(result.returncode, 2)
        self.assertIn('unknown lane', result.stderr)
        self.assertEqual(self.calls(), [])

    def test_invalid_json_stops_before_codex(self):
        self.config.write_text('{broken')
        result = self.run_lane('routine')
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.calls(), [])

    def test_workspace_override_for_both_implementers_not_inherited_cwd(self):
        self.env.pop('ARCH_ADVISOR_CONFIG')
        target = self.workspace / '.arch-advisor'
        target.mkdir()
        caller = self.base / '.arch-advisor'
        caller.mkdir()
        wrong = json.loads(json.dumps(self.data))
        for lane in ('routine', 'complex'):
            self.data['lanes'][lane]['model'] = 'target-' + lane
            wrong['lanes'][lane]['model'] = 'wrong-' + lane
        (target / 'lanes.json').write_text(json.dumps(self.data))
        (caller / 'lanes.json').write_text(json.dumps(wrong))
        for lane in ('routine', 'complex'):
            with self.subTest(lane=lane):
                result = self.run_lane(lane, '--effort', 'high')
                self.assertEqual(result.returncode, 0, result.stderr)
                call = self.calls()[-1]
                self.assertEqual(Path(call['cwd']).resolve(), self.workspace.resolve())
                self.assertIn('target-' + lane, call['args'])
                self.assertNotIn('wrong-' + lane, call['args'])
                self.assertEqual(Path(call['args'][call['args'].index('--cd') + 1]).resolve(), self.workspace.resolve())

    def test_workspace_required_and_dangerous_sandbox_refused(self):
        result = self.run_command(RUNNER, 'routine', '--sandbox', 'workspace-write')
        self.assertEqual(result.returncode, 2)
        result = self.run_command(RUNNER, 'routine', '--cd', str(self.workspace), '--sandbox', 'danger-full-access')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_advisor_cannot_use_workspace_write_even_through_runner(self):
        result = self.run_command(RUNNER, '2nd-advisor', '--cd', str(self.workspace), '--sandbox', 'workspace-write')
        self.assertEqual(result.returncode, 2)
        self.assertIn('only permits read-only', result.stderr)
        self.assertEqual(self.calls(), [])

    def test_null_efforts_reject_explicit_and_allow_omission_in_every_lane(self):
        for lane in self.data['lanes']:
            self.data['lanes'][lane]['efforts'] = None
        self.save_config()
        for lane in self.data['lanes']:
            with self.subTest(lane=lane):
                before = len(self.calls())
                result = self.run_lane(lane, '--effort', 'high')
                self.assertEqual(result.returncode, 4)
                self.assertEqual(len(self.calls()), before)
                result = self.run_lane(lane)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(any(a.startswith('model_reasoning_effort=') for a in self.calls()[-1]['args']))

    def test_advisor_null_efforts_follows_same_rule(self):
        self.data['lanes']['2nd-advisor']['efforts'] = None
        self.save_config()
        result = self.run_command(ADVISOR, '--cd', str(self.workspace), '--effort', 'high')
        self.assertEqual(result.returncode, 4)
        self.assertEqual(self.calls(), [])
        result = self.run_command(ADVISOR, '--cd', str(self.workspace))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('STATUS: complete', result.stdout)

    def test_runner_preserves_exit_code_even_when_codex_prints_ok(self):
        self.env['MOCK_CODEX_MODE'] = 'failure_ok'
        result = self.run_lane('complex')
        self.assertEqual(result.returncode, 9)
        self.assertIn('OK', result.stdout)

    def test_saved_effort_reaches_cli_and_explicit_task_wins(self):
        self.data['lanes']['routine']['default_effort'] = 'medium'
        self.save_config()
        result = self.run_lane('routine')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('model_reasoning_effort=medium', self.calls()[-1]['args'])
        result = self.run_lane('routine', '--effort', 'high')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('model_reasoning_effort=high', self.calls()[-1]['args'])
        self.assertNotIn('model_reasoning_effort=medium', self.calls()[-1]['args'])

    def test_invalid_saved_default_and_null_allowlist_never_call_codex(self):
        self.data['lanes']['routine']['default_effort'] = 'ultra'
        self.save_config()
        self.assertEqual(self.run_lane('routine').returncode, 4)
        self.data['lanes']['routine'].update(default_effort='high', efforts=None)
        self.save_config()
        self.assertEqual(self.run_lane('routine').returncode, 4)
        self.assertEqual(self.calls(), [])

    def test_advisor_report_and_smoke_use_saved_default(self):
        self.data['lanes']['2nd-advisor']['default_effort'] = 'high'
        self.save_config()
        result = self.run_command(ADVISOR, '--cd', str(self.workspace))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('effort: high', result.stdout)
        self.assertIn('model_reasoning_effort=high', self.calls()[-1]['args'])
        result = self.run_command(SMOKE, '2nd-advisor', '--cd', str(self.workspace))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('effort=high', result.stdout)
        self.assertIn('model_reasoning_effort=high', self.calls()[-1]['args'])

    def test_smoke_rejects_ok_with_nonzero_exit(self):
        self.env['MOCK_CODEX_MODE'] = 'failure_ok'
        result = self.run_command(SMOKE, '--cd', str(self.workspace))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.count('FAIL codex_or_preflight_exit=9'), 3)
        self.assertNotIn(' ok ', result.stdout)

    def test_smoke_requires_final_message_not_ok_in_logs(self):
        self.env['MOCK_CODEX_MODE'] = 'stdout_only'
        result = self.run_command(SMOKE, 'routine', '--cd', str(self.workspace))
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(' ok ', result.stdout)

    def test_smoke_does_not_reuse_previous_lane_result(self):
        self.env['MOCK_NO_RESULT_MODEL'] = self.data['lanes']['complex']['model']
        result = self.run_command(SMOKE, '--cd', str(self.workspace))
        self.assertEqual(result.returncode, 1)
        self.assertIn('routine ok ', result.stdout)
        self.assertIn('complex FAIL no exact OK', result.stdout)
        self.assertIn('2nd-advisor ok ', result.stdout)

    def test_smoke_success_has_read_only_approval_policy_and_timeout(self):
        result = self.run_command(SMOKE, '--cd', str(self.workspace), '--effort', 'high')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.calls()), 3)
        for call in self.calls():
            self.assertIn('read-only', call['args'])
            self.assertIn('approval_policy="never"', call['args'])
            self.assertIn('model_reasoning_effort=high', call['args'])
        timeouts = (self.base / 'timeouts').read_text().splitlines()
        self.assertEqual(timeouts, ['600', '1800', '900'])

    def test_smoke_unknown_lane_never_calls_codex(self):
        result = self.run_command(SMOKE, 'nonexistent', '--cd', str(self.workspace))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), [])

    def test_smoke_null_efforts_and_explicit_effort_is_failure(self):
        self.data['lanes']['routine']['efforts'] = None
        self.save_config()
        result = self.run_command(SMOKE, 'routine', '--cd', str(self.workspace), '--effort', 'high')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), [])
        result = self.run_command(SMOKE, 'routine', '--cd', str(self.workspace))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dry_run_checks_configuration_without_calling_codex(self):
        result = self.run_command(SMOKE, '--cd', str(self.workspace), '--dry-run')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('DRY lane=routine', result.stdout)
        self.assertEqual(self.calls(), [])

    def test_timeout_does_not_pass_smoke(self):
        self.env['MOCK_CODEX_MODE'] = 'timeout'
        result = self.run_command(SMOKE, 'routine', '--cd', str(self.workspace))
        self.assertEqual(result.returncode, 1)
        self.assertIn('codex_or_preflight_exit=124', result.stdout)


if __name__ == '__main__':
    unittest.main()
