"""Subscription quota readings. No inference, credentials on disk or paid fallback."""
from datetime import datetime
import json
import math
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time
from urllib.request import Request, HTTPSHandler, build_opener

HOUR = 3600
DAY = 24 * HOUR
MAX_AGE = 15 * 60


def claude_file():
    directory = Path(os.environ.get("ARCH_ADVISOR_STATE_DIR",
                     Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "arch-advisor"))
    return directory / "claude-quota.json"


def window(name, used, reset, duration):
    if isinstance(reset, str):
        dt = datetime.fromisoformat(reset.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Reset time needs a timezone")
        reset = dt.timestamp()
    if any(type(n) not in (int, float) or not math.isfinite(n) for n in (used, reset, duration)) or used < 0 or duration <= 0:
        raise ValueError("Invalid subscription window")
    return dict(name=name, remaining_percent=max(0, 100-used), resets_at=reset, duration_seconds=duration)


def save_claude(payload):
    limits = payload.get("rate_limits") or {}
    windows = [window(k, limits[k]["used_percentage"], limits[k]["resets_at"], duration)
               for k, duration in (("five_hour", 5*HOUR), ("seven_day", 7*DAY)) if limits.get(k)]
    path = claude_file()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    from adaptive import atomic_write
    atomic_write(path, json.dumps(dict(observed_at=time.time(), windows=windows)).encode())
    return windows


def codex_limits():
    # Keep stdin open until the response arrives. No thread or turn is started.
    with tempfile.TemporaryDirectory() as cwd, subprocess.Popen(
            ["codex", "app-server"], cwd=cwd, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        try:
            def send(message):
                proc.stdin.write(json.dumps(message).encode() + b"\n")
                proc.stdin.flush()
            send(dict(id=1, method="initialize", params={"clientInfo": {"name":"arch-advisor","version":"1"}}))
            deadline = time.monotonic() + 5
            buf = b""
            while time.monotonic() < deadline:
                if not select.select([proc.stdout], [], [], max(0, deadline-time.monotonic()))[0]:
                    break
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk or len(buf) > 2**20:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    reply = json.loads(line)
                    if reply.get("id") in (1, 2) and "error" in reply:
                        raise ValueError("Quota read unavailable")
                    if reply.get("id") == 1:
                        send(dict(method="initialized"))
                        send(dict(id=2, method="account/rateLimits/read", params={}))
                    elif reply.get("id") == 2:
                        return reply["result"]
        finally:
            proc.kill()
    raise ValueError("Quota read timed out")


def provider_read(provider):
    if provider == "claude":
        snapshot = json.loads(claude_file().read_text())
        if not 0 <= time.time() - snapshot["observed_at"] < MAX_AGE:
            raise ValueError("Expired status line")
        return snapshot["windows"], False
    if provider == "codex":
        result = codex_limits()
        meters = result["rateLimits"]
        windows = [window(k, m["usedPercent"], m["resetsAt"], m["windowDurationMins"] * 60)
                   for k in ("primary", "secondary") if (m := meters.get(k))]
        return windows, result.get("ordinaryUsageAllowed") is False
    from recommend import NoRedirect, https_context
    key = os.environ.get("OPENCODE_GO_API_KEY")
    if not key:
        raise ValueError("Go key unavailable")
    request = Request("https://opencode.ai/inference/go/v1/usage",
                      headers={"Authorization": "Bearer " + key, "User-Agent": "arch-advisor/1"})
    with build_opener(NoRedirect, HTTPSHandler(context=https_context())).open(request, timeout=5) as response:
        meters = json.loads(response.read(65536))["usage"]
    windows = []
    for k, duration in (("rolling", 5*HOUR), ("weekly", 7*DAY), ("monthly", 30*DAY)):
        m = meters[k]
        windows.append(window(k, 100 if m["status"] == "rate-limited" else m["percent"], m["resetsAt"], duration))
    return windows, False


def read(providers, offline=False):
    readings = {}
    for p in sorted(set(providers)):
        item = {"status": "unknown", "windows": []}
        if not offline:
            try:
                windows, blocked = provider_read(p)
                now = time.time()
                active = [w for w in windows if w["resets_at"] > now]
                if blocked or any(w["remaining_percent"] == 0 for w in active):
                    item.update(status="exhausted", windows=active)
                elif windows and len(active) == len(windows):
                    # 1 means remaining share matches the fraction of the window left.
                    pace = min(w["remaining_percent"] / 100 / min(1, (w["resets_at"]-now)/w["duration_seconds"]) for w in active)
                    item.update(status="available", pace=pace, windows=active)
            except (ValueError, KeyError, TypeError, AttributeError, OSError, OverflowError, subprocess.SubprocessError):
                pass
        readings[p] = item
    return readings


def free_go_models(catalog):
    result = set()
    section = catalog.get("opencode-go")
    models = section.get("models") if isinstance(section, dict) else None
    if not isinstance(models, dict):
        return []
    for model, entry in models.items():
        cost = entry.get("cost") if isinstance(entry, dict) else None
        if not isinstance(cost, dict):
            continue
        if cost.get("input") == cost.get("output") == 0 and not cost.get("tiers") and all(v == 0 for v in cost.values()):
            result.add(model)
    return sorted(result)


def tier(readings, provider, model=None):
    item = readings.get(provider, {})
    if provider == "opencode" and model in item.get("free_models", ()):
        return 2
    if item.get("status") == "exhausted":
        return -1
    if item.get("status") != "available":
        return 0
    return 2 if item["pace"] >= 1 else 1
