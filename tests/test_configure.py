import json
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='advisor-picker-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "project with spaces 'quoted'"
        self.workspace.mkdir()
        self.user = self.base / 'claude config'
        self.env = os.environ.copy()
        self.env.pop('ARCH_ADVISOR_CONFIG', None)
        self.env['CLAUDE_CONFIG_DIR'] = str(self.user)
        self.user_config = self.user / 'arch-advisor/lanes.json'
        self.project_config = self.workspace / '.arch-advisor/lanes.json'

    def configure(self, changes=None, scope='user', root=ROOT):
        return subprocess.run([sys.executable, str(root / 'scripts/configure.py'),
                               'show' if changes is None else 'set', '--scope', scope,
                               '--cd', str(self.workspace)], env=self.env, cwd=self.base,
                              input='' if changes is None else json.dumps(changes),
                              text=True, capture_output=True)

    def runner(self, lane='routine', root=ROOT, effort=None):
        args = [str(root / 'scripts/run-lane.sh'), lane, '--cd', str(self.workspace),
                '--sandbox', 'read-only', '--dry-run']
        if effort:
            args += ['--effort', effort]
        return subprocess.run(args, env=self.env, text=True, capture_output=True)

    def test_show_has_no_writes_and_default_is_inherited(self):
        result = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.user.exists())
        config = json.loads(result.stdout)['configuration']
        self.assertIsNone(config['lanes']['routine']['default_effort'])

    def test_custom_model_and_effort_saved_outside_plugin_reach_runner(self):
        result = self.configure({'lanes': {'routine': {'model': 'future-model-7', 'default_effort': 'medium'}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertTrue(receipt['active_in_workspace'])
        self.assertEqual(Path(receipt['saved_path']).resolve(), self.user_config.resolve())
        self.assertIn('model=future-model-7 effort=medium', self.runner().stdout)
        self.assertIn('effort=max', self.runner(effort='max').stdout)

    def test_preferences_survive_plugin_directory_update(self):
        result = self.configure({'lanes': {'routine': {'model': 'future-model-7', 'default_effort': 'medium'}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        replacement = self.base / 'cache version 99'
        shutil.copytree(ROOT, replacement, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        data = json.loads((replacement / 'config/lanes.json').read_text())
        data['lanes']['routine']['model'] = 'new-factory-default'
        (replacement / 'config/lanes.json').write_text(json.dumps(data))
        self.assertIn('model=future-model-7 effort=medium', self.runner(root=replacement).stdout)
        result = self.configure({'lanes': {'complex': {'default_effort': 'high'}}}, root=replacement)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('model=future-model-7', self.runner(root=replacement).stdout)

    def test_claude_selection_is_refused_and_codex_selection_creates_no_agents(self):
        result = self.configure({'claude_advisor': {'model': 'sonnet'}})
        self.assertEqual(result.returncode, 3)
        self.assertFalse(self.user.exists())
        result = self.configure({'lanes': {'routine': {'default_effort': 'high'}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.user / 'agents').exists())

    def test_second_opinion_alias_reuses_existing_override_and_effort(self):
        result = self.configure({'lanes': {'2nd-advisor': {'model': 'saved-review-model', 'default_effort': 'medium'}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.configure({'lanes': {'second-opinion': {'default_effort': 'high'}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(self.user_config.read_text())
        self.assertNotIn('second-opinion', config['lanes'])
        self.assertIn('model=saved-review-model effort=high', self.runner('second-opinion').stdout)
        self.assertIn('model=saved-review-model effort=high', self.runner('2nd-advisor').stdout)
        result = self.configure({'lanes': {'second-opinion': {'model': 'a'}, '2nd-advisor': {'model': 'b'}}})
        self.assertEqual(result.returncode, 3)
        self.assertIn('model=saved-review-model', self.runner('second-opinion').stdout)

    def test_partial_changes_preserve_unknown_settings_and_other_lanes(self):
        self.user_config.parent.mkdir(parents=True)
        data = json.loads((ROOT / 'config/lanes.json').read_text())
        data['custom_metadata'] = {'leave': 'alone'}
        data['lanes']['routine']['timeout_seconds'] = 17
        self.user_config.write_text(json.dumps(data))
        result = self.configure({'lanes': {'complex': {'default_effort': 'max'}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(self.user_config.read_text())
        self.assertEqual(saved['custom_metadata'], data['custom_metadata'])
        self.assertEqual(saved['lanes']['routine'], data['lanes']['routine'])

    def test_project_scope_and_masked_user_scope_are_reported(self):
        result = self.configure({'lanes': {'routine': {'model': 'project-model'}}}, scope='project')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.configure({'lanes': {'routine': {'model': 'user-model'}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['active_in_workspace'])
        self.assertIn('model=project-model', self.runner().stdout)
        # User scope did not freeze the unrelated project's values.
        saved = json.loads(self.user_config.read_text())
        self.assertEqual(saved['lanes']['routine']['model'], 'user-model')
        self.assertFalse((self.workspace / '.claude/agents').exists())

    def test_invalid_model_effort_or_lane_never_changes_saved_selections(self):
        result = self.configure({'lanes': {'routine': {'default_effort': 'low'}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.user_config.read_bytes()
        for changes in (
            {'lanes': {'routine': {'model': 'bad\neffort: max'}}},
            {'lanes': {'routine': {'model': '$(touch secret)'}}},
            {'lanes': {'routine': {'default_effort': 'ultra'}}},
            {'lanes': {'missing': {'default_effort': 'low'}}},
            {'claude_advisor': {'model': 'bad`command`'}},
        ):
            with self.subTest(changes=changes):
                result = self.configure(changes)
                self.assertEqual(result.returncode, 3)
                self.assertEqual(self.user_config.read_bytes(), before)

    def test_null_allowlist_refuses_saved_effort_but_inherit_clears_it(self):
        self.user_config.parent.mkdir(parents=True)
        data = json.loads((ROOT / 'config/lanes.json').read_text())
        data['lanes']['routine']['efforts'] = None
        self.user_config.write_text(json.dumps(data))
        result = self.configure({'lanes': {'routine': {'default_effort': 'high'}}})
        self.assertEqual(result.returncode, 3)
        result = self.configure({'lanes': {'routine': {'default_effort': 'inherit'}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('effort=<codex default>', self.runner().stdout)

    def test_migration_removes_only_marked_legacy_agents_and_preserves_claude_settings(self):
        directory = self.user / 'agents'
        directory.mkdir(parents=True)
        conflict = directory / 'arch-advisor-selected-max.md'
        conflict.write_text('My own agent\n')
        legacy = directory / 'arch-advisor-selected.md'
        legacy.write_text('<!-- arch-advisor:configure managed agent -->\nOld generated agent\n')
        unrelated = self.user / 'settings.json'
        unrelated.write_text('{"model":"my-chosen-claude","effortLevel":"high"}')
        self.user_config.parent.mkdir(parents=True)
        data = json.loads((ROOT / 'config/lanes.json').read_text())
        data['claude_advisor'] = {'model': 'opus', 'default_effort': 'max'}
        self.user_config.write_text(json.dumps(data))
        result = self.configure({'lanes': {'routine': {'default_effort': 'medium'}}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('claude_advisor', json.loads(self.user_config.read_text()))
        self.assertEqual(json.loads(result.stdout)['removed_legacy_agents'], [str(legacy.resolve())])
        self.assertEqual(list(directory.iterdir()), [conflict])
        self.assertEqual(conflict.read_text(), 'My own agent\n')
        self.assertEqual(unrelated.read_text(), '{"model":"my-chosen-claude","effortLevel":"high"}')

    def test_explicit_environment_override_is_not_silently_ignored(self):
        self.env['ARCH_ADVISOR_CONFIG'] = str(ROOT / 'config/lanes.json')
        result = self.configure({'lanes': {'routine': {'default_effort': 'high'}}})
        self.assertEqual(result.returncode, 3)
        self.assertIn('Unset it', result.stderr)
        self.assertFalse(self.user_config.exists())

    def test_failed_save_restores_retired_agents_and_previous_config(self):
        directory = self.user / 'agents'
        directory.mkdir(parents=True)
        legacy = directory / 'arch-advisor-selected.md'
        old_agent = '<!-- arch-advisor:configure managed agent -->\nOld agent\n'
        legacy.write_text(old_agent)
        self.user_config.parent.mkdir(parents=True)
        old_config = (ROOT / 'config/lanes.json').read_text()
        self.user_config.write_text(old_config)
        spec = importlib.util.spec_from_file_location('picker', ROOT / 'scripts/configure.py')
        picker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(picker)
        original_write = picker.atomic_write

        def failing_write(path, content):
            if path.resolve() == self.user_config.resolve():
                raise OSError('simulated write failure')
            original_write(path, content)

        args = ['configure.py', 'set', '--cd', str(self.workspace), '--scope', 'user']
        changes = io.StringIO('{"lanes":{"routine":{"default_effort":"high"}}}')
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(sys, 'argv', args), \
                mock.patch.object(sys, 'stdin', changes), mock.patch.object(picker, 'atomic_write', failing_write):
            with self.assertRaisesRegex(OSError, 'simulated write failure'):
                picker.main()
        self.assertEqual(legacy.read_text(), old_agent)
        self.assertEqual(self.user_config.read_text(), old_config)


if __name__ == '__main__':
    unittest.main()
