#!/usr/bin/env python3
"""Local task evidence, personal Pareto frontiers and reviewable project changes."""
import argparse
from collections import defaultdict
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import signal
import sqlite3
from statistics import NormalDist, mean
import subprocess
import sys
import tempfile
import time
import uuid

ROLES = ("session", "routine", "complex", "second-opinion", "verification")
PROVIDERS = ("claude", "codex", "opencode")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
METRICS = ("duration_seconds", "tokens", "api_cost_usd", "quota_units")
ROOT = Path(__file__).resolve().parents[1]


class AdaptiveError(Exception):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def workspace_id(workspace):
    return digest(str(Path(workspace).resolve()))


def state_dir():
    return Path(os.environ.get("ARCH_ADVISOR_STATE_DIR", str(Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "arch-advisor")))


@contextmanager
def connect():
    directory = state_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "history.sqlite3"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    os.chmod(path, 0o600)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, scope TEXT NOT NULL,
          task_id TEXT NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outcomes (scope TEXT NOT NULL, task_id TEXT NOT NULL,
          outcome TEXT NOT NULL, evidence TEXT NOT NULL, updated REAL NOT NULL,
          PRIMARY KEY(scope, task_id));
        CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, scope TEXT NOT NULL,
          status TEXT NOT NULL, payload TEXT NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS policies (scope TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS runs_scope ON runs(scope, created);
    """)
    try:
        with db:
            yield db
    finally:
        db.close()


def scope_id(workspace, host):
    return digest([workspace_id(workspace), host])


def valid_text(value, label, limit=200):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0," + str(limit - 1) + "}", value):
        raise AdaptiveError(f"Invalid {label}; use a short identifier, not free text")
    return value


def valid_number(value, label):
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
        raise AdaptiveError(f"Invalid {label}; expected a nonnegative finite number or null")
    return value


def record(workspace, host, value):
    allowed = {"id", "task_id", "role", "context", "provider", "model", "effort", "exit_code",
               "duration_seconds", "tokens", "api_cost_usd", "quota_units", "quota_key", "usage_source"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise AdaptiveError("Record contains unknown fields; prompts, responses and code are not accepted")
    row = dict(value)
    if host not in ("claude", "codex") or row.get("role") not in ROLES or row.get("provider") not in PROVIDERS:
        raise AdaptiveError("Invalid host, role or provider")
    row["id"] = valid_text(row.get("id", uuid.uuid4().hex), "run ID")
    row["task_id"] = valid_text(row.get("task_id") or uuid.uuid4().hex, "task ID")
    row["context"] = valid_text(row.get("context", "general"), "task class")
    valid_text(row.get("model"), "model ID")
    if row.get("effort") not in (None, *EFFORTS):
        raise AdaptiveError("Invalid effort")
    if type(row.get("exit_code")) is not int or not 0 <= row["exit_code"] <= 255:
        raise AdaptiveError("Invalid exit code")
    for key in METRICS:
        row[key] = valid_number(row.get(key), key)
    if row["duration_seconds"] is None:
        raise AdaptiveError("duration_seconds is required")
    if row["quota_units"] is not None:
        valid_text(row.get("quota_key"), "quota unit/provider/plan key")
    else:
        row["quota_key"] = None
    row["usage_source"] = valid_text(row.get("usage_source", "manual"), "usage source")
    scope = scope_id(workspace, host)
    with connect() as db:
        previous = db.execute("SELECT payload FROM runs WHERE scope=? AND task_id=? LIMIT 1", (scope, row["task_id"])).fetchone()
        if previous:
            first = json.loads(previous[0])
            if first["context"] != row["context"]:
                raise AdaptiveError("Retries must retain the task class; use a new task ID for another task")
        try:
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?)", (row["id"], scope, row["task_id"], time.time(), json.dumps(row)))
        except sqlite3.IntegrityError:
            raise AdaptiveError("Run ID already recorded; duplicate imports are refused") from None
        # New attempts invalidate an earlier terminal outcome until verified again.
        db.execute("DELETE FROM outcomes WHERE scope=? AND task_id=?", (scope, row["task_id"]))
    return row


def feedback(workspace, host, task_id, outcome, evidence):
    scope = scope_id(workspace, host)
    valid_text(task_id, "task ID")
    valid_text(evidence, "evidence reference")
    if outcome not in ("accepted", "rejected", "unknown"):
        raise AdaptiveError("Outcome must be accepted, rejected or unknown")
    with connect() as db:
        if not db.execute("SELECT 1 FROM runs WHERE scope=? AND task_id=?", (scope, task_id)).fetchone():
            raise AdaptiveError("Unknown task in this project/host")
        db.execute("INSERT OR REPLACE INTO outcomes VALUES (?,?,?,?,?)", (scope, task_id, outcome, evidence, time.time()))


class Usage:
    """Retain only numeric usage fields; discard all other provider output."""
    def __init__(self):
        self.values = {"tokens": None, "api_cost_usd": None, "quota_units": None, "quota_key": None}
        self.incomplete = set()

    def feed(self, line, provider):
        try:
            event = json.loads(line) if isinstance(line, str) else line
            if not isinstance(event, dict):
                return
            usage = event.get("usage")
            if provider == "codex" and event.get("type") == "turn.completed" and isinstance(usage, dict):
                self.add("tokens", usage.get("input_tokens"), usage.get("output_tokens"))
            elif provider == "claude" and event.get("type") == "result" and isinstance(usage, dict):
                self.add("tokens", usage.get("input_tokens"), usage.get("output_tokens"),
                         usage.get("cache_read_input_tokens", 0), usage.get("cache_creation_input_tokens", 0))
                self.add("api_cost_usd", event.get("total_cost_usd"))
            elif provider == "opencode" and isinstance(usage, dict):
                # Import a terminal inference usage event, not every stream delta.
                details = usage.get("opencode", {})
                if not isinstance(details, dict):
                    return
                tokens = details.get("tokens", {})
                if isinstance(tokens, dict):
                    self.add("tokens", tokens.get("input"), tokens.get("output"), tokens.get("cache_read", 0),
                             tokens.get("cache_write_5m", 0), tokens.get("cache_write_1h", 0))
                self.add("api_cost_usd", details.get("list_cost"))
                if details.get("billing_source") in ("go", "go-plus"):
                    key = "opencode:" + details["billing_source"] + ":allowance-usd"
                    if self.values["quota_key"] not in (None, key):
                        self.values["quota_units"] = None
                        self.incomplete.add("quota_units")
                    self.add("quota_units", details.get("allowance"))
                    self.values["quota_key"] = key if "quota_units" not in self.incomplete else None
        except (ValueError, TypeError):
            return

    def add(self, key, *values):
        if key in self.incomplete:
            return
        if values and all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values):
            self.values[key] = (self.values[key] or 0) + sum(values)
        else:
            self.values[key] = None
            self.incomplete.add(key)


def collect(workspace, host, role, provider, model, effort, duration, exit_code, usage, task_id=None, context=None):
    if os.environ.get("ARCH_ADVISOR_TELEMETRY", "1") == "0":
        return None
    try:
        row = record(workspace, host, {"role": "second-opinion" if role == "2nd-advisor" else role,
            "provider": provider, "model": model, "effort": effort or None,
            "task_id": task_id or os.environ.get("ARCH_ADVISOR_TASK_ID"),
            "context": context or os.environ.get("ARCH_ADVISOR_TASK_CLASS", "general"),
            "duration_seconds": duration, "exit_code": exit_code, **usage.values, "usage_source": "cli"})
        print(f"ADAPTIVE_TASK_ID: {row['task_id']} (outcome unknown; record verification with adaptive feedback)", file=sys.stderr)
        return row
    except (AdaptiveError, OSError, sqlite3.Error):
        print("WARN: local adaptive evidence could not be recorded; provider result is unchanged", file=sys.stderr)
        return None


def capture(args):
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not argv:
        raise AdaptiveError("capture requires a command after --")
    started, usage = time.monotonic(), Usage()
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, errors="replace", start_new_session=True)
    previous = {}
    def forward(sig, frame):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, forward)
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            usage.feed(line, args.provider)
        code = process.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    code = 128 - code if code < 0 else code
    if code:
        usage.values = dict.fromkeys(usage.values)
    collect(args.cd, args.host, args.role, args.provider, args.model, args.effort,
            time.monotonic() - started, code, usage, args.task_id, args.context)
    return code


def option(row):
    return {key: row.get(key) for key in ("provider", "model", "effort")}


def tasks(db, scope, days):
    raw = db.execute("SELECT * FROM runs WHERE scope=? ORDER BY created,id", (scope,)).fetchall()
    outcomes = {r["task_id"]: dict(r) for r in db.execute("SELECT * FROM outcomes WHERE scope=?", (scope,))}
    grouped = defaultdict(list)
    for r in raw:
        grouped[r["task_id"]].append((r["created"], json.loads(r["payload"])))
    result = []
    for task_id, attempts in grouped.items():
        if attempts[-1][0] < time.time() - days * 86400:
            continue
        rows = [r for _, r in attempts]
        first, outcome = rows[0], outcomes.get(task_id, {})
        task = {"task_id": task_id, "role": first["role"], "context": first["context"],
                "option": option(first), "attempts": len(rows), "outcome": outcome.get("outcome", "unknown"),
                "evidence": outcome.get("evidence"), "last_attempt_at": attempts[-1][0]}
        for metric in METRICS:
            task[metric] = sum(r[metric] for r in rows) if all(r[metric] is not None for r in rows) else None
        keys = {r["quota_key"] for r in rows}
        task["quota_key"] = next(iter(keys)) if len(keys) == 1 else None
        if task["quota_key"] is None:
            task["quota_units"] = None
        result.append(task)
    revision = digest([[dict(r) for r in raw], outcomes, result])
    return result, revision


def wilson(successes, count, z):
    p = successes / count
    center = (p + z * z / (2 * count)) / (1 + z * z / count)
    width = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / (1 + z * z / count)
    return [max(0, center - width), min(1, center + width)]


def analyze(task_rows, role, context, metric, quota_key, min_samples, confidence):
    groups = defaultdict(list)
    unknown = 0
    for task in task_rows:
        if task["role"] != role or task["context"] != context:
            continue
        if task["outcome"] == "unknown":
            unknown += 1
            continue
        groups[json.dumps(task["option"], sort_keys=True)].append(task)
    summaries = []
    for key, rows in sorted(groups.items()):
        n, success = len(rows), sum(r["outcome"] == "accepted" for r in rows)
        costs = [r[metric] for r in rows if r[metric] is not None and
                 (metric != "quota_units" or r["quota_key"] == quota_key)]
        summaries.append({"option": json.loads(key), "tasks": n, "accepted": success,
                          "acceptance_rate": success / n,
                          "mean_resource": mean(costs) if len(costs) == n else None,
                          "resource_per_accepted_task": sum(costs) / success if success and len(costs) == n else None,
                          "attempts": sum(r["attempts"] for r in rows), "samples": costs,
                          "evidence": [{k: r[k] for k in ("task_id", "outcome", "evidence")} for r in rows],
                          "eligible_evidence": n >= min_samples and len(costs) == n})
    eligible = [row for row in summaries if row["eligible_evidence"]]
    for row in summaries:
        count = len(eligible) if row["eligible_evidence"] else len(summaries)
        z = NormalDist().inv_cdf(1 - (1 - confidence) / (2 * count))
        row["quality_interval"] = wilson(row["accepted"], row["tasks"], z)
    return summaries, eligible, unknown


def personal_frontier(options):
    return [r for r in options if not any(
        other["acceptance_rate"] >= r["acceptance_rate"] and other["mean_resource"] <= r["mean_resource"]
        and (other["acceptance_rate"] > r["acceptance_rate"] or other["mean_resource"] < r["mean_resource"])
        for other in options)]


def saving_interval(baseline, candidate, confidence, seed):
    rng = random.Random(seed)
    values = sorted(mean(rng.choices(baseline, k=len(baseline))) - mean(rng.choices(candidate, k=len(candidate)))
                    for _ in range(1000))
    tail = (1 - confidence) / 2
    return [values[int(tail * len(values))], values[min(len(values) - 1, int((1 - tail) * len(values)))]]


def current(workspace, host, role):
    if role not in ("routine", "complex", "second-opinion"):
        return None
    if host == "codex":
        import advisor
        try:
            config, _ = advisor.effective(Path(workspace).resolve())
            provider, model, effort, _ = advisor.selection(config, role)
        except advisor.ConfigError as error:
            raise AdaptiveError(str(error)) from None
        return {"provider": provider, "model": model, "effort": effort}
    scripts = ROOT.parent / "scripts"
    if not (scripts / "lane.sh").exists():
        raise AdaptiveError("Claude package is not installed alongside this helper")
    path = subprocess.run([str(scripts / "lane.sh"), "config-path"], cwd=workspace, capture_output=True, text=True, check=True).stdout.strip()
    key = "2nd-advisor" if role == "second-opinion" else role
    raw = subprocess.run([sys.executable, str(scripts / "native-config.py"), path, key], cwd=workspace,
                         capture_output=True, text=True, check=True)
    entry = json.loads(raw.stdout)["lanes"][key]
    return {"provider": "codex", "model": entry["model"], "effort": entry.get("default_effort")}


def availability(args):
    import recommend
    providers = args.providers
    if not providers:
        path = recommend.profile_path()
        try:
            providers = recommend.read_json(path).get("providers") if path.exists() else None
        except recommend.RecommendationError as error:
            raise AdaptiveError(str(error)) from None
    if not isinstance(providers, list) or not providers or any(p not in PROVIDERS for p in providers):
        raise AdaptiveError("Select active subscriptions with --providers claude codex [opencode]")
    try:
        catalog = recommend.read_json(args.catalog) if args.catalog else recommend.fetch_json(recommend.CATALOG_URL)
        override = recommend.read_json(args.available) if args.available else None
        entries, warnings = recommend.available_models(catalog, providers, override)
    except recommend.RecommendationError as error:
        raise AdaptiveError(str(error)) from None
    return {(r["provider"], r["model_id"]) for r in entries}, warnings


def supported(host, role, choice):
    return role in ("routine", "complex", "second-opinion") and (
        choice["provider"] == "codex" or host == "codex" and role == "second-opinion" and choice["provider"] == "claude")


def propose(args):
    allowed, warnings = availability(args)
    scope = scope_id(args.cd, args.host)
    with connect() as db:
        observations, revision = tasks(db, scope, args.days)
        baseline_choice = current(args.cd, args.host, args.role)
        if getattr(args, "current", None):
            if baseline_choice is not None:
                raise AdaptiveError("--current is only for roles without a configurable lane")
            baseline_choice = json.loads(args.current.read_text())
            if (not isinstance(baseline_choice, dict) or set(baseline_choice) != {"provider", "model", "effort"}
                    or baseline_choice["provider"] not in PROVIDERS or baseline_choice["effort"] not in (None, *EFFORTS)):
                raise AdaptiveError("--current must contain provider, model and effort")
            valid_text(baseline_choice["model"], "current model")
        observations = [o for o in observations if (o["option"]["provider"], o["option"]["model"]) in allowed or o["option"] == baseline_choice]
        groups, eligible, unknown = analyze(observations, args.role, args.context, args.metric, args.quota_key, args.min_samples, args.confidence)
        observed = [g for g in groups if g["mean_resource"] is not None]
        baseline = next((g for g in eligible if g["option"] == baseline_choice), None)
        suggestions = []
        for candidate in personal_frontier(eligible):
            choice = candidate["option"]
            if not baseline or choice == baseline_choice or (choice["provider"], choice["model"]) not in allowed:
                continue
            quality_bound = candidate["quality_interval"][0] - baseline["quality_interval"][1]
            interval = saving_interval(baseline["samples"], candidate["samples"],
                                       1 - (1 - args.confidence) / len(eligible),
                                       digest([baseline["samples"], candidate["samples"]]))
            if quality_bound < -args.quality_margin or interval[0] <= 0:
                continue
            proposal_id = digest([scope, args.role, args.context, args.metric, args.quota_key, baseline_choice, choice])[:20]
            value = {"id": proposal_id, "host": args.host, "role": args.role, "context": args.context,
                     "from": baseline_choice, "to": choice, "revision": revision, "days": args.days,
                     "metric": args.metric, "quota_key": args.quota_key, "quality_margin": args.quality_margin,
                     "confidence": args.confidence, "quality_difference_lower_bound": quality_bound,
                     "saving_interval": interval, "baseline": {k: v for k, v in baseline.items() if k != "samples"},
                     "candidate": {k: v for k, v in candidate.items() if k != "samples"},
                     "route_supported": supported(args.host, args.role, choice), "generated_at": time.time()}
            existing = db.execute("SELECT status FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            status = existing[0] if existing else "pending"
            if status == "pending":
                db.execute("INSERT OR REPLACE INTO proposals VALUES (?,?,?,?,?)", (proposal_id, scope, status, json.dumps(value), time.time()))
            value["status"] = status
            suggestions.append(value)
        policy = db.execute("SELECT enabled FROM policies WHERE scope=?", (scope,)).fetchone()
        auto = bool(policy and policy[0])
    result = {"role": args.role, "context": args.context, "metric": args.metric,
              "min_samples": args.min_samples, "window_days": args.days,
              "quality_margin": args.quality_margin, "confidence": args.confidence,
              "current": baseline_choice, "unknown_outcomes": unknown,
              "options": [{k: v for k, v in g.items() if k != "samples"} for g in groups],
              "personal_frontier": [g["option"] for g in personal_frontier(observed)], "suggestions": suggestions,
              "auto_adjust": auto, "warnings": warnings + [
                  "Observational comparisons within one project/role/task class, not randomized causal evidence.",
                  "Tokens and API list-price estimates are not subscription quota; quota comparisons require the same quota_key.",
                  "Missing outcomes/metrics stay unknown. Retried tasks are attributed to their initial option, including all attempt resources."]}
    if baseline_choice is None:
        result["warnings"].append("No configurable current lane: supply --current JSON to compare a manually selected model. Acceptance records manual advice only.")
    if auto:
        print("WARNING: AUTO-ADJUST ENABLED for this project; qualifying supported changes may be applied now.", file=sys.stderr)
        # One change per analysis; never chain promotions from an old baseline.
        pending = [p for p in suggestions if p["status"] == "pending" and p["route_supported"]]
        if pending and not args.catalog:
            winner = max(pending, key=lambda p: p["saving_interval"][0])
            result["auto_applied"] = accept(args, winner["id"], allowed)
    return result


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".adaptive-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def change_project(workspace, host, role, choice):
    if not supported(host, role, choice):
        raise AdaptiveError("This provider/role has no execution route; apply the suggestion manually")
    if os.environ.get("ARCH_ADVISOR_CONFIG") or os.environ.get("ARCH_ADVISOR_CODEX_CONFIG"):
        raise AdaptiveError("Explicit configuration override is active; refusing to write an ineffective project preference")
    target = Path(workspace).resolve() / ".arch-advisor" / ("codex.json" if host == "codex" else "lanes.json")
    if target.is_symlink() or target.parent.is_symlink():
        raise AdaptiveError("Refusing to replace a symlinked project configuration")
    before = target.read_text() if target.exists() else None
    data = json.loads(before) if before is not None else {}
    if host == "codex":
        import advisor
        lane = data.setdefault("lanes", {}).setdefault(role, {})
        if role == "second-opinion":
            lane["provider"] = choice["provider"]
            lane = lane.setdefault(choice["provider"], {})
        lane.update(model=choice["model"], effort=choice["effort"])
        try:
            config, _ = advisor.load_user()
            advisor.validate(advisor.merge(config, data))
        except advisor.ConfigError as error:
            raise AdaptiveError(str(error)) from None
    else:
        if before is None:
            scripts = ROOT.parent / "scripts"
            path = subprocess.run([str(scripts / "lane.sh"), "config-path"], cwd=workspace, capture_output=True, text=True, check=True).stdout.strip()
            raw = subprocess.run([sys.executable, str(scripts / "native-config.py"), path], cwd=workspace,
                                 capture_output=True, text=True, check=True)
            data = json.loads(raw.stdout)
            for entry in data["lanes"].values():
                entry.pop("_native_options_source", None)
        key = "2nd-advisor" if role == "second-opinion" else role
        lane = data["lanes"][key]
        if choice["effort"] is not None and choice["effort"] not in (lane.get("efforts") or []):
            raise AdaptiveError("Suggested effort is not declared in this lane; update its allowlist explicitly")
        lane.update(model=choice["model"], default_effort=choice["effort"])
    after = json.dumps(data, indent=2) + "\n"
    atomic_write(target, after.encode())
    return {"before": before, "after_hash": digest(after), "filename": target.name}


def accept(args, proposal_id, allowed=None):
    allowed = allowed if allowed is not None else availability(args)[0]
    scope = scope_id(args.cd, args.host)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        stored = db.execute("SELECT * FROM proposals WHERE id=? AND scope=?", (proposal_id, scope)).fetchone()
        if not stored or stored["status"] != "pending":
            raise AdaptiveError("No pending proposal with this ID in this project/host")
        value = json.loads(stored["payload"])
        _, revision = tasks(db, scope, value["days"])
        if revision != value["revision"] or time.time() - value["generated_at"] > 86400:
            raise AdaptiveError("Evidence changed or proposal expired; run suggest again")
        if value["role"] in ("routine", "complex", "second-opinion") and current(args.cd, args.host, value["role"]) != value["from"]:
            raise AdaptiveError("Current lane changed since the suggestion; run suggest again")
        if (value["to"]["provider"], value["to"]["model"]) not in allowed:
            raise AdaptiveError("Suggested model is no longer in the selected available catalog")
        if not value["route_supported"]:
            db.execute("UPDATE proposals SET status='accepted-manual',updated=? WHERE id=?", (time.time(), proposal_id))
            return {"id": proposal_id, "status": "accepted-manual", "to": value["to"],
                    "instruction": "No configuration changed. Select this model/effort manually in the target application."}
        value["application"] = change_project(args.cd, args.host, value["role"], value["to"])
        try:
            db.execute("UPDATE proposals SET status='accepted',payload=?,updated=? WHERE id=?", (json.dumps(value), time.time(), proposal_id))
            db.commit()
        except sqlite3.Error:
            application = value["application"]
            target = Path(args.cd).resolve() / ".arch-advisor" / application["filename"]
            if target.exists() and digest(target.read_text()) == application["after_hash"]:
                if application["before"] is None:
                    target.unlink()
                else:
                    atomic_write(target, application["before"].encode())
            raise
    return {"id": proposal_id, "status": "accepted", "from": value["from"], "to": value["to"], "scope": "project"}


def decide(args):
    scope = scope_id(args.cd, args.host)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM proposals WHERE id=? AND scope=?", (args.id, scope)).fetchone()
        if not row:
            raise AdaptiveError("Unknown proposal in this project/host")
        if args.action == "undo":
            if row["status"] != "accepted":
                raise AdaptiveError("Only an applied proposal can be undone")
            application = json.loads(row["payload"])["application"]
            target = Path(args.cd).resolve() / ".arch-advisor" / application["filename"]
            if target.is_symlink() or target.parent.is_symlink() or not target.exists() or digest(target.read_text()) != application["after_hash"]:
                raise AdaptiveError("Project configuration changed after acceptance; refusing to overwrite it")
            if application["before"] is None:
                target.unlink()
            else:
                atomic_write(target, application["before"].encode())
            status = "undone"
        else:
            if row["status"] not in ("pending", "rejected"):
                raise AdaptiveError("Only pending/rejected proposals can be rejected or reopened")
            status = "rejected" if args.action == "reject" else "pending"
        db.execute("UPDATE proposals SET status=?,updated=? WHERE id=?", (status, time.time(), args.id))
    return {"id": args.id, "status": status}


def main(argv=None, default_host="codex"):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("claude", "codex"), default=default_host)
    parser.add_argument("--cd", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="action", required=True)
    capture_parser = sub.add_parser("capture", help="Wrap a CLI and collect numeric usage locally")
    capture_parser.add_argument("--role", choices=(*ROLES, "2nd-advisor"), required=True)
    capture_parser.add_argument("--provider", choices=PROVIDERS, required=True)
    capture_parser.add_argument("--model", required=True)
    capture_parser.add_argument("--effort", choices=EFFORTS)
    capture_parser.add_argument("--task-id")
    capture_parser.add_argument("--context")
    capture_parser.add_argument("command", nargs=argparse.REMAINDER)
    importer = sub.add_parser("record", help="Import one normalized observation; no prompts or responses")
    importer.add_argument("file", type=Path)
    outcome = sub.add_parser("feedback", help="Record verified terminal task outcome")
    outcome.add_argument("task_id")
    outcome.add_argument("outcome", choices=("accepted", "rejected", "unknown"))
    outcome.add_argument("--evidence", required=True, help="Short local test/run/review reference, not raw output")
    listing = sub.add_parser("history")
    listing.add_argument("--days", type=int, default=30)
    suggestion = sub.add_parser("suggest", help="Personal frontier and evidence-backed change proposals")
    suggestion.add_argument("--role", choices=ROLES, required=True)
    suggestion.add_argument("--context", default="general")
    suggestion.add_argument("--current", type=Path, help="JSON provider/model/effort for a manually selected session or verifier")
    suggestion.add_argument("--metric", choices=METRICS, default="duration_seconds")
    suggestion.add_argument("--quota-key")
    suggestion.add_argument("--min-samples", type=int, default=10)
    suggestion.add_argument("--confidence", type=float, default=.95)
    suggestion.add_argument("--quality-margin", type=float, default=0,
                            help="Maximum tolerated acceptance-rate loss; default 0 (strict)")
    suggestion.add_argument("--days", type=int, default=30)
    acceptance = sub.add_parser("accept")
    acceptance.add_argument("id")
    for child in (suggestion, acceptance):
        child.add_argument("--providers", nargs="+", choices=PROVIDERS)
        child.add_argument("--available", type=Path)
        child.add_argument("--catalog", type=Path, help="Offline models.dev snapshot (disables auto-apply)")
    for action in ("reject", "reopen", "undo"):
        sub.add_parser(action).add_argument("id")
    policy = sub.add_parser("auto-adjust", help="Off by default; enabled mode applies when suggest runs")
    policy.add_argument("mode", choices=("status", "enable", "disable"))
    policy.add_argument("--acknowledge", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.cd.is_dir():
            raise AdaptiveError("Workspace does not exist")
        if args.action == "capture":
            return capture(args)
        if args.action == "record":
            value = record(args.cd, args.host, json.loads(args.file.read_text()))
        elif args.action == "feedback":
            feedback(args.cd, args.host, args.task_id, args.outcome, args.evidence)
            value = {"task_id": args.task_id, "outcome": args.outcome}
        elif args.action in ("history", "suggest"):
            if not 1 <= args.days <= 3650:
                raise AdaptiveError("days must be between 1 and 3650")
            if args.action == "history":
                with connect() as db:
                    value = {"tasks": tasks(db, scope_id(args.cd, args.host), args.days)[0]}
            else:
                if not 2 <= args.min_samples <= 100000 or not .8 <= args.confidence < 1 or not 0 <= args.quality_margin <= .2:
                    raise AdaptiveError("Invalid evidence policy: min-samples >=2; confidence 0.8..<1; quality-margin 0..0.2")
                if args.metric == "quota_units" and not args.quota_key:
                    raise AdaptiveError("quota_units requires --quota-key; different plan units must not be combined")
                value = propose(args)
        elif args.action == "accept":
            value = accept(args, args.id)
        elif args.action in ("reject", "reopen", "undo"):
            value = decide(args)
        else:
            scope = scope_id(args.cd, args.host)
            if args.mode == "enable":
                print("WARNING: AUTO-ADJUST will change supported project lane models when suggest finds sufficient evidence. Session models and unsupported routes are never changed. Use undo to revert, or auto-adjust disable to stop.", file=sys.stderr)
                if not args.acknowledge:
                    raise AdaptiveError("Enabling requires --acknowledge after reading the warning")
            with connect() as db:
                if args.mode != "status":
                    db.execute("INSERT OR REPLACE INTO policies VALUES (?,?)", (scope, int(args.mode == "enable")))
                row = db.execute("SELECT enabled FROM policies WHERE scope=?", (scope,)).fetchone()
                value = {"auto_adjust": bool(row and row[0]), "trigger": "suggest command", "scope": "project"}
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return 0
    except (AdaptiveError, OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, AdaptiveError) else "Invalid local data or unavailable configuration; no raw content logged"
        print("arch-advisor adaptive: " + message, file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
