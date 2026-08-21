#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from market_router import classify  # noqa: E402


if __name__ == "__main__":
    targets = sys.argv[1:] or ["TSLA"]
    out = [classify(t) for t in targets]
    print(json.dumps(out[0] if len(out) == 1 else out, ensure_ascii=False, indent=2))
