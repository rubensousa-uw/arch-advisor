#!/usr/bin/env python3
"""Read native Claude plugin options; never write settings or call a model."""
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = 'arch-advisor@arch-advisor'
PREFIXES = {'routine': 'routine', 'complex': 'complex', '2nd-advisor': 'second_opinion'}


def read_object(path):
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError) as error:
        # Don't include full settings or decoder fragments: other plugins may
        # have credentials in the same file.
        raise ValueError(f'Cannot read valid JSON from {path}') from error
    if not isinstance(data, dict):
        raise ValueError(f'Expected an object in {path}')
    return data


def effective(config_path, lane=None):
    config = read_object(config_path)
    # Preserve deliberately scoped/explicit lane overrides. Native user choices
    # take precedence over legacy user preferences and the shipped defaults.
    if os.environ.get('ARCH_ADVISOR_CONFIG') or config_path.resolve() == (Path.cwd() / '.arch-advisor/lanes.json').resolve():
        return config
    user_root = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude'))).expanduser()
    settings_path = user_root / 'settings.json'
    if not settings_path.exists():
        return config
    settings = read_object(settings_path)
    plugins = settings.get('pluginConfigs', {})
    if not isinstance(plugins, dict):
        raise ValueError('pluginConfigs must be an object')
    plugin = plugins.get(PLUGIN_ID, {})
    if not isinstance(plugin, dict) or not isinstance(plugin.get('options', {}), dict):
        raise ValueError(f'Invalid native options for {PLUGIN_ID}')
    options = plugin.get('options', {})
    schema = read_object(ROOT / '.claude-plugin/plugin.json')['userConfig']
    for name, prefix in PREFIXES.items():
        if lane is not None and name != lane:
            continue
        if name not in config.get('lanes', {}):
            continue
        entry = config['lanes'][name]
        model_key, effort_key = prefix + '_model', prefix + '_effort'
        selected = options.get(model_key, 'default')
        effort = options.get(effort_key, 'default')
        for key, value in ((model_key, selected), (effort_key, effort)):
            if value not in schema[key]['options']:
                raise ValueError(f'Invalid saved option {key}; choose a value in /config')
        if selected != 'default':
            model = options.get(prefix + '_custom_model', '') if selected == 'custom' else selected
            if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}', model):
                raise ValueError(f'{model_key}: custom needs a valid custom model ID in /config')
            entry['model'] = model
        if effort != 'default':
            entry['default_effort'] = None if effort == 'inherit' else effort
        if selected != 'default' or effort != 'default':
            entry['_native_options_source'] = str(settings_path)
    return config


if __name__ == '__main__':
    try:
        if len(sys.argv) not in (2, 3):
            raise ValueError('Usage: native-config.py CONFIG [LANE]')
        print(json.dumps(effective(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) == 3 else None)))
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f'arch-advisor: {error}', file=sys.stderr)
        sys.exit(3)
