"""Synthetic AA v2 / models.dev contracts; no credentials or inference calls."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "codex/scripts/recommend.py"
spec = importlib.util.spec_from_file_location("recommend", SCRIPT)
recommend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recommend)


def model(slug, score, cost, name=None):
    return {"id": "uuid-" + slug, "slug": slug, "name": name or slug,
            "evaluations": {"artificial_analysis_intelligence_index": score,
                            "artificial_analysis_coding_index": None},
            "artificial_analysis_intelligence_index_cost": {"cost_per_task": {"total_cost": cost}}}


def page(rows, current=1, total=1, version=5):
    return {"tier": "free", "intelligence_index_version": version,
            "pagination": {"page": current, "page_size": 200, "total_pages": total, "has_more": current < total},
            "data": rows}


def catalog():
    def provider(ids):
        return {"models": {i: {"id": i, "name": i, "tool_call": True,
                              "modalities": {"output": ["text"]}} for i in ids}}
    return {"anthropic": provider(["claude-small", "claude-large"]),
            "openai": provider(["gpt-fast", "gpt-smart", "gpt-old", "gpt-no-cost"]),
            "opencode-go": provider(["deepseek-new", "unknown-free"])}


def dataset():
    return page([model("claude-small", 20, 1), model("claude-large", 55, 9),
                 model("gpt-fast", 30, 2), model("gpt-smart", 50, 4), model("gpt-old", 25, 3),
                 model("deepseek-new", 40, 1), model("gpt-no-cost", 99, None)])


class RecommendationTests(unittest.TestCase):
    def test_macos_ca_fallback_preserves_explicit_and_existing_trust(self):
        paths = ssl.get_default_verify_paths()._replace(cafile=None, capath=None)
        for platform, env, cafile, capath, exists, fallback in [
            ("darwin", {}, None, None, True, True),
            ("darwin", {}, None, None, False, False),
            ("linux", {}, None, None, True, False),
            ("darwin", {"SSL_CERT_FILE": "custom.pem"}, None, None, True, False),
            ("darwin", {"SSL_CERT_DIR": "custom"}, None, None, True, False),
            ("darwin", {}, "installed.pem", None, True, False),
            ("darwin", {}, None, "installed", True, False),
        ]:
            with self.subTest(platform=platform, env=env, cafile=cafile, capath=capath, exists=exists), \
                 patch.object(sys, "platform", platform), patch.dict(os.environ, env, clear=True), \
                 patch.object(ssl, "get_default_verify_paths", return_value=paths._replace(cafile=cafile, capath=capath)), \
                 patch.object(ssl, "create_default_context") as create, patch.object(Path, "is_file", return_value=exists):
                self.assertIs(recommend.https_context(), create.return_value)
                self.assertEqual(create.return_value.load_verify_locations.called, fallback)
                if fallback:
                    create.return_value.load_verify_locations.assert_called_once_with(cafile="/etc/ssl/cert.pem")
        context = recommend.https_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_every_active_subscription_competes_for_every_lane(self):
        data = dataset()
        data["data"][3]["name"] = "gpt-smart (High)"
        for providers in [["codex"], ["claude"], ["opencode"], ["claude", "codex"],
                          ["codex", "opencode"], ["claude", "opencode"], ["claude", "codex", "opencode"]]:
            entries, warnings = recommend.available_models(catalog(), providers)
            results = [recommend.report(data, entries, warnings, "fixture", host) for host in ["claude", "codex"]]
            self.assertEqual([r["model"] for r in results[0]["lane_recommendations"]],
                             [r["model"] for r in results[1]["lane_recommendations"]])
            for result in results:
                for lane in result["lane_recommendations"]:
                    self.assertIn(lane["access"]["provider"], providers)
                    self.assertIn(lane["model"], result["frontier"])
                    self.assertEqual(lane["model"], result["recommendations"][lane["policy"]])
            if providers == ["claude", "codex"]:
                lanes = results[0]["lane_recommendations"]
                self.assertEqual(lanes[1]["model"]["slug"], "gpt-smart")
                self.assertEqual(lanes[1]["benchmark_effort"], "high")
                self.assertEqual(lanes[2]["access"]["provider"], "codex")
                self.assertTrue(lanes[2]["route_supported"])
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    recommend.print_report(results[0])
                for label in ["| Component |", "implementer-routine", "implementer-complex", "second-opinion", "gpt-smart (High)"]:
                    self.assertIn(label, output.getvalue())

    def test_opencode_can_win_all_lanes_without_a_native_route(self):
        entries, warnings = recommend.available_models(catalog(), ["claude", "codex", "opencode"])
        data = dataset()
        data["data"][5] = model("deepseek-new", 100, 0)
        for host in ["claude", "codex"]:
            result = recommend.report(data, entries, warnings, "fixture", host)
            for lane in result["lane_recommendations"]:
                self.assertEqual(lane["model"]["slug"], "deepseek-new")
                self.assertEqual(lane["access"]["provider"], "opencode")
                self.assertFalse(lane["route_supported"])
                self.assertIn("manually", lane["invocation"])

    def test_price_quality_for_all_roles_with_reported_aa_frontier(self):
        # Regression: do not recommend Fable Max at $26.54 just for its top score.
        points = [(9.7, .0051), (18.5, .0422), (18.6, .0438), (29.4, .0713),
                  (29.9, .1586), (34.8, .1615), (38.2, .1688), (42.4, .2593),
                  (46.3, .3594), (50.3, .5910), (52.5, .7640), (54.6, 1.2721),
                  (54.8, 3.2386), (55.3, 3.6589), (56.4, 3.8026), (57.4, 6.0472),
                  (61.8, 9.0784), (62.2, 14.8286), (64.6, 26.5386)]
        rows = [{"id": str(i), "name": str(i), "intelligence": score, "cost_per_task_usd": cost,
                 "access": [{"provider": "codex" if i < 14 else "claude", "model_id": str(i)}]}
                for i, (score, cost) in enumerate(points)]
        for host in ["claude", "codex"]:
            lanes = recommend.lane_recommendations(recommend.pareto(rows), host)
            self.assertEqual({r["policy"] for r in lanes}, {"balanced"})
            self.assertEqual({r["model"]["intelligence"] for r in lanes}, {54.6})
            self.assertEqual({r["model"]["cost_per_task_usd"] for r in lanes}, {1.2721})

    def test_frontier_filters_access_before_dominance(self):
        entries, warnings = recommend.available_models(catalog(), ["codex"])
        result = recommend.report(dataset(), entries, warnings, "fixture")
        self.assertEqual([r["slug"] for r in result["frontier"]], ["gpt-fast", "gpt-smart"])
        self.assertEqual(result["recommendations"]["most_capable"]["slug"], "gpt-smart")
        self.assertEqual(len(result["skipped"]), 1)
        entries, warnings = recommend.available_models(catalog(), ["claude", "codex", "opencode"])
        result = recommend.report(dataset(), entries, warnings, "fixture")
        self.assertEqual([r["slug"] for r in result["frontier"]], ["deepseek-new", "gpt-smart", "claude-large"])
        self.assertEqual(result["recommendations"]["balanced"]["slug"], "gpt-smart")
        self.assertEqual(result["unmatched"], [{"provider": "opencode", "model_id": "unknown-free"}])

    def test_pareto_strict_dominance_ties_and_zero(self):
        rows = [{"id": i, "intelligence": s, "cost_per_task_usd": c}
                for i, s, c in [("a", 0, 0), ("b", 1, 0), ("c", 1, 0), ("d", 1, 1), ("e", 2, 2)]]
        self.assertEqual([r["id"] for r in recommend.pareto(rows)], ["b", "c", "e"])

    def test_single_candidate_has_all_three_labels(self):
        entries, warnings = recommend.available_models(catalog(), ["codex"], {"codex": ["gpt-smart"]})
        result = recommend.report(dataset(), entries, warnings, "fixture")
        self.assertEqual({r["slug"] for r in result["recommendations"].values()}, {"gpt-smart"})

    def test_missing_nan_negative_and_boolean_metrics_are_not_zero(self):
        entries, _ = recommend.available_models(catalog(), ["codex"])
        for bad in [None, float("nan"), float("inf"), -1, True, "1"]:
            with self.subTest(bad=bad):
                for row in [model("gpt-fast", bad, 1), model("gpt-fast", 1, bad)]:
                    candidates, skipped, _ = recommend.candidates([row], entries)
                    self.assertEqual(candidates, [])
                    self.assertEqual(len(skipped), 1)

    def test_unknown_metrics_and_catalog_do_not_fall_back_to_rankings(self):
        entries, _ = recommend.available_models(catalog(), ["opencode"])
        with self.assertRaises(recommend.RecommendationError):
            recommend.report(dataset(), [], [], "fixture")
        for bad in [None, [], {"openai": {"models": []}}]:
            with self.assertRaises(recommend.RecommendationError):
                recommend.available_models(bad, ["codex"])

    def test_exact_release_and_effort_mapping_preserves_variants(self):
        cat = {"openai": {"models": {"gpt-6.1-sol": {"name": "GPT-6.1 Sol", "tool_call": True,
               "modalities": {"output": ["text"]}}}}}
        entries, _ = recommend.available_models(cat, ["codex"])
        rows = [model("gpt-6-1-sol", 50, 3, "GPT-6.1 Sol (Max)"),
                model("gpt-6-1-sol-high", 40, 1, "GPT-6.1 Sol (High)"),
                model("gpt-6-1-sol-preview", 80, 0.1, "GPT-6.1 Sol Preview")]
        candidates, _, _ = recommend.candidates(rows, entries)
        self.assertEqual([r["slug"] for r in candidates], ["gpt-6-1-sol", "gpt-6-1-sol-high"])
        self.assertEqual(candidates[0]["access"][0]["model_id"], "gpt-6.1-sol")

    def test_slug_match_prevents_old_identically_named_release(self):
        entries = [{"provider": "opencode", "model_id": "qwen3.8-max", "keys": {"qwen38max"}}]
        rows = [model("qwen3-8-max", 50, 2, "Qwen3.8 Max (0902)"),
                model("qwen3-8-max-0803", 60, 1, "Qwen3.8 Max")]
        candidates, _, _ = recommend.candidates(rows, entries)
        self.assertEqual([r["slug"] for r in candidates], ["qwen3-8-max"])

    def test_free_preview_contributor_not_guessed_without_canonical_identity(self):
        entries = [{"provider": "opencode", "model_id": "beam-free", "keys": {"beamfree"}}]
        self.assertEqual(recommend.candidates([model("beam-1-0", 10, 1, "Beam 1.0")], entries)[0], [])

    def test_shared_model_keeps_both_access_routes(self):
        cat = catalog()
        cat["opencode-go"]["models"]["claude-small"] = cat["anthropic"]["models"]["claude-small"]
        entries, _ = recommend.available_models(cat, ["claude", "opencode"])
        rows, _, _ = recommend.candidates([model("claude-small", 20, 1)], entries)
        self.assertEqual(len(rows), 1)
        self.assertEqual([a["provider"] for a in rows[0]["access"]], ["claude", "opencode"])

    def test_available_allowlist_is_strict(self):
        for bad in [[], {"unknown": []}, {"codex": "gpt-smart"}, {"codex": ["typo"]}, {}]:
            with self.assertRaises(recommend.RecommendationError):
                recommend.available_models(catalog(), ["codex"], bad)

    def test_pagination_all_pages_and_key_header_boundary(self):
        with patch.object(recommend, "fetch_json", side_effect=[page(dataset()["data"][:2], 1, 2), page(dataset()["data"][2:], 2, 2)]) as get:
            result = recommend.load_aa("test-secret")
        self.assertEqual(len(result["data"]), len(dataset()["data"]))
        self.assertEqual([c.args for c in get.call_args_list], [(recommend.AA_URL + "?page=1", "test-secret"), (recommend.AA_URL + "?page=2", "test-secret")])
        self.assertNotIn("test-secret", json.dumps(result))

    def test_inconsistent_pagination_versions_and_duplicate_ids_fail(self):
        first = page([model("x", 1, 1)], 1, 2)
        for second in [page([model("y", 2, 2)], 2, 2, version=6),
                       page([model("x", 1, 1)], 2, 2), page([], 1, 2), page([], 2, 3)]:
            with patch.object(recommend, "fetch_json", side_effect=[first, second]):
                with self.assertRaises(recommend.RecommendationError):
                    recommend.load_aa("secret")

    def test_http_and_connection_failures_do_not_leak_credentials_or_body(self):
        for error in [HTTPError("https://example.test", code, "secret", {}, None) for code in [401, 403, 429, 500]] + [URLError("secret")]:
            with patch.object(recommend, "build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(recommend.RecommendationError) as raised:
                    recommend.fetch_json(recommend.AA_URL, "secret")
            self.assertNotIn("secret", str(raised.exception))
        self.assertIsNone(recommend.NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.test"))

    def test_fetch_diagnoses_source_and_failure_without_leaking_details(self):
        for error, message in [
            (URLError(ssl.SSLCertVerificationError(1, "secret")), "certificate verification failed"),
            (ssl.SSLCertVerificationError(1, "secret"), "certificate verification failed"),
            (URLError(TimeoutError("secret")), "request timed out"),
            (TimeoutError("secret"), "request timed out"),
            (URLError("secret"), "connection failed"),
        ]:
            with self.subTest(error=type(error).__name__, message=message), patch.object(recommend, "build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(recommend.RecommendationError) as raised:
                    recommend.fetch_json(recommend.AA_URL + "?page=1", "secret")
                self.assertTrue(str(raised.exception).startswith("Artificial Analysis: "))
                self.assertIn(message, str(raised.exception))
                self.assertNotIn("secret", str(raised.exception))
        with patch.object(recommend, "build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = b"not JSON secret"
            with self.assertRaisesRegex(recommend.RecommendationError, "^Model catalog: response is not valid JSON$"):
                recommend.fetch_json(recommend.CATALOG_URL)

    def test_read_only_end_to_end_all_entrypoints_and_portable_package(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            data, cat, access = base / "aa.json", base / "catalog.json", base / "access.json"
            data.write_text(json.dumps(dataset()))
            cat.write_text(json.dumps(catalog()))
            access.write_text(json.dumps({"codex": ["gpt-smart", "gpt-fast"]}))
            env = dict(os.environ, CODEX_HOME=str(base / "home"), XDG_CONFIG_HOME=str(base / "config"))
            env.pop("ARTIFICIAL_ANALYSIS_API_KEY", None)
            portable = base / "copied-codex"
            shutil.copytree(ROOT / "codex", portable)
            before = {p: p.read_bytes() for p in base.rglob("*.json")}
            commands = [[sys.executable, str(SCRIPT)], [sys.executable, str(ROOT / "scripts/recommend.py")],
                        [sys.executable, str(ROOT / "codex/scripts/advisor.py"), "recommend"],
                        [sys.executable, str(portable / "scripts/advisor.py"), "recommend"]]
            for command in commands:
                result = subprocess.run(command + ["--providers", "codex", "--data", str(data), "--catalog", str(cat), "--available", str(access), "--json"],
                                        capture_output=True, text=True, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                value = json.loads(result.stdout)
                self.assertEqual(value["host"], "claude" if command[1] == str(ROOT / "scripts/recommend.py") else "codex")
                self.assertEqual(len(value["lane_recommendations"]), 4)
                self.assertEqual(value["recommendations"]["most_capable"]["slug"], "gpt-smart")
                self.assertIn("local snapshot", value["data_source"])
                self.assertIn("Artificial Analysis", value["source"])
            self.assertEqual(before, {p: p.read_bytes() for p in base.rglob("*.json")})
            self.assertFalse((base / "home").exists())
            self.assertFalse((base / "config").exists())

    def test_interactive_questionnaire_remembers_choices_and_cancels(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "profile.json"
            with patch("builtins.input", side_effect=["", "n", "s"]), contextlib.redirect_stdout(io.StringIO()):
                providers = recommend.ask_providers(target)
            self.assertEqual(providers, ["claude", "opencode"])
            recommend.save_profile(target, providers)
            before = target.read_bytes()
            with patch("builtins.input", side_effect=["n", "q"]), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    recommend.ask_providers(target)
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(json.loads(before), {"providers": ["claude", "opencode"]})

    def test_missing_key_and_noninteractive_questionnaire_fail_without_network(self):
        with patch.dict(os.environ, {"ARTIFICIAL_ANALYSIS_API_KEY": ""}), patch.object(recommend, "keychain_key", return_value=""), patch.object(recommend, "fetch_json") as get:
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(recommend.main(["--providers", "claude"]), 4)
                self.assertEqual(recommend.main([]), 4)
            self.assertIn("ARTIFICIAL_ANALYSIS_API_KEY", errors.getvalue())
            get.assert_not_called()

    def test_api_key_reuses_environment_then_keychain_without_prompting(self):
        with patch.object(recommend, "keychain_key", return_value="saved-key") as store, \
             patch.object(recommend.getpass, "getpass") as prompt:
            with patch.dict(os.environ, {"ARTIFICIAL_ANALYSIS_API_KEY": "env-key"}):
                self.assertEqual(recommend.api_key(True), ("env-key", False))
                store.assert_not_called()
            with patch.dict(os.environ, {"ARTIFICIAL_ANALYSIS_API_KEY": ""}):
                self.assertEqual(recommend.api_key(True), ("saved-key", False))
                self.assertEqual(recommend.api_key(False), ("saved-key", False))
            prompt.assert_not_called()
        with patch.dict(os.environ, {"ARTIFICIAL_ANALYSIS_API_KEY": ""}), \
             patch.object(recommend, "keychain_key", side_effect=recommend.RecommendationError("locked")), \
             patch.object(recommend.getpass, "getpass") as prompt:
            with self.assertRaises(recommend.RecommendationError):
                recommend.api_key(True)
            prompt.assert_not_called()

    def test_keychain_keeps_credentials_out_of_argv_and_errors(self):
        with patch.object(sys, "platform", "darwin"), patch.object(subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "saved-key\n", "")
            self.assertEqual(recommend.keychain_key(), "saved-key")
            self.assertEqual(run.call_args.args[0][-1], "-w")
            key = 'secret with "quotes"\ncommand'
            recommend.keychain_key(key)
            self.assertEqual(run.call_args.args[0], ["/usr/bin/security", "-i"])
            command = run.call_args.kwargs["input"]
            self.assertEqual(command.count("\n"), 1)
            self.assertNotIn(key, command)
            self.assertIn(key.encode().hex(), command)
            run.return_value = subprocess.CompletedProcess([], 44, "", "secret")
            self.assertEqual(recommend.keychain_key(), "")
            for code in [1, 36, 128]:
                run.return_value = subprocess.CompletedProcess([], code, "secret", "secret")
                with self.assertRaises(recommend.RecommendationError) as error:
                    recommend.keychain_key()
                self.assertNotIn("secret", str(error.exception))
            run.side_effect = subprocess.TimeoutExpired("secret", 30, output="secret")
            with self.assertRaises(recommend.RecommendationError) as error:
                recommend.keychain_key()
            self.assertNotIn("secret", str(error.exception))
        with patch.object(sys, "platform", "linux"), patch.object(subprocess, "run") as run:
            self.assertEqual(recommend.keychain_key(), "")
            run.assert_not_called()

    def test_failed_aa_request_does_not_store_prompted_key(self):
        with patch.object(recommend, "api_key", return_value=("secret", True)), \
             patch.object(recommend, "load_aa", side_effect=recommend.RecommendationError("HTTP 401")), \
             patch.object(recommend, "keychain_key") as store, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(recommend.main(["--providers", "codex"]), 4)
            store.assert_not_called()

    def test_interactive_full_run_saves_key_only_in_keychain(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "access.json"
            with (patch.dict(os.environ, {"ARTIFICIAL_ANALYSIS_API_KEY": ""}),
                  patch.object(recommend, "keychain_key", return_value="") as store,
                  patch.object(sys.stdin, "isatty", return_value=True),
                  patch.object(sys.stdout, "isatty", return_value=True),
                  patch("builtins.input", side_effect=["s", "s", "n"]),
                  patch.object(recommend.getpass, "getpass", return_value="private-aa-key"),
                  patch.object(recommend, "fetch_json", side_effect=[dataset(), catalog()]) as fetch,
                  contextlib.redirect_stdout(io.StringIO()) as output):
                # redirect_stdout replaces the TTY; patch it inside this context.
                with patch.object(sys.stdout, "isatty", return_value=True):
                    self.assertEqual(recommend.main(["--profile", str(target)]), 0)
            self.assertEqual(json.loads(target.read_text()), {"providers": ["claude", "codex"]})
            self.assertNotIn("private-aa-key", target.read_text() + output.getvalue())
            self.assertEqual(fetch.call_args_list[0].args, (recommend.AA_URL + "?page=1", "private-aa-key"))
            self.assertEqual(fetch.call_args_list[1].args, (recommend.CATALOG_URL,))
            self.assertEqual([c.args for c in store.call_args_list], [(), ("private-aa-key",)])

    def test_snapshot_rejects_partial_data_duplicate_ids_and_terminal_controls(self):
        for data in [page([model("x", 1, 1), model("x", 2, 2)]),
                     page([model("x", 1, 1, "bad\x1bname")]), {"data": []}]:
            with self.assertRaises(recommend.RecommendationError):
                recommend.validate_page(data, 1)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "partial.json"
            target.write_text(json.dumps(page([model("x", 1, 1)], 1, 2)))
            with patch.object(recommend, "fetch_json") as fetch, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(recommend.main(["--providers", "codex", "--data", str(target)]), 4)
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
