# VeighNa / vn.py audit notes

Use this reference when evaluating whether to absorb patterns from `vnpy/vnpy`, `vnpy_paperaccount`, or related gateway projects into `trading-research`.

## Audited snapshot

- Main repo: `https://github.com/vnpy/vnpy`
- Commit audited: `1b78494979deb4c4996f6b864f234d9839f2f239`
- Version observed: `4.4.0`
- License: MIT
- Auxiliary repos checked:
  - `vnpy_paperaccount` commit `fcfe2b5`, version `1.1.0`
  - `vnpy_ib` commit `6ec7115`
  - `vnpy_xtp` commit `e5a386e`
  - `vnpy_tora` commit `c0ae44a`

## What is durable enough to reuse

- `BaseGateway` as a clean interface contract for broker/data gateways.
- `Exchange` + `vt_symbol` normalization as a model for cross-market contract identity.
- `MainEngine` + App plugin separation as a boundary pattern.
- Explicit gateway capability declarations: market scope, order path, data path, and account mode.
- PaperAccount as a **local matching simulator** pattern, not a broker-side paper account proof.

## Market applicability rule

vn.py is not A-share-only, but support is gateway-dependent:

| Market | Evidence from audit | Boundary |
|---|---|---|
| A-share | `SSE`, `SZSE`, `BSE`; XTP/TORA/OST/EMT-style gateways | Needs broker gateway, quote permission, and account setup |
| HK | `SEHK`; IB gateway maps `SEHK` | Often requires IB contract subscription/contract discovery |
| US | `NYSE`, `NASDAQ`, `SMART`; IB gateway maps major US exchanges | Requires IB/TWS or another gateway; core enum alone is not enough |
| Paper/sim | `vnpy_paperaccount` local matching smoke test passed | Local matching sim ≠ broker paper account ≠ LongBridge Demo gate |

## Verification pattern from audit

Useful probes for future similar audits:

```bash
# static/core import probe
python - <<'PY'
import vnpy
from vnpy.trader.constant import Exchange
from vnpy.trader.utility import extract_vt_symbol
print(vnpy.__version__)
print([x.value for x in [Exchange.SSE, Exchange.SZSE, Exchange.BSE, Exchange.SEHK, Exchange.NYSE, Exchange.NASDAQ, Exchange.SMART]])
print(extract_vt_symbol("600519.SSE"))
print(extract_vt_symbol("00700.SEHK"))
print(extract_vt_symbol("AAPL.NASDAQ"))
PY

# repository checks used in this audit
python -m pytest -q tests/alpha/test_dataproxy.py
python -m pytest -q tests/test_alpha101.py --maxfail=1
python -m pytest -q tests
python -m ruff check vnpy tests
```

## Observed upstream issue

At the audited commit, the bundled Alpha101 tests failed on `NameError: cast_to_int is not defined` for Alpha#62/64/65/68. Treat this as an upstream `vnpy.alpha` completeness issue unless a later commit fixes it. Do not encode the failure as a permanent tool limitation; rerun tests against the current commit.

## Integration boundary for this skill

- Absorb only methodology: gateway contract, identity normalization, capability schema, paper-simulation boundary.
- Do **not** import vn.py GUI/runtime/gateway dependencies into `trading-research`.
- Do **not** create a new Hermes order path from vn.py.
- Do **not** let local matching simulation bypass LongBridge `paper_account_gate`.
- Keep LongBridge MCP/CLI/SDK as the primary data/account layer unless a separate project explicitly scopes a vn.py sandbox.
