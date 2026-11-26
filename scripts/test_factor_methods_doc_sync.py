#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import factor_engine


def documented_factor_names(markdown: str) -> set[str]:
    """Return the factor names listed in factor-research-engine.md section 2."""
    section_match = re.search(
        r"^## 2\. 因子注册表\s*$\n(?P<body>.*?)(?=^## 3\.|\Z)",
        markdown,
        flags=re.MULTILINE | re.DOTALL,
    )
    if section_match is None:
        raise AssertionError("missing references/factor-research-engine.md section 2 factor registry")

    names: set[str] = set()
    in_table = False
    for line in section_match.group("body").splitlines():
        stripped = line.strip()
        if stripped.startswith("| name |"):
            in_table = True
            continue
        if not in_table:
            continue
        if not stripped.startswith("|"):
            break
        first_cell = stripped.strip("|").split("|", 1)[0].strip()
        if not first_cell or set(first_cell) <= {"-", ":"}:
            continue
        names.add(first_cell.strip("`"))
    return names


def factor_name_differences(documented: set[str]) -> tuple[list[str], list[str]]:
    runtime = set(factor_engine.FACTOR_METHODS)
    return sorted(runtime - documented), sorted(documented - runtime)


class FactorMethodsDocumentationSyncTests(unittest.TestCase):
    def test_section_2_factor_rows_match_runtime_registry(self) -> None:
        markdown = (SKILL_ROOT / "references" / "factor-research-engine.md").read_text(
            encoding="utf-8"
        )
        missing_in_doc, extra_in_doc = factor_name_differences(
            documented_factor_names(markdown)
        )
        self.assertFalse(
            missing_in_doc or extra_in_doc,
            "FACTOR_METHODS/documentation drift: "
            f"missing_in_doc={missing_in_doc}; extra_in_doc={extra_in_doc}",
        )

    def test_difference_helper_reports_both_directions(self) -> None:
        runtime = set(factor_engine.FACTOR_METHODS)
        removed = min(runtime)
        documented = (runtime - {removed}) | {"doc_only_factor"}
        missing_in_doc, extra_in_doc = factor_name_differences(documented)
        self.assertEqual(missing_in_doc, [removed])
        self.assertEqual(extra_in_doc, ["doc_only_factor"])


if __name__ == "__main__":
    unittest.main()
