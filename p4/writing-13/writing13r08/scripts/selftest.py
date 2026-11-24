#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 交付前自检：SKILL.md 契约、脚本可导入、空输入、坏 JSON、
坏编码、确定性、result.json 契约。仅 Python 标准库。

退出码 0 表示全部通过，1 表示存在失败项。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PASS = []
FAIL = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


# 1. SKILL.md frontmatter
skill_path = os.path.join(ROOT, "SKILL.md")
if os.path.isfile(skill_path):
    text, _ = C.read_text_safe(skill_path)
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text or "", re.DOTALL)
    fm = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip()
    check("frontmatter_name", bool(fm.get("name")), str(fm.get("name")))
    check("frontmatter_description", bool(fm.get("description")),
          "desc %d chars" % len(fm.get("description", "")))
    check("frontmatter_version", bool(fm.get("version")), str(fm.get("version")))
    body = text.split("---", 2)[2] if text.count("---") >= 2 else ""
    nlines = len(body.splitlines())
    check("skill_body_under_180_lines", nlines <= 180, "%d lines" % nlines)
else:
    check("skill_md_exists", False, "SKILL.md missing")

# 2. references 存在
for name in ("elements.md", "checklist.md"):
    p = os.path.join(ROOT, "references", name)
    check("reference_%s" % name, os.path.isfile(p), p if os.path.isfile(p) else "missing")

# 3. 脚本可导入且关键函数存在
check("core_import", True, "core_impl imported")
for fn in ("build_skeleton", "snapshot_sources", "validate_result",
           "check_element_coverage", "check_unverifiable_superlatives",
           "check_correspondence", "load_case_meta"):
    check("fn_%s" % fn, callable(getattr(C, fn, None)))

# 4. 契约回填：未知键保留、缺值给空占位（dict→{}、list→[]、其他→None）
hint = {"custom_field": {"a": 1}, "extra": None, "list_field": []}
s = C.build_skeleton("caseX", hint)
check("contract_roundtrip_custom", "custom_field" in s and s["custom_field"] == {})
check("contract_roundtrip_extra", "extra" in s and s["extra"] is None)
check("contract_roundtrip_list", "list_field" in s and s["list_field"] == [])
check("skeleton_status", s.get("status") == "in_progress")

# 5. 空输入目录 → 不崩溃，返回 insufficient_evidence
tmp = tempfile.mkdtemp(prefix="selftest_")
try:
    empty_in = os.path.join(tmp, "empty_in")
    os.makedirs(empty_in)
    snap = C.snapshot_sources(empty_in)
    check("empty_input_no_crash", isinstance(snap, dict) and snap["files"] == {})
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# 6. 坏 JSON → 不崩溃，记录错误
tmp = tempfile.mkdtemp(prefix="selftest_")
try:
    bad = os.path.join(tmp, "case.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{ this is not json ]")
    case_id, hint, errs = C.load_case_meta(bad)
    check("bad_json_no_crash", case_id is None and any("bad_json" in e for e in errs),
          str(errs))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# 7. 坏编码 → 不崩溃（写 GBK 字节）
tmp = tempfile.mkdtemp(prefix="selftest_")
try:
    p = os.path.join(tmp, "note.txt")
    with open(p, "wb") as f:
        f.write("中文内容".encode("gbk"))
    text, errs = C.read_text_safe(p)
    check("bad_encoding_no_crash", text is not None, str(errs[:1]))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# 8. 四闸门：覆盖、修饰词、一致性
draft = "# 立项依据\n研究背景。\n# 科学问题\n# 研究目标\n目标一。\n# 研究内容\n内容一。\n# 创新点\n本课题首次填补空白。\n# 技术路线\n# 研究基础与可行性\n"
cov = C.check_element_coverage(draft)
check("gate_cover_all", all(cov.values()), str(cov))
hits = C.check_unverifiable_superlatives(draft)
check("gate_superlative_detected", len(hits) >= 1, str(hits[:1]))
corr = C.check_correspondence(["目标一"], ["内容一"], ["步骤一"], ["指标一"])
check("gate_correspondence_equal", corr.get("equal_counts") is True, str(corr))

# 9. result.json 契约校验
good = C.build_skeleton("caseX")
check("validate_good_result_empty_issues", C.validate_result(good) == [])
bad = {"case_id": "x", "status": "bogus"}
check("validate_bad_result_has_issues", len(C.validate_result(bad)) >= 1)

# 10. 确定性：同一骨架两次结果结构一致（忽略时间戳）
a1 = C.build_skeleton("caseX")
a2 = C.build_skeleton("caseX")
k1 = sorted(k for k in a1 if k != "change_log")
k2 = sorted(k for k in a2 if k != "change_log")
check("deterministic_skeleton", k1 == k2, "%d keys" % len(k1))

print("\nSELFTEST: %d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
