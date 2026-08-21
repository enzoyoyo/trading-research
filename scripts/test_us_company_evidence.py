#!/usr/bin/env python3
"""Hermetic contracts for LongBridge-first US company evidence.

The compact fixtures preserve shapes observed from the local LongBridge CLI on
2026-08-11. Tests never read credentials, the network, HOME, or /tmp.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import us_company_evidence as subject  # noqa: E402
import evidence_run as evidence_pipeline  # noqa: E402
import decision_compiler as compiler  # noqa: E402
import fundamental_snapshot as fundamental_pipeline  # noqa: E402


FILING_10Q = {
    "description": "",
    "file_count": 4,
    "file_name": "10-Q - Apple Inc. (0000320193) (Filer)",
    "file_urls": [
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/aapl-20260627.htm",
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/a10-qexhibit31106272026.htm",
    ],
    "id": "676489353887748225",
    "publish_at": "2026-07-31T10:01:02Z",
    "title": "Apple | 10-Q - Apple Inc. (0000320193) (Filer)",
}

FILING_FORM4 = {
    "file_count": 1,
    "file_name": "4 - Apple Inc. (0000320193) (Issuer)",
    "file_urls": [
        "https://www.sec.gov/Archives/edgar/data/320193/000114036126025622/xslF345X06/form4.xml"
    ],
    "id": "660734070661714049",
    "publish_at": "2026-06-17T22:40:43Z",
}

FINANCIAL_REPORT = {
    "currency": "USD",
    "indicators": [
        {"field_name": "operating_revenue", "indicator_name": "营业收入", "indicator_value": "364357000000.00", "yoy": "0.1615"},
        {"field_name": "roe", "indicator_name": "ROE", "indicator_value": "148.75%", "yoy": ""},
    ],
    "report": "2026.3Q",
    "report_txt": "2026 财年三季报",
}

MIXED_SHAREHOLDERS = {
    "shareholder_list": [
        {"percent_of_shares": "7.11", "report_date": "2026-03-31", "shareholder_id": "0", "shareholder_name": "", "shares_changed": "1038042316", "stocks": []},
        {"percent_of_shares": "7.97", "report_date": "2026-06-30", "shareholder_id": "0", "shareholder_name": "BlackRock, Inc.", "shares_changed": "18301514", "stocks": []},
        {"percent_of_shares": "1.56", "report_date": "2026-03-31", "shareholder_id": "0", "shareholder_name": "Berkshire Hathaway Inc.", "shares_changed": "0", "stocks": []},
    ]
}

INVESTOR_13F = {
    "accession_number": "0001193125-26-226661",
    "cik": "0001067983",
    "filing_date": "2026-05-15",
    "firm": "BERKSHIRE HATHAWAY INC",
    "holdings": [
        {"cusip": "037833100", "name": "APPLE INC", "shares": 227917808, "value_usd": 57843260493, "weight_pct": "21.99"}
    ],
    "investor": "BERKSHIRE HATHAWAY INC",
    "period": "2026-03-31",
    "total_holdings": 29,
    "total_value_usd": 263095703570,
}


class LongBridgeJsonContractTests(unittest.TestCase):
    def test_real_cli_json_with_upgrade_notice_is_parsed(self) -> None:
        stdout = (
            '[{"id":"676489353887748225"}]\n\n'
            "New version 0.26.0 is available, run `longbridge update` to update.\n"
            "Release notes: https://open.longbridge.cn/docs/cli/release-notes.md\n"
        )
        result = subject.parse_longbridge_json(stdout)
        self.assertTrue(result["ok"])
        self.assertEqual(result["payload"][0]["id"], "676489353887748225")
        self.assertEqual(result["trailing_output_class"], "version_notice")

    def test_non_json_empty_and_unknown_trailing_output_fail_closed(self) -> None:
        for stdout, expected in [
            ("warning only; no payload", "unparsable_output"),
            ("", "unparsable_output"),
            ('{"ok":true}\nnot-a-version-notice', "unexpected_trailing_output"),
            ('debug {not-json}\n[{"ok":true}]', "unparsable_output"),
        ]:
            with self.subTest(stdout=stdout):
                result = subject.parse_longbridge_json(stdout)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error_class"], expected)


class NormalizationTests(unittest.TestCase):
    def test_accession_normalization(self) -> None:
        self.assertEqual(subject.normalize_accession("000032019326000020"), "0000320193-26-000020")
        self.assertEqual(subject.normalize_accession("0000320193-26-000020"), "0000320193-26-000020")
        self.assertIsNone(subject.normalize_accession("676489353887748225"))
        self.assertIsNone(subject.normalize_accession("bad"))

    def test_sec_ticker_map_requires_exact_unique_identity(self) -> None:
        payload = {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire Hathaway Inc."},
        }
        self.assertEqual(subject.cik_from_company_tickers(payload, "AAPL.US"), "0000320193")
        self.assertEqual(subject.cik_from_company_tickers(payload, "BRK.B.US"), "0001067983")
        self.assertIsNone(subject.cik_from_company_tickers(payload, "APPL.US"))

        ambiguous = {
            **payload,
            "2": {"cik_str": 1234567, "ticker": "AAPL", "title": "Collision fixture"},
        }
        self.assertIsNone(subject.cik_from_company_tickers(ambiguous, "AAPL.US"))

    def test_filing_derives_regulatory_identity_without_misusing_provider_id(self) -> None:
        row = subject.normalize_filing_row(FILING_10Q)
        self.assertEqual(row["form_type"], "10-Q")
        self.assertFalse(row["amendment"])
        self.assertEqual(row["issuer_cik"], "0000320193")
        self.assertEqual(row["accession_number"], "0000320193-26-000020")
        self.assertEqual(row["document_url"], FILING_10Q["file_urls"][0])
        self.assertEqual(row["provider_id"], "676489353887748225")
        self.assertNotEqual(row["provider_id"], row["accession_number"])
        self.assertEqual(row["provider_published_at"], "2026-07-31T10:01:02Z")
        self.assertIsNone(row["filed_at"])
        self.assertEqual(row["underlying_fact_key"], "sec:0000320193-26-000020")
        self.assertEqual(row["retrieval_paths"], ["longbridge_cli"])

        form4 = subject.normalize_filing_row(FILING_FORM4)
        self.assertEqual(form4["issuer_cik"], "0000320193")
        self.assertEqual(form4["accession_number"], "0001140361-26-025622")
        self.assertEqual(form4["form_type"], "4")

        amended = dict(FILING_10Q, file_name="10-Q/A - Apple Inc. (0000320193) (Filer)")
        self.assertTrue(subject.normalize_filing_row(amended)["amendment"])

    def test_filing_without_document_url_records_gap_and_does_not_invent_accession(self) -> None:
        row = subject.normalize_filing_row({"id": "123", "file_name": "10-K - Example (0000001234) (Filer)"})
        self.assertIsNone(row["accession_number"])
        self.assertIsNone(row["underlying_fact_key"])
        self.assertTrue(any(gap["error_class"] == "document_url_missing" for gap in row["data_gaps"]))

    def test_mixed_period_shareholders_are_not_aggregated_or_ranked(self) -> None:
        result = subject.normalize_shareholder_payload(MIXED_SHAREHOLDERS)
        self.assertTrue(result["mixed_period"])
        self.assertEqual(result["as_of_dates"], ["2026-03-31", "2026-06-30"])
        self.assertFalse(result["aggregation_allowed"])
        self.assertIsNone(result["aggregate_percent_of_shares"])
        self.assertEqual(result["unattributed_holder_count"], 1)
        self.assertTrue(result["rows"][0]["unattributed_holder"])
        self.assertEqual(result["ordering_semantics"], "provider_order_unknown")

    def test_financial_report_preserves_units_and_unknown_period_basis(self) -> None:
        result = subject.normalize_financial_report(FINANCIAL_REPORT)
        fields = {row["field_name"]: row for row in result["indicators"]}
        self.assertEqual(fields["operating_revenue"]["value"], 364357000000.0)
        self.assertEqual(fields["operating_revenue"]["yoy"], 0.1615)
        self.assertEqual(fields["roe"]["value"], 148.75)
        self.assertEqual(fields["roe"]["unit"], "percent")
        self.assertIsNone(fields["roe"]["yoy"])
        self.assertEqual(result["period_basis"], "unknown")
        self.assertEqual(result["source_kind"], "aggregator_derived")
        self.assertIsNone(result["underlying_fact_key"])

    def test_valuation_strips_html_and_omits_peer_noise(self) -> None:
        payload = {
            "history": {"range": 5, "metrics": {"pe": {"desc": "当前 <strong>34.89</strong>", "high": "40", "low": "20", "median": "30", "list": [{"timestamp": "1", "value": "29"}, {"timestamp": "2", "value": "34.89"}]}}},
            "layouts": {"pe": {"groups": [{"list": [{"ticker": "", "name": "", "value": "-13"} for _ in range(100)]}]}}
        }
        result = subject.normalize_valuation(payload)
        self.assertEqual(result["metrics"]["pe"]["description"], "当前 34.89")
        self.assertNotIn("layouts", result)
        self.assertEqual(result["omitted_peer_rows"], 100)
        self.assertLess(len(str(result)), 2000)

    def test_13f_is_an_investor_portfolio_not_target_holder_lookup(self) -> None:
        result = subject.normalize_13f_portfolio(INVESTOR_13F)
        self.assertEqual(result["underlying_fact_key"], "sec:0001193125-26-226661")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["semantic_role"], "institutional_portfolio")
        self.assertFalse(result["is_target_holder_lookup"])


class SecAdapterTests(unittest.TestCase):
    def test_missing_identity_fails_before_transport(self) -> None:
        calls: list[object] = []

        def fake_transport(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("transport must not run without identity")

        result = subject.fetch_sec_json(
            "https://data.sec.gov/submissions/CIK0000320193.json",
            identity="",
            transport=fake_transport,
            cache={},
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["error_class"], "identity_missing")
        self.assertEqual(calls, [])

    def test_missing_identity_cannot_touch_disk_cache_or_transport(self) -> None:
        calls: list[object] = []

        def fake_transport(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("transport must not run without identity")

        original_cache_file = subject._cache_file

        def forbidden_cache_file(*args, **kwargs):
            raise AssertionError("disk cache must not be inspected without identity")

        subject._cache_file = forbidden_cache_file
        try:
            result = subject.fetch_sec_json_cached(
                "https://data.sec.gov/submissions/CIK0000320193.json",
                identity="",
                transport=fake_transport,
            )
        finally:
            subject._cache_file = original_cache_file
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["error_class"], "identity_missing")
        self.assertEqual(calls, [])

    def test_identity_headers_and_memory_cache(self) -> None:
        calls: list[dict] = []

        def fake_transport(url, *, headers, timeout):
            calls.append({"url": url, "headers": headers, "timeout": timeout})
            return {"status": 200, "body": b'{"cik":"0000320193"}', "headers": {}}

        cache: dict = {}
        url = "https://data.sec.gov/submissions/CIK0000320193.json"
        first = subject.fetch_sec_json(url, identity="the user Research the user@example.com", transport=fake_transport, cache=cache)
        second = subject.fetch_sec_json(url, identity="the user Research the user@example.com", transport=fake_transport, cache=cache)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")
        self.assertFalse(first["from_cache"])
        self.assertTrue(second["from_cache"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["headers"]["User-Agent"], "the user Research the user@example.com")
        self.assertEqual(calls[0]["headers"]["Accept-Encoding"], "gzip, deflate")

    def test_http_failures_are_not_rewritten_as_no_filings(self) -> None:
        for status, error_class in [(403, "blocked_identity"), (404, "no_such_cik"), (429, "rate_limited")]:
            def fake_transport(url, *, headers, timeout, status=status):
                return {"status": status, "body": b"", "headers": {}}
            result = subject.fetch_sec_json(
                "https://data.sec.gov/submissions/CIK0000320193.json",
                identity="the user Research the user@example.com",
                transport=fake_transport,
                cache={},
            )
            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["error_class"], error_class)
            self.assertNotIn("no_filings", str(result))

    def test_injected_transport_cannot_bypass_response_size_limit(self) -> None:
        self.assertEqual(subject.SEC_MAX_BYTES, 8 * 1024 * 1024)

        def fake_transport(url, *, headers, timeout):
            return {
                "status": 200,
                "body": b"x" * (subject.SEC_MAX_BYTES + 1),
                "headers": {},
            }

        result = subject.fetch_sec_json(
            "https://data.sec.gov/submissions/CIK0000320193.json",
            identity="the user Research the user@example.com",
            transport=fake_transport,
            cache={},
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["error_class"], "response_too_large")
        self.assertIsNone(result["payload"])

    def test_disk_cache_is_private_and_refreshes_after_ttl(self) -> None:
        calls: list[int] = []

        def fake_transport(url, *, headers, timeout):
            calls.append(len(calls) + 1)
            body = json.dumps({"revision": calls[-1]}).encode("utf-8")
            return {"status": 200, "body": body, "headers": {}}

        url = "https://data.sec.gov/submissions/CIK0000320193.json"
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "sec-cache"
            first = subject.fetch_sec_json_cached(
                url,
                identity="the user Research the user@example.com",
                transport=fake_transport,
                cache_dir=cache_dir,
                ttl_seconds=60,
            )
            cached = subject.fetch_sec_json_cached(
                url,
                identity="the user Research the user@example.com",
                transport=fake_transport,
                cache_dir=cache_dir,
                ttl_seconds=60,
            )
            files = list(cache_dir.glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(cache_dir.stat().st_mode & 0o777, 0o700)
            os.utime(files[0], (1, 1))
            refreshed = subject.fetch_sec_json_cached(
                url,
                identity="the user Research the user@example.com",
                transport=fake_transport,
                cache_dir=cache_dir,
                ttl_seconds=1,
            )
        self.assertEqual(first["payload"]["revision"], 1)
        self.assertTrue(cached["from_cache"])
        self.assertEqual(refreshed["payload"]["revision"], 2)
        self.assertEqual(len(calls), 2)

    def test_sec_source_health_uses_complete_safe_contract(self) -> None:
        row = subject.sec_source_health(
            "sec_edgar:submissions",
            {"status": "ok", "error_class": None, "from_cache": True, "payload": {"filings": {}}},
        )
        self.assertEqual(row["provider_family"], "sec_edgar")
        self.assertEqual(row["source_family"], "sec_edgar")
        self.assertEqual(row["status"], "ok")
        self.assertTrue(row["from_cache"])
        self.assertIn("checked_at", row)
        self.assertEqual(row["safe_summary"], {"payload_type": "object", "top_level_keys": ["filings"]})
        self.assertNotIn("payload", row)


class ProvenanceAndSafetyTests(unittest.TestCase):
    def test_same_sec_accession_merges_retrieval_paths_without_source_inflation(self) -> None:
        longbridge = subject.normalize_filing_row(FILING_10Q)
        sec = dict(longbridge, provider="sec_edgar", retrieval_paths=["sec_edgar"], filed_at="2026-07-31")
        merged = subject.dedupe_regulatory_evidence([longbridge, sec])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["retrieval_paths"], ["longbridge_cli", "sec_edgar"])
        self.assertEqual(merged[0]["independent_source_count"], 1)
        self.assertEqual(merged[0]["filed_at"], "2026-07-31")

    def test_generated_longbridge_commands_are_read_only_and_allowlisted(self) -> None:
        commands = subject.build_longbridge_commands("AAPL.US")
        roots = {command[0] for command in commands}
        self.assertEqual(roots, {"financial-report", "valuation", "filing", "insider-trades", "shareholder"})
        self.assertTrue(roots.issubset(subject.READ_ONLY_LONGBRIDGE_COMMANDS))
        forbidden = {"order", "submit", "buy", "sell", "cancel", "replace", "account"}
        self.assertFalse(any(token.lower() in forbidden for command in commands for token in command))


class PipelineIntegrationTests(unittest.TestCase):
    def test_empty_longbridge_filing_resolves_cik_from_sec_ticker_map_then_fetches_submissions(self) -> None:
        payloads = {
            "financial-report": FINANCIAL_REPORT,
            "valuation": {"history": {"range": 5, "metrics": {"pe": {"list": [{"timestamp": "1", "value": "34.89"}]}}}},
            "filing": [],
            "insider-trades": [],
            "shareholder": {"shareholder_list": []},
        }

        def fake_runner(command, *, capture_output, text, timeout):
            return SimpleNamespace(returncode=0, stdout=json.dumps(payloads[command[1]]), stderr="")

        calls: list[str] = []

        def fake_transport(url, *, headers, timeout):
            calls.append(url)
            if url == "https://www.sec.gov/files/company_tickers.json":
                payload = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
            elif url == "https://data.sec.gov/submissions/CIK0000320193.json":
                payload = {
                    "filings": {
                        "recent": {
                            "accessionNumber": ["0000320193-26-000020"],
                            "filingDate": ["2026-07-31"],
                            "reportDate": ["2026-06-27"],
                            "acceptanceDateTime": ["2026-07-31T10:01:02Z"],
                            "form": ["10-Q"],
                            "primaryDocument": ["aapl-20260627.htm"],
                        }
                    }
                }
            else:
                raise AssertionError(f"unexpected SEC URL: {url}")
            return {"status": 200, "body": json.dumps(payload).encode("utf-8"), "headers": {}}

        with tempfile.TemporaryDirectory() as temp_dir:
            result = subject.collect_company_evidence(
                "AAPL.US",
                runner=fake_runner,
                sec_identity="the user Research the user@example.com",
                sec_transport=fake_transport,
                sec_cache_dir=Path(temp_dir) / "sec-cache",
            )

        self.assertEqual(calls, [
            "https://www.sec.gov/files/company_tickers.json",
            "https://data.sec.gov/submissions/CIK0000320193.json",
        ])
        self.assertEqual(result["parts"]["sec_submissions"]["issuer_cik"], "0000320193")
        self.assertEqual(result["regulatory_evidence"][0]["accession_number"], "0000320193-26-000020")
        self.assertEqual(result["regulatory_evidence"][0]["retrieval_paths"], ["sec_edgar"])
        health = {row["source"]: row for row in result["source_health"]}
        self.assertEqual(health["sec_edgar:company_tickers"]["status"], "ok")
        self.assertEqual(health["sec_edgar:submissions"]["status"], "ok")
        self.assertNotIn("issuer_cik_missing", {row["error_class"] for row in result["gaps"]})

    def test_empty_longbridge_filing_without_sec_identity_never_requests_ticker_map(self) -> None:
        payloads = {
            "financial-report": FINANCIAL_REPORT,
            "valuation": {"history": {"metrics": {}}},
            "filing": [],
            "insider-trades": [],
            "shareholder": {"shareholder_list": []},
        }

        def fake_runner(command, *, capture_output, text, timeout):
            return SimpleNamespace(returncode=0, stdout=json.dumps(payloads[command[1]]), stderr="")

        calls: list[str] = []

        def forbidden_transport(url, *, headers, timeout):
            calls.append(url)
            raise AssertionError("SEC transport must not run without identity")

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "sec-cache"
            result = subject.collect_company_evidence(
                "AAPL.US",
                runner=fake_runner,
                sec_identity="",
                sec_transport=forbidden_transport,
                sec_cache_dir=cache_dir,
            )
            self.assertFalse(cache_dir.exists())

        self.assertEqual(calls, [])
        self.assertIn("identity_missing", {row["error_class"] for row in result["gaps"]})
        self.assertNotIn("issuer_cik_missing", {row["error_class"] for row in result["gaps"]})

    def test_collector_propagates_normalizer_gaps_to_top_level(self) -> None:
        payloads = {
            "financial-report": FINANCIAL_REPORT,
            "valuation": {"history": {"range": 5, "metrics": {"pe": {"list": [{"timestamp": "1", "value": "34.89"}]}}}},
            "filing": [FILING_10Q],
            "insider-trades": [],
            "shareholder": MIXED_SHAREHOLDERS,
        }

        def fake_runner(command, *, capture_output, text, timeout):
            endpoint = command[1]
            return SimpleNamespace(returncode=0, stdout=json.dumps(payloads[endpoint]), stderr="")

        result = subject.collect_company_evidence(
            "AAPL.US", runner=fake_runner, include_sec=False
        )
        error_classes = {gap["error_class"] for gap in result["gaps"]}
        self.assertIn("period_basis_unknown", error_classes)
        self.assertIn("mixed_report_periods", error_classes)
        self.assertIn("empty_insider_trades", error_classes)
        self.assertEqual(result["status"], "partial")

    def test_empty_longbridge_core_payloads_do_not_block_akshare_fallback(self) -> None:
        payloads = {
            "financial-report": {"currency": "USD", "indicators": []},
            "valuation": {"history": {"metrics": {}}},
            "filing": [FILING_10Q],
            "insider-trades": [],
            "shareholder": {"shareholder_list": []},
        }

        def fake_runner(command, *, capture_output, text, timeout):
            return SimpleNamespace(returncode=0, stdout=json.dumps(payloads[command[1]]), stderr="")

        collected = subject.collect_company_evidence("AAPL.US", runner=fake_runner, include_sec=False)
        self.assertNotIn("financial_report", collected["parts"])
        self.assertNotIn("valuation", collected["parts"])
        endpoint_health = {row["source"]: row for row in collected["source_health"]}
        self.assertEqual(endpoint_health["longbridge:financial-report"]["error_class"], "empty_payload")
        self.assertEqual(endpoint_health["longbridge:valuation"]["error_class"], "empty_payload")

        original_collector = fundamental_pipeline.collect_company_evidence
        original_run_py = fundamental_pipeline.run_py
        fundamental_pipeline.collect_company_evidence = lambda code: collected
        responses = iter([
            {"status": "pass", "stdout": '[{"REPORT_DATE":"2025-12-31","ROE":12.3}]', "stderr_tail": ""},
            {"status": "pass", "stdout": '[{"date":"2026-08-10","value":34.5}]', "stderr_tail": ""},
        ])
        fundamental_pipeline.run_py = lambda *args, **kwargs: next(responses)
        try:
            snapshot = fundamental_pipeline.us_share_fundamentals("AAPL")
        finally:
            fundamental_pipeline.collect_company_evidence = original_collector
            fundamental_pipeline.run_py = original_run_py
        self.assertIn("us_financial_indicators", snapshot["parts"])
        self.assertIn("valuation_pe_ttm", snapshot["parts"])

    def test_us_fundamentals_prefers_longbridge_and_skips_akshare_when_core_parts_exist(self) -> None:
        collector_result = {
            "schema_version": subject.SCHEMA_VERSION,
            "market": "US",
            "symbol": "AAPL.US",
            "fetched_at": "2026-08-11T00:00:00+00:00",
            "status": "partial",
            "parts": {
                "financial_report": subject.normalize_financial_report(FINANCIAL_REPORT),
                "valuation": subject.normalize_valuation({
                    "history": {"range": 5, "metrics": {"pe": {"list": [{"timestamp": "1", "value": "34.89"}]}}}
                }),
                "regulatory_filings": [subject.normalize_filing_row(FILING_10Q)],
            },
            "regulatory_evidence": [subject.normalize_filing_row(FILING_10Q)],
            "source_health": [{"source": "longbridge:financial-report", "status": "ok"}],
            "gaps": [{"gap": "SEC fallback unavailable", "error_class": "identity_missing"}],
            "no_order_execution": True,
        }

        original_collector = getattr(fundamental_pipeline, "collect_company_evidence", None)
        original_run_py = fundamental_pipeline.run_py
        setattr(fundamental_pipeline, "collect_company_evidence", lambda code: collector_result)

        def forbidden_akshare(*args, **kwargs):
            raise AssertionError("AkShare must not run when LongBridge supplied both core parts")

        fundamental_pipeline.run_py = forbidden_akshare
        try:
            result = fundamental_pipeline.us_share_fundamentals("AAPL")
        finally:
            fundamental_pipeline.run_py = original_run_py
            if original_collector is None:
                delattr(fundamental_pipeline, "collect_company_evidence")
            else:
                setattr(fundamental_pipeline, "collect_company_evidence", original_collector)

        self.assertEqual(result["parts"]["financial_report"]["provider"], "longbridge")
        self.assertEqual(result["parts"]["valuation"]["provider"], "longbridge")
        self.assertEqual(result["regulatory_evidence"][0]["underlying_fact_key"], "sec:0000320193-26-000020")
        self.assertEqual(result["status"], "partial")

    def test_evidence_pipeline_emits_fundamental_and_deduped_filing_eids(self) -> None:
        regulatory = subject.normalize_filing_row(FILING_10Q)
        regulatory["retrieval_paths"] = ["longbridge_cli", "sec_edgar"]
        regulatory["independent_source_count"] = 1
        snapshot = {
            "status": "partial",
            "parts": {
                "financial_report": subject.normalize_financial_report(FINANCIAL_REPORT),
                "valuation": subject.normalize_valuation({
                    "history": {"range": 5, "metrics": {"pe": {"list": [{"timestamp": "1", "value": "34.89"}]}}}
                }),
            },
            "regulatory_evidence": [regulatory, dict(regulatory)],
            "source_health": [
                {"source": "longbridge:financial-report", "provider_family": "longbridge", "status": "ok"},
                {"source": "sec_edgar:submissions", "provider_family": "sec_edgar", "status": "ok"},
            ],
            "gaps": [{"gap": "period basis unknown", "error_class": "period_basis_unknown"}],
        }
        original_run_cmd = evidence_pipeline.run_cmd
        evidence_pipeline.run_cmd = lambda *args, **kwargs: {
            "status": "pass", "stdout": json.dumps(snapshot), "stderr": ""
        }
        try:
            health, evidence, gaps = evidence_pipeline.fundamental_snapshot(
                {"market": "US", "symbol": "AAPL", "query": "AAPL"}
            )
        finally:
            evidence_pipeline.run_cmd = original_run_cmd

        self.assertEqual([row["source"] for row in health], [
            "longbridge:financial-report", "sec_edgar:submissions"
        ])
        for row in health:
            self.assertIn("checked_at", row)
            self.assertIn("safe_summary", row)
        self.assertEqual(sum(row["type"] == "financial" for row in evidence), 1)
        financial = next(row for row in evidence if row["type"] == "financial")
        self.assertEqual(financial["claim_type"], "reported_metric")
        self.assertLessEqual(financial["reliability"], 0.85)
        filings = [row for row in evidence if row["type"] == "filing"]
        self.assertEqual(len(filings), 1)
        self.assertEqual(filings[0]["underlying_fact_key"], "sec:0000320193-26-000020")
        self.assertEqual(filings[0]["retrieval_paths"], ["longbridge_cli", "sec_edgar"])
        self.assertEqual(filings[0]["independent_source_count"], 1)
        self.assertEqual(filings[0]["source_family"], "sec_edgar")
        self.assertTrue(any(gap["error_class"] == "period_basis_unknown" for gap in gaps))

    def test_filing_eids_use_reserved_range_and_are_bounded(self) -> None:
        regulatory = []
        for index in range(12):
            accession = f"0000320193-26-{index + 1:06d}"
            regulatory.append({
                "provider": "sec_edgar",
                "provider_family": "sec_edgar",
                "source_family": "sec_edgar",
                "source_kind": "regulatory_filing",
                "underlying_fact_key": f"sec:{accession}",
                "accession_number": accession,
                "form_type": "8-K",
                "filed_at": f"2026-08-{index + 1:02d}",
                "document_url": f"https://www.sec.gov/Archives/edgar/data/320193/{accession.replace('-', '')}/doc.htm",
                "retrieval_paths": ["sec_edgar"],
            })
        snapshot = {
            "status": "partial",
            "parts": {"sec_companyfacts": {"provider": "sec_edgar", "indicators": [{"field_name": "net_income", "value": 1}]}},
            "regulatory_evidence": regulatory,
            "source_health": [],
            "gaps": [],
        }
        original_run_cmd = evidence_pipeline.run_cmd
        evidence_pipeline.run_cmd = lambda *args, **kwargs: {"status": "pass", "stdout": json.dumps(snapshot), "stderr": ""}
        try:
            _, evidence, gaps = evidence_pipeline.fundamental_snapshot({"market": "US", "symbol": "AAPL"})
        finally:
            evidence_pipeline.run_cmd = original_run_cmd
        filings = [row for row in evidence if row["type"] == "filing"]
        self.assertEqual(len(filings), 8)
        self.assertEqual([row["eid"] for row in filings], [f"E{n}" for n in range(300, 308)])
        self.assertTrue(any(row.get("error_class") == "filing_eid_limit" for row in gaps))

    def test_filing_without_accession_is_gap_not_official_sec_eid(self) -> None:
        raw = {"id": "provider-only", "file_name": "8-K - Example (0000001234) (Filer)"}
        payloads = {
            "financial-report": FINANCIAL_REPORT,
            "valuation": {"history": {"metrics": {"pe": {"list": [{"timestamp": "1", "value": "12"}]}}}},
            "filing": [raw],
            "insider-trades": [],
            "shareholder": {"shareholder_list": []},
        }

        def fake_runner(command, *, capture_output, text, timeout):
            return SimpleNamespace(returncode=0, stdout=json.dumps(payloads[command[1]]), stderr="")

        collected = subject.collect_company_evidence("TEST.US", runner=fake_runner, include_sec=False)
        self.assertTrue(any(row.get("error_class") == "document_url_missing" for row in collected["gaps"]))
        snapshot = {
            "status": collected["status"],
            "parts": collected["parts"],
            "regulatory_evidence": collected["regulatory_evidence"],
            "source_health": collected["source_health"],
            "gaps": collected["gaps"],
        }
        original_run_cmd = evidence_pipeline.run_cmd
        evidence_pipeline.run_cmd = lambda *args, **kwargs: {"status": "pass", "stdout": json.dumps(snapshot), "stderr": ""}
        try:
            _, evidence, _ = evidence_pipeline.fundamental_snapshot({"market": "US", "symbol": "TEST"})
        finally:
            evidence_pipeline.run_cmd = original_run_cmd
        self.assertFalse(any(row["type"] == "filing" for row in evidence))

    def test_single_fundamental_source_family_emits_l0_tightening_flag(self) -> None:
        evidence = [
            {"eid": "E300", "type": "filing", "source_family": "sec_edgar"},
            {"eid": "E301", "type": "filing", "source_family": "sec_edgar"},
            {"eid": "E302", "type": "filing", "source_family": "sec_edgar"},
        ]
        flag = evidence_pipeline.fundamental_source_family_flag(evidence)
        self.assertEqual(flag["type"], "single_source_family")
        self.assertEqual(flag["max_action_cap"], "L0")

    def test_two_source_families_cap_l1_and_three_remove_extra_cap(self) -> None:
        two = [
            {"eid": "E30", "type": "financial", "source_family": "issuer_ir"},
            {"eid": "E300", "type": "filing", "source_family": "sec_edgar"},
        ]
        flag = evidence_pipeline.fundamental_source_family_flag(two)
        self.assertIsNotNone(flag)
        assert flag is not None
        self.assertEqual(flag["type"], "insufficient_source_families")
        self.assertEqual(flag["independent_source_count"], 2)
        self.assertEqual(flag["max_action_cap"], "L1")
        signal = evidence_pipeline.suggested_module_signals(
            [flag], two, as_of="2026-08-11T00:02:00+00:00"
        )[0]
        self.assertEqual(signal["max_action_level"], "L1")
        self.assertEqual(signal["position_multiplier"], 0.5)

        three = [
            *two,
            {"eid": "E301", "type": "filing", "source_family": "exchange_official"},
        ]
        self.assertIsNone(evidence_pipeline.fundamental_source_family_flag(three))
        self.assertEqual(evidence_pipeline.build_red_flags(None, [], three), [])
        self.assertEqual(
            evidence_pipeline.suggested_module_signals([], three, as_of="2026-08-11T00:02:00+00:00"),
            [],
        )

    def test_unknown_provenance_cannot_manufacture_independence(self) -> None:
        evidence = [
            {"eid": "E30", "type": "financial", "source_family": "unknown_aggregator"},
            {"eid": "E300", "type": "filing", "source_family": "sec_edgar"},
        ]
        flags = evidence_pipeline.build_red_flags(None, [], evidence)
        self.assertIn("unknown_source_family", {row["type"] for row in flags})
        self.assertIn("single_source_family", {row["type"] for row in flags})

    def test_source_family_flag_emits_existing_data_quality_signal(self) -> None:
        evidence = [
            {"eid": "E300", "type": "filing", "source_family": "sec_edgar", "retrieved_at": "2026-08-11T00:00:00+00:00"},
            {"eid": "E301", "type": "filing", "source_family": "sec_edgar", "retrieved_at": "2026-08-11T00:01:00+00:00"},
        ]
        flags = evidence_pipeline.build_red_flags(None, [], evidence)
        signals = evidence_pipeline.suggested_module_signals(flags, evidence, as_of="2026-08-11T00:02:00+00:00")
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal["module"], "data_quality")
        self.assertEqual(signal["sub_framework"], "source_coverage")
        self.assertEqual(signal["max_action_level"], "L0")
        self.assertEqual(signal["position_multiplier"], 0.0)
        self.assertFalse(signal["hard_veto"])
        self.assertTrue(signal["tighten_only"])
        self.assertTrue(signal["cannot_raise_upstream"])
        self.assertEqual(signal["evidence_refs"], ["E300", "E301"])

    def test_existing_data_quality_signal_is_enforced_by_strict_compiler(self) -> None:
        evidence = [
            {"eid": "E300", "type": "filing", "source_family": "sec_edgar"},
            {"eid": "E301", "type": "filing", "source_family": "sec_edgar"},
        ]
        flags = evidence_pipeline.build_red_flags(None, [], evidence)
        data_signal = evidence_pipeline.suggested_module_signals(
            flags, evidence, as_of="2026-08-11T00:02:00+00:00"
        )[0]
        payload = {
            "schema_version": "decision_request.v2",
            "decision_context": {
                "query_tier": "T2",
                "intent": "open",
                "has_position": False,
                "as_of": "2026-08-11T00:02:00+00:00",
                "required_modules": ["risk_regime", "portfolio_risk_budget", "data_quality"],
            },
            "module_signals": [
                {
                    "module": "risk_regime", "max_action_level": "L3", "position_multiplier": 1.0,
                    "hard_veto": False, "evidence_refs": ["E1"],
                    "observed_at": "2026-08-11T00:00:00+00:00", "stale_after": "2026-08-12T00:00:00+00:00",
                },
                {
                    "module": "portfolio_risk_budget", "max_action_level": "L3", "position_multiplier": 1.0,
                    "hard_veto": False, "evidence_refs": ["E2"],
                    "observed_at": "2026-08-11T00:00:00+00:00", "stale_after": "2026-08-12T00:00:00+00:00",
                },
                data_signal,
            ],
        }
        result = compiler.compile_payload(payload, now=subject.datetime(2026, 8, 11, 0, 2, tzinfo=subject.timezone.utc))
        self.assertTrue(result["ok"])
        self.assertEqual(result["compiled_action"], "L0")
        self.assertEqual(result["entry_permission"], "WATCH")

    def test_financial_transport_families_do_not_inflate_independence(self) -> None:
        snapshot = {
            "status": "ok",
            "parts": {
                "us_financial_indicators": {"source_family": "akshare_eastmoney", "x": 1},
                "valuation_pe_ttm": {"source_family": "akshare_baidu", "x": 2},
            },
            "source_health": [],
            "gaps": [],
        }
        original = evidence_pipeline.run_cmd
        evidence_pipeline.run_cmd = lambda *args, **kwargs: {"status": "pass", "stdout": json.dumps(snapshot), "stderr": ""}
        try:
            _, evidence, _ = evidence_pipeline.fundamental_snapshot({"market": "US", "symbol": "TEST"})
        finally:
            evidence_pipeline.run_cmd = original
        financial = next(row for row in evidence if row["type"] == "financial")
        self.assertEqual(financial["independent_source_count"], 1)


if __name__ == "__main__":
    unittest.main()
