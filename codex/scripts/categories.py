"""One project preference per host, role and exact task class."""
import hashlib
import json
import os
from pathlib import Path
import re


def category_path(workspace, host, role, context):
    # Task class identifiers can include slash; never interpolate them into paths.
    key = hashlib.sha256(context.encode()).hexdigest()
    return Path(workspace) / ".arch-advisor" / f"category-{host}-{role}-{key}.json"


def apply(config, workspace, host, context=None):
    if os.environ.get("ARCH_ADVISOR_CODEX_CONFIG" if host == "codex" else "ARCH_ADVISOR_CONFIG"):
        return config
    context = context or os.environ.get("ARCH_ADVISOR_TASK_CLASS", "general")
    for role in ("routine", "complex", "second-opinion"):
        path = category_path(workspace, host, role, context)
        if path.is_symlink() or path.parent.is_symlink() and path.exists():
            raise ValueError("Refusing symlinked category configuration")
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        if (data["context"] != context
                or data["role"] != role or data["host"] != host):
            raise ValueError("Invalid category configuration")
        provider, model, effort = (data[key] for key in ("provider", "model", "effort"))
        if (provider != "codex" and not (host == "codex" and role == "second-opinion" and provider == "claude")
                or not isinstance(model, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/@+\-]{0,199}", model)
                or effort not in (None, "low", "medium", "high", "xhigh", "max")):
            raise ValueError("Invalid category model or effort")
        if host == "claude":
            lane = config["lanes"]["2nd-advisor" if role == "second-opinion" else role]
            lane.update(model=model, default_effort=effort)
        else:
            lane = config["lanes"][role]
            if role == "second-opinion":
                lane["provider"] = provider
                lane = lane[provider]
            lane.update(model=model, effort=effort)
    return config
