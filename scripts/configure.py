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


def agents(config):
    reviewer = config.get('claude_advisor')
    if reviewer is None:
        return {}
    model = model_id(reviewer['model'])
    default = effort_value(reviewer.get('default_effort'))
    body = (ROOT / 'agents/arch-advisor.md').read_text().split('---', 2)[2].strip()
    # The shipped reviewer remains the fallback. Generated definitions are
    # self-contained: no dependency on a particular plugin cache/version path.
    start = body.index('This agent pins ')
    end = body.index('## When you\'re called', start)
    body = body[:start] + (
        'Use the model and effort in this definition. If unavailable, report the\n'
        'failure; never silently substitute. Keep the review independent of the\n'
        'Codex second advisor.\n\n') + body[end:]
    definitions = {}
    for suffix, effort in [('', default)] + [('-' + e, e) for e in EFFORTS]:
        name = 'arch-advisor-selected' + suffix
        header = ('---\nname: ' + name + '\ndescription: "Configured independent read-only Claude advisor. '
                  'Use for architecture advice and final reviews."\nmodel: ' + json.dumps(model) + '\n')
        if effort:
            header += 'effort: ' + effort + '\n'
        definitions[name + '.md'] = header + 'tools: Read, Grep, Glob\n---\n\n' + MARKER + '\n\n' + body + '\n'
    return definitions


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
                          'agent_directory': str(agent_dir), 'configuration': load(source),
                          'scope_configuration': load(target) if target.exists() else None}, indent=2))
        return
    if os.environ.get('ARCH_ADVISOR_CONFIG'):
        raise ValueError('ARCH_ADVISOR_CONFIG overrides the picker. Unset it before configuring user/project selections.')
    changes = json.load(sys.stdin)
    if not isinstance(changes, dict) or not changes or set(changes) - {'lanes', 'claude_advisor'}:
        raise ValueError('Expected a nonempty JSON object with lanes and/or claude_advisor')
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
        for lane, patch in changes.get('lanes', {}).items():
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
        if 'claude_advisor' in changes:
            patch = changes['claude_advisor']
            if not isinstance(patch, dict) or not patch or set(patch) - {'model', 'default_effort'}:
                raise ValueError('claude_advisor accepts model and/or default_effort')
            reviewer = config.setdefault('claude_advisor', {'model': 'claude-opus-5-5', 'default_effort': None})
            if 'model' in patch:
                reviewer['model'] = model_id(patch['model'])
            if 'default_effort' in patch:
                reviewer['default_effort'] = effort_value(patch['default_effort'])
        definitions = agents(config)
        for name in definitions:
            path = agent_dir / name
            if path.exists() and MARKER not in path.read_text():
                raise ValueError(f'Refusing to overwrite an unmanaged agent: {path}')
        # Roll back partial writes if saving a definition or config fails.
        paths = [agent_dir / name for name in definitions] + [target]
        previous = {path: path.read_text() if path.exists() else None for path in paths}
        written = []
        try:
            for name, content in definitions.items():
                path = agent_dir / name
                atomic_write(path, content)
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
                      'claude_agent': 'arch-advisor-selected' if definitions else None,
                      'agent_directory': str(agent_dir) if definitions else None,
                      'configuration': config}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f'arch-advisor configure: {error}', file=sys.stderr)
        sys.exit(3)
