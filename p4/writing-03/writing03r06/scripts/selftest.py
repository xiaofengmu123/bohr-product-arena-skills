#!/usr/bin/env python3
"""Skill self-check and result verification.

Modes:
  python scripts/selftest.py                 # package self-check (default)
  python scripts/selftest.py --verify ...    # verify a produced result.json

--selftest checks that SKILL.md has valid frontmatter, referenced scripts and
references exist, and every script compiles. Exit 0 = pass, 1 = warnings, 2 = fail.

--verify checks that result.json has case_id, every output_contract field, the
reserved fields, the four intermediate artifacts, and that each evidence quote
can be located in material text files. Exit 0 = pass, 1 = warnings, 2 = fail.
Standard library only.
"""
import argparse
import json
import os
import re
import sys

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

RESERVED_KEYS = ["status", "sources", "uncertainty", "pending_human", "change_log",
                 "intermediates"]
VALID_STATUS = {"complete", "partial", "failed", "ok", "insufficient_evidence",
                "input_error"}
INTERMEDIATE_FILES = ["format_spec.json", "content_units.json", "plan.json",
                      "fidelity_report.json"]
VALID_UNIT_STATUS = {"pending", "kept", "mapped", "missing", "dropped"}


def _read_text(path):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, OSError):
            continue
    return None


def _load_json(path):
    text = _read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def selftest(root):
    issues, warns = [], []
    sk = os.path.join(root, "SKILL.md")
    if not os.path.exists(sk):
        issues.append("缺少 SKILL.md")
        return 2, issues, warns
    text = _read_text(sk) or ""
    m = FRONTMATTER_RE.match(text)
    if not m:
        issues.append("SKILL.md 缺少 YAML frontmatter")
    else:
        fm = m.group(1)
        for key in ("name", "description", "version"):
            if not re.search(r"^%s\s*:" % re.escape(key), fm, re.MULTILINE):
                issues.append("frontmatter 缺 %s" % key)
        for key in ("/input/case.json", "/app/output/result.json",
                    "900", "64", "status", "sources", "uncertainty", "change_log"):
            if key not in text:
                warns.append("SKILL.md 未提及 %s" % key)
    sdir = os.path.join(root, "scripts")
    if not os.path.isdir(sdir):
        issues.append("缺少 scripts/ 目录")
    else:
        for name in sorted(os.listdir(sdir)):
            if not name.endswith(".py"):
                continue
            p = os.path.join(sdir, name)
            src = _read_text(p)
            if src is None:
                issues.append("无法读取 %s" % name)
                continue
            try:
                compile(src, p, "exec")
            except SyntaxError as e:
                issues.append("%s 语法错误: %s" % (name, e))
    rdir = os.path.join(root, "references")
    if not os.path.isdir(rdir):
        warns.append("无 references/ 目录（可选）")
    else:
        refs = [f for f in os.listdir(rdir) if f.endswith(".md")]
        if not refs:
            warns.append("references/ 为空")
    code = 2 if issues else (1 if warns else 0)
    return code, issues, warns


def verify(result_path, case_path, report_path, material_roots):
    issues, warns = [], []
    result = _load_json(result_path)
    if result is None:
        issues.append("result.json 不存在或不可解析")
        return 2, issues, warns, {}

    case = _load_json(case_path) if case_path and os.path.exists(case_path) else {}

    # case_id
    cid = result.get("case_id")
    if not cid or not isinstance(cid, str):
        issues.append("result.json 缺 case_id")
    elif cid.startswith("unknown:"):
        warns.append("case_id 为占位值 %s" % cid)

    # output_contract fields
    oc = case.get("output_contract")
    if isinstance(oc, dict) and oc:
        missing = [k for k in oc if k not in result]
        if missing:
            issues.append("缺 output_contract 字段: %s" % ", ".join(missing))
    else:
        for k in ("deliverable", "fact_source_map", "version_boundary"):
            if k not in result:
                warns.append("缺兜底字段 %s" % k)

    # reserved fields
    for k in RESERVED_KEYS:
        if k not in result:
            issues.append("缺保留字段 %s" % k)

    if "status" in result:
        s = result.get("status")
        if s not in VALID_STATUS:
            warns.append("status 值 %r 不在公认集合" % s)

    if not isinstance(result.get("change_log"), list):
        issues.append("change_log 应为数组")

    # intermediate artifacts
    inter = result.get("intermediates")
    inter_ok = isinstance(inter, dict)
    for name in INTERMEDIATE_FILES:
        key = name[:-5]  # strip .json -> schema key name
        if inter_ok and inter.get(key):
            if not os.path.exists(inter[key]):
                warns.append("中间产物路径不存在: %s" % inter[key])
        else:
            warns.append("intermediates.%s 未回填" % key)

    # locator/quote verifiability against material text files
    mat_texts = []
    for root in material_roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                p = os.path.join(dirpath, fn)
                t = _read_text(p)
                if t:
                    mat_texts.append((p, t))
    unverifiable = _check_quotes(result, mat_texts)
    if unverifiable:
        warns.append("有 %d 条摘录无法在材料全文定位（已记入报告）" % len(unverifiable))

    report = {
        "result": result_path,
        "case": case_path,
        "issues": issues,
        "warnings": warns,
        "unverifiable_quotes": unverifiable,
    }
    code = 2 if issues else (1 if warns else 0)
    return code, issues, warns, report


def _check_quotes(result, mat_texts):
    unverifiable = []
    fsm = result.get("fact_source_map")
    entries = fsm if isinstance(fsm, list) else []
    if isinstance(fsm, dict):
        # Accept both list and {claims: [...]} shapes.
        entries = fsm.get("claims", fsm.get("facts", []))
        if isinstance(entries, dict):
            entries = list(entries.values())
    for e in entries:
        if not isinstance(e, dict):
            continue
        quote = e.get("quote") or e.get("excerpt") or e.get("text")
        if not quote or not isinstance(quote, str):
            continue
        q = quote.strip()
        if not q or len(q) < 4:
            continue
        found = any(q in t for _p, t in mat_texts)
        if not found:
            unverifiable.append({
                "id": e.get("claim_id") or e.get("id") or e.get("unit_id"),
                "locator": e.get("locator") or e.get("source_locator"),
                "quote": q[:120],
            })
    # Also check content_units excerpts if present in intermediates
    inter = result.get("intermediates")
    if isinstance(inter, dict) and inter.get("content_units"):
        cu = _load_json(inter["content_units"])
        if isinstance(cu, dict):
            for u in cu.get("units", []):
                if not isinstance(u, dict):
                    continue
                excerpt = u.get("excerpt")
                if not excerpt or not isinstance(excerpt, str):
                    continue
                ex = excerpt.strip()
                if len(ex) < 4:
                    continue
                if not any(ex in t for _p, t in mat_texts):
                    unverifiable.append({
                        "id": u.get("id"),
                        "locator": u.get("locator"),
                        "quote": ex[:120],
                    })
    return unverifiable


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true", help="package self-check (default)")
    ap.add_argument("--verify", action="store_true", help="verify a result.json")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--report", default="/app/output/verification_report.json")
    ap.add_argument("--material-root", action="append", default=[])
    a = ap.parse_args()

    if not a.material_root:
        a.material_root = ["/input", "/app/work"]

    if a.verify:
        code, issues, warns, report = verify(a.result, a.case, a.report, a.material_root)
        if a.report:
            try:
                os.makedirs(os.path.dirname(a.report), exist_ok=True)
                with open(a.report, "w", encoding="utf-8") as f:
                    json.dump(report, f, ensure_ascii=False, indent=2)
            except OSError:
                pass
        for i in issues:
            print("[FAIL] %s" % i)
        for w in warns:
            print("[WARN] %s" % w)
        if not issues and not warns:
            print("[PASS] result.json 通过契约与可核验性自检")
        print(json.dumps({"exit": code, "issues": len(issues), "warnings": len(warns)},
                         ensure_ascii=False))
        return code

    code, issues, warns = selftest(a.root)
    for i in issues:
        print("[FAIL] %s" % i)
    for w in warns:
        print("[WARN] %s" % w)
    if code == 0:
        print("[PASS] Skill 包自检通过")
    print(json.dumps({"exit": code, "issues": len(issues), "warnings": len(warns)},
                     ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
