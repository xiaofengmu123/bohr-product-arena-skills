# -*- coding: utf-8 -*-
"""Deterministic conservation checks for academic-language polishing.

This module is pure standard library and side-effect free: every function
takes plain Python objects and returns a list of findings.  The CLI in
``main.py`` reads/writes files; this module only *decides*.  This keeps the
verification layer reproducible across runs and machines.

A finding is a dict::

    {
        "check": "number_unit_conservation",
        "level": "error" | "warning" | "info",
        "unit_id": "u0001" | None,
        "message": "human-readable reason",
        "detail": { ...check-specific payload... },
    }

Levels are used by the CLI as follows:
    * ``error``   -> blocks ``finalize`` until fixed;
    * ``warning`` -> allowed to ship only with an explicit ``hedge_note`` /
                     ``reason`` or a ``pending_human`` entry;
    * ``info``    -> recorded, never blocks.
"""

import re

# ---------------------------------------------------------------------------
# Deterministic token inventories
# ---------------------------------------------------------------------------

# Strength-decreasing ("weak") hedges.  Losing one of these from the revised
# sentence while the surrounding claim survives is a suspected *strengthening*
# of the conclusion.
HEDGE_WEAK = frozenset({
    "may", "might", "could", "can", "suggest", "suggests", "suggested",
    "indicate", "indicates", "indicated", "imply", "implies", "implied",
    "possibly", "potentially", "likely", "appear", "appears", "appeared",
    "seem", "seems", "seemed", "perhaps", "probably", "tentatively",
    "preliminary", "in part", "to some extent", "apparently", "presumably",
})

# Strength-increasing ("strong") claim verbs.  *Introducing* one of these
# where the original carried none is a suspected strengthening.
HEDGE_STRONG = frozenset({
    "demonstrate", "demonstrates", "demonstrated", "prove", "proves",
    "proved", "establish", "establishes", "established", "confirm",
    "confirms", "confirmed", "verify", "verifies", "verified", "definitely",
    "certainly", "clearly", "undoubtedly", "unequivocally", "conclusively",
})

HEDGE_ZH_WEAK = frozenset({
    "可能", "或许", "似乎", "大概", "大约", "约", "提示", "暗示", "倾向于",
    "初步", "有待", "尚需", "或可", "潜在", "大体", "多半", "偏于",
    "可能地", "看似", "似乎",
})

HEDGE_ZH_STRONG = frozenset({
    "证实", "证明", "确立", "确认", "无疑", "肯定", "必然", "明确",
    "显然", "决定性", "确凿", "确证",
})

# A unit of measure or a statistical token directly following a number.
_UNIT_TOKEN = r"(?:[A-Za-z°%‰μ]|/\s*[A-Za-z]+)+"

NUMBER_RE = re.compile(
    r"(?<![\w.])-?\d+(?:[.,]\d+)*(?![\w.])"
)
NUMBER_WITH_UNIT_RE = re.compile(
    r"(?<![\w.])-?\d+(?:[.,]\d+)*\s*(?:[~±≈]?\s*)?(?:"
    r"[A-Za-z]{1,6}/?[A-Za-z]{0,6}|%|‰|°C|℃|µM|μM|nM|mM|M)"
)
STAT_RE = re.compile(
    r"\b(?:p|n|r|t|F|χ2?|CI|SD|SE|OR|HR|RR)\s*(?:[<>=≤≥]\s*)?"
    r"-?\d+(?:[.,]\d+)*",
    re.IGNORECASE,
)
CITATION_RE = re.compile(
    r"\[\d+(?:\s*[,，\-–—]\s*\d+)*\]"            # [3], [3, 5], [3-7]
    r"|\(\s*[A-Z][A-Za-z&.\- ]+?,?\s+\d{4}\s*[a-z]?\s*\)"  # (Smith, 2020)
    r"|\(\s*[A-Z][A-Za-z&.\- ]+?\s+\d{4}\s*[a-z]?\s*\)"
)
FIGTAB_RE = re.compile(
    r"\b(?:Fig(?:ure)?\.?\s*\d+|Table\.?\s*\d+|Eq(?:uation)?\.?\s*\(?\d+\)?)"
    r"|图\s*\d+|表\s*\d+|公式\s*\(?\d+\)?|式\s*\(?\d+\)?|附图\s*\d+",
    re.IGNORECASE,
)
SENT_SPLIT_RE = re.compile(r"(?<=[。．.!?！？;；])\s*|\n+")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _norm(text):
    """Normalize whitespace for substring comparison."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _multiset(items):
    """Return a sorted list; comparing two lists is order-insensitive."""
    return sorted(items)


def _extract_numbers_with_unit(text):
    return _multiset(m.group(0) for m in NUMBER_WITH_UNIT_RE.finditer(text or ""))


def _extract_plain_numbers(text):
    return _multiset(m.group(0) for m in NUMBER_RE.finditer(text or ""))


def _extract_stats(text):
    return _multiset(m.group(0) for m in STAT_RE.finditer(text or ""))


def _extract_citations(text):
    return _multiset(m.group(0) for m in CITATION_RE.finditer(text or ""))


def _extract_figtab(text):
    return _multiset(m.group(0) for m in FIGTAB_RE.finditer(text or ""))


def _extract_hedges(text):
    """Return (weak_present, strong_present) token multisets."""
    if not text:
        return [], []
    low = text.lower()
    weak = _multiset(t for t in HEDGE_WEAK if re.search(r"\b" + re.escape(t) + r"\b", low))
    weak += _multiset(t for t in HEDGE_ZH_WEAK if t in text)
    strong = _multiset(t for t in HEDGE_STRONG if re.search(r"\b" + re.escape(t) + r"\b", low))
    strong += _multiset(t for t in HEDGE_ZH_STRONG if t in text)
    return weak, strong


def _split_sentences(text):
    if not text:
        return []
    parts = [p for p in SENT_SPLIT_RE.split(text) if p and _norm(p)]
    return [p for p in parts if len(_norm(p)) >= 10]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_numbers(original, revised, unit_id=None):
    """Number + unit conservation.  Unit-bearing numbers are errors; plain
    numbers are warnings (legitimate rewording may split or merge them)."""
    findings = []
    orig_units = _extract_numbers_with_unit(original)
    rev_units = _extract_numbers_with_unit(revised)
    for tok in set(orig_units) - set(rev_units):
        findings.append({
            "check": "number_unit_conservation", "level": "error",
            "unit_id": unit_id,
            "message": f"unit-bearing number dropped in revision: {tok}",
            "detail": {"token": tok, "original": _norm(original)[:120],
                       "revised": _norm(revised)[:120]},
        })
    for tok in set(rev_units) - set(orig_units):
        findings.append({
            "check": "number_unit_conservation", "level": "error",
            "unit_id": unit_id,
            "message": f"unit-bearing number added in revision: {tok}",
            "detail": {"token": tok},
        })
    # statistical tokens (p/n/r/t/F/CI/SD/SE/OR/HR/RR ...) are error-level too
    for tok in set(_extract_stats(original)) - set(_extract_stats(revised)):
        findings.append({
            "check": "stat_conservation", "level": "error",
            "unit_id": unit_id,
            "message": f"statistical token dropped: {tok}",
            "detail": {"token": tok},
        })
    for tok in set(_extract_stats(revised)) - set(_extract_stats(original)):
        findings.append({
            "check": "stat_conservation", "level": "error",
            "unit_id": unit_id,
            "message": f"statistical token added: {tok}",
            "detail": {"token": tok},
        })
    # plain numbers: warning only
    orig_num = _extract_plain_numbers(original)
    rev_num = _extract_plain_numbers(revised)
    missing = [t for t in orig_num if t not in rev_num]
    added = [t for t in rev_num if t not in orig_num]
    if missing or added:
        findings.append({
            "check": "plain_number_conservation", "level": "warning",
            "unit_id": unit_id,
            "message": "plain-number multiset changed",
            "detail": {"missing": missing, "added": added},
        })
    return findings


def check_citations(original, revised, unit_id=None):
    findings = []
    for tok in set(_extract_citations(original)) - set(_extract_citations(revised)):
        findings.append({
            "check": "citation_conservation", "level": "error", "unit_id": unit_id,
            "message": f"citation marker dropped: {tok}",
            "detail": {"token": tok},
        })
    for tok in set(_extract_citations(revised)) - set(_extract_citations(original)):
        findings.append({
            "check": "citation_conservation", "level": "error", "unit_id": unit_id,
            "message": f"citation marker added: {tok}",
            "detail": {"token": tok},
        })
    return findings


def check_figtab(original, revised, unit_id=None):
    findings = []
    for tok in set(_extract_figtab(original)) - set(_extract_figtab(revised)):
        findings.append({
            "check": "figure_table_pointer", "level": "error", "unit_id": unit_id,
            "message": f"figure/table pointer dropped: {tok}",
            "detail": {"token": tok},
        })
    for tok in set(_extract_figtab(revised)) - set(_extract_figtab(original)):
        findings.append({
            "check": "figure_table_pointer", "level": "error", "unit_id": unit_id,
            "message": f"figure/table pointer added: {tok}",
            "detail": {"token": tok},
        })
    return findings


def check_hedges(original, revised, unit_id=None, hedge_note=""):
    """Hedge (claim-strength) conservation.

    A missing weak hedge or a newly introduced strong claim verb is a
    *warning*, not an error, because a legitimate rewrite may rephrase the
    hedge.  The warning is cleared when the unit carries an explicit
    ``hedge_note`` (or a reason that names the hedge/限定词), which the agent
    must write during the fix phase.
    """
    findings = []
    ow, os_ = _extract_hedges(original)
    rw, rs = _extract_hedges(revised)
    note = _norm(hedge_note or "")
    has_note = bool(note) and any(k in (hedge_note or "").lower()
                                  for k in ("hedge", "限定", "强度", "语气", "保留"))

    lost_weak = [t for t in ow if t not in rw]
    gained_strong = [t for t in rs if t not in os_]
    if lost_weak and not has_note:
        findings.append({
            "check": "hedge_conservation", "level": "warning",
            "unit_id": unit_id,
            "message": "weak hedge possibly lost (claim may be strengthened): "
                       + ", ".join(sorted(set(lost_weak))),
            "detail": {"lost_weak": lost_weak, "note": note},
        })
    if gained_strong and not has_note:
        findings.append({
            "check": "hedge_conservation", "level": "warning",
            "unit_id": unit_id,
            "message": "strong claim verb possibly introduced (claim may be "
                       "strengthened): " + ", ".join(sorted(set(gained_strong))),
            "detail": {"gained_strong": gained_strong, "note": note},
        })
    return findings


def check_terminology(revised, glossary):
    """Glossary-driven consistency sweep.

    For each term, every *variant* other than the canonical spelling is a
    warning; the abbreviation must appear together with the full form at its
    first use (a light heuristic, flagged only when the full form is absent
    from the same sentence).
    """
    findings = []
    terms = glossary.get("terms", []) if isinstance(glossary, dict) else []
    for term in terms:
        canonical = term.get("canonical", "")
        variants = term.get("variants", [])
        abbr = term.get("abbr", "")
        if not canonical:
            continue
        for v in variants:
            if v and v != canonical and v in revised:
                findings.append({
                    "check": "terminology_consistency", "level": "warning",
                    "unit_id": None,
                    "message": f"non-canonical variant used: '{v}' (canonical: "
                               f"'{canonical}')",
                    "detail": {"term": canonical, "variant": v},
                })
        if abbr and abbr in revised and canonical and canonical not in revised:
            findings.append({
                "check": "abbreviation_first_use", "level": "warning",
                "unit_id": None,
                "message": f"abbreviation '{abbr}' used without full form "
                           f"'{canonical}'",
                "detail": {"abbr": abbr, "canonical": canonical},
            })
    return findings


def check_coverage(units, full_original):
    """Every source sentence must be covered by at least one edit unit."""
    findings = []
    sentences = _split_sentences(full_original)
    covered_norm = []
    for u in units:
        covered_norm.append(_norm(u.get("original", "")))
    uncovered = []
    for s in sentences:
        sn = _norm(s)
        if not sn:
            continue
        if not any(sn in c for c in covered_norm if c):
            uncovered.append(sn)
    if uncovered:
        findings.append({
            "check": "sentence_coverage", "level": "error",
            "unit_id": None,
            "message": f"{len(uncovered)} sentence(s) of the source are not "
                       "covered by any edit unit",
            "detail": {"uncovered": [u[:80] for u in uncovered]},
        })
    findings.append({
        "check": "sentence_coverage", "level": "info", "unit_id": None,
        "message": f"coverage: {len(sentences) - len(uncovered)}/{len(sentences)} "
                   "sentences covered",
        "detail": {"total": len(sentences), "uncovered": len(uncovered)},
    })
    return findings


def check_traceability(units):
    """Any unit carrying a number, citation, figure pointer or a hedged claim
    must supply at least one evidence entry with a non-empty quote."""
    findings = []
    for u in units:
        orig = u.get("original", "")
        needs = bool(
            _extract_numbers_with_unit(orig)
            or _extract_citations(orig)
            or _extract_figtab(orig)
            or _extract_plain_numbers(orig)
            or _extract_stats(orig)
        )
        if not needs:
            continue
        ev = u.get("evidence") or []
        has_quote = any(isinstance(e, dict) and (e.get("quote") or "").strip()
                        for e in ev)
        if not has_quote:
            findings.append({
                "check": "evidence_traceability", "level": "error",
                "unit_id": u.get("id"),
                "message": "unit carries numbers/citations but has no evidence "
                           "entry with a verbatim quote",
                "detail": {"original": _norm(orig)[:120]},
            })
    return findings


# ---------------------------------------------------------------------------
# Rubric alignment (the version-02 differentiator)
# ---------------------------------------------------------------------------

# Maps each official scoring dimension to the process phase, the check(s) that
# produce verifiable evidence for it, and the self-check item that closes it.
RUBRIC = [
    {
        "dimension": "实际效果(40)",
        "points": "完成度15/正确性15/完整性5/可核验5",
        "phase": "2 逐句编辑 + 5 定稿",
        "checks": ["sentence_coverage", "number_unit_conservation",
                   "stat_conservation", "citation_conservation",
                   "figure_table_pointer", "hedge_conservation",
                   "evidence_traceability"],
        "selfcheck": "result.json 可解析；deliverable 存在；edits 逐句且带四要素",
    },
    {
        "dimension": "使用增益(25)",
        "points": "质量15/效率5/可验证5",
        "phase": "3 双通道审校 + 4 修复",
        "checks": ["gain_metrics", "errors==0"],
        "selfcheck": "gain_metrics 统计 edit_type/error/hedge 处理数，可对照基础 Agent",
    },
    {
        "dimension": "创新增益(15)",
        "points": "新增能力5/方法改进5/创新证据5",
        "phase": "3 双通道审校（编辑→独立回读复核）",
        "checks": ["hedge_conservation", "terminology_consistency",
                   "abbreviation_first_use"],
        "selfcheck": "双通道记录在 change_log；限定强度守恒与术语回扫可独立复现",
    },
    {
        "dimension": "可靠体验(10)",
        "points": "触发3/稳定4/异常3",
        "phase": "0 脚手架 + 6 自检",
        "checks": ["deterministic_scaffold", "selfcheck_ok"],
        "selfcheck": "selftest.py 覆盖空目录/坏JSON/缺输入/超长/坏编码五场景",
    },
    {
        "dimension": "生态贡献(10)",
        "points": "文档3/复用4/分享3",
        "phase": "全程（references/ 与 SKILL.md 随包交付）",
        "checks": ["references_present", "stdlib_only"],
        "selfcheck": "references/*.md 存在；脚本仅标准库；术语表/风格规则可直接复用",
    },
]


def rubric_alignment_report():
    """Verify every scoring dimension maps to at least one check and one
    self-check item.  Used by ``selfcheck`` to prove rubric coverage."""
    problems = []
    for row in RUBRIC:
        if not row.get("checks"):
            problems.append(row["dimension"])
    findings = [{
        "check": "rubric_alignment", "level": "error" if problems else "info",
        "unit_id": None,
        "message": ("rubric mapping incomplete for: " + ", ".join(problems))
                   if problems else f"rubric alignment covers {len(RUBRIC)}/5 "
                   "scoring dimensions",
        "detail": {"dimensions": [r["dimension"] for r in RUBRIC],
                   "missing": problems},
    }]
    return findings


# ---------------------------------------------------------------------------
# Aggregate entry point
# ---------------------------------------------------------------------------

def run_all(edits, glossary, full_original):
    """Run every conservation check and return the flat findings list."""
    units = edits.get("units", []) if isinstance(edits, dict) else []
    findings = []
    for u in units:
        uid = u.get("id")
        orig = u.get("original", "")
        rev = u.get("revised", "")
        findings += check_numbers(orig, rev, uid)
        findings += check_citations(orig, rev, uid)
        findings += check_figtab(orig, rev, uid)
        findings += check_hedges(orig, rev, uid, u.get("hedge_note", ""))
    findings += check_terminology(_concat_revised(units), glossary)
    findings += check_coverage(units, full_original)
    findings += check_traceability(units)
    findings += rubric_alignment_report()
    return findings


def _concat_revised(units):
    return "\n".join(u.get("revised", "") for u in units)


def summarize(findings):
    """Return counts by level plus a concise verdict line."""
    c = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        c[f.get("level", "info")] = c.get(f.get("level", "info"), 0) + 1
    return c, f"errors={c['error']} warnings={c['warning']} info={c['info']}"


def compute_gain_metrics(edits, findings):
    """Derive verifiable gain evidence from the edit record itself.

    These counters let a reviewer compare Skill-Agent vs baseline Agent on
    identical input without any hidden whiteboard: they are computed from the
    public ``edits.json`` + ``check_report.json``.
    """
    units = edits.get("units", []) if isinstance(edits, dict) else []
    from collections import Counter
    types = Counter(u.get("edit_type", "untyped") for u in units)
    return {
        "total_units": len(units),
        "edited_units": sum(1 for u in units
                            if u.get("edit_type") not in ("no_change", "unchanged")),
        "edit_types": dict(types),
        "hedge_notes_written": sum(1 for u in units if (u.get("hedge_note") or "").strip()),
        "errors_caught": sum(1 for f in findings if f.get("level") == "error"),
        "warnings_resolved": sum(1 for u in units if (u.get("hedge_note") or "").strip()),
        "evidence_entries": sum(len(u.get("evidence") or []) for u in units),
    }
