#!/usr/bin/env python3
"""Read-only model suggestions from AA's cost/intelligence Pareto frontier."""
import argparse
from datetime import datetime, timezone
import getpass
import json
import math
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

AA_URL = "https://artificialanalysis.ai/api/v2/language/models/free"
CATALOG_URL = "https://models.dev/api.json"
PROVIDERS = {"claude": "anthropic", "codex": "openai", "opencode": "opencode-go"}
SOURCE = "Artificial Analysis — https://artificialanalysis.ai/"
KEYCHAIN_SERVICE = "arch-advisor.artificial-analysis"
MIN_PERCENTILES = {"session": 75, "routine": 50, "complex": 90, "second-opinion": 90, "verification": 75}


class RecommendationError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the AA key to a redirected origin.
        return None


def https_context():
    context = ssl.create_default_context()
    paths = ssl.get_default_verify_paths()
    # python.org macOS installs can lack their optional CA bundle.
    if (sys.platform == "darwin" and not paths.cafile and not paths.capath
            and "SSL_CERT_FILE" not in os.environ and "SSL_CERT_DIR" not in os.environ
            and Path("/etc/ssl/cert.pem").is_file()):
        context.load_verify_locations(cafile="/etc/ssl/cert.pem")
    return context


def fetch_json(url, key=None):
    source = "Artificial Analysis" if url.split("?", 1)[0] == AA_URL else "Model catalog"
    headers = {"Accept": "application/json", "User-Agent": "arch-advisor-recommend/1"}
    if key:
        headers["x-api-key"] = key
    try:
        with build_opener(NoRedirect, HTTPSHandler(context=https_context())).open(Request(url, headers=headers), timeout=30) as response:
            body = response.read(32 * 1024 * 1024 + 1)
        if len(body) > 32 * 1024 * 1024:
            raise RecommendationError("API response is too large")
        return json.loads(body)
    except HTTPError as error:
        reason = {401: "invalid or missing API key", 403: "API access denied",
                  429: "rate limit reached; retry later"}.get(error.code, "request failed")
        raise RecommendationError(f"{source}: HTTP {error.code}: {reason}") from None
    except (URLError, OSError) as error:
        cause = error.reason if isinstance(error, URLError) else error
        if isinstance(cause, ssl.SSLCertVerificationError):
            reason = "HTTPS certificate verification failed; configure Python's trusted CA certificates"
            if sys.platform == "darwin":
                reason += "; try SSL_CERT_FILE=/etc/ssl/cert.pem when running this command"
        elif isinstance(cause, TimeoutError):
            reason = "request timed out; retry later"
        else:
            reason = "connection failed; check the network and retry"
        raise RecommendationError(f"{source}: {reason}") from None
    except ValueError:
        raise RecommendationError(f"{source}: response is not valid JSON") from None


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RecommendationError(f"Cannot read valid JSON from {path}") from None


def keychain_key(value=None):
    if sys.platform != "darwin":
        return ""
    try:
        if value is None:
            result = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", "api-key",
                                     "-s", KEYCHAIN_SERVICE, "-w"], capture_output=True, text=True, timeout=30)
            if result.returncode == 44:  # errSecItemNotFound (-25300), modulo 256.
                return ""
        else:
            # Send hex-encoded credential over stdin, never in process arguments.
            command = f"add-generic-password -U -a api-key -s {KEYCHAIN_SERVICE} -X {value.encode().hex()}\n"
            result = subprocess.run(["/usr/bin/security", "-i"], input=command,
                                    capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RecommendationError("Cannot access macOS Keychain; unlock it or set ARTIFICIAL_ANALYSIS_API_KEY")
        return result.stdout.strip() if value is None else ""
    except (OSError, subprocess.TimeoutExpired):
        raise RecommendationError("macOS Keychain unavailable; set ARTIFICIAL_ANALYSIS_API_KEY") from None


def api_key(interactive):
    key = os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY", "").strip() or keychain_key()
    prompted = False
    if not key and interactive:
        storage = "saved to macOS Keychain after validation" if sys.platform == "darwin" else "not saved; use ARTIFICIAL_ANALYSIS_API_KEY to reuse"
        key = getpass.getpass(f"AA API key (hidden; {storage}): ").strip()
        prompted = True
    if not key:
        raise RecommendationError("Set ARTIFICIAL_ANALYSIS_API_KEY; create a key at https://artificialanalysis.ai/api-key-management-redirect")
    return key, prompted


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def validate_page(value, page):
    if not isinstance(value, dict) or not isinstance(value.get("data"), list):
        raise RecommendationError("AA response must contain a data array")
    meta = value.get("pagination", {})
    if (not isinstance(meta, dict) or type(meta.get("page")) is not int
            or meta["page"] != page or type(meta.get("total_pages")) is not int
            or not page <= meta["total_pages"] <= 100
            or type(meta.get("has_more")) is not bool
            or meta["has_more"] != (page < meta["total_pages"])):
        raise RecommendationError("Invalid AA pagination; refusing an incomplete comparison")
    if not number(value.get("intelligence_index_version")):
        raise RecommendationError("Missing or invalid AA Intelligence Index version")
    seen = set()
    for model in value["data"]:
        if not isinstance(model, dict) or any(
            not isinstance(model.get(k), str) or not model[k] or not model[k].isprintable()
            for k in ("id", "name", "slug")
        ):
            raise RecommendationError("Invalid model identity in AA response")
        if model["id"] in seen:
            raise RecommendationError("Duplicate AA model ID; refusing an ambiguous comparison")
        seen.add(model["id"])
    return value


def load_aa(key):
    rows, seen = [], set()
    version, total_pages = None, None
    for page in range(1, 101):
        data = validate_page(fetch_json(f"{AA_URL}?page={page}", key), page)
        if page == 1:
            version = data["intelligence_index_version"]
            total_pages = data["pagination"]["total_pages"]
        if version != data["intelligence_index_version"] or total_pages != data["pagination"]["total_pages"]:
            raise RecommendationError("AA dataset changed during pagination; retry")
        for model in data["data"]:
            if model["id"] in seen:
                raise RecommendationError("Duplicate AA model ID across pages; retry")
            seen.add(model["id"])
            rows.append(model)
        if not data["pagination"]["has_more"]:
            return {"data": rows, "intelligence_index_version": version}
    raise RecommendationError("AA pagination exceeded its safety limit")


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


def model_name(model):
    # Strip only reasoning labels, never version dates or Preview/Free/Contributor.
    name = re.sub(r"\s*\((?:max|xhigh|high|medium|low|non-reasoning|reasoning)(?:, [^()]*)?\)$",
                  "", model["name"], flags=re.I)
    return normalized(name)


def available_models(catalog, providers, override=None):
    if not isinstance(catalog, dict):
        raise RecommendationError("Model catalog must be an object")
    if override is not None and (not isinstance(override, dict) or set(override) - set(PROVIDERS)):
        raise RecommendationError("--available must map claude/codex/opencode to catalog model IDs")
    entries, warnings = [], []
    for provider in providers:
        section = catalog.get(PROVIDERS[provider])
        models = section.get("models") if isinstance(section, dict) else None
        if not isinstance(models, dict) or not models:
            raise RecommendationError(f"No model catalog for {provider}; cannot verify candidates")
        allowed = None
        if override is not None:
            allowed = override.get(provider)
            if not isinstance(allowed, list) or any(not isinstance(x, str) for x in allowed):
                raise RecommendationError(f"--available needs a model ID list for {provider}")
            if any(x not in models for x in allowed):
                raise RecommendationError(f"Unknown {provider} model ID in --available")
        for model_id, model in models.items():
            if allowed is not None and model_id not in allowed:
                continue
            if not isinstance(model, dict) or model.get("status") in ("deprecated", "alpha"):
                continue
            modalities = model.get("modalities")
            if (model.get("tool_call") is not True or not isinstance(modalities, dict)
                    or not isinstance(modalities.get("output"), list) or "text" not in modalities["output"]):
                continue
            if not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9._:/+@-]+", model_id):
                continue
            keys = {normalized(model_id)}
            canonical = model.get("canonical_model_id")
            for value in (model.get("name"), canonical.split("/")[-1] if isinstance(canonical, str) else None):
                if isinstance(value, str) and value:
                    keys.add(normalized(value))
            entries.append({"provider": provider, "model_id": model_id, "keys": keys})
        if allowed is None:
            warnings.append(f"{provider}: public catalog only; confirm model/effort access in your account or use --available.")
    return entries, warnings


def candidates(rows, entries):
    result, skipped, matched = [], [], set()
    # Prefer release slugs over display names (e.g. an older identically named Qwen).
    releases = {}
    for index, entry in enumerate(entries):
        exact = [r for r in rows if normalized(r["slug"]) in entry["keys"]]
        releases[index] = {model_name(r) for r in exact} if exact else entry["keys"]
    for model in rows:
        matches = [entry for index, entry in enumerate(entries) if model_name(model) in releases[index]]
        if not matches:
            continue
        for entry in matches:
            matched.add((entry["provider"], entry["model_id"]))
        evaluations = model.get("evaluations") or {}
        cost_block = model.get("artificial_analysis_intelligence_index_cost") or {}
        task_cost = (cost_block.get("cost_per_task") or {}) if isinstance(cost_block, dict) else {}
        score = evaluations.get("artificial_analysis_intelligence_index") if isinstance(evaluations, dict) else None
        cost = task_cost.get("total_cost") if isinstance(task_cost, dict) else None
        if not number(score) or not number(cost):
            skipped.append({"id": model["id"], "name": model["name"], "reason": "missing/invalid intelligence or cost per task"})
            continue
        result.append({"id": model["id"], "slug": model["slug"], "name": model["name"],
                       "intelligence": score, "cost_per_task_usd": cost,
                       "access": [{"provider": m["provider"], "model_id": m["model_id"]} for m in matches]})
    unmatched = [{"provider": e["provider"], "model_id": e["model_id"]} for e in entries
                 if (e["provider"], e["model_id"]) not in matched]
    return result, skipped, unmatched


def pareto(rows):
    return sorted((row for row in rows if not any(
        other["intelligence"] >= row["intelligence"]
        and other["cost_per_task_usd"] <= row["cost_per_task_usd"]
        and (other["intelligence"] > row["intelligence"]
             or other["cost_per_task_usd"] < row["cost_per_task_usd"])
        for other in rows)), key=lambda r: (r["cost_per_task_usd"], -r["intelligence"], r["id"]))


def choices(frontier):
    low, high = frontier[0], max(frontier, key=lambda r: (r["intelligence"], -r["cost_per_task_usd"]))
    cost_range = high["cost_per_task_usd"] - low["cost_per_task_usd"]
    score_range = high["intelligence"] - low["intelligence"]
    def distance(row):
        return ((row["cost_per_task_usd"] - low["cost_per_task_usd"]) / (cost_range or 1)) ** 2 + (
            (high["intelligence"] - row["intelligence"]) / (score_range or 1)) ** 2
    balanced = min(frontier, key=lambda r: (distance(r), r["cost_per_task_usd"], r["id"]))
    return {"economical": low, "balanced": balanced, "most_capable": high}


def quality_thresholds(data, minimums):
    scores = []
    for row in data["data"]:
        evaluations = row.get("evaluations")
        score = evaluations.get("artificial_analysis_intelligence_index") if isinstance(evaluations, dict) else None
        if number(score):
            scores.append(score)
    scores.sort()
    if not scores:
        raise RecommendationError("No valid AA intelligence scores for quality thresholds")
    thresholds = {lane: {"min_percentile": percentile,
                         "min_intelligence": scores[max(0, math.ceil(percentile / 100 * len(scores)) - 1)]}
                  for lane, percentile in minimums.items()}
    return thresholds, len(scores)


def lane_recommendations(frontier, host, thresholds, quotas=None):
    from quota import tier
    specs = [
        ("session", "Session", "Architecture and coordination", [host],
         "/model", "Architecture, planning and coordination"),
        ("routine", "implementer-routine", "Routine implementation", ["codex"],
         "implementer-routine agent" if host == "claude" else "advisor.py run routine",
         "The spec determines the outcome; mechanical edits and ordinary features"),
        ("complex", "implementer-complex", "Complex implementation", ["codex"],
         "implementer-complex agent" if host == "claude" else "advisor.py run complex",
         "Subtle concurrency, hard debugging, security-sensitive paths and wide refactors"),
        ("second-opinion", "second-opinion", "Independent read-only advice and final review",
         ["codex"] if host == "claude" else ["claude", "codex"],
         "second-opinion agent" if host == "claude" else "advisor.py opinion",
         "Commitment boundaries and always once at the end, in a separate context"),
        ("verification", "verifier", "Verification and tests", [], "",
         "After implementation: run tests, validate acceptance criteria and check regressions; report evidence"),
    ]
    result = []
    for lane, component, role, providers, invocation, route_when in specs:
        threshold = thresholds[lane]
        eligible = [row for row in frontier if row["intelligence"] >= threshold["min_intelligence"]]
        routes = [(tier(quotas or {}, a["provider"], a["model_id"]), row, a) for row in eligible for a in row["access"]]
        best_tier = max((t for t, _, _ in routes if t >= 0), default=None)
        eligible = [r for t, r, _ in routes if t == best_tier]
        selected = min(eligible, key=lambda r: (r["cost_per_task_usd"], -r["intelligence"], r["id"])) if eligible else None
        # Route support describes execution; it must never filter the ranking.
        accesses = [a for t, r, a in routes if r is selected and t == best_tier]
        access = next((a for a in accesses if a["provider"] in providers), accesses[0]) if accesses else None
        supported = bool(access and access["provider"] in providers)
        effort = re.search(r"\((low|medium|high|xhigh|max)(?:, [^()]*)?\)$", selected["name"], re.I) if selected else None
        result.append({"lane": lane, "component": component, "role": role,
                       "policy": "cheapest_above_quality_floor", **threshold, "route_supported": supported,
                       "invocation": invocation if supported else access["provider"] + " manually; plugin route unavailable" if access else "—",
                       "route_when": route_when, "model": selected, "access": access,
                       "benchmark_effort": effort.group(1).lower() if effort else None,
                       "reason": "Cheapest eligible model meeting the quality floor within the preferred quota tier" if selected else
                                 "No model in your active subscriptions meets this quality floor with usable quota; no automatic downgrade"})
    return result


def report(data, entries, warnings, source, host="codex", minimums=None, quotas=None):
    rows, skipped, unmatched = candidates(data["data"], entries)
    frontier = pareto(rows)
    if not frontier:
        raise RecommendationError("No matched models have both intelligence and cost per task. Check access IDs and AA coverage.")
    from quota import tier
    usable = []
    for row in rows:
        accesses = [a for a in row["access"] if tier(quotas or {}, a["provider"], a["model_id"]) >= 0]
        if accesses:
            usable.append(dict(row, access=accesses))
    frontier = pareto(usable)
    thresholds, population_count = quality_thresholds(data, minimums if minimums is not None else MIN_PERCENTILES)
    return {"source": SOURCE, "data_source": source, "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "intelligence_index_version": data["intelligence_index_version"],
            "axes": {"maximize": "intelligence", "minimize": "cost_per_task_usd"},
            "cost_basis": "AA benchmark API cost per task; NOT subscription price, quota or Go allowance consumption",
            "balance_method": "Equal-weight normalized distance to ideal intelligence/cost within the filtered frontier",
            "recommendations": choices(frontier) if frontier else {}, "host": host,
            "selection_method": "Meet each role's quality floor, prefer usable quota at a sustainable pace, then minimize AA cost",
            "quality_reference": {"population_count": population_count,
                                  "population": "All AA entries with valid Intelligence Index, including effort variants, before subscription filtering",
                                  "method": "Nearest-rank percentile of general intelligence, computed locally; not an AA coding percentile"},
            "subscription_quota": quotas or {},
            "lane_recommendations": lane_recommendations(rows, host, thresholds, quotas),
            "frontier": frontier, "candidate_count": len(rows), "skipped": skipped, "unmatched": unmatched,
            "warnings": warnings + ["General Intelligence Index, not a coding-only ranking. Benchmark effort variants are shown; CLI support is not verified.",
                                      "Public catalogs do not prove subscription entitlement, remaining quota, region or privacy eligibility.",
                                      "Lane policies are advisor heuristics, not AA endorsements. Review independence requires a separate context; models may repeat.",
                                      "All active subscriptions compete for every role. Unsupported plugin routes are marked for manual use, not excluded from ranking."]}


def print_report(value):
    print(f"\n{SOURCE}\nIndex v{value['intelligence_index_version']} | {value['data_source']} | {value['retrieved_at']}")
    print(value["cost_basis"])
    for provider, reading in value.get("subscription_quota", {}).items():
        detail = ", ".join(f"{w['name']} {w['remaining_percent']:g}% remaining until {datetime.fromtimestamp(w['resets_at'], timezone.utc):%Y-%m-%d %H:%M UTC}" for w in reading['windows'])
        print(f"Quota {provider}: {reading['status']} {detail}")
    print(f"\nSuggested setup for the {value['host'].title()} plugin (not applied):")
    print("| Component | Role | Minimum quality | Suggested model (AA variant) | Access / model ID | Invocation | Route here when |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for lane in value["lane_recommendations"]:
        row, access = lane["model"], lane["access"]
        cells = [lane["component"], lane["role"], f"P{lane['min_percentile']:g} / AA >= {lane['min_intelligence']:g}",
                 row["name"] if row else "No qualifying model",
                 f"{access['provider']}: {access['model_id']}" if access else "—", lane["invocation"], lane["route_when"]]
        print("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    for lane in value["lane_recommendations"]:
        row = lane["model"]
        metrics = f" | AA {row['intelligence']:g} | ${row['cost_per_task_usd']:.4f}/task" if row else ""
        print(f"  {lane['component']}: {lane['reason']}{metrics}")
    print("\nPareto frontier (no cheaper equally capable candidate in your selection):")
    for row in value["frontier"]:
        print(f"  {row['name']} | AA {row['intelligence']:g} | ${row['cost_per_task_usd']:.4f}/task")
    print(f"\n{len(value['skipped'])} excluded for missing metrics; {len(value['unmatched'])} catalog entries without an exact AA match.")
    print("Selection: " + value["selection_method"])
    print(f"Quality reference: {value['quality_reference']['population_count']} AA entries; " + value["quality_reference"]["method"])
    for warning in value["warnings"]:
        print("Note: " + warning)


def profile_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "arch-advisor/recommendations.json"


def ask_providers(path):
    saved = read_json(path) if path.exists() else {"providers": ["claude", "codex"]}
    if (not isinstance(saved, dict) or not isinstance(saved.get("providers"), list)
            or any(not isinstance(p, str) or p not in PROVIDERS for p in saved["providers"])):
        raise RecommendationError("Invalid recommendation access profile")
    selected = []
    print("Tens acesso a quê? (s/n; Enter mantém a seleção; q cancela)")
    for provider in PROVIDERS:
        default = provider in saved["providers"]
        label = "OpenCode Go" if provider == "opencode" else provider.title()
        while True:
            answer = input(f"  [{'✓' if default else '✗'}] {label}: ").strip().lower()
            if answer == "q":
                raise KeyboardInterrupt
            if answer in ("", "s", "sim", "y", "yes", "n", "não", "nao", "no"):
                enabled = default if not answer else answer in ("s", "sim", "y", "yes")
                if enabled:
                    selected.append(provider)
                break
    return selected


def save_profile(path, providers):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".recommendations-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"providers": providers}, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def percentile_override(value):
    try:
        lane, raw = value.split("=", 1)
        percentile = float(raw)
        if lane not in MIN_PERCENTILES or not number(percentile) or percentile > 100:
            raise ValueError
        return lane, percentile
    except ValueError:
        raise argparse.ArgumentTypeError("Use " + "|".join(MIN_PERCENTILES) + "=NUMBER, with NUMBER between 0 and 100") from None


def add_arguments(parser, default_host="codex"):
    parser.add_argument("--min-percentile", action="append", type=percentile_override, default=[], metavar="ROLE=0..100",
                        help="Override the minimum general-intelligence percentile; repeat per role. Defaults: " + ", ".join(f"{role}={value}" for role, value in MIN_PERCENTILES.items()))
    parser.add_argument("--host", choices=("claude", "codex"), default=default_host,
                        help="Current plugin, used only to label execution support; never filters recommendations")
    parser.add_argument("--providers", nargs="+", choices=PROVIDERS, help="Skip the interactive access questions")
    parser.add_argument("--available", type=Path, help="JSON map of selected providers to account-confirmed catalog model IDs")
    parser.add_argument("--data", type=Path, help="Complete single-page AA JSON snapshot instead of the live API")
    parser.add_argument("--catalog", type=Path, help="models.dev JSON snapshot instead of the live catalog")
    parser.add_argument("--profile", type=Path, default=profile_path(), help="Access preference file; contains no keys")
    parser.add_argument("--json", action="store_true")


def run(args):
    interactive = args.providers is None
    if interactive and (not sys.stdin.isatty() or not sys.stdout.isatty() or args.json):
        raise RecommendationError("Use --providers claude codex [opencode] outside an interactive terminal")
    providers = ask_providers(args.profile) if interactive else list(dict.fromkeys(args.providers))
    if not providers:
        raise RecommendationError("Select at least one provider")
    if args.data:
        data = validate_page(read_json(args.data), 1)
        if data["pagination"]["has_more"]:
            raise RecommendationError("--data must include all models in a single-page snapshot")
    else:
        key, prompted = api_key(interactive)
        data = load_aa(key)
        if prompted:
            try:
                keychain_key(key)
            except RecommendationError as error:
                print(f"Warning: key not saved. {error}", file=sys.stderr)
    catalog = read_json(args.catalog) if args.catalog else fetch_json(CATALOG_URL)
    entries, warnings = available_models(catalog, providers, read_json(args.available) if args.available else None)
    minimums = dict(MIN_PERCENTILES, **dict(args.min_percentile))
    import quota
    quotas = quota.read(providers, offline=bool(args.catalog))
    if "opencode" in quotas:
        quotas["opencode"]["free_models"] = quota.free_go_models(catalog)
    value = report(data, entries, warnings, f"local snapshot: {args.data}" if args.data else AA_URL, args.host, minimums, quotas)
    if args.json:
        print(json.dumps(value, indent=2, ensure_ascii=False))
    else:
        print_report(value)
    if interactive:
        save_profile(args.profile, providers)
    return 0


def main(argv=None, default_host="codex"):
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser, default_host)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (RecommendationError, OSError) as error:
        print(f"arch-advisor recommend: {error}", file=sys.stderr)
        return 4
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled; no access preferences saved.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
