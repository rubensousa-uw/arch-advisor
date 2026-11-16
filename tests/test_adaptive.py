"""Local evidence and consent tests; synthetic usage, no inference or credentials."""
import argparse
import contextlib
import io
import json
import os
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'codex/scripts'))
import adaptive
import advisor


class AdaptiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.work = self.base / 'project'
        self.work.mkdir()
        env = patch.dict(os.environ, ARCH_ADVISOR_STATE_DIR=str(self.base / 'state'),
                         CODEX_HOME=str(self.base / 'codex'), CLAUDE_CONFIG_DIR=str(self.base / 'claude'))
        env.start()
        self.addCleanup(env.stop)
        for key in ('ARCH_ADVISOR_CONFIG', 'ARCH_ADVISOR_CODEX_CONFIG', 'ARCH_ADVISOR_TELEMETRY',
                    'ARCH_ADVISOR_TASK_ID', 'ARCH_ADVISOR_TASK_CLASS'):
            os.environ.pop(key, None)
        self.baseline = adaptive.current(self.work, 'codex', 'routine')
        self.new = dict(provider='codex', model='gpt-new', effort='high')
        self.allowed = {(c['provider'], c['model']) for c in (self.baseline, self.new)}
        self.args = argparse.Namespace(cd=self.work, host='codex', role='routine', context='general',
            metric='duration_seconds', quota_key=None, min_samples=10, confidence=.95, quality_margin=0,
            days=30, providers=['codex'], available=None, catalog=None, current=None)
        catalog = {'openai': {'models': {m: {'tool_call': True, 'modalities': {'output': ['text']}}
                                      for _, m in self.allowed}}}
        self.catalog = self.base / 'catalog.json'
        self.catalog.write_text(json.dumps(catalog))

    def run_cli(self, *args, host='codex', **kwargs):
        script = ROOT / ('codex/scripts/adaptive.py' if host == 'codex' else 'scripts/adaptive.py')
        return subprocess.run([sys.executable, str(script), '--cd', str(self.work), *args],
                              capture_output=True, text=True, **kwargs)

    def record(self, choice=None, outcome=None, **extra):
        row = dict(role='routine', context='general', **(choice or self.baseline),
                   exit_code=0, duration_seconds=10, tokens=100)
        row.update(extra)
        row = adaptive.record(self.work, self.args.host, row)
        if outcome:
            adaptive.feedback(self.work, self.args.host, row['task_id'], outcome, 'test:run-1')
        return row

    def observations(self):
        with adaptive.connect() as db:
            return adaptive.tasks(db, adaptive.scope_id(self.work, self.args.host), 30)[0]

    def seed(self):
        # Enough contrasting outcomes for strict confidence bounds, not a baked-in model ranking.
        for i in range(40):
            self.record(outcome='accepted' if i % 2 else 'rejected', duration_seconds=100)
            self.record(self.new, outcome='accepted', duration_seconds=20)

    def propose(self):
        with patch.object(adaptive, 'availability', return_value=(self.allowed, [])):
            return adaptive.propose(self.args)

    def test_zero_exit_is_unknown_and_raw_content_refused(self):
        self.record()
        self.assertEqual(self.observations()[0]['outcome'], 'unknown')
        with self.assertRaises(adaptive.AdaptiveError):
            self.record(prompt='private data')
        db = self.base / 'state/history.sqlite3'
        self.assertEqual(db.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(b'private data', db.read_bytes())

    def test_invalid_numbers_and_duplicates(self):
        for value in (-1, float('nan'), float('inf'), True, '2'):
            with self.assertRaises(adaptive.AdaptiveError):
                self.record(tokens=value)
        row = self.record(id='unique')
        with self.assertRaises(adaptive.AdaptiveError):
            self.record(id=row['id'])

    def test_retries_include_escalation_cost_and_invalidate_feedback(self):
        first = self.record(outcome='accepted')
        self.record(self.new, task_id=first['task_id'], role='complex', duration_seconds=30)
        task = self.observations()[0]
        self.assertEqual((task['duration_seconds'], task['attempts'], task['role']), (40, 2, 'routine'))
        self.assertEqual(task['option'], self.baseline)
        self.assertEqual(task['outcome'], 'unknown')
        with self.assertRaises(adaptive.AdaptiveError):
            self.record(task_id=first['task_id'], context='different')

    def test_project_and_host_isolation(self):
        row = self.record()
        with self.assertRaises(adaptive.AdaptiveError):
            adaptive.feedback(self.work, 'claude', row['task_id'], 'accepted', 'tests:1')
        other = self.base / 'other'
        other.mkdir()
        with adaptive.connect() as db:
            self.assertEqual(adaptive.tasks(db, adaptive.scope_id(other, 'codex'), 30)[0], [])

    def test_usage_parsers_and_incomplete_usage(self):
        usage = adaptive.Usage()
        usage.feed({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'cached_input_tokens': 5, 'output_tokens': 3}}, 'codex')
        self.assertEqual(usage.values['tokens'], 13)
        usage.feed({'type': 'turn.completed', 'usage': {'input_tokens': 2}}, 'codex')
        self.assertIsNone(usage.values['tokens'])
        usage = adaptive.Usage()
        usage.feed({'type': 'result', 'usage': {'input_tokens': 10, 'output_tokens': 3, 'cache_read_input_tokens': 4}, 'total_cost_usd': .2}, 'claude')
        self.assertEqual((usage.values['tokens'], usage.values['api_cost_usd']), (17, .2))
        usage = adaptive.Usage()
        usage.feed({'usage': {'opencode': {'tokens': {'input': 10, 'output': 2}, 'list_cost': .5,
                    'billing_source': 'go', 'allowance': .01}}}, 'opencode')
        self.assertEqual((usage.values['tokens'], usage.values['quota_units'], usage.values['quota_key']),
                         (12, .01, 'opencode:go:allowance-usd'))
        usage.feed('not json with a secret', 'codex')
        self.assertNotIn('secret', str(usage.values))

    def test_capture_preserves_stdin_stdout_exit_and_skips_disabled(self):
        command = [sys.executable, '-c', 'import sys; print(sys.stdin.read()); sys.exit(9)']
        result = self.run_cli('capture', '--role', 'routine', '--provider', 'codex', '--model', 'gpt-new', '--', *command, input='PRIVATE')
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertIn('PRIVATE', result.stdout)
        self.assertIn('ADAPTIVE_TASK_ID:', result.stderr)
        self.assertNotIn(b'PRIVATE', (self.base / 'state/history.sqlite3').read_bytes())
        self.assertEqual(self.observations()[0]['outcome'], 'unknown')
        with patch.dict(os.environ, ARCH_ADVISOR_TELEMETRY='0'):
            self.run_cli('capture', '--role', 'routine', '--provider', 'codex', '--model', 'gpt-new', '--', *command)
        self.assertEqual(len(self.observations()), 1)

    def test_insufficient_or_equal_quality_evidence_does_not_promote(self):
        for _ in range(10):
            self.record(outcome='accepted', duration_seconds=100)
            self.record(self.new, outcome='accepted', duration_seconds=10)
        report = self.propose()
        self.assertEqual(report['suggestions'], [])  # Small equal samples do not prove noninferiority.
        self.assertEqual(report['personal_frontier'], [self.new])

    def test_active_subscriptions_filter_before_pareto(self):
        self.seed()
        inactive = dict(provider='claude', model='claude-unavailable', effort='high')
        for _ in range(40):
            self.record(inactive, outcome='accepted', duration_seconds=1)
        report = self.propose()
        self.assertEqual(report['personal_frontier'], [self.new])
        self.assertEqual(len(report['suggestions']), 1)

    def test_proposal_does_not_change_models_until_accept_and_undo(self):
        self.seed()
        report = self.propose()
        self.assertFalse(report['auto_adjust'])
        self.assertEqual(adaptive.current(self.work, 'codex', 'routine'), self.baseline)
        item = report['suggestions'][0]
        self.assertGreater(item['saving_interval'][0], 0)
        adaptive.accept(self.args, item['id'], self.allowed)
        self.assertEqual(adaptive.current(self.work, 'codex', 'routine'), self.new)
        self.args.id, self.args.action = item['id'], 'undo'
        adaptive.decide(self.args)
        self.assertEqual(adaptive.current(self.work, 'codex', 'routine'), self.baseline)

    def test_rejection_persists_and_can_reopen(self):
        self.seed()
        item = self.propose()['suggestions'][0]
        self.args.id, self.args.action = item['id'], 'reject'
        adaptive.decide(self.args)
        self.assertEqual(self.propose()['suggestions'][0]['status'], 'rejected')
        with self.assertRaises(adaptive.AdaptiveError):
            adaptive.accept(self.args, item['id'], self.allowed)
        self.args.action = 'reopen'
        adaptive.decide(self.args)
        adaptive.accept(self.args, item['id'], self.allowed)

    def test_stale_evidence_and_unavailable_model_are_refused(self):
        self.seed()
        item = self.propose()['suggestions'][0]
        with self.assertRaises(adaptive.AdaptiveError):
            adaptive.accept(self.args, item['id'], set())
        self.record()
        with self.assertRaises(adaptive.AdaptiveError):
            adaptive.accept(self.args, item['id'], self.allowed)

    def test_failed_decision_write_restores_configuration(self):
        self.seed()
        item = self.propose()['suggestions'][0]
        original = adaptive.connect
        class FailingConnection:
            def __init__(self, db): self.db = db
            def execute(self, sql, *args):
                if sql.startswith("UPDATE proposals SET status='accepted'"):
                    raise sqlite3.OperationalError('simulated disk failure')
                return self.db.execute(sql, *args)
            def commit(self): self.db.commit()
        @contextlib.contextmanager
        def failing():
            with original() as db:
                yield FailingConnection(db)
        with patch.object(adaptive, 'connect', failing):
            with self.assertRaises(sqlite3.OperationalError):
                adaptive.accept(self.args, item['id'], self.allowed)
        self.assertEqual(adaptive.current(self.work, 'codex', 'routine'), self.baseline)
        self.assertFalse((self.work / '.arch-advisor/codex.json').exists())

    def test_stale_current_settings_are_refused(self):
        self.seed()
        item = self.propose()['suggestions'][0]
        changed = dict(self.baseline, model='user-changed-model')
        adaptive.change_project(self.work, 'codex', 'routine', changed)
        with self.assertRaises(adaptive.AdaptiveError):
            adaptive.accept(self.args, item['id'], self.allowed)
        self.assertEqual(adaptive.current(self.work, 'codex', 'routine'), changed)

    def test_changed_configuration_is_not_overwritten(self):
        self.seed()
        item = self.propose()['suggestions'][0]
        adaptive.accept(self.args, item['id'], self.allowed)
        target = self.work / '.arch-advisor/codex.json'
        target.write_text(target.read_text() + '\n')
        self.args.id, self.args.action = item['id'], 'undo'
        with self.assertRaises(adaptive.AdaptiveError):
            adaptive.decide(self.args)

    def test_auto_requires_acknowledgment_and_warns_when_applying(self):
        refused = self.run_cli('auto-adjust', 'enable')
        self.assertEqual(refused.returncode, 4)
        self.assertIn('WARNING', refused.stderr)
        self.assertFalse(json.loads(self.run_cli('auto-adjust', 'status').stdout)['auto_adjust'])
        enabled = self.run_cli('auto-adjust', 'enable', '--acknowledge')
        self.assertEqual(enabled.returncode, 0, enabled.stderr)
        self.seed()
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            report = self.propose()
        self.assertIn('WARNING', output.getvalue())
        self.assertEqual(report['auto_applied']['status'], 'accepted')
        self.assertEqual(adaptive.current(self.work, 'codex', 'routine'), self.new)

    def test_offline_catalog_never_auto_applies(self):
        self.run_cli('auto-adjust', 'enable', '--acknowledge')
        self.seed()
        self.args.catalog = self.catalog
        with contextlib.redirect_stderr(io.StringIO()):
            report = self.propose()
        self.assertNotIn('auto_applied', report)
        self.assertEqual(adaptive.current(self.work, 'codex', 'routine'), self.baseline)

    def test_missing_metric_and_quota_units_are_not_guessed(self):
        for _ in range(12):
            self.record(outcome='accepted', quota_units=1, quota_key='codex:plan:unit')
            self.record(self.new, outcome='accepted', quota_units=.1, quota_key='opencode:go:allowance-usd')
        self.args.metric, self.args.quota_key = 'quota_units', 'codex:plan:unit'
        report = self.propose()
        self.assertEqual(report['personal_frontier'], [self.baseline])
        self.assertEqual(report['suggestions'], [])
        self.assertEqual(self.run_cli('suggest', '--role', 'routine', '--metric', 'quota_units').returncode, 4)

    def test_manual_verifier_proposals_can_be_accepted_without_configuration(self):
        self.args.role = 'verification'
        baseline_file = self.base / 'current.json'
        baseline_file.write_text(json.dumps(self.baseline))
        self.args.current = baseline_file
        for i in range(40):
            self.record(outcome='accepted' if i % 2 else 'rejected', role='verification', duration_seconds=100)
            self.record(self.new, outcome='accepted', role='verification', duration_seconds=10)
        item = self.propose()['suggestions'][0]
        self.assertFalse(item['route_supported'])
        result = adaptive.accept(self.args, item['id'], self.allowed)
        self.assertEqual(result['status'], 'accepted-manual')
        self.assertFalse((self.work / '.arch-advisor').exists())

    def test_claude_project_changes_preserve_other_lanes(self):
        old = adaptive.current(self.work, 'claude', 'complex')
        adaptive.change_project(self.work, 'claude', 'routine', self.new)
        self.assertEqual(adaptive.current(self.work, 'claude', 'routine'), self.new)
        self.assertEqual(adaptive.current(self.work, 'claude', 'complex'), old)

    def test_symlinks_and_override_are_not_written(self):
        other = self.base / 'other'
        other.mkdir()
        (self.work / '.arch-advisor').symlink_to(other, target_is_directory=True)
        with self.assertRaises(adaptive.AdaptiveError):
            adaptive.change_project(self.work, 'codex', 'routine', self.new)
        (self.work / '.arch-advisor').unlink()
        with patch.dict(os.environ, ARCH_ADVISOR_CODEX_CONFIG='override.json'):
            with self.assertRaises(adaptive.AdaptiveError):
                adaptive.change_project(self.work, 'codex', 'routine', self.new)

    def test_native_runners_collect_usage_but_not_dry_runs_or_probes(self):
        binary = self.base / 'bin'
        binary.mkdir()
        executable = binary / 'codex'
        executable.write_text("#!/usr/bin/env python3\nimport json, pathlib, sys\nargs=sys.argv[1:]\nsys.stdin.read()\nif '--output-last-message' in args: pathlib.Path(args[args.index('--output-last-message')+1]).write_text('OK')\nprint(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':23}}))\n")
        executable.chmod(0o755)
        with patch.dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH']):
            for host in ('claude', 'codex'):
                runner = ([str(ROOT / 'scripts/run-lane.sh'), 'routine', '--sandbox', 'read-only', '--cd', str(self.work)]
                          if host == 'claude' else [sys.executable, str(ROOT / 'codex/scripts/advisor.py'), 'run', 'routine', '--cd', str(self.work)])
                result = subprocess.run(runner, input='PRIVATE', text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('ADAPTIVE_TASK_ID:', result.stderr)
                result = subprocess.run(runner + ['--dry-run'], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                smoke = ([str(ROOT / 'scripts/smoke.sh'), 'routine', '--cd', str(self.work)] if host == 'claude'
                         else [sys.executable, str(ROOT / 'codex/scripts/advisor.py'), 'smoke', 'routine', '--cd', str(self.work)])
                result = subprocess.run(smoke, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                with adaptive.connect() as db:
                    rows = adaptive.tasks(db, adaptive.scope_id(self.work, host), 30)[0]
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0]['tokens'], rows[0]['outcome']), (123, 'unknown'))
        self.assertNotIn(b'PRIVATE', (self.base / 'state/history.sqlite3').read_bytes())

    def test_cli_catalog_failure_is_clean_and_entrypoints_work(self):
        result = self.run_cli('suggest', '--role', 'routine', '--providers', 'codex', '--catalog', str(self.base / 'absent'))
        self.assertEqual(result.returncode, 4)
        self.assertNotIn('Traceback', result.stderr)
        result = self.run_cli('suggest', '--role', 'routine', '--providers', 'codex', '--catalog', str(self.catalog))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['suggestions'], [])
        result = self.run_cli('history', host='claude')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, str(ROOT / 'codex/scripts/advisor.py'), 'adaptive', '--cd', str(self.work), 'history'], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
