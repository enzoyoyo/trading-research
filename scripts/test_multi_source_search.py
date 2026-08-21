#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

MODULE_PATH = Path(__file__).with_name("multi_source_search.py")
SPEC = importlib.util.spec_from_file_location("multi_source_search", MODULE_PATH)
assert SPEC and SPEC.loader
mss = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mss
SPEC.loader.exec_module(mss)


class HermeticTestCase(unittest.TestCase):
    """Fails loudly if any test reaches the real network.

    Without this, adding the keyed fallback tier made tests that only patch
    `fetch_engine` silently issue live Doubao requests whenever the operator happened to
    have a credential configured -- slow, flaky, and quota-burning.
    """

    def setUp(self) -> None:
        for name in ("fetch_bytes", "fetch_json_post"):
            patcher = patch.object(mss, name, side_effect=AssertionError(f"real network call via {name}"))
            patcher.start()
            self.addCleanup(patcher.stop)


class MultiSourceSearchTests(HermeticTestCase):
    def test_plan_is_market_language_and_profile_aware(self) -> None:
        plan = mss.build_plan("贵州茅台 最新公告", "filing", "A")
        self.assertEqual(plan["language"], "zh")
        self.assertEqual(plan["market"], "A")
        self.assertIn("baidu", plan["selected_engines"])
        self.assertIn("cninfo.com.cn", plan["expanded_query"])
        with self.assertRaisesRegex(ValueError, "freshness_hours"):
            mss.build_plan("NVDA", "news", "US", freshness_hours=0)

    def test_explicit_provider_list_is_not_silently_expanded(self) -> None:
        plan = mss.build_plan("AAPL dividend", "dividend", "US", ["bing_web", "duckduckgo"])
        self.assertEqual(plan["selected_engines"], ["bing_web", "duckduckgo"])
        self.assertEqual(plan["provider_families"], ["bing", "duckduckgo"])

    def test_sensitive_query_fails_privacy_precheck(self) -> None:
        for query in (
            "search 10.0.0.8 token=abc",
            "service 172.20.1.9",
            "Authorization Bearer abcdefghijklmnop",
            "file://${HOME}/private",
            "email analyst@example.com",
            # Synthetic placeholders only: deliberately checksum-invalid / non-issuable
            # so the fixtures can never resemble a real person's identifiers.
            "手机号 13800138000",
            "身份证 000000000000000000",
            "银行卡 0000 0000 0000 0000",
            "内部交易计划 NVDA",
        ):
            with self.subTest(query=query):
                sensitive, reasons = mss.query_is_sensitive(query)
                self.assertTrue(sensitive)
                self.assertGreaterEqual(len(reasons), 1)

    def test_rss_parser_preserves_redirect_status(self) -> None:
        rss = b"""<rss><channel><item><title>Example result title</title><link>https://news.google.com/rss/articles/abc</link><description>summary</description><pubDate>Sun, 12 Jul 2026 00:00:00 GMT</pubDate></item></channel></rss>"""
        rows = mss.parse_rss(rss, mss.ENGINES["google_news"], 5)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["redirect_unresolved"])

    def test_html_parser_rejects_engine_navigation(self) -> None:
        page = b'<html><a href="https://search.brave.com/settings">Settings page</a><a href="https://example.com/report?utm_source=x">Company report details</a></html>'
        rows = mss.parse_html(page, mss.ENGINES["brave"], 5)
        self.assertEqual([row["url"] for row in rows], ["https://example.com/report"])

    def test_same_family_does_not_inflate_independence(self) -> None:
        raw = [
            (mss.ENGINES["bing_web"], {"title": "A sufficiently long shared headline", "url": "https://example.com/x", "snippet": "", "redirect_unresolved": False}),
            (mss.ENGINES["bing_news"], {"title": "A sufficiently long shared headline", "url": "https://example.com/x?utm_medium=rss", "snippet": "", "redirect_unresolved": False}),
            (mss.ENGINES["brave"], {"title": "A sufficiently long shared headline", "url": "https://example.com/x", "snippet": "", "redirect_unresolved": False}),
        ]
        rows = mss.aggregate_candidates("q", "hot", "US", "2026-07-12T00:00:00Z", raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["independent_provider_count"], 2)
        self.assertEqual(rows[0]["attention_state"], "emerging")
        self.assertEqual(rows[0]["verification_status"], "unverified")
        self.assertIn("position_sizing", rows[0]["forbidden_use"])

    def test_irrelevant_results_are_filtered(self) -> None:
        raw = [
            (mss.ENGINES["bing_web"], {"title": "NV Access download screen reader", "url": "https://example.com/nvda", "snippet": "screen reader", "redirect_unresolved": False}),
            (mss.ENGINES["google_news"], {"title": "NVDA earnings guidance raises outlook", "url": "https://example.com/earnings", "snippet": "NVDA guidance", "redirect_unresolved": False}),
        ]
        rows = mss.aggregate_candidates("NVDA earnings guidance", "news", "US", "2026-07-12T00:00:00Z", raw)
        self.assertEqual([row["url"] for row in rows], ["https://example.com/earnings"])
        self.assertGreaterEqual(rows[0]["discovery_relevance"], 0.66)

    def test_entity_term_blocks_generic_filing_pages(self) -> None:
        raw = [
            (mss.ENGINES["bing_web"], {"title": "公司公告 上海证券交易所", "url": "https://sse.com.cn/notices", "snippet": "公告", "redirect_unresolved": False}),
            (mss.ENGINES["google_news_zh"], {"title": "贵州茅台年度分红实施公告", "url": "https://example.com/moutai", "snippet": "贵州茅台 分红", "redirect_unresolved": False}),
        ]
        rows = mss.aggregate_candidates("贵州茅台 最新公告 分红", "filing", "A", "2026-07-12T00:00:00Z", raw)
        self.assertEqual([row["url"] for row in rows], ["https://example.com/moutai"])

    def test_mocked_fetch_reports_parse_empty(self) -> None:
        def fake_fetch(url: str, timeout: float, attempts: int):
            return b"<html><body>no links</body></html>", 200

        health, rows = mss.fetch_engine(mss.ENGINES["brave"], "q", 1.0, 5, fake_fetch)
        self.assertEqual(rows, [])
        self.assertEqual(health["status"], "fail")
        self.assertEqual(health["error"], "parse_empty")

    def test_news_freshness_filters_dated_stale_and_marks_unknown(self) -> None:
        plan = mss.build_plan("NVDA earnings", "news", "US")
        self.assertEqual(plan["freshness_hours"], 72)
        raw = [
            (mss.ENGINES["google_news"], {"title": "NVDA earnings recent update", "url": "https://example.com/recent", "snippet": "NVDA earnings", "published_at": "Sat, 11 Jul 2026 23:00:00 GMT", "redirect_unresolved": False}),
            (mss.ENGINES["bing_news"], {"title": "NVDA earnings old update", "url": "https://example.com/old", "snippet": "NVDA earnings", "published_at": "Wed, 01 Jul 2026 00:00:00 GMT", "redirect_unresolved": False}),
            (mss.ENGINES["brave"], {"title": "NVDA earnings undated update", "url": "https://example.com/unknown", "snippet": "NVDA earnings", "published_at": None, "redirect_unresolved": False}),
            (mss.ENGINES["duckduckgo"], {"title": "NVDA earnings future update", "url": "https://example.com/future", "snippet": "NVDA earnings", "published_at": "Mon, 13 Jul 2026 00:00:00 GMT", "redirect_unresolved": False}),
        ]
        diagnostics = {}
        rows = mss.aggregate_candidates("NVDA earnings", "news", "US", "2026-07-12T00:00:00Z", raw, plan["freshness_hours"], diagnostics)
        self.assertEqual([row["url"] for row in rows], ["https://example.com/recent", "https://example.com/unknown"])
        self.assertEqual(rows[0]["freshness_state"], "within_window")
        self.assertTrue(rows[0]["freshness_eligible"])
        self.assertEqual(rows[1]["freshness_state"], "unknown")
        self.assertFalse(rows[1]["freshness_eligible"])
        self.assertEqual(diagnostics["stale_filtered"], 1)
        self.assertEqual(diagnostics["future_timestamp_filtered"], 1)

    def test_http_429_is_blocked_without_retry_evasion(self) -> None:
        def blocked(url: str, timeout: float, attempts: int):
            raise mss.urllib.error.HTTPError(url, 429, "rate limited", None, None)

        health, rows = mss.fetch_engine(mss.ENGINES["brave"], "NVDA", 1.0, 5, blocked)
        self.assertEqual(rows, [])
        self.assertEqual(health["status"], "blocked")
        self.assertEqual(health["http_status"], 429)

    def test_unknown_news_timestamp_emits_data_gap(self) -> None:
        plan = mss.build_plan("NVDA earnings", "news", "US", ["brave"])

        def undated(engine, query, timeout, limit):
            return {"engine": engine.name, "family": engine.family, "status": "ok", "http_status": 200, "error": None, "latency_ms": 1, "candidate_count": 1}, [{"title": "NVDA earnings undated report", "url": "https://example.com/undated", "snippet": "NVDA earnings", "published_at": None, "redirect_unresolved": False}]

        with patch.object(mss, "fetch_engine", side_effect=undated):
            result = mss.execute_search(plan, 1.0, 2, 1)
        self.assertTrue(result["ok"])
        self.assertFalse(result["candidates"][0]["freshness_eligible"])
        self.assertTrue(any(gap["gap"] == "published_at_unknown" for gap in result["data_gaps"]))

    def test_all_provider_failure_returns_false_and_data_gaps(self) -> None:
        # No fallback tier here: this asserts the primary-only failure contract, and the
        # result must not depend on whether a Doubao credential happens to be configured.
        plan = mss.build_plan("NVDA", "general", "US", ["brave", "duckduckgo"], fallback_engines=[])

        def failed(engine, query, timeout, limit):
            return {"engine": engine.name, "family": engine.family, "status": "fail", "http_status": None, "error": "TimeoutError", "latency_ms": 1, "candidate_count": 0}, []

        with patch.object(mss, "fetch_engine", side_effect=failed):
            result = mss.execute_search(plan, 1.0, 2, 2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["filter_diagnostics"]["raw_candidates"], 0)
        self.assertTrue(any(gap["gap"] == "no_candidates" for gap in result["data_gaps"]))
        self.assertTrue(result["no_order_execution"])
        self.assertEqual(result["privacy"]["search_api_credentials_used"], [])

    def test_cli_plan_only_does_not_execute_network_and_prints_banner(self) -> None:
        stdout = io.StringIO()
        with patch.object(sys, "argv", ["multi_source_search.py", "NVDA news", "--profile", "news"]), patch.object(mss, "execute_search", side_effect=AssertionError("network path called")), patch("sys.stdout", stdout):
            code = mss.main()
        self.assertEqual(code, 0)
        self.assertIn("DISCOVERY_ONLY", stdout.getvalue())

    def test_cli_sensitive_query_fails_closed_before_network(self) -> None:
        stdout = io.StringIO()
        with patch.object(sys, "argv", ["multi_source_search.py", "analyst@example.com NVDA", "--allow-external-search"]), patch.object(mss, "execute_search", side_effect=AssertionError("network path called")), patch("sys.stdout", stdout):
            code = mss.main()
        self.assertEqual(code, 3)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["mode"], "blocked_sensitive_query")
        self.assertFalse(payload["privacy"]["query_sent_to_selected_engines"])


class KeyedBackupProviderTests(HermeticTestCase):
    """Doubao Global (VolcEngine) backup search provider."""

    def test_keyed_provider_never_joins_default_primary_tier(self) -> None:
        for market, profile in (("US", "news"), ("A", "filing"), ("HK", "general"), ("unknown", "rumor")):
            with self.subTest(market=market, profile=profile):
                plan = mss.build_plan("NVDA guidance", profile, market)
                self.assertNotIn("doubao", plan["selected_engines"])
                self.assertEqual(plan["fallback_engines"], ["doubao"])

    def test_fallback_tier_rejects_non_keyed_engines(self) -> None:
        with self.assertRaisesRegex(ValueError, "fallback_requires_keyed_engine"):
            mss.build_plan("NVDA", "news", "US", fallback_engines=["brave"])
        with self.assertRaisesRegex(ValueError, "unknown_engine"):
            mss.build_plan("NVDA", "news", "US", fallback_engines=["not_a_provider"])

    def test_raw_query_mode_avoids_unsupported_operator_syntax(self) -> None:
        plan = mss.build_plan("NVDA latest filing", "filing", "US")
        self.assertIn("site:sec.gov", plan["expanded_query"])
        # Doubao rejects multi-word/operator syntax, so it must receive the raw query.
        self.assertEqual(mss.engine_query(mss.ENGINES["doubao"], plan), "NVDA latest filing")
        self.assertEqual(mss.engine_query(mss.ENGINES["bing_web"], plan), plan["expanded_query"])

    def test_query_truncated_to_documented_100_char_limit(self) -> None:
        plan = mss.build_plan("贵州茅台" * 40, "general", "A")
        self.assertEqual(len(mss.engine_query(mss.ENGINES["doubao"], plan)), 100)

    def test_credential_loader_only_reads_allow_listed_search_keys(self) -> None:
        parsed = mss._parse_env_file(
            "# comment\nOKX_DEMO_API_KEY=broker-secret\nexport VOLC_DOUBAO_SEARCH_API_KEY='doubao-key'\nX_BEARER_TOKEN=social\n"
        )
        self.assertEqual(parsed, {"VOLC_DOUBAO_SEARCH_API_KEY": "doubao-key"})
        token, diagnostics = mss.load_search_credential("OKX_DEMO_API_KEY")
        self.assertIsNone(token)
        self.assertEqual(diagnostics["reason"], "env_name_not_allow_listed")

    def test_credential_file_with_open_permissions_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "search.env"
            path.write_text("VOLC_DOUBAO_SEARCH_API_KEY=doubao-key\n", encoding="utf-8")
            path.chmod(0o644)
            with patch.dict(mss.os.environ, {}, clear=True):
                token, diagnostics = mss.load_search_credential("VOLC_DOUBAO_SEARCH_API_KEY", path)
            self.assertIsNone(token)
            self.assertEqual(diagnostics["reason"], "credential_file_permissions_too_open")
            self.assertIn("chmod 600", diagnostics["remediation"])
            path.chmod(0o600)
            with patch.dict(mss.os.environ, {}, clear=True):
                token, diagnostics = mss.load_search_credential("VOLC_DOUBAO_SEARCH_API_KEY", path)
            self.assertEqual(token, "doubao-key")
            self.assertEqual(diagnostics["source"], "credential_file")

    def test_missing_credential_is_skipped_not_reported_as_no_news(self) -> None:
        plan = mss.build_plan("NVDA guidance", "news", "US", ["brave"])
        with patch.dict(mss.os.environ, {}, clear=True), patch.object(mss, "CREDENTIAL_FILE", Path("/nonexistent/search.env")), patch.object(mss, "fetch_keyed_engine", side_effect=AssertionError("must not call keyed provider without a key")):
            health, raw = mss.run_engine_tier(plan, ["doubao"], 1.0, 5, 1)
        self.assertEqual(raw, [])
        self.assertEqual(health[0]["status"], "skipped")
        self.assertTrue(health[0]["error"].startswith("auth_"))
        self.assertIn("VOLC_DOUBAO_SEARCH_API_KEY", health[0]["error_hint"])

    def test_doubao_response_parsed_with_text_snippets_and_publish_time(self) -> None:
        payload = json.dumps({
            "ResponseMetadata": {"RequestId": "req-1", "Action": "", "Version": ""},
            "Result": {
                "TotalDocCount": 2,
                "ErrorCode": 0,
                "ErrorMsg": "",
                "Documents": [{
                    "Rank": 0,
                    "Url": "https://example.com/report?utm_source=x",
                    "Title": "NVDA earnings guidance detail",
                    "Snippet": [
                        {"Type": "text", "Text": "guidance raised"},
                        {"Type": "image", "Image": {"Width": 1, "Height": 1, "ImageUrl": "https://img.example/a.png"}},
                        {"Type": "text", "Text": "second fragment"},
                    ],
                    "DocumentInfo": {"ContentCharCount": 10, "Filetype": "webpage", "PublishTime": "2026-07-12 08:00:00"},
                    "HostInfo": {"Hostname": "示例财经", "IconUrl": "https://img.example/i.png"},
                }],
            },
        }).encode()
        rows = mss.parse_doubao(payload, mss.ENGINES["doubao"], 5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://example.com/report")
        self.assertEqual(rows[0]["snippet"], "guidance raised second fragment")
        self.assertEqual(rows[0]["published_at"], "2026-07-12T08:00:00Z")
        self.assertEqual(rows[0]["provider_site_label"], "示例财经")
        self.assertEqual(rows[0]["filetype"], "webpage")

    def test_publish_time_formats_normalize_or_stay_unknown(self) -> None:
        self.assertEqual(mss.parse_doubao_publish_time("1783843200"), "2026-07-12T08:00:00Z")
        self.assertEqual(mss.parse_doubao_publish_time("1783843200000"), "2026-07-12T08:00:00Z")
        self.assertEqual(mss.parse_doubao_publish_time("2026/07/12"), "2026-07-12T00:00:00Z")
        # Observed from a real Global-version response: offset-aware ISO-8601, which the
        # docs do not show. It must survive intact so the offset is honoured, not dropped.
        self.assertEqual(mss.parse_doubao_publish_time("2026-07-31T00:00:00+08:00"), "2026-07-31T00:00:00+08:00")
        self.assertEqual(
            mss.freshness_state("2026-07-31T00:00:00+08:00", "2026-07-31T13:00:00Z", 72),
            "within_window",
        )
        self.assertEqual(
            mss.freshness_state("2026-07-31T00:00:00+08:00", "2026-09-01T00:00:00Z", 72),
            "stale",
        )
        self.assertIsNone(mss.parse_doubao_publish_time(""))
        # An unparsable value must not be silently coerced into a fake timestamp.
        self.assertEqual(mss.parse_doubao_publish_time("last week"), "last week")
        self.assertEqual(
            mss.freshness_state(mss.parse_doubao_publish_time("last week"), "2026-07-12T00:00:00Z", 72),
            "unknown",
        )

    def test_documented_error_codes_are_not_reported_as_success(self) -> None:
        cases = {
            700901: "invalid_api_key",
            10412: "search_quota_exhausted",
            10410: "search_plan_not_enabled",
            700429: "rate_limited_5qps",
        }
        for code, hint in cases.items():
            with self.subTest(code=code):
                payload = json.dumps({
                    "ResponseMetadata": {"RequestId": "r", "Error": {"CodeN": code, "Code": str(code), "Message": "denied"}},
                    "Result": None,
                }).encode()
                with self.assertRaises(mss.DoubaoProviderError) as ctx:
                    mss.parse_doubao(payload, mss.ENGINES["doubao"], 5)
                self.assertEqual(ctx.exception.code, code)
                self.assertEqual(ctx.exception.hint, hint)

    def test_result_level_error_code_also_fails(self) -> None:
        payload = json.dumps({"ResponseMetadata": {"RequestId": "r"}, "Result": {"ErrorCode": 10400, "ErrorMsg": "query is empty", "Documents": []}}).encode()
        with self.assertRaises(mss.DoubaoProviderError) as ctx:
            mss.parse_doubao(payload, mss.ENGINES["doubao"], 5)
        self.assertEqual(ctx.exception.code, 10400)
        self.assertFalse(ctx.exception.config_error)

    def test_config_errors_are_marked_blocked_and_transient_errors_are_not(self) -> None:
        def provider_error(url, timeout, attempts, body, token):
            return json.dumps({"ResponseMetadata": {"Error": {"CodeN": 10412, "Code": "10412", "Message": "quota"}}, "Result": None}).encode(), 200

        health, rows = mss.fetch_keyed_engine(mss.ENGINES["doubao"], "NVDA", 1.0, 5, "k", provider_error)
        self.assertEqual(rows, [])
        self.assertEqual(health["status"], "blocked")
        self.assertTrue(health["config_error"])
        self.assertEqual(health["error_hint"], "search_quota_exhausted")

        def internal_error(url, timeout, attempts, body, token):
            return json.dumps({"ResponseMetadata": {"Error": {"CodeN": 10500, "Code": "10500", "Message": "oops"}}, "Result": None}).encode(), 200

        health, _ = mss.fetch_keyed_engine(mss.ENGINES["doubao"], "NVDA", 1.0, 5, "k", internal_error)
        self.assertEqual(health["status"], "fail")
        self.assertFalse(health["config_error"])

    def test_http_401_is_blocked_and_marked_config_error(self) -> None:
        def unauthorized(url, timeout, attempts, body, token):
            raise mss.urllib.error.HTTPError(url, 401, "unauthorized", None, None)

        health, rows = mss.fetch_keyed_engine(mss.ENGINES["doubao"], "NVDA", 1.0, 5, "k", unauthorized)
        self.assertEqual(rows, [])
        self.assertEqual(health["status"], "blocked")
        self.assertEqual(health["http_status"], 401)
        self.assertTrue(health["config_error"])

    def test_request_body_matches_documented_contract(self) -> None:
        captured: dict[str, Any] = {}

        def capture(url, timeout, attempts, body, token):
            captured["url"] = url
            captured["body"] = body
            captured["token"] = token
            return json.dumps({"ResponseMetadata": {"RequestId": "r"}, "Result": {"ErrorCode": 0, "Documents": []}}).encode(), 200

        mss.fetch_keyed_engine(mss.ENGINES["doubao"], "NVDA earnings", 1.0, 50, "secret-key", capture)
        self.assertEqual(captured["url"], "https://open.feedcoopapi.com/search_api/global_search")
        body = captured["body"]
        self.assertEqual(body["Query"], "NVDA earnings")
        self.assertEqual(body["DocCount"], 20)  # documented maximum
        self.assertLessEqual(body["MaxSnippetLength"], 3000)
        self.assertEqual(body["MaxImageCountPerDoc"], 0)

    def test_fallback_triggers_only_when_primary_coverage_is_weak(self) -> None:
        ok = [{"status": "ok"}, {"status": "ok"}]
        self.assertFalse(mss.fallback_trigger(ok, 5, 3)[0])
        self.assertEqual(mss.fallback_trigger(ok, 1, 3)[1], "insufficient_primary_candidates")
        self.assertEqual(mss.fallback_trigger([{"status": "fail"}], 5, 3)[1], "all_primary_providers_failed")
        blocked = [{"status": "blocked"}, {"status": "blocked"}, {"status": "ok"}]
        self.assertEqual(mss.fallback_trigger(blocked, 9, 3)[1], "majority_primary_providers_blocked")

    def test_backup_tier_runs_when_primary_fails_and_results_merge(self) -> None:
        plan = mss.build_plan("NVDA earnings guidance", "general", "US", ["brave"])

        def failed_primary(engine, query, timeout, limit):
            return {"engine": engine.name, "family": engine.family, "status": "fail", "http_status": None, "error": "TimeoutError", "latency_ms": 1, "candidate_count": 0}, []

        def backup_ok(engine, query, timeout, limit, token, poster=None):
            return {"engine": engine.name, "family": engine.family, "keyed": True, "status": "ok", "http_status": 200, "error": None, "latency_ms": 5, "candidate_count": 1}, [
                {"title": "NVDA earnings guidance detail", "url": "https://example.com/report", "snippet": "NVDA earnings guidance", "published_at": None, "redirect_unresolved": False}
            ]

        with patch.dict(mss.os.environ, {"VOLC_DOUBAO_SEARCH_API_KEY": "test-key"}), patch.object(mss, "fetch_engine", side_effect=failed_primary), patch.object(mss, "fetch_keyed_engine", side_effect=backup_ok):
            result = mss.execute_search(plan, 1.0, 5, 2)
        self.assertTrue(result["ok"])
        self.assertTrue(result["fallback"]["triggered"])
        self.assertEqual(result["fallback"]["reason"], "all_primary_providers_failed")
        self.assertEqual([row["url"] for row in result["candidates"]], ["https://example.com/report"])
        self.assertEqual(result["candidates"][0]["engines"], ["doubao"])
        self.assertEqual(result["privacy"]["search_api_credentials_used"], ["doubao"])
        tiers = {row["engine"]: row["tier"] for row in result["source_health"]}
        self.assertEqual(tiers, {"brave": "primary", "doubao": "fallback"})

    def test_backup_tier_skipped_when_primary_coverage_is_sufficient(self) -> None:
        plan = mss.build_plan("NVDA earnings guidance", "general", "US", ["brave"], min_candidates=1)

        def primary_ok(engine, query, timeout, limit):
            return {"engine": engine.name, "family": engine.family, "status": "ok", "http_status": 200, "error": None, "latency_ms": 1, "candidate_count": 1}, [
                {"title": "NVDA earnings guidance detail", "url": "https://example.com/primary", "snippet": "NVDA earnings guidance", "published_at": None, "redirect_unresolved": False}
            ]

        with patch.dict(mss.os.environ, {"VOLC_DOUBAO_SEARCH_API_KEY": "test-key"}), patch.object(mss, "fetch_engine", side_effect=primary_ok), patch.object(mss, "fetch_keyed_engine", side_effect=AssertionError("backup must not run when primary is sufficient")):
            result = mss.execute_search(plan, 1.0, 5, 2)
        self.assertTrue(result["ok"])
        self.assertFalse(result["fallback"]["triggered"])
        self.assertEqual(result["fallback"]["reason"], "primary_coverage_sufficient")
        self.assertEqual(result["privacy"]["search_api_credentials_used"], [])

    def test_cross_tier_duplicates_do_not_inflate_independence(self) -> None:
        plan = mss.build_plan("NVDA earnings guidance", "general", "US", ["brave"])

        def primary_thin(engine, query, timeout, limit):
            return {"engine": engine.name, "family": engine.family, "status": "ok", "http_status": 200, "error": None, "latency_ms": 1, "candidate_count": 1}, [
                {"title": "NVDA earnings guidance detail", "url": "https://example.com/shared", "snippet": "NVDA earnings guidance", "published_at": None, "redirect_unresolved": False}
            ]

        def backup_same(engine, query, timeout, limit, token, poster=None):
            return {"engine": engine.name, "family": engine.family, "keyed": True, "status": "ok", "http_status": 200, "error": None, "latency_ms": 5, "candidate_count": 1}, [
                {"title": "NVDA earnings guidance detail", "url": "https://example.com/shared?utm_source=doubao", "snippet": "NVDA earnings guidance", "published_at": None, "redirect_unresolved": False}
            ]

        with patch.dict(mss.os.environ, {"VOLC_DOUBAO_SEARCH_API_KEY": "test-key"}), patch.object(mss, "fetch_engine", side_effect=primary_thin), patch.object(mss, "fetch_keyed_engine", side_effect=backup_same):
            result = mss.execute_search(plan, 1.0, 5, 2)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["independent_provider_count"], 2)
        self.assertEqual(result["candidates"][0]["engines"], ["brave", "doubao"])

    def test_backup_results_remain_discovery_only(self) -> None:
        rows = mss.aggregate_candidates(
            "NVDA earnings guidance", "news", "US", "2026-07-12T00:00:00Z",
            [(mss.ENGINES["doubao"], {"title": "NVDA earnings guidance detail", "url": "https://example.com/a", "snippet": "NVDA earnings guidance", "published_at": "2026-07-11T23:00:00Z", "redirect_unresolved": False})],
            72,
        )
        self.assertTrue(rows[0]["discovery_only"])
        self.assertEqual(rows[0]["verification_status"], "unverified")
        self.assertEqual(rows[0]["readiness_impact"], "monitoring_only")
        self.assertIn("order_execution", rows[0]["forbidden_use"])
        self.assertIn("position_sizing", rows[0]["forbidden_use"])

    def test_sensitive_query_blocks_before_any_keyed_call(self) -> None:
        stdout = io.StringIO()
        with patch.object(sys, "argv", ["multi_source_search.py", "内部交易计划 NVDA", "--allow-external-search"]), patch.object(mss, "fetch_keyed_engine", side_effect=AssertionError("keyed provider called on sensitive query")), patch.object(mss, "execute_search", side_effect=AssertionError("network path called")), patch("sys.stdout", stdout):
            code = mss.main()
        self.assertEqual(code, 3)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["privacy"]["search_api_credentials_used"], [])

    def test_check_credentials_reports_state_without_printing_secret(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "search.env"
            path.write_text("VOLC_DOUBAO_SEARCH_API_KEY=super-secret-value\n", encoding="utf-8")
            path.chmod(0o600)
            with patch.object(sys, "argv", ["multi_source_search.py", "--check-credentials"]), patch.dict(mss.os.environ, {}, clear=True), patch.object(mss, "CREDENTIAL_FILE", path), patch("sys.stdout", stdout):
                code = mss.main()
        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertNotIn("super-secret-value", output)
        payload = json.loads(output)
        self.assertTrue(payload["providers"][0]["configured"])
        self.assertFalse(payload["secret_values_printed"])

    def test_no_fallback_flag_disables_backup_tier(self) -> None:
        stdout = io.StringIO()
        with patch.object(sys, "argv", ["multi_source_search.py", "NVDA news", "--no-fallback", "--json"]), patch("sys.stdout", stdout):
            code = mss.main()
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["plan"]["fallback_engines"], [])
        self.assertEqual([row["engine"] for row in payload["source_health"] if row.get("tier") == "fallback"], [])


if __name__ == "__main__":
    unittest.main()
