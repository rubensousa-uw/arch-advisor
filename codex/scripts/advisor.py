#!/usr/bin/env python3
"""Local preferences, terminal pickers and bounded CLI execution. No SDK/API keys."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import select
import shlex
import signal
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ROOT / "config/defaults.json"
LANES = ("routine", "complex", "second-opinion")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODELS = {
    "codex": ("gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra"),
    "claude": ("opus", "sonnet", "haiku"),
}


class ConfigError(Exception):
    pass


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConfigError(f"Cannot read valid JSON from {path}") from None
    if not isinstance(value, dict):
        raise ConfigError(f"Expected a JSON object in {path}")
    return value


def merge(base, patch, location="config"):
    """Reject typos and malformed shapes rather than silently ignoring settings."""
    if not isinstance(patch, dict):
        raise ConfigError(f"{location} must be an object")
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if key not in base:
            raise ConfigError(f"Unknown setting in {location}")
        if isinstance(base[key], dict):
            result[key] = merge(base[key], value, f"{location}.{key}")
        else:
            result[key] = value
    return result


def validate(config):
    if type(config["config_version"]) is not int or config["config_version"] != 1:
        raise ConfigError("Unsupported config_version; expected 1")
    for name, lane in config["lanes"].items():
        timeout = lane["timeout_seconds"]
        if type(timeout) is not int or not 1 <= timeout <= 86400:
            raise ConfigError(f"{name}: timeout_seconds must be an integer from 1 to 86400")
        if name == "second-opinion":
            if lane["provider"] not in ("codex", "claude"):
                raise ConfigError("second-opinion provider must be codex or claude")
            selections = (lane["codex"], lane["claude"])
        else:
            selections = (lane,)
        for selection in selections:
            model = selection["model"]
            if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}", model):
                raise ConfigError(f"{name}: invalid model ID (use an ID or alias, without spaces)")
            effort = selection["effort"]
            if effort is not None and effort not in EFFORTS:
                raise ConfigError(f"{name}: effort must be null or one of {', '.join(EFFORTS)}")
    return config


def user_path():
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser() / "arch-advisor/config.json"


def load_user():
    defaults = read_json(DEFAULTS)
    saved = read_json(user_path()) if user_path().exists() else {}
    return validate(merge(defaults, saved)), saved


def effective(workspace):
    config, _ = load_user()
    sources = [str(DEFAULTS)]
    if user_path().exists():
        sources.append(str(user_path()))
    project = workspace / ".arch-advisor/codex.json"
    explicit = os.environ.get("ARCH_ADVISOR_CODEX_CONFIG")
    paths = [project] if project.exists() else []
    if explicit:
        path = Path(explicit).expanduser()
        paths.append(path if path.is_absolute() else workspace / path)
    for path in paths:
        config = validate(merge(config, read_json(path)))
        sources.append(str(path))
    return config, sources


def save_user(config):
    # Validate before opening any file; replace atomically, outside the plugin.
    validate(merge(read_json(DEFAULTS), config))
    target = user_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, scratch = tempfile.mkstemp(prefix=".config-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(scratch, target)
    finally:
        if os.path.exists(scratch):
            os.unlink(scratch)


def selection(config, name, provider=None, effort=None):
    lane = config["lanes"][name]
    provider = provider or (lane["provider"] if name == "second-opinion" else "codex")
    choice = lane[provider] if name == "second-opinion" else lane
    rung = choice["effort"] if effort is None else (None if effort == "inherit" else effort)
    return provider, choice["model"], rung, lane["timeout_seconds"]


def show(config, sources, as_json=False):
    if as_json:
        print(json.dumps({"sources": sources, **config}, indent=2))
        return
    print("Effective settings (later sources take priority):")
    for source in sources:
        print(f"  {source}")
    for name in LANES:
        provider, model, effort, timeout = selection(config, name)
        print(f"{name}: {provider} / {model} / effort={effort or 'inherit'} / timeout={timeout}s")
    advisor = config["lanes"]["second-opinion"]
    for provider in ("codex", "claude"):
        choice = advisor[provider]
        print(f"  saved {provider}: {choice['model']} / effort={choice['effort'] or 'inherit'}")


def picker(title, options, current):
    """Arrow-key selector on Unix terminals; numeric selection on other TTYs."""
    index = options.index(current) if current in options else 0
    if os.name != "posix" or os.environ.get("TERM") == "dumb":
        print(title)
        for number, label in enumerate(options, 1):
            print(f"  {number}. {label}")
        while True:
            answer = input(f"Choice [{index + 1}] (q cancels): ").strip()
            if answer.lower() == "q":
                raise KeyboardInterrupt
            if not answer:
                return options[index]
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1]
    import termios
    import tty
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    print(f"\n{title} — ↑/↓, Enter; Esc cancels", flush=True)
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\x1b[?25l")
        while True:
            for number, label in enumerate(options):
                sys.stdout.write(f"\x1b[2K{'❯' if number == index else ' '} {label}\n")
            sys.stdout.flush()
            key = os.read(fd, 1)
            if key in (b"\r", b"\n"):
                return options[index]
            if key in (b"", b"\x03", b"\x04", b"q", b"Q"):
                raise KeyboardInterrupt
            if key == b"\x1b":
                # Escape on its own cancels; arrow keys consume their sequence.
                if not select.select([fd], [], [], 0.15)[0]:
                    raise KeyboardInterrupt
                suffix = os.read(fd, 1)
                if suffix in (b"[", b"O") and select.select([fd], [], [], 0.15)[0]:
                    key = os.read(fd, 1)
                else:
                    raise KeyboardInterrupt
            if key in (b"A", b"k"):
                index = (index - 1) % len(options)
            elif key in (b"B", b"j"):
                index = (index + 1) % len(options)
            sys.stdout.write(f"\x1b[{len(options)}A")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def configure():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ConfigError("configure needs an interactive terminal. Use show or set for noninteractive access.")
    config, _ = load_user()
    print(f"User settings: {user_path()}\nProject overrides are not edited. No model calls are made.")
    while True:
        action = picker("Configure", ["Routine", "Complex", "Second opinion", "Save and exit", "Cancel"], "Routine")
        if action == "Cancel":
            print("Cancelled; no settings saved.")
            return
        if action == "Save and exit":
            save_user(config)
            print(f"Saved {user_path()}")
            show(config, [str(user_path())])
            return
        name = {"Routine": "routine", "Complex": "complex", "Second opinion": "second-opinion"}[action]
        lane = config["lanes"][name]
        provider = "codex"
        if name == "second-opinion":
            provider = picker("Second opinion — provider", ["codex", "claude"], lane["provider"])
            lane["provider"] = provider
        choice = lane[provider] if name == "second-opinion" else lane
        models = list(MODELS[provider])
        if choice["model"] not in models:
            models.append(choice["model"])
        model = picker(f"{action} — {provider} model", models + ["custom"], choice["model"])
        if model == "custom":
            model = input("Model ID: ").strip()
        draft = copy.deepcopy(config)
        draft_lane = draft["lanes"][name]
        draft_choice = draft_lane[provider] if name == "second-opinion" else draft_lane
        draft_choice["model"] = model
        validate(draft)
        effort = picker(f"{action} — default effort", ["inherit", *EFFORTS], choice["effort"] or "inherit")
        draft_choice["effort"] = None if effort == "inherit" else effort
        config = draft


def set_preference(args):
    config, saved = load_user()
    lane = config["lanes"][args.lane]
    if args.provider and args.lane != "second-opinion":
        raise ConfigError("Only second-opinion has a selectable provider")
    if args.provider is None and args.model is None and args.effort is None:
        raise ConfigError("set requires --provider, --model or --effort")
    if args.lane == "second-opinion":
        provider = args.provider or lane["provider"]
        patch = saved.setdefault("lanes", {}).setdefault(args.lane, {})
        if args.provider:
            patch["provider"] = provider
        choice = patch.setdefault(provider, {})
    else:
        choice = saved.setdefault("lanes", {}).setdefault(args.lane, {})
    if args.model is not None:
        choice["model"] = args.model
    if args.effort is not None:
        choice["effort"] = None if args.effort == "inherit" else args.effort
    save_user(saved)
    print(f"Saved {user_path()}")


def command(provider, model, effort, workspace, sandbox, final):
    if provider == "codex":
        argv = ["codex", "exec", "--model", model, "--sandbox", sandbox,
                "-c", 'approval_policy="never"', "--skip-git-repo-check",
                "--cd", str(workspace), "--output-last-message", str(final)]
        if effort:
            argv += ["-c", f"model_reasoning_effort={effort}"]
        return argv + ["-"]
    # Neither --allowedTools alone nor plan mode guarantees a read-only review.
    # Remove customizations/MCP and all built-in tools except file inspection.
    argv = ["claude", "-p", "--model", model, "--output-format", "json",
            "--safe-mode", "--restricted", "--tools", "Read,Grep,Glob",
            "--allowedTools", "Read,Grep,Glob", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--permission-mode", "dontAsk",
            "--permission-prompts", "none", "--no-session-persistence",
            "--disable-slash-commands", "--no-chrome"]
    if effort:
        argv += ["--effort", effort]
    return argv


def stop_process(process):
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.terminate()
    try:
        return process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        return process.communicate()


def execute(argv, workspace, prompt, timeout):
    try:
        process = subprocess.Popen(argv, cwd=workspace, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=(os.name == "posix"))
    except OSError as error:
        return 127, "", str(error), "unavailable"
    try:
        output, errors = process.communicate(prompt, timeout=timeout)
        rc = process.returncode
        return (128 - rc if rc < 0 else rc), output, errors, "complete" if rc == 0 else "provider-error"
    except subprocess.TimeoutExpired:
        output, errors = stop_process(process)
        return 124, output, errors, "timeout"
    except KeyboardInterrupt:
        stop_process(process)
        raise


def invocation(args, config, name, workspace, probe=False):
    provider, model, effort, timeout = selection(config, name, getattr(args, "provider", None) if name == "second-opinion" else None, args.effort)
    sandbox = "read-only" if name == "second-opinion" or probe else "workspace-write"
    with tempfile.TemporaryDirectory(prefix="arch-advisor-") as scratch:
        final = Path(scratch) / "final.txt"
        argv = command(provider, model, effort, workspace, sandbox, final)
        print(f"COMMAND: {shlex.join(argv)}", flush=True)
        print(f"WORKSPACE: {workspace}\nPROVIDER: {provider}\nMODEL_CONFIGURED: {model}\nEFFORT: {effort or 'inherit'}\nTIMEOUT_SECONDS: {timeout}", flush=True)
        if args.dry_run:
            print("STATUS: dry-run (no model invocation)")
            return 0
        prompt = "Respond with exactly OK, without using tools." if probe else sys.stdin.read()
        if not prompt.strip():
            print("ARCH ADVISOR REPORT\nSTATUS: refused\nEXIT_CODE: 2\nREASON: Empty prompt")
            return 2
        if not probe:
            if name == "second-opinion":
                prompt = ("Independent read-only consultation. Inspect the supplied evidence. "
                          "Do not edit files, run builds/tests, perform external writes, change models "
                          "or delegate. Use only read-only inspection. This invocation opts out of implementation/orchestration; "
                          "other user/project constraints still apply. Return ship, fix-first or "
                          "rethink, decisive risks, file:line findings and missing evidence, under "
                          "300 words. Never claim tests you did not run.\n\nConsultation:\n" + prompt)
            else:
                prompt = ("Implement the supplied specification in this workspace. The caller "
                          "coordinates verification and review. Do not delegate or invoke advisor "
                          "or implementer lanes recursively. Respect user/project constraints. "
                          "Report the changes and actual verification evidence.\n\nSpecification:\n" + prompt)
        rc, output, errors, status = execute(argv, workspace, prompt, timeout)
        advice = ""
        reported = []
        if rc == 0:
            if provider == "codex":
                try:
                    advice = final.read_text(encoding="utf-8")
                except OSError:
                    pass
            else:
                try:
                    result = json.loads(output)
                    if not isinstance(result, dict) or result.get("type") != "result":
                        raise ValueError
                    if result.get("is_error") or result.get("subtype") != "success":
                        rc, status = 1, "provider-error"
                    elif isinstance(result.get("result"), str):
                        advice = result["result"]
                    usage = result.get("modelUsage", {})
                    if isinstance(usage, dict):
                        reported = list(usage)
                except (ValueError, TypeError):
                    rc, status = 1, "refused"
            if rc == 0 and not advice.strip():
                rc, status = 1, "refused"
        if probe and rc == 0 and advice.strip() != "OK":
            rc, status = 1, "probe-failed"
        print(f"ARCH ADVISOR REPORT\nLANE: {name}\nSTATUS: {status}\nEXIT_CODE: {rc}")
        if reported:
            print(f"MODELS_REPORTED: {', '.join(reported)}")
        if advice:
            print("ORIGINAL RESPONSE:")
            sys.stdout.write(advice)
            if not advice.endswith("\n"):
                print()
        if rc:
            print("REASON: CLI failure, timeout, invalid/missing response or unsuccessful probe.")
            if output:
                print("PROVIDER STDOUT:")
                print(output.rstrip())
            if errors:
                print("PROVIDER STDERR:", file=sys.stderr)
                print(errors.rstrip(), file=sys.stderr)
        return rc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    display = sub.add_parser("show", help="Show effective settings without model calls")
    display.add_argument("--cd", default=os.getcwd())
    display.add_argument("--json", action="store_true")
    sub.add_parser("configure", help="Open local selection lists; no model calls")
    change = sub.add_parser("set", help="Save exact user preferences without model calls")
    change.add_argument("lane", choices=LANES)
    change.add_argument("--provider", choices=("codex", "claude"))
    change.add_argument("--model")
    change.add_argument("--effort", choices=("inherit", *EFFORTS))
    for action in ("run", "opinion", "smoke"):
        child = sub.add_parser(action)
        if action == "run":
            child.add_argument("lane", choices=("routine", "complex"))
        if action == "smoke":
            child.add_argument("lane", choices=LANES, nargs="?")
        child.add_argument("--cd", required=True, help="Target workspace, required before override resolution")
        child.add_argument("--effort", choices=("inherit", *EFFORTS))
        child.add_argument("--dry-run", action="store_true")
        if action != "run":
            child.add_argument("--provider", choices=("codex", "claude"))
    args = parser.parse_args()
    try:
        if args.action == "configure":
            configure()
            return 0
        if args.action == "set":
            set_preference(args)
            return 0
        workspace = Path(args.cd).expanduser().resolve()
        if not workspace.is_dir():
            raise ConfigError(f"Workspace does not exist: {workspace}")
        config, sources = effective(workspace)
        if args.action == "show":
            show(config, sources, args.json)
            return 0
        if args.action == "smoke":
            if args.provider and args.lane in ("routine", "complex"):
                raise ConfigError("--provider only applies to second-opinion")
            names = (args.lane,) if args.lane else LANES
            codes = [invocation(args, config, name, workspace, probe=True) for name in names]
            return 0 if all(code == 0 for code in codes) else 1
        name = "second-opinion" if args.action == "opinion" else args.lane
        return invocation(args, config, name, workspace)
    except ConfigError as error:
        print(f"ARCH ADVISOR REPORT\nSTATUS: configuration-error\nEXIT_CODE: 4\nREASON: {error}", file=sys.stderr)
        return 4
    except OSError as error:
        print(f"arch-advisor: {error}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\nCancelled; no pending settings saved.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
