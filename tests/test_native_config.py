import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = 'arch-advisor@arch-advisor'


class NativeConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-settings-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace with spaces 'quoted'"
        self.workspace.mkdir()
        self.user = self.base / 'claude config'
        self.user.mkdir()
        self.settings = self.user / 'settings.json'
        self.user_lane = self.user / 'arch-advisor/lanes.json'
        self.data = json.loads((ROOT / 'config/lanes.json').read_text())
        self.env = os.environ.copy()
        self.env.pop('ARCH_ADVISOR_CONFIG', None)
        self.env['CLAUDE_CONFIG_DIR'] = str(self.user)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.record = self.base / 'call.json'
        mock = self.bin / 'codex'
        mock.write_text('''#!/usr/bin/env python3
import json, pathlib, sys
args = sys.argv[1:]
pathlib.Path(__file__).parent.parent.joinpath('call.json').write_text(json.dumps(args))
if '--output-last-message' in args:
    pathlib.Path(args[args.index('--output-last-message')+1]).write_text('OK')
print('OK')
''')
        mock.chmod(0o755)
        self.env['PATH'] = str(self.bin) + os.pathsep + self.env['PATH']

    def options(self, values):
        data = {'model': 'my-chosen-claude', 'effortLevel': 'high',
                'enabledPlugins': {PLUGIN_ID: True},
                'pluginConfigs': {PLUGIN_ID: {'options': values}, 'other-plugin@other': {'options': {'keep': 'value'}}}}
        self.settings.write_text(json.dumps(data))

    def legacy(self, path=None):
        path = path or self.user_lane
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.data))

    def command(self, script, *args, root=ROOT):
        return subprocess.run([str(root / 'scripts' / script), *args], env=self.env,
                              cwd=self.workspace, input='Reply OK', text=True, capture_output=True)

    def run_lane(self, lane='routine', *args, root=ROOT):
        return self.command('run-lane.sh', lane, '--cd', str(self.workspace),
                            '--sandbox', 'read-only', *args, root=root)

    def test_unset_native_preferences_preserve_existing_configuration(self):
        self.data['lanes']['routine'].update(model='old-model', default_effort='medium')
        self.legacy()
        self.options({})
        result = self.run_lane()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('old-model', json.loads(self.record.read_text()))
        self.assertIn('model_reasoning_effort=medium', json.loads(self.record.read_text()))

    def test_native_model_effort_reach_cli_without_writes_or_claude_call(self):
        self.options({'routine_model': 'gpt-6-astra', 'routine_effort': 'high'})
        before = self.settings.read_bytes()
        result = self.run_lane()
        self.assertEqual(result.returncode, 0, result.stderr)
        call = json.loads(self.record.read_text())
        self.assertIn('gpt-6-astra', call)
        self.assertIn('model_reasoning_effort=high', call)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertFalse((self.user / 'agents').exists())
        self.assertFalse(self.user_lane.exists())
        self.assertFalse((ROOT / 'skills/configure/SKILL.md').exists())

    def test_task_effort_wins_and_inherit_clears_legacy_effort(self):
        self.data['lanes']['routine']['default_effort'] = 'max'
        self.legacy()
        self.options({'routine_effort': 'inherit'})
        self.assertEqual(self.run_lane().returncode, 0)
        self.assertFalse(any(a.startswith('model_reasoning_effort=') for a in json.loads(self.record.read_text())))
        self.options({'routine_effort': 'medium'})
        self.assertEqual(self.run_lane('routine', '--effort', 'high').returncode, 0)
        self.assertIn('model_reasoning_effort=high', json.loads(self.record.read_text()))

    def test_custom_future_models_and_alias_share_native_preferences(self):
        self.options({'second_opinion_model': 'custom', 'second_opinion_custom_model': 'future-model-99',
                      'second_opinion_effort': 'max'})
        for name in ('second-opinion', '2nd-advisor'):
            result = self.run_lane(name)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('future-model-99', json.loads(self.record.read_text()))
        result = self.command('second-opinion.sh', '--cd', str(self.workspace))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('future-model-99, effort: max', result.stdout)

    def test_invalid_custom_or_saved_options_fail_before_codex(self):
        for values in ({'routine_model': 'custom'},
                       {'routine_model': 'custom', 'routine_custom_model': '$(touch secret)'},
                       {'routine_model': 'unknown-choice'}, {'routine_effort': 'ultra'},
                       {'routine_model': None}):
            with self.subTest(values=values):
                self.options(values)
                self.assertEqual(self.run_lane().returncode, 3)
                self.assertFalse(self.record.exists())

    def test_null_efforts_refuse_native_default_without_dropping_it(self):
        self.data['lanes']['routine']['efforts'] = None
        self.legacy()
        self.options({'routine_effort': 'high'})
        self.assertEqual(self.run_lane().returncode, 4)
        self.assertFalse(self.record.exists())
        self.options({'routine_effort': 'inherit'})
        self.assertEqual(self.run_lane().returncode, 0)

    def test_project_and_explicit_overrides_keep_priority(self):
        self.options({'routine_model': 'gpt-6-astra', 'routine_effort': 'max'})
        self.data['lanes']['routine'].update(model='project-model', default_effort='low')
        self.legacy(self.workspace / '.arch-advisor/lanes.json')
        self.assertEqual(self.run_lane().returncode, 0)
        call = json.loads(self.record.read_text())
        self.assertIn('project-model', call)
        self.assertIn('model_reasoning_effort=low', call)
        self.data['lanes']['routine']['model'] = 'explicit-model'
        explicit = self.base / 'explicit.json'
        self.legacy(explicit)
        self.env['ARCH_ADVISOR_CONFIG'] = str(explicit)
        self.assertEqual(self.run_lane().returncode, 0)
        self.assertIn('explicit-model', json.loads(self.record.read_text()))

    def test_live_settings_read_survives_plugin_update_and_partial_choices(self):
        self.options({'routine_model': 'gpt-6-astra', 'routine_effort': 'medium'})
        replacement = self.base / 'version 99'
        shutil.copytree(ROOT, replacement, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        self.assertEqual(self.run_lane(root=replacement).returncode, 0)
        self.assertIn('gpt-6-astra', json.loads(self.record.read_text()))
        self.options({'routine_model': 'gpt-6.1-sol', 'routine_effort': 'high'})
        self.assertEqual(self.run_lane(root=replacement).returncode, 0)
        self.assertIn('gpt-6.1-sol', json.loads(self.record.read_text()))
        self.assertEqual(self.run_lane('complex', root=replacement).returncode, 0)
        self.assertFalse(any(a.startswith('model_reasoning_effort=') for a in json.loads(self.record.read_text())))

    def test_list_and_smoke_reflect_saved_native_model_effort(self):
        self.options({'routine_model': 'gpt-6-astra', 'routine_effort': 'high'})
        listing = self.command('lane.sh', 'list')
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn('model:   gpt-6-astra', listing.stdout)
        self.assertIn(str(self.settings), listing.stdout)
        result = self.command('smoke.sh', 'routine', '--cd', str(self.workspace))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('routine ok gpt-6-astra effort=high', result.stdout)

    def test_malformed_settings_fail_without_printing_contents(self):
        self.settings.write_text('{"unrelated_secret":"do-not-print", broken')
        result = self.run_lane()
        self.assertEqual(result.returncode, 3)
        self.assertNotIn('do-not-print', result.stderr + result.stdout)
        self.assertFalse(self.record.exists())

    @unittest.skipUnless(shutil.which('claude'), 'Claude Code CLI is needed for native integration check')
    def test_real_native_cli_exposes_pickers_saves_partial_values_and_drives_runner(self):
        plugins = self.user / 'plugins'
        plugins.mkdir()
        (plugins / 'installed_plugins.json').write_text(json.dumps({'version': 2, 'plugins': {
            PLUGIN_ID: [{'scope': 'user', 'installPath': str(ROOT), 'version': '6.3.0',
                         'installedAt': '2026-10-07T00:00:00.000Z', 'lastUpdated': '2026-10-07T00:00:00.000Z'}]}}))
        (plugins / 'known_marketplaces.json').write_text(json.dumps({'arch-advisor': {
            'source': {'source': 'directory', 'path': str(ROOT)}, 'installLocation': str(ROOT),
            'lastUpdated': '2026-10-07T00:00:00.000Z'}}))
        self.options({})
        command = ['claude', 'plugin', 'configure', PLUGIN_ID, '--json']
        listing = subprocess.run(command, cwd=self.workspace, env=self.env, text=True, capture_output=True)
        self.assertEqual(listing.returncode, 0, listing.stderr)
        choices = json.loads(listing.stdout)['choices']
        self.assertEqual(set(choices), {'routine_model', 'routine_effort', 'complex_model', 'complex_effort',
                                        'second_opinion_model', 'second_opinion_effort'})
        self.assertIn('custom', choices['routine_model'])
        selected = {'routine_model': 'gpt-6-astra', 'routine_effort': 'high'}
        saved = subprocess.run(command + ['--values-stdin'], cwd=self.workspace, env=self.env,
                               input=json.dumps(selected), text=True, capture_output=True)
        self.assertEqual(saved.returncode, 0, saved.stderr)
        saved = subprocess.run(command + ['--values-stdin'], cwd=self.workspace, env=self.env,
                               input='{"routine_effort":"medium"}', text=True, capture_output=True)
        self.assertEqual(saved.returncode, 0, saved.stderr)
        settings = json.loads(self.settings.read_text())
        self.assertEqual(settings['model'], 'my-chosen-claude')
        self.assertEqual(settings['pluginConfigs'][PLUGIN_ID]['options']['routine_model'], 'gpt-6-astra')
        self.assertEqual(self.run_lane().returncode, 0)
        call = json.loads(self.record.read_text())
        self.assertIn('gpt-6-astra', call)
        self.assertIn('model_reasoning_effort=medium', call)


if __name__ == '__main__':
    unittest.main()
