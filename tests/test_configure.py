import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

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

    def test_claude_native_definitions_apply_model_effort_and_read_only_tools(self):
        result = self.configure({'claude_advisor': {'model': 'claude-future-7', 'default_effort': 'high'}})
        self.assertEqual(result.returncode, 0, result.stderr)
        agent_dir = self.user / 'agents'
        definition = (agent_dir / 'arch-advisor-selected.md').read_text()
        self.assertIn('model: "claude-future-7"', definition)
        self.assertIn('effort: high\n', definition)
        self.assertIn('tools: Read, Grep, Glob\n', definition)
        self.assertNotIn('This agent pins', definition)
        self.assertNotIn(str(ROOT), definition)
        variant = (agent_dir / 'arch-advisor-selected-max.md').read_text()
        self.assertIn('effort: max\n', variant)
        result = self.configure({'claude_advisor': {'model': 'sonnet', 'default_effort': 'inherit'}})
        self.assertEqual(result.returncode, 0, result.stderr)
        definition = (agent_dir / 'arch-advisor-selected.md').read_text()
        self.assertIn('model: "sonnet"', definition)
        self.assertNotIn('effort:', definition.split('---', 2)[1])

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
        result = self.configure({'claude_advisor': {'default_effort': 'medium'}}, scope='project')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.workspace / '.claude/agents/arch-advisor-selected.md').exists())

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

    def test_unmanaged_agent_conflict_preserves_configuration_and_agents(self):
        directory = self.user / 'agents'
        directory.mkdir(parents=True)
        conflict = directory / 'arch-advisor-selected-max.md'
        conflict.write_text('My own agent\n')
        result = self.configure({'claude_advisor': {'model': 'sonnet'}})
        self.assertEqual(result.returncode, 3)
        self.assertFalse(self.user_config.exists())
        self.assertEqual(list(directory.iterdir()), [conflict])
        self.assertEqual(conflict.read_text(), 'My own agent\n')

    def test_explicit_environment_override_is_not_silently_ignored(self):
        self.env['ARCH_ADVISOR_CONFIG'] = str(ROOT / 'config/lanes.json')
        result = self.configure({'lanes': {'routine': {'default_effort': 'high'}}})
        self.assertEqual(result.returncode, 3)
        self.assertIn('Unset it', result.stderr)
        self.assertFalse(self.user_config.exists())


if __name__ == '__main__':
    unittest.main()
