#!/usr/bin/env python3
"""slides-01 package self-test + determinism check. Standard library only.

Usage (run from the skill root):

    python3 -B scripts/selftest.py --root . --work /app/work --output /app/output
    python3 -B scripts/selftest.py --root /skills/weekly-skill

Exit code 0 = all checks pass; 1 = at least one FAIL (skip/warn do not fail).
The output is a checkable line-by-line report that mirrors SKILL.md's
"交付前自检清单".

Determinism contract checked here: rebuilding the deck from the *same*
``work/draft.json`` + *same* input files must reproduce the same content
fingerprint.  If draft.json is absent the determinism check is skipped (no
content to reproduce), never faked.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pptx_core as core  # noqa: E402

STATUS_OK = {"ok", "partial", "insufficient_evidence", "input_error"}
MAX_FILES = 14
MAX_TOTAL_BYTES = 1_200_000
MAX_SKILL_LINES = 180


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def _find_template(input_root: Path, draft):
    t = draft.get("template") if isinstance(draft, dict) else None
    if t:
        cand = input_root / str(t)
        if cand.is_file():
            return cand
    if input_root.is_dir():
        for p in sorted(input_root.rglob("*")):
            if p.is_file() and p.suffix.lower() in (".pptx", ".ppt"):
                return p
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="slides-01 self-test")
    ap.add_argument("--root", default=".", help="skill package root")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--input", default="/input")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    work = Path(args.work).expanduser()
    output = Path(args.output).expanduser()
    input_root = Path(args.input).expanduser()

    checks = []  # (name, status, detail)  status in pass/fail/skip/warn
    fails = 0

    def add(name, status, detail=""):
        nonlocal fails
        checks.append((name, status, detail))
        if status == "fail":
            fails += 1

    # 1) package structure & frontmatter
    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        add("SKILL.md exists", "fail", "missing %s" % skill_md)
    else:
        text = _read_text(skill_md)
        add("SKILL.md exists", "pass", "%d lines" % len(text.splitlines()))
        if len(text.splitlines()) > MAX_SKILL_LINES:
            add("SKILL.md <= %d lines" % MAX_SKILL_LINES, "fail",
                "%d lines" % len(text.splitlines()))
        else:
            add("SKILL.md <= %d lines" % MAX_SKILL_LINES, "pass", "%d lines" % len(text.splitlines()))
        fm = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.S)
        if not fm:
            add("SKILL.md YAML frontmatter", "fail", "no --- frontmatter")
        else:
            for key in ("name", "description", "version"):
                if re.search(r"^%s\s*:" % key, fm.group(1), re.M):
                    add("frontmatter has %s" % key, "pass")
                else:
                    add("frontmatter has %s" % key, "fail", "missing key")

    # 2) scripts present + parseable
    for fname in ("main.py", "pptx_core.py", "selftest.py"):
        p = root / "scripts" / fname
        if not p.is_file():
            add("scripts/%s exists" % fname, "fail", "missing")
            continue
        try:
            compile(p.read_text(encoding="utf-8"), str(p), "exec")
            add("scripts/%s compiles" % fname, "pass")
        except (OSError, SyntaxError) as exc:
            add("scripts/%s compiles" % fname, "fail", str(exc))

    # 3) package budget (exclude bytecode caches)
    files = [p for p in root.rglob("*")
             if p.is_file() and "__pycache__" not in p.parts]
    total = sum(p.stat().st_size for p in files)
    add("file count <= %d" % MAX_FILES, "pass" if len(files) <= MAX_FILES else "fail",
        "%d files" % len(files))
    add("total size <= 1.2MB", "pass" if total <= MAX_TOTAL_BYTES else "fail",
        "%d bytes" % total)

    # 4) result.json contract
    result_path = output / "result.json"
    if not result_path.is_file():
        add("result.json exists", "fail", "missing %s" % result_path)
    else:
        try:
            result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            add("result.json parses", "fail", str(exc))
            result = None
        if isinstance(result, dict):
            add("result.json parses", "pass")
            add("case_id non-empty",
                "pass" if str(result.get("case_id") or "").strip() else "fail",
                str(result.get("case_id")))
            add("status valid",
                "pass" if result.get("status") in STATUS_OK else "fail",
                str(result.get("status")))
            for key in ("case_id", "status", "editable_source", "export_artifact",
                        "content_map", "compliance_checks", "sources", "uncertainty",
                        "change_log"):
                add("result.json has %s" % key,
                    "pass" if key in result else "fail", "present" if key in result else "missing")
            if isinstance(result.get("change_log"), list):
                add("change_log is a list", "pass", "%d entries" % len(result["change_log"]))
            else:
                add("change_log is a list", "fail", "not a list")

    # 5) pptx integrity
    pptx = output / "presentation.pptx"
    ok, msg = core.is_valid_pptx(pptx)
    add("presentation.pptx valid OOXML", "pass" if ok else "fail", msg)
    slides = core.pptx_slide_count(pptx) if ok else 0
    if isinstance(result, dict):
        declared = result.get("editable_source", {}).get("slides")
        if declared is not None and int(declared) != slides:
            add("slide count matches result.json", "fail",
                "result says %s, file has %d" % (declared, slides))
        else:
            add("slide count matches result.json", "pass", "%d slides" % slides)

    # 6) PDF consistency (skip when not yet exported)
    pdf = output / "presentation.pdf"
    if pdf.is_file():
        data = core.read_bytes(pdf)
        add("presentation.pdf has %PDF header", "pass" if data and data.startswith(b"%PDF-") else "fail")
        pdf_pages = core.pdf_page_count(pdf)
        if ok and pdf_pages and pdf_pages != slides:
            add("PDF page count equals slide count", "fail",
                "%d vs %d" % (pdf_pages, slides))
        else:
            add("PDF page count equals slide count", "pass",
                "%d pages / %d slides" % (pdf_pages, slides))
    else:
        add("presentation.pdf present", "skip", "not yet exported (office-renderer step)")

    # 7) draft validation
    draft_path = work / "draft.json"
    draft = None
    if not draft_path.is_file():
        add("work/draft.json present", "skip", "missing; content authored by agent")
    else:
        try:
            draft = json.loads(draft_path.read_text(encoding="utf-8-sig"))
            add("work/draft.json parses", "pass")
        except (OSError, json.JSONDecodeError) as exc:
            add("work/draft.json parses", "fail", str(exc))
        if draft is not None:
            valid, errs = core.validate_draft(draft)
            add("draft schema valid", "pass" if valid else "fail",
                "; ".join(errs[:3]) if errs else "ok")

    # 8) determinism: rebuild from same draft + input and compare fingerprints
    if draft is not None and ok:
        try:
            tpl = _find_template(input_root, draft)
            parts, _, _ = core.build_presentation_parts(draft, tpl, input_root)
            rebuilt_fp = core.parts_fingerprint(parts)
            on_disk = core.parts_fingerprint(core.read_zip(pptx))
            recorded = (result or {}).get("runtime", {}).get("output_fingerprint", "")
            if rebuilt_fp == on_disk:
                add("deterministic rebuild (draft -> same pptx parts)", "pass", rebuilt_fp[:16])
            else:
                add("deterministic rebuild (draft -> same pptx parts)", "fail",
                    "rebuilt %s != on-disk %s" % (rebuilt_fp[:16], on_disk[:16]))
            if recorded and recorded != rebuilt_fp:
                add("result.json output_fingerprint matches rebuild", "fail",
                    "recorded %s != rebuilt %s" % (recorded[:16], rebuilt_fp[:16]))
            else:
                add("result.json output_fingerprint matches rebuild", "pass", rebuilt_fp[:16])
        except Exception as exc:  # noqa: BLE001 - self-test must not crash
            add("determinism check runs", "fail", "%s: %s" % (exc.__class__.__name__, exc))
    else:
        add("determinism check runs", "skip", "requires draft.json and a valid pptx")

    # report
    width = max(len(n) for n, _, _ in checks) if checks else 10
    for name, status, detail in checks:
        mark = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP", "warn": "WARN"}[status]
        print("%-6s %-*s %s" % (mark, width, name, detail))
    print("-" * 60)
    print("TOTAL: %d checks, %d fail" % (len(checks), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
