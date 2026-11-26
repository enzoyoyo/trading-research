#!/usr/bin/env python3
"""trading-research · validate_report — concise report contract.

The validator enforces decision safety, evidence traceability, freshness gates, and no-secret rules.
It does NOT force the user-facing report to dump every audit table.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import provenance_guard

# Tier 2 report contract. The canonical templates live in templates/universal-equity-report.md —
# changing a label/field name on either side without the other causes generated reports to fail here.
REQUIRED_ANY = {
    "core_conclusion": ["核心结论", "一句话结论"],
    "evidence": ["关键依据", "关键证据", "Evidence Ledger", "证据账本"],
    "action_plan": ["行动线", "行动计划", "若空仓"],
    "data_gap": ["数据缺口", "Data Gap"],
    "validation": ["验证结果", "执行记录"],
    "intelligence coverage": ["intelligence coverage", "coverage="],
    "research watch triggers": ["research watch triggers"],
}
REQUIRED_PHRASES = [
    "readiness_level", "critical gaps", "stale_after",
    "must_refresh_if", "falsifier", "review clock",
]
SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{20,}", r"(?i)api[_-]?key\s*[:=]\s*[^\s]+",
    r"(?i)access[_-]?token\s*[:=]\s*[^\s]+", r"(?i)secret\s*[:=]\s*[^\s]+",
    r"(?i)grok2api", r"127\.0\.0\.1:8000/v1", r"/chat/completions",
]
PLACEHOLDER_PATTERNS = [r"\{\{[A-Z0-9_]+\}\}", r"\[公司/代码\]", r"`\s*`"]
PROMPT_INJECTION_PATTERNS = [
    r"(?i)\b(?:ignore|disregard|override)\s+(?:(?:all|any)\s+)?"
    r"(?:previous|prior|earlier|above|system|developer)\s+"
    r"(?:instructions?|prompts?|messages?)\b",
    r"(?i)\b(?:reveal|print|return|expose)\s+(?:the\s+)?"
    r"(?:system|developer)\s+(?:prompt|message|instructions?)\b",
    r"(?:忽略|无视|绕过|覆盖).{0,24}(?:先前|此前|以上|系统|开发者).{0,12}"
    r"(?:指令|提示词|消息)",
]
MAX_CANONICALIZATION_PASSES = 8
MAX_RENDER_EQUIVALENT_PASSES = 8
HTML_COMMENT_PATTERN = re.compile(r"(?<!\\)<!--.*?-->", re.S)
INLINE_HTML_TAG_PATTERN = re.compile(r"(?<!\\)</?[A-Za-z][^<>\n]{0,512}>")
INLINE_LINK_PATTERN = re.compile(
    r"(?<!\\)\[([^\]\n]{1,512})\]\([^\)\n]{0,2048}\)"
)
INLINE_FORMATTING_PATTERN = re.compile(
    r"(?<!\\)(?P<delimiter>\*{1,3}|~{2}|\x60{1,3})"
    r"(?P<body>\S(?:[^\n]*?\S)?)(?<!\\)(?P=delimiter)"
)
UNDERSCORE_EMPHASIS_PATTERN = re.compile(
    r"(?<![\\\w])(?P<delimiter>_{1,3})"
    r"(?P<body>\S(?:[^\n]*?\S)?)(?<!\\)(?P=delimiter)(?!\w)"
)
DEFAULT_IGNORABLE_CODEPOINT_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
EID_PATTERN = re.compile(r"\bE\d+\b")
EVIDENCE_ROW_EID_PATTERN = re.compile(
    r"(?m)^[ \t]*(?:[-*][ \t]+)?\[(E\d+)\]"
)
PROVENANCE_TARGET_PATTERN = re.compile(
    r"(?im)^[ \t]*[-*]?[ \t]*provenance_target[ \t]*(?:\||:|：)[ \t]*(.*)$"
)
CANONICAL_EIDS_PATTERN = re.compile(
    r"(?im)^[ \t]*[-*]?[ \t]*canonical_eids[ \t]*(?:\||:|：)[ \t]*(.*)$"
)
REPORT_PRIMARY_HEADING_PATTERN = re.compile(
    r"(?m)^[ \t]*#[ \t]+`?([A-Z0-9][A-Z0-9._-]{0,23})`?(?=[ \t：:]|$)"
)
REPORT_PRIMARY_ACTION_PATTERN = re.compile(
    r"(?m)^[ \t]*(?:[-*][ \t]+)?`?([A-Z0-9][A-Z0-9._-]{0,23})`?"
    r"[ \t]*[：:].*?(?:行动等级|动作等级|compiled_action|短线执行).*?L\d+"
)
KOL_METHOD_CARD_ID_PATTERN = re.compile(
    r"(?im)^[ \t]*[-*]?[ \t]*kol_method_card_id[ \t]*(?:\||:|：)[ \t]*(.*)$"
)
ACTION_LEVEL_PATTERN = re.compile(r"(?:行动等级|动作等级|compiled_action|短线执行).*?L(\d+)", re.S)
KOL_LABEL_JOIN = r"[_\s-]+"
KOL_DECLARATION_SEPARATOR = r"(?:->|→|⇒|[-:：=|/])"
KOL_ACTION_LABEL_PATTERN = (
    r"(?:行动等级|动作等级|动作建议|行动建议|当前动作|当前行动|建议动作|"
    r"最终动作|最终行动|短线执行|"
    rf"(?<![A-Za-z0-9_])(?:max{KOL_LABEL_JOIN}action{KOL_LABEL_JOIN}level|"
    rf"action{KOL_LABEL_JOIN}level|compiled{KOL_LABEL_JOIN}action)(?![A-Za-z0-9_]))"
)
KOL_ACTION_DECLARATION_PATTERN = re.compile(
    rf"{KOL_ACTION_LABEL_PATTERN}\s*(?:{KOL_DECLARATION_SEPARATOR}\s*)?"
    r"`?L(\d+)(?!\d)`?",
    re.I,
)
KOL_COMPILER_QUALIFIER = (
    r"(?:(?:最终\s*)?(?:result|结果|结论|判定)|最终|"
    r"final(?:\s+result)?|\(\s*final\s*\)|（\s*最终\s*）|verdict|output)"
)
DECISION_COMPILER_RESULT_PATTERN = re.compile(
    r"\bDecision\s+Compiler\s*"
    rf"(?:{KOL_DECLARATION_SEPARATOR}\s*|"
    rf"{KOL_COMPILER_QUALIFIER}\s*(?:{KOL_DECLARATION_SEPARATOR}\s*)?)"
    r"\s*`?L(\d+)(?!\d)`?",
    re.I,
)
KOL_ENTRY_PERMISSION_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_])entry{KOL_LABEL_JOIN}permission(?![A-Za-z0-9_])"
    rf"\s*{KOL_DECLARATION_SEPARATOR}\s*`?([A-Za-z_]+)`?",
    re.I,
)
KOL_HOLDING_DIRECTIVE_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_])holding{KOL_LABEL_JOIN}directive(?![A-Za-z0-9_])"
    rf"\s*{KOL_DECLARATION_SEPARATOR}\s*`?([A-Za-z_]+)`?",
    re.I,
)
FINAL_POSITION_MULTIPLIER_LABEL_PATTERN = re.compile(r"\bfinal_position_multiplier\b", re.I)
FINAL_POSITION_MULTIPLIER_PATTERN = re.compile(
    r"\bfinal_position_multiplier\b\s*(?:=|:|：)\s*`?"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))`?"
    r"(?=\s|[。；;,，/|)]|$)",
    re.I,
)
READINESS_PATTERN = re.compile(r"readiness_level\s*(?:\||:|：)\s*`?([a-z_]+)`?", re.I)
WRITE_STATUS_PATTERN = re.compile(r"write_status\s*(?:\||:|：)\s*`?([a-z_]+)`?", re.I)
COMPLETENESS_PATTERN = re.compile(r"completeness\s*(?:\||:|：)\s*`?(full|partial)`?", re.I)
KOL_EVIDENCE_SECTION_MARKERS = ("关键依据", "关键证据", "Evidence Ledger", "证据账本")
KOL_DATA_GAP_SECTION_MARKERS = ("数据缺口", "Data Gap")
KOL_EVIDENCE_ATTRIBUTION_PATTERN = re.compile(
    r"(?:原作者|来源原文|原文|作者|来源).{0,24}"
    r"(?:公开表示|表示|曾说|写道|称|指出|提到)|"
    r"(?:according\s+to|source|author).{0,24}"
    r"(?:said|says?|states?|wrote|quoted)",
    re.I,
)
KOL_ATTRIBUTION_STOP_PATTERN = re.compile(
    r"[，,。；;！？!?：:]|因此|所以|从而|不过|但(?:是)?|然而|本报告|我们|客户|"
    r"\b(?:therefore|thus|but|however|we|client|report)\b",
    re.I,
)
KOL_UNQUOTED_ATTRIBUTION_LEAD_PATTERN = re.compile(
    r"^\s*(?:(?:可以|可|允许|建议|应(?:当|该)?|should|may|can)\s*)?"
    r"(?:小仓|少量|small\s+)?\s*$",
    re.I,
)
KOL_CHINESE_POSITION_ACTIONS = (
    "建立小仓位", "建立仓位", "建立头寸", "买入", "建仓",
    "开仓", "试仓", "试错", "加仓", "增持",
)
KOL_CHINESE_POSITION_ACTION_TEXT = "|".join(
    re.escape(value) for value in KOL_CHINESE_POSITION_ACTIONS
)
KOL_CHINESE_POSITION_ACTION_PATTERN = re.compile(KOL_CHINESE_POSITION_ACTION_TEXT)
KOL_ENGLISH_POSITION_ACTION_PATTERN = re.compile(
    r"\b(?:buy|buying)\b|"
    r"\b(?:open|opening|initiate|initiating)\s+"
    r"(?:(?:a|an|the|new|small|initial)\s+){0,3}"
    r"(?:positions?|trades?|exposure|shares|holdings)\b|"
    r"\b(?:add|adding|increase|increasing)\s+(?:to\s+)?"
    r"(?:(?:a|an|the|this|that|our|your|existing|current)\s+){0,3}"
    r"(?:positions?|trades?|exposure|shares|holdings|share\s+count)\b",
    re.I,
)
KOL_DATA_GAP_INSUFFICIENCY_PATTERN = re.compile(
    rf"(?:没有足够证据支持|尚无证据支持|无法支持)\s*(?:{KOL_CHINESE_POSITION_ACTION_TEXT})|"
    rf"未发现\s*可?\s*(?:{KOL_CHINESE_POSITION_ACTION_TEXT})\s*依据|"
    rf"无依据\s*允许\s*(?:{KOL_CHINESE_POSITION_ACTION_TEXT})"
)
KOL_NEGATION_TOKEN_PATTERN = re.compile(
    r"仅研究|不允许|未允许|不建议|未建议|不应(?:当|该)?|不得|严禁|不再|"
    r"不可|不能|不是|禁止|反对|未|不|"
    r"\bresearch\s+only\b|\b(?:do|must)\s+not\b|"
    r"\b(?:don't|can't|cannot|never|not|no)\b",
    re.I,
)
KOL_DOUBLE_NEGATION_PATTERN = re.compile(
    r"(?:不|未)\s*(?:禁止|反对)|不是\s*(?:不能|没有)|"
    r"\bcannot\s+not\b|\bnot\s+(?:prohibited|forbidden|opposed)\s+to\b",
    re.I,
)
KOL_ADVERSATIVE_RESET_PATTERN = re.compile(
    r"但(?:是)?|不过|然而|可是|转为|改为|"
    r"\b(?:but|however|yet|instead)\b",
    re.I,
)
KOL_POSITIVE_RESET_PATTERN = re.compile(
    r"仍?可(?:以)?|允许|建议|应(?:该|当)?|\b(?:can|may|should)\b",
    re.I,
)
KOL_NEGATION_WINDOW = 96
KOL_CLAUSE_BOUNDARIES = "\n。；;！？!?：:"
MAX_MAIN_REPORT_LINES = 40
MAX_CORE_CONCLUSION_NONEMPTY_LINES = 2
MAX_NON_TABLE_EXPLANATION_BULLETS = 5
NUMERIC_WIN_STAT_PATTERN = re.compile(
    r"(?:胜率|准确率|命中率)\s*(?:[:：=为是达至约近]|达到)?\s*"
    r"(?:\d+(?:\.\d+)?\s*%|\d+\s*/\s*\d+|0(?:\.\d+)?|1(?:\.0+)?)"
    r"|\b(?:win[\s_-]*rate|accuracy|hit[\s_-]*rate)\b\s*"
    r"(?:(?:is|was|of|about)\s+|[:=]\s*)?"
    r"(?:\d+(?:\.\d+)?\s*%|\d+\s*/\s*\d+|0(?:\.\d+)?|1(?:\.0+)?)",
    re.I,
)
EVALUATED_N_DISCLOSURE_PATTERN = re.compile(
    r"\bevaluated_n\b\s*[:=]\s*\d+|\bn\s*=\s*\d+",
    re.I,
)
ABSTENTION_RATE_DISCLOSURE_PATTERN = re.compile(
    r"(?:弃权率|\babstention_rate\b)\s*[:：=]?\s*"
    r"(?:\d+(?:\.\d+)?\s*%|0(?:\.\d+)?|1(?:\.0+)?)",
    re.I,
)


def fail(msg: str) -> NoReturn:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def warn(msg: str) -> None:
    print(f"WARN: {msg}")


def _is_unexpected_default_ignorable(value: str) -> bool:
    codepoint = ord(value)
    return (
        unicodedata.category(value) == "Cf"
        or any(
            start <= codepoint <= end
            for start, end in DEFAULT_IGNORABLE_CODEPOINT_RANGES
        )
    )


def _reject_unexpected_default_ignorables(text: str) -> None:
    controls = sorted({ord(value) for value in text if _is_unexpected_default_ignorable(value)})
    if controls:
        rendered = ",".join(f"U+{value:04X}" for value in controls[:8])
        fail(
            "unexpected Unicode default-ignorable/format-control character "
            f"detected: {rendered}"
        )


def canonical_safety_representation(text: str) -> str:
    """Return a deterministic scan-only representation without mutating the report."""
    _reject_unexpected_default_ignorables(text)
    canonical = text
    for _ in range(MAX_CANONICALIZATION_PASSES):
        transformed = unicodedata.normalize("NFKC", html.unescape(canonical))
        if transformed == canonical:
            _reject_unexpected_default_ignorables(canonical)
            return canonical
        canonical = transformed
    fail(
        "report canonicalization did not reach a fixed point within "
        f"{MAX_CANONICALIZATION_PASSES} passes"
    )


def render_equivalent_safety_representation(text: str) -> str:
    """Remove only proven non-rendered safety boundaries for additional scans."""
    rendered = text
    for _ in range(MAX_RENDER_EQUIVALENT_PASSES):
        transformed = HTML_COMMENT_PATTERN.sub("", rendered)
        transformed = INLINE_HTML_TAG_PATTERN.sub("", transformed)
        transformed = INLINE_LINK_PATTERN.sub(r"\1", transformed)
        transformed = INLINE_FORMATTING_PATTERN.sub(
            lambda match: match.group("body"), transformed
        )
        transformed = UNDERSCORE_EMPHASIS_PATTERN.sub(
            lambda match: match.group("body"), transformed
        )
        if transformed == rendered:
            return rendered
        rendered = transformed
    fail(
        "render-equivalent report scanning did not reach a fixed point within "
        f"{MAX_RENDER_EQUIVALENT_PASSES} passes"
    )


def has_any(text: str, phrases: list[str]) -> bool:
    return any(p in text for p in phrases)


def count_core_conclusion_lines(lines: list[str]) -> int:
    start = None
    for i, line in enumerate(lines):
        if "核心结论" in line or "一句话结论" in line:
            start = i + 1
            break
    if start is None:
        return 0
    count = 0
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.endswith("：") or stripped.endswith(":") or stripped.startswith("##"):
            break
        if stripped:
            count += 1
    return count


def count_non_table_action_bullets(lines: list[str]) -> int:
    total = 0
    in_explanation_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            continue
        if stripped.endswith("：") or stripped.endswith(":") or stripped.startswith("##"):
            in_explanation_section = any(k in stripped for k in ("解释", "补充说明", "说明")) and not any(k in stripped for k in ("关键依据", "行动线", "行动计划", "数据缺口", "验证结果", "质量门"))
            continue
        if in_explanation_section and stripped.startswith("-"):
            total += 1
    return total


def _is_report_section_header(line: str) -> bool:
    return line.startswith("#") or (
        not line.startswith(("-", "*", "|")) and line.endswith((":", "："))
    )


def _kol_report_section(normalized_header: str) -> str:
    lowered = normalized_header.lower()
    if any(marker.lower() in lowered for marker in KOL_EVIDENCE_SECTION_MARKERS):
        return "evidence"
    if any(marker.lower() in lowered for marker in KOL_DATA_GAP_SECTION_MARKERS):
        return "data_gap"
    return "other"


def _kol_position_action_matches(line: str):
    matches = list(KOL_CHINESE_POSITION_ACTION_PATTERN.finditer(line))
    matches.extend(KOL_ENGLISH_POSITION_ACTION_PATTERN.finditer(line))
    return sorted(matches, key=lambda value: value.start())


def _iter_kol_action_language_lines(text: str):
    section = "other"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        normalized = line.lstrip("#*- ").strip()
        if _is_report_section_header(line):
            section = _kol_report_section(normalized)
        matches = _kol_position_action_matches(line)
        if not matches:
            continue
        yield raw_line, section, matches


def _is_traceable_attributed_evidence_action(
    line: str,
    action_match,
    action_matches,
) -> bool:
    if not EID_PATTERN.search(line):
        return False
    action_start, action_end = action_match.span()
    attributions = [
        match
        for match in KOL_EVIDENCE_ATTRIBUTION_PATTERN.finditer(line)
        if match.start() < action_start
    ]
    if not attributions:
        return False
    attribution = attributions[-1]
    for opening, closing in (("“", "”"), ("‘", "’"), ('"', '"')):
        quote_start = line.rfind(opening, attribution.start(), action_start)
        quote_end = line.find(closing, action_end)
        if quote_start >= attribution.start() and quote_end >= action_end:
            return True
    first_source_action = next(
        (match for match in action_matches if match.start() >= attribution.end()), None
    )
    if first_source_action is None:
        return False
    lead = line[attribution.end():first_source_action.start()]
    if any(value in lead for value in ("”", "’", '"')):
        return False
    if KOL_ATTRIBUTION_STOP_PATTERN.search(lead):
        return False
    if not KOL_UNQUOTED_ATTRIBUTION_LEAD_PATTERN.fullmatch(lead):
        return False
    boundary = KOL_ATTRIBUTION_STOP_PATTERN.search(
        line, first_source_action.end()
    )
    source_span_end = boundary.start() if boundary else len(line)
    return first_source_action.start() <= action_start and action_end <= source_span_end


def _is_conservative_data_gap_action(line: str, action_match) -> bool:
    return any(
        match.start() <= action_match.start() and action_match.end() <= match.end()
        for match in KOL_DATA_GAP_INSUFFICIENCY_PATTERN.finditer(line)
    )


def _local_clause_prefix(line: str, action_start: int) -> str:
    prefix = line[max(0, action_start - KOL_NEGATION_WINDOW):action_start]
    boundary = max((prefix.rfind(value) for value in KOL_CLAUSE_BOUNDARIES), default=-1)
    prefix = prefix[boundary + 1:]
    resets = list(KOL_ADVERSATIVE_RESET_PATTERN.finditer(prefix))
    return prefix[resets[-1].end():] if resets else prefix


def _has_local_kol_negation(line: str, action_start: int) -> bool:
    prefix = _local_clause_prefix(line, action_start)
    negations = list(KOL_NEGATION_TOKEN_PATTERN.finditer(prefix))
    if not negations:
        return False
    last_negation_end = negations[-1].end()
    if KOL_POSITIVE_RESET_PATTERN.search(prefix[last_negation_end:]):
        return False
    if any(
        match.end() >= last_negation_end
        for match in KOL_DOUBLE_NEGATION_PATTERN.finditer(prefix)
    ):
        return False
    return True


def _find_unnegated_kol_position_action(text: str) -> str | None:
    for line, section, matches in _iter_kol_action_language_lines(text):
        for match in matches:
            if section == "evidence" and _is_traceable_attributed_evidence_action(
                line, match, matches
            ):
                continue
            if section == "data_gap" and _is_conservative_data_gap_action(line, match):
                continue
            if not _has_local_kol_negation(line, match.start()):
                return match.group(0)
    return None


def _validate_kol_handoff_contract(result: dict[str, object]) -> None:
    memory_link = result.get("memory_link")
    card_ids = memory_link.get("kol_method_card_ids") if isinstance(memory_link, dict) else []
    has_cards = isinstance(card_ids, list) and bool(card_ids)
    raw_signals = result.get("suggested_module_signals")
    if raw_signals is None:
        signals: list[dict[str, object]] = []
    elif isinstance(raw_signals, list) and all(isinstance(row, dict) for row in raw_signals):
        signals = raw_signals
    else:
        fail("KOL handoff collection must be an array of module signals")
    kol_signals = [
        signal for signal in signals
        if signal.get("module") == "x_frontline"
        and signal.get("sub_framework") == "kol_method_card"
    ]
    if has_cards and len(kol_signals) != 1:
        fail("KOL handoff missing or duplicated for non-empty kol_method_cards")
    if not kol_signals:
        return
    signal = kol_signals[0]
    multiplier = signal.get("position_multiplier")
    valid_multiplier = (
        isinstance(multiplier, (int, float))
        and not isinstance(multiplier, bool)
        and float(multiplier) == 0.0
    )
    if signal.get("max_action_level") != "L0" or not valid_multiplier:
        fail("KOL handoff must be L0 with position_multiplier=0.0")
    if signal.get("tighten_only") is not True or signal.get("no_order_execution") is not True:
        fail("KOL handoff must preserve tighten_only and no_order_execution semantics")
    if has_cards and result.get("suggested_module_signal") != signal:
        fail("KOL handoff must remain the legacy suggested_module_signal for compatibility")


def _validate_provenance_bundle(
    path: Path,
    *,
    now: datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"provenance bundle unreadable: {exc}")
    if not isinstance(bundle, dict):
        fail("provenance bundle root must be a JSON object")
    result = provenance_guard.validate_bundle(bundle, base_dir=path.parent, now=now)
    if not result["ok"]:
        codes = ",".join(sorted({row["code"] for row in result["errors"]}))
        fail(f"provenance bundle blocked: {codes}")
    _validate_kol_handoff_contract(result)
    return result, bundle


def _validate_report_structure(text: str) -> None:
    raw_lines = text.splitlines()
    nonempty_lines = [line for line in raw_lines if line.strip()]
    if len(nonempty_lines) > MAX_MAIN_REPORT_LINES:
        fail(f"main report too long: {len(nonempty_lines)} non-empty lines > {MAX_MAIN_REPORT_LINES}; move full ledger to appendix/file")
    core_lines = count_core_conclusion_lines(raw_lines)
    if core_lines > MAX_CORE_CONCLUSION_NONEMPTY_LINES:
        fail(f"core conclusion too long: {core_lines} non-empty lines > {MAX_CORE_CONCLUSION_NONEMPTY_LINES}")
    bullet_count = count_non_table_action_bullets(raw_lines)
    if bullet_count > MAX_NON_TABLE_EXPLANATION_BULLETS:
        fail(f"too many non-table explanatory bullets: {bullet_count} > {MAX_NON_TABLE_EXPLANATION_BULLETS}; use a table or appendix")
    for label, phrases in REQUIRED_ANY.items():
        if not has_any(text, phrases):
            fail(f"missing required report block: {label} ({'/'.join(phrases)})")
    for phrase in REQUIRED_PHRASES:
        if phrase not in text:
            fail(f"missing required quality gate phrase: {phrase}")


def _validate_report_safety(text: str) -> None:
    for pattern in SECRET_PATTERNS:
        if re.search(pattern, text):
            fail(f"secret/grok2api pattern detected: {pattern}")
    for pattern in PLACEHOLDER_PATTERNS:
        if re.search(pattern, text):
            fail(f"template placeholder not replaced: {pattern}")
    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, text):
            fail(f"prompt-injection instruction detected: {pattern}")


def _quality_gate_values(text: str, label: str) -> list[str]:
    pattern = re.compile(
        rf"(?im)^[ \t]*[-*]?[ \t]*{re.escape(label)}[ \t]*(?:\||:|：)[ \t]*(.*)$"
    )
    return [match.strip() for match in pattern.findall(text)]


def _quality_gate_value(text: str, label: str) -> str | None:
    values = _quality_gate_values(text, label)
    return values[0] if values else None


def _strict_quality_gate_value(text: str, label: str, code: str) -> str:
    values = _quality_gate_values(text, label)
    if len(values) != 1:
        fail(f"{code}: purpose-bound report requires exactly one {label} declaration")
    return values[0]


def _parse_report_time(
    value: object,
    *,
    allow_legacy_date_suffix: bool = False,
) -> datetime | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    date_match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:\s|$)", candidate)
    if date_match and len(date_match.group(1)) == len(candidate):
        try:
            parsed = datetime.fromisoformat(date_match.group(1)).replace(
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=UTC
            )
        except ValueError:
            return None
        return parsed
    if (
        allow_legacy_date_suffix
        and date_match
        and not re.match(r"^\d{4}-\d{2}-\d{2}T", candidate)
    ):
        try:
            return datetime.fromisoformat(date_match.group(1)).replace(
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=UTC
            )
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _is_observable_refresh_trigger(value: str) -> bool:
    return bool(re.search(
        r"\d{4}-\d{2}-\d{2}|(?:[<>]=?|≤|≥)[ \t]*\d|"
        r"\d+(?:\.\d+)?[ \t]*(?:%|bp|bps|天|日|小时|分钟|days?|hours?|minutes?)\b|"
        r"更新|发布|披露|财报|指引|跌破|突破|收盘|上穿|下穿|修正|公告|"
        r"政策变化|评级变化|基准变化|公式变化|阈值|触发|出现|转为|异动|"
        r"\b(?:updated?|released?|published|filed?|earnings|guidance|cross(?:es|ed)?|"
        r"break(?:s|out|down)?|above|below|correction|announcement|policy[ -]change|"
        r"rating[ -]change|benchmark[ -]change|formula[ -]change|threshold|"
        r"trigger(?:ed)?|falsifier)\b|"
        r"\b(?:source|data|filing|correction)\b.{0,24}\b(?:available|updated|released|published)\b",
        value,
        re.I,
    ))


def _validate_freshness_contract(
    text: str,
    *,
    now: datetime,
    strict_runtime: bool,
    provenance_as_of: datetime | None,
) -> None:
    value_for = (
        lambda label, code: _strict_quality_gate_value(text, label, code)
        if strict_runtime
        else _quality_gate_value(text, label)
    )
    stale_after = value_for(
        "stale_after", "report_freshness_stale_after_declaration_count"
    )
    if stale_after is None or not stale_after:
        fail("report_freshness_stale_after_missing: stale_after must have a value")
    parsed_stale_after = _parse_report_time(
        stale_after,
        allow_legacy_date_suffix=not strict_runtime,
    )
    if parsed_stale_after is None:
        fail("report_freshness_stale_after_invalid: stale_after must be date-only or RFC3339")
    must_refresh_if = value_for(
        "must_refresh_if", "report_freshness_trigger_declaration_count"
    )
    if (
        must_refresh_if is None
        or not must_refresh_if
        or must_refresh_if.strip().lower() in {"none", "n/a", "na", "unknown", "-"}
    ):
        fail("report_freshness_trigger_missing: must_refresh_if requires an observable trigger")
    if strict_runtime and not _is_observable_refresh_trigger(must_refresh_if):
        fail("report_freshness_trigger_invalid: must_refresh_if is not an observable trigger")
    review_clock = value_for(
        "review clock", "report_review_clock_declaration_count"
    )
    if review_clock is None or not review_clock:
        fail("report_review_clock_missing: review clock must have a nonblank value")
    if strict_runtime and parsed_stale_after <= now:
        fail("report_freshness_expired: stale_after is not later than the trusted runtime clock")
    if strict_runtime and provenance_as_of is not None and parsed_stale_after <= provenance_as_of:
        fail("report_freshness_order_invalid: stale_after must be later than provenance as_of")


def _single_declared_value(pattern: re.Pattern[str], text: str, code: str) -> str:
    values = [match.strip() for match in pattern.findall(text) if match.strip()]
    if len(values) != 1:
        fail(f"{code}: exactly one nonblank declaration is required")
    return values[0]


def _declared_eids(text: str) -> set[str]:
    raw = _single_declared_value(
        CANONICAL_EIDS_PATTERN,
        text,
        "report_provenance_eid_declaration_missing",
    )
    return set(EID_PATTERN.findall(raw))


def _report_evidence_eids(text: str) -> set[str]:
    return set(EVIDENCE_ROW_EID_PATTERN.findall(text))


def _report_primary_targets(text: str) -> set[str]:
    return {
        value.upper()
        for pattern in (REPORT_PRIMARY_HEADING_PATTERN, REPORT_PRIMARY_ACTION_PATTERN)
        for value in pattern.findall(text)
    }


def _validate_provenance_binding(
    text: str,
    level: int,
    report_eids: set[str],
    provenance_result: dict[str, object] | None,
    provenance_bundle: dict[str, object] | None,
) -> None:
    if not provenance_result or not provenance_bundle:
        return
    purpose = provenance_bundle.get("bundle_purpose")
    coverage = provenance_bundle.get("claim_coverage")
    if not isinstance(purpose, dict) or not isinstance(coverage, dict):
        return
    purpose_kind = str(purpose.get("kind") or "")
    target_binding = str(coverage.get("target_binding") or "")
    if purpose_kind == "kol_method_handoff":
        if level != 0:
            fail(f"KOL purpose-bound report must remain L0, got L{level}")
        if coverage.get("accepted_claim_ids") or coverage.get("canonical_eids"):
            fail("report_kol_claim_promotion_forbidden: KOL handoff cannot carry accepted claims or canonical EIDs")
        card_declarations = KOL_METHOD_CARD_ID_PATTERN.findall(text)
        if len(card_declarations) != 1 or not card_declarations[0].strip():
            fail("report_kol_method_card_id_missing: exactly one nonblank declaration is required")
        declared_card = card_declarations[0].strip()
        memory_link = provenance_result.get("memory_link")
        installed_cards = {
            str(value)
            for value in (
                memory_link.get("kol_method_card_ids")
                if isinstance(memory_link, dict)
                and isinstance(memory_link.get("kol_method_card_ids"), list)
                else []
            )
            if str(value).strip()
        }
        if declared_card not in installed_cards:
            fail("report_kol_method_card_id_unknown: report card ID is not installed in the validated ledger")
        kol_signal = provenance_result.get("suggested_module_signal")
        safe_registry_signal = bool(
            isinstance(kol_signal, dict)
            and kol_signal.get("module") == "x_frontline"
            and kol_signal.get("sub_framework") == "kol_method_card"
            and kol_signal.get("max_action_level") == "L0"
            and kol_signal.get("position_multiplier") == 0.0
            and kol_signal.get("tighten_only") is True
            and kol_signal.get("no_order_execution") is True
            and set(kol_signal.get("allowed_effects") or [])
            == {"tighten", "refresh_source", "request_manual_review"}
            and isinstance(memory_link, dict)
            and memory_link.get("materiality_eligible") is False
            and provenance_result.get("no_order_execution") is True
        )
        if not safe_registry_signal:
            fail("report_kol_registry_safety_invalid: installed-card compatibility requires L0/zero/tighten-only/nonmaterial/no-order provenance")
        # claim_coverage remains exclusive to target_binding. Other explicitly selected
        # ledger cards retain their legacy, nonpromoting L0 path and do not inherit the
        # target card's quotes, claims, or freshness assertion.
        return
    if level < 1:
        return
    declared_target = _single_declared_value(
        PROVENANCE_TARGET_PATTERN,
        text,
        "report_provenance_target_missing",
    )
    if declared_target != target_binding:
        fail("report_provenance_target_mismatch: report target does not match claim coverage")
    primary_targets = _report_primary_targets(text)
    if primary_targets != {target_binding.upper()}:
        fail("report_primary_target_conflict: report primary target conflicts with provenance target")
    declared_eids = _declared_eids(text)
    coverage_eids = {str(value) for value in coverage.get("canonical_eids") or []}
    if not coverage_eids or declared_eids != coverage_eids:
        fail("report_provenance_eid_mismatch: report canonical EIDs must exactly match claim coverage")
    if not coverage_eids.issubset(report_eids):
        fail("report_evidence_canonical_eid_missing: canonical EIDs must appear in report evidence rows")
    accepted_claim_ids = coverage.get("accepted_claim_ids")
    if not isinstance(accepted_claim_ids, list) or not accepted_claim_ids:
        fail("report_provenance_accepted_claim_missing: L1+ report requires an accepted claim")


def _validate_action_contract(text: str, provenance_result: dict[str, object] | None) -> int:
    declared_levels = [
        int(value)
        for pattern in (KOL_ACTION_DECLARATION_PATTERN, DECISION_COMPILER_RESULT_PATTERN)
        for value in pattern.findall(text)
    ]
    if len(set(declared_levels)) > 1:
        rendered = ",".join(f"L{value}" for value in declared_levels)
        signals = provenance_result.get("suggested_module_signals") if provenance_result else None
        has_kol_handoff = isinstance(signals, list) and any(
            isinstance(signal, dict)
            and signal.get("module") == "x_frontline"
            and signal.get("sub_framework") == "kol_method_card"
            for signal in signals
        )
        scope = "KOL action/Decision Compiler" if has_kol_handoff else "report action/Decision Compiler"
        fail(f"report_action_declaration_conflict: {scope} declarations disagree ({rendered})")
    action_level = ACTION_LEVEL_PATTERN.search(text)
    level = declared_levels[0] if declared_levels else (
        int(action_level.group(1)) if action_level else 0
    )
    if provenance_result and provenance_result.get("provenance_readiness") == "partial" and level > 1:
        fail(f"provenance readiness=partial caps action at L1, got L{level}")
    readiness = READINESS_PATTERN.search(text)
    if level > 0 and not readiness:
        fail("action level above L0 but readiness_level is missing")
    if level > 0 and (not WRITE_STATUS_PATTERN.search(text) or not COMPLETENESS_PATTERN.search(text)):
        fail("L1+ executable decision missing write_status/completeness status line; follow SKILL.md step 8 record-decision protocol")
    if readiness:
        hard_caps = {"draft": 0, "not_actionable": 0, "needs_refresh": 0, "irreducible_uncertainty": 0, "working_view": 1, "watch_only": 1, "research_hypothesis": 1}
        readiness_level = readiness.group(1).lower()
        cap = hard_caps.get(readiness_level)
        if cap is not None and level > cap:
            fail(f"readiness_level={readiness_level} caps action at L{cap}, got L{level}")
    return level


def _validate_kol_report_contract(
    text: str,
    level: int,
    provenance_result: dict[str, object] | None,
) -> None:
    if not provenance_result:
        return
    signals = provenance_result.get("suggested_module_signals")
    has_kol_handoff = isinstance(signals, list) and any(
        isinstance(signal, dict)
        and signal.get("module") == "x_frontline"
        and signal.get("sub_framework") == "kol_method_card"
        for signal in signals
    )
    if not has_kol_handoff:
        return
    if level != 0:
        fail(f"KOL handoff caps client report action at L0, got L{level}")
    action_levels = [int(value) for value in KOL_ACTION_DECLARATION_PATTERN.findall(text)]
    compiler_levels = [int(value) for value in DECISION_COMPILER_RESULT_PATTERN.findall(text)]
    if not action_levels:
        fail("KOL client report requires an explicit L0 action declaration")
    if len(compiler_levels) != 1:
        fail("KOL client report requires exactly one Decision Compiler result declaration")
    if any(value != 0 for value in action_levels + compiler_levels):
        rendered = ",".join(f"L{value}" for value in action_levels + compiler_levels)
        fail(f"KOL client report action declarations must all agree at L0, got {rendered}")
    entry_permissions = [value.upper() for value in KOL_ENTRY_PERMISSION_PATTERN.findall(text)]
    holding_directives = [value.upper() for value in KOL_HOLDING_DIRECTIVE_PATTERN.findall(text)]
    if any(value not in {"WATCH", "BLOCK"} for value in entry_permissions):
        rendered = ",".join(entry_permissions)
        fail(f"KOL client report entry_permission must remain WATCH or BLOCK, got {rendered}")
    if any(value != "HOLD" for value in holding_directives):
        rendered = ",".join(holding_directives)
        fail(f"KOL client report holding_directive must remain HOLD, got {rendered}")
    declarations = FINAL_POSITION_MULTIPLIER_LABEL_PATTERN.findall(text)
    multipliers = FINAL_POSITION_MULTIPLIER_PATTERN.findall(text)
    if len(declarations) != 1 or len(multipliers) != 1:
        fail("KOL client report requires exactly one numeric final_position_multiplier=0.0")
    if float(multipliers[0]) != 0.0:
        fail("KOL handoff requires client report final_position_multiplier=0.0")
    positive_action = _find_unnegated_kol_position_action(text)
    if positive_action is not None:
        fail(
            "KOL L0 report contains unnegated positive position language: "
            f"{positive_action}"
        )


def _validate_evidence_contract(text: str, level: int) -> set[str]:
    if "derived_calculation" in text and not re.search(r"Formula:|formula|calculation_ref|calculation-ledger|Calculation Ledger|公式", text):
        fail("derived_calculation present without formula/calculation_ref/calculation-ledger")
    unique_eids = _report_evidence_eids(text)
    if level > 0 and len(unique_eids) < 3:
        fail(f"action level L{level} but only {len(unique_eids)} unique EIDs (need >= 3)")
    if re.search(r"行动等级.*?L[1-5]|动作等级.*?L[1-5]", text, re.S) and "冲突" not in text and "Conflict" not in text:
        warn("action level above L0 but no conflict handling language")
    if "仓位上限" in text and not unique_eids:
        warn("position cap language lacks EID references")
    return unique_eids


def _validate_disclaimer_and_falsifier(text: str) -> None:
    if not re.search(r"非下单|不构成投资建议|不下单", text):
        fail("missing non-order/disclaimer language")
    false_check = re.search(r"falsifier\s*(?:[:：]|\|)\s*(.*?)(?:\n|$)", text, re.I)
    if false_check and len(false_check.group(1).strip()) < 8:
        warn("falsifier too vague — should be concrete observable condition")


def _validate_numeric_win_stat_disclosure(text: str) -> None:
    """Numeric win/accuracy claims require the evaluated and abstention denominators."""
    if not NUMERIC_WIN_STAT_PATTERN.search(text):
        return
    if not EVALUATED_N_DISCLOSURE_PATTERN.search(text):
        fail("numeric win-rate statement missing evaluated_n (or n=<evaluated>)")
    if not ABSTENTION_RATE_DISCLOSURE_PATTERN.search(text):
        fail("numeric win-rate statement missing abstention_rate (or 弃权率)")


def validate(
    path: Path,
    provenance_bundle: Path | None = None,
    *,
    now: datetime | None = None,
) -> int:
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        fail("trusted runtime now must be timezone-aware")
    current = current.astimezone(UTC)
    if provenance_bundle:
        provenance_result, provenance_payload = _validate_provenance_bundle(
            provenance_bundle,
            now=current,
        )
    else:
        provenance_result, provenance_payload = None, None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        fail(f"report is not readable as strict UTF-8: {exc}")
    canonical_text = canonical_safety_representation(text)
    safety_representations = list(
        dict.fromkeys(
            (
                text,
                canonical_text,
                render_equivalent_safety_representation(canonical_text),
            )
        )
    )
    _validate_report_structure(text)
    strict_runtime = bool(
        isinstance(provenance_payload, dict)
        and isinstance(provenance_payload.get("bundle_purpose"), dict)
        and isinstance(provenance_payload.get("claim_coverage"), dict)
    )
    provenance_as_of = None
    if strict_runtime and provenance_payload is not None:
        provenance_as_of = _parse_report_time(provenance_payload.get("as_of"))
    _validate_freshness_contract(
        text,
        now=current,
        strict_runtime=strict_runtime,
        provenance_as_of=provenance_as_of,
    )
    levels: list[int] = []
    for safety_text in safety_representations:
        _validate_report_safety(safety_text)
        _validate_numeric_win_stat_disclosure(safety_text)
        level = _validate_action_contract(safety_text, provenance_result)
        _validate_kol_report_contract(safety_text, level, provenance_result)
        levels.append(level)
    level = max(levels, default=0)
    report_eids = _report_evidence_eids(text)
    _validate_provenance_binding(
        text,
        level,
        report_eids,
        provenance_result,
        provenance_payload,
    )
    _validate_evidence_contract(text, level)
    _validate_disclaimer_and_falsifier(text)
    print(f"OK: {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--provenance-bundle", type=Path)
    ap.add_argument("--now", help="Optional timezone-aware RFC3339 validation clock for reproducible fixtures")
    args = ap.parse_args()
    parsed_now = None
    if args.now:
        parsed_now = _parse_report_time(args.now)
        if parsed_now is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.now.strip()):
            fail("--now must be a timezone-aware RFC3339 timestamp")
    return validate(Path(args.report), args.provenance_bundle, now=parsed_now)


if __name__ == "__main__":
    raise SystemExit(main())
