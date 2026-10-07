#!/usr/bin/env python3
"""Persistent selections for /arch-advisor:configure (macOS and Linux)."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
MARKER = '<!-- arch-advisor:configure managed agent -->'


def load(path):
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get('lanes'), dict):
        raise ValueError(f'Invalid lanes configuration: {path}')
    return data


def effective(workspace):
    result = subprocess.run([str(ROOT / 'scripts/lane.sh'), 'config-path'],
                            cwd=workspace, text=True, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return Path(result.stdout.strip())


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def model_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}', value):
        raise ValueError('Model must be a single model ID or alias, without shell syntax or whitespace')
    return value


def effort_value(value):
    if value in (None, 'inherit'):
        return None
    if value not in EFFORTS:
        raise ValueError(f'Invalid default effort: {value!r}')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('show', 'set'))
    parser.add_argument('--cd', type=Path, required=True)
    parser.add_argument('--scope', choices=('user', 'project'), default='user')
    args = parser.parse_args()
    workspace = args.cd.resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError('--cd must be a directory')
    user_root = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude'))).expanduser().resolve()
    target = (user_root / 'arch-advisor' if args.scope == 'user' else workspace / '.arch-advisor') / 'lanes.json'
    agent_dir = user_root / 'agents' if args.scope == 'user' else workspace / '.claude/agents'
    source = effective(workspace)
    if args.command == 'show':
        print(json.dumps({'effective_path': str(source), 'save_path': str(target),
                          'configuration': load(source),
                          'scope_configuration': load(target) if target.exists() else None}, indent=2))
        return
    if os.environ.get('ARCH_ADVISOR_CONFIG'):
        raise ValueError('ARCH_ADVISOR_CONFIG overrides the picker. Unset it before configuring user/project selections.')
    changes = json.load(sys.stdin)
    if not isinstance(changes, dict) or set(changes) != {'lanes'}:
        raise ValueError('Expected a JSON object with only lanes; choose the Claude model inside Claude Code')
    if 'lanes' in changes and (not isinstance(changes['lanes'], dict) or not changes['lanes']):
        raise ValueError('lanes must contain selections')
    # Lock the scope, retaining unrelated settings and concurrent selections.
    target.parent.mkdir(parents=True, exist_ok=True)
    with (target.parent / '.configure.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # A user selection must never accidentally copy a project's overrides.
        fallback = user_root / 'arch-advisor/lanes.json'
        base = fallback if args.scope == 'project' and fallback.exists() else ROOT / 'config/lanes.json'
        config = load(target if target.exists() else base)
        config.pop('claude_advisor', None)  # Retire the 6.2.0-only Claude selector.
        patches = changes['lanes']
        if 'second-opinion' in patches and '2nd-advisor' in patches:
            raise ValueError('Use second-opinion or its 2nd-advisor alias, not both in one selection')
        for lane, patch in changes.get('lanes', {}).items():
            lane = '2nd-advisor' if lane == 'second-opinion' else lane
            if lane not in ('routine', 'complex', '2nd-advisor') or lane not in config['lanes']:
                raise ValueError(f'Unknown/missing lane: {lane}')
            if not isinstance(patch, dict) or not patch or set(patch) - {'model', 'default_effort'}:
                raise ValueError('A lane selection accepts model and/or default_effort')
            entry = config['lanes'][lane]
            if 'model' in patch:
                entry['model'] = model_id(patch['model'])
            if 'default_effort' in patch:
                entry['default_effort'] = effort_value(patch['default_effort'])
            effort = entry.get('default_effort')
            if effort is not None and (not isinstance(entry.get('efforts'), list) or effort not in entry['efforts']):
                raise ValueError(f'{lane}: default effort {effort!r} is not declared in efforts')
        # Remove only this tool's obsolete generated reviewer files in the
        # selected scope. Keep independently authored agents intact.
        retired = [agent_dir / ('arch-advisor-selected' + suffix + '.md')
                   for suffix in ('',) + tuple('-' + e for e in EFFORTS)]
        retired = [p for p in retired if p.is_file() and MARKER in p.read_text()]
        paths = retired + [target]
        previous = {path: path.read_text() if path.exists() else None for path in paths}
        written = []
        try:
            for path in retired:
                path.unlink()
                written.append(path)
            atomic_write(target, json.dumps(config, indent=2) + '\n')
            written.append(target)
        except OSError:
            for path in reversed(written):
                if previous[path] is None:
                    path.unlink()
                else:
                    atomic_write(path, previous[path])
            raise
    current = effective(workspace)
    print(json.dumps({'saved_path': str(target), 'effective_path': str(current),
                      'active_in_workspace': current.resolve() == target.resolve(),
                      'removed_legacy_agents': [str(p) for p in retired],
                      'configuration': config}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f'arch-advisor configure: {error}', file=sys.stderr)
        sys.exit(3)
