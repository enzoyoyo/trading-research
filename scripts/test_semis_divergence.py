#!/usr/bin/env python3
"""semis_divergence.py 离线单测：不联网，用 --panel-root / 直接函数调用注入合成面板。"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import semis_divergence as sd

N_DAYS = 65  # >= BETA_WINDOW(60) + 1，保证 residual_z 窗口可算


def _trading_dates(n: int, start: date = date(2026, 1, 1)) -> list[str]:
    dates: list[str] = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += timedelta(days=1)
    return dates


def _spy_series(n: int, start_price: float) -> list[float]:
    closes = [start_price]
    for i in range(1, n):
        ret = 0.1 if i % 2 == 1 else -0.1
        closes.append(closes[-1] * (1 + ret / 100.0))
    return closes


def _beta_series(n: int, start_price: float, beta: float, offset: float) -> list[float]:
    """soxx_ret = beta*spy_ret + eps；eps 用 period-4 图样，与 spy 的 period-2 不共线，
    保证残差方差非零（不会因 eps 与 spy 完全线性相关而被回归吸收成 0）。"""
    closes = [start_price]
    for i in range(1, n):
        spy_ret = 0.1 if i % 2 == 1 else -0.1
        eps = offset if (i % 4) in (1, 2) else -offset
        closes.append(closes[-1] * (1 + (beta * spy_ret + eps) / 100.0))
    return closes


def _write_panel(root: Path, symbol: str, dates: list[str], closes: list[float]) -> None:
    rows = [{"date": d, "close": c} for d, c in zip(dates, closes)]
    (root / f"{symbol}.json").write_text(json.dumps({"rows": rows}))


class SyntheticPanelTestCase(unittest.TestCase):
    """共享的确定性合成面板：SOXX/SPY/QQQ/IWM 65 个交易日，最后一日可被各测试覆写。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dates = _trading_dates(N_DAYS)
        self.spy = _spy_series(N_DAYS, 500.0)
        self.soxx = _beta_series(N_DAYS, 200.0, beta=1.2, offset=0.05)
        self.qqq = _beta_series(N_DAYS, 400.0, beta=0.8, offset=0.0)
        self.iwm = _beta_series(N_DAYS, 180.0, beta=1.0, offset=0.0)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _force_last(self, series: list[float], ret_pct: float) -> None:
        series[-1] = series[-2] * (1 + ret_pct / 100.0)

    def _write_all(self, *, soxx: bool = True, smh: bool = False, spy: bool = True,
                    qqq: bool = True, iwm: bool = True) -> None:
        if soxx:
            _write_panel(self.root, "SOXX", self.dates, self.soxx)
        if smh:
            _write_panel(self.root, "SMH", self.dates, self.soxx)
        if spy:
            _write_panel(self.root, "SPY", self.dates, self.spy)
        if qqq:
            _write_panel(self.root, "QQQ", self.dates, self.qqq)
        if iwm:
            _write_panel(self.root, "IWM", self.dates, self.iwm)

    def _snapshot(self) -> dict:
        return sd.build_snapshot(self.root, None, sd.BETA_WINDOW)


# 1. 分类表逐行：五种状态/标签 + no_divergence 各至少一条构造样例 ------------------
class ClassificationTableTests(SyntheticPanelTestCase):
    def test_semis_strong_index_down_is_unvalidated(self) -> None:
        self._force_last(self.soxx, 0.8)
        self._force_last(self.spy, -0.6)
        self._force_last(self.qqq, -0.1)
        self._write_all()
        snap = self._snapshot()
        self.assertEqual(snap["state"], "semis_strong_index_down")
        self.assertIsNone(snap["grades"]["state"])
        self.assertIsNone(snap["forbidden_inference"])

    def test_semis_strong_qqq_down_is_unvalidated(self) -> None:
        self._force_last(self.soxx, 0.8)
        self._force_last(self.spy, 0.1)
        self._force_last(self.qqq, -0.4)
        self._write_all()
        snap = self._snapshot()
        self.assertEqual(snap["state"], "semis_strong_qqq_down")
        self.assertIsNone(snap["grades"]["state"])

    def test_semis_weak_index_up_forbids_short_inference(self) -> None:
        self._force_last(self.soxx, -0.6)
        self._force_last(self.spy, 0.4)
        self._force_last(self.qqq, 0.1)
        self._write_all()
        snap = self._snapshot()
        self.assertEqual(snap["state"], "semis_weak_index_up")
        self.assertIsNone(snap["grades"]["state"])
        self.assertEqual(snap["forbidden_inference"], "fake_rally_short")

    def test_semis_abnormal_weakness_is_unvalidated(self) -> None:
        self._force_last(self.soxx, -1.0)
        self._force_last(self.qqq, -0.4)
        self._force_last(self.spy, -0.1)
        self._write_all()
        snap = self._snapshot()
        self.assertEqual(snap["state"], "no_divergence")
        self.assertIn("semis_abnormal_weakness", snap["tags"])
        self.assertIsNone(snap["grades"]["semis_abnormal_weakness"])

    def test_residual_z_extreme_block(self) -> None:
        self._force_last(self.soxx, 3.0)
        self._force_last(self.spy, -0.5)
        self._force_last(self.qqq, -0.1)
        self._write_all()
        snap = self._snapshot()
        rz = snap["residual_z"]
        self.assertIsNotNone(rz)
        self.assertTrue(rz["extreme"])
        self.assertGreaterEqual(rz["z"], sd.Z_STRONG)

    def test_no_divergence(self) -> None:
        self._force_last(self.soxx, 0.05)
        self._force_last(self.spy, 0.05)
        self._force_last(self.qqq, 0.05)
        self._write_all()
        snap = self._snapshot()
        self.assertEqual(snap["state"], "no_divergence")
        self.assertEqual(snap["tags"], [])


# 2. β/Z 数学正确性：手工构造已知 β 的 4 日窗口，容差 1e-6 -------------------------
class BetaResidualMathTests(unittest.TestCase):
    def setUp(self) -> None:
        # 窗口 d1..d4（严格早于 t），spy_ret={1,-1,2,-2}，soxx_ret=1.5*spy_ret+eps，
        # eps={0.2,0.2,-0.2,-0.2}（与 spy 正交：mean(eps*spy)=0）→ beta_est 精确=1.5。
        self.dates = ["2026-02-02", "2026-02-03", "2026-02-04", "2026-02-05", "2026-02-06"]
        d1, d2, d3, d4, t = self.dates
        self.t = t
        self.ret_spy = {d1: 1.0, d2: -1.0, d3: 2.0, d4: -2.0, t: -1.0}
        self.ret_semis = {d1: 1.7, d2: -1.3, d3: 2.8, d4: -3.2, t: 1.0}

    def test_beta_and_z_match_hand_computation(self) -> None:
        result = sd.compute_beta_residual_z(self.ret_semis, self.ret_spy, self.t, window=4)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["beta60"], 1.5, places=6)
        self.assertAlmostEqual(result["window_mean_resid"], 0.0, places=6)
        self.assertAlmostEqual(result["window_std_resid"], 0.2, places=6)
        # resid_t = semis_t - beta*spy_t = 1.0 - 1.5*(-1.0) = 2.5；Z = 2.5/0.2 = 12.5。
        self.assertAlmostEqual(result["residual_t_pct"], 2.5, places=6)
        self.assertAlmostEqual(result["z"], 12.5, places=6)


# 3. 无前视：扰动 t 日值不改变 β/窗口统计量的分母（只用严格 < t 的数据）--------------
class NoLookaheadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dates = ["2026-02-02", "2026-02-03", "2026-02-04", "2026-02-05", "2026-02-06"]
        d1, d2, d3, d4, self.t = self.dates
        self.ret_spy_base = {d1: 1.0, d2: -1.0, d3: 2.0, d4: -2.0}
        self.ret_semis_base = {d1: 1.7, d2: -1.3, d3: 2.8, d4: -3.2}

    def test_perturbing_t_does_not_change_window_stats(self) -> None:
        ret_spy_a = {**self.ret_spy_base, self.t: -1.0}
        ret_semis_a = {**self.ret_semis_base, self.t: 1.0}
        ret_spy_b = {**self.ret_spy_base, self.t: 9.0}
        ret_semis_b = {**self.ret_semis_base, self.t: -9.0}

        result_a = sd.compute_beta_residual_z(ret_semis_a, ret_spy_a, self.t, window=4)
        result_b = sd.compute_beta_residual_z(ret_semis_b, ret_spy_b, self.t, window=4)

        self.assertEqual(result_a["beta60"], result_b["beta60"])
        self.assertEqual(result_a["window_mean_resid"], result_b["window_mean_resid"])
        self.assertEqual(result_a["window_std_resid"], result_b["window_std_resid"])
        # t 日值确实改变了，residual_t/z 应当不同——证明这两个量只受 t 日值驱动。
        self.assertNotEqual(result_a["residual_t_pct"], result_b["residual_t_pct"])
        self.assertNotEqual(result_a["z"], result_b["z"])


# 5. SOXX→SMH 代理 + SPY 缺失→unavailable（含真实 CLI 退出码）---------------------
class ProxyAndGapTests(SyntheticPanelTestCase):
    def test_soxx_missing_falls_back_to_smh_proxy(self) -> None:
        self._force_last(self.soxx, 0.8)
        self._force_last(self.spy, -0.6)
        self._force_last(self.qqq, -0.1)
        self._write_all(soxx=False, smh=True)
        snap = self._snapshot()
        self.assertEqual(snap["proxy_symbol"], "SMH")
        self.assertEqual(snap["state"], "semis_strong_index_down")

    def test_spy_missing_is_unavailable_with_exit_code_0(self) -> None:
        self._write_all(spy=False)
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "semis_divergence.py"), "--json",
             "--panel-root", str(self.root)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["state"], "unavailable")
        self.assertTrue(any("SPY" in g for g in payload["gaps"]))


# 6. 信封字段恒在 ---------------------------------------------------------------
class EnvelopeFieldsTests(SyntheticPanelTestCase):
    REQUIRED_KEYS = {
        "schema_version", "no_order_execution", "tighten_only", "module_mapping",
        "asof", "state", "tags", "grades", "forbidden_inference", "residual_z",
        "iwm_control", "proxy_symbol", "gaps", "hypothesis_ids",
    }

    def test_envelope_present_when_unavailable(self) -> None:
        snap = sd.build_snapshot(self.root, None, sd.BETA_WINDOW)  # 无面板 → unavailable
        self.assertTrue(self.REQUIRED_KEYS.issubset(snap.keys()))
        self.assertIs(snap["no_order_execution"], True)
        self.assertIs(snap["tighten_only"], True)
        self.assertEqual(snap["module_mapping"], "endogenous_structure")

    def test_envelope_present_when_state_computed(self) -> None:
        self._force_last(self.soxx, 0.8)
        self._force_last(self.spy, -0.6)
        self._force_last(self.qqq, -0.1)
        self._write_all()
        snap = self._snapshot()
        self.assertTrue(self.REQUIRED_KEYS.issubset(snap.keys()))
        self.assertIs(snap["no_order_execution"], True)
        self.assertIs(snap["tighten_only"], True)
        self.assertEqual(snap["module_mapping"], "endogenous_structure")


# 7. residual_z_extreme 块恒带 usable_for_action:false ---------------------------
class ResidualZExtremeUsableForActionTests(SyntheticPanelTestCase):
    def test_train_only_block_marks_not_usable_for_action(self) -> None:
        self._force_last(self.soxx, 3.0)
        self._force_last(self.spy, -0.5)
        self._force_last(self.qqq, -0.1)
        self._write_all()
        snap = self._snapshot()
        block = snap["residual_z"]["train_only_block"]
        self.assertIsNotNone(block)
        self.assertIs(block["usable_for_action"], False)
        self.assertIs(block["in_sample_only"], True)


class PublicEvidenceBoundaryTests(unittest.TestCase):
    def test_default_and_populated_snapshots_never_claim_validated_performance(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for populated in (False, True):
                if populated:
                    for symbol, closes in {"SOXX": [100, 101], "SPY": [100, 99],
                                           "QQQ": [100, 99], "IWM": [100, 101]}.items():
                        _write_panel(root, symbol, ["2026-01-02", "2026-01-05"], closes)
                snapshot = sd.build_snapshot(root, None, 60)
                self.assertEqual(snapshot["validation_posture"], "train_only")
                self.assertFalse(snapshot["usable_for_action"])
                self.assertEqual(snapshot["performance_validation"], "not_provided")
                self.assertEqual(snapshot["hypothesis_ids"], {})
                self.assertTrue(all(value is None for value in snapshot["grades"].values()))
                self.assertNotIn("probability", snapshot)


if __name__ == "__main__":
    unittest.main()
