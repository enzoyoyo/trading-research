# VeighNa / vn.py audit notes

Use this reference when evaluating whether to absorb patterns from `vnpy/vnpy`, `vnpy_paperaccount`, or related gateway projects into `trading-research`.

Upstream reference: https://github.com/vnpy/vnpy (MIT).

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
| Paper/sim | `vnpy_paperaccount` local matching simulator | Local matching sim ≠ broker paper account ≠ LongBridge Demo gate |



## Integration boundary for this skill

- Absorb only methodology: gateway contract, identity normalization, capability schema, paper-simulation boundary.
- Do **not** import vn.py GUI/runtime/gateway dependencies into `trading-research`.
- Do **not** create a new Hermes order path from vn.py.
- Do **not** let local matching simulation bypass LongBridge `paper_account_gate`.
- Keep LongBridge MCP/CLI/SDK as the primary data/account layer unless a separate project explicitly scopes a vn.py sandbox.
