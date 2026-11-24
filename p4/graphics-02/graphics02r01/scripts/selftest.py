#!/usr/bin/env python3
"""自检：契约完整性、产物存在、格式魔数、SVG 可编辑性、证据链一致性。

只依赖 Python 标准库。调用：

    python scripts/selftest.py --result /app/output/result.json

报告写入 --report（默认 WORK_DIR/check_report.json）。退出码：报告生成成功→0；
result.json 无法读取→2。
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_impl import load_json_safe, read_text_safe, extract_pdf_text  # noqa: E402

DIMENSIONS = {
    "actual": "实际效果", "gain": "使用增益", "innovation": "创新增益",
    "reliability": "可靠体验", "ecosystem": "生态贡献",
}


def _report_check(checks, cid, dim, ok, note):
    checks.append({"id": cid, "dimension": dim, "ok": ok, "note": note})


def _png_ok(path):
    try:
        with open(path, "rb") as f:
            return f.read(8) == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


def _pdf_ok(path):
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def _quote_check(quote, source_file, input_dir):
    """quote 是否为源文本子串；不可读返回 'unverified'。"""
    if not quote or not source_file:
        return "unverified"
    base = input_dir
    p = source_file if os.path.isabs(source_file) else os.path.join(base, source_file)
    if not os.path.exists(p):
        return "unverified"
    if source_file.lower().endswith(".pdf"):
        text, _err = extract_pdf_text(p)
        if text is None:
            return "unverified"
    else:
        text, _err = read_text_safe(p)
        if text is None:
            return "unverified"
    return "hit" if quote in text else "miss"


def main():
    ap = argparse.ArgumentParser(prog="selftest.py")
    ap.add_argument("--result", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--input", default=None)
    a = ap.parse_args()

    input_dir = a.input or os.environ.get("INPUT_DIR", "/input")
    out_dir = os.environ.get("OUTPUT_DIR", "/app/output")
    work_dir = os.environ.get("WORK_DIR", "/app/work")
    result_path = a.result or os.path.join(out_dir, "result.json")
    report_path = a.report or os.path.join(work_dir, "check_report.json")

    checks = []
    result, err = load_json_safe(result_path)
    if not isinstance(result, dict):
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        report = {"overall": "fail", "critical": ["result.json 无法读取：%s" % err],
                  "checks": checks, "result_path": result_path}
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    # 1. case_id / status
    _report_check(checks, "case_id", "actual", bool(result.get("case_id")),
                  "case_id=%r" % result.get("case_id"))
    _report_check(checks, "status", "actual",
                  result.get("status") in ("complete", "partial", "failed"),
                  "status=%r" % result.get("status"))

    # 2. output_contract 字段
    contract = result.get("output_contract")
    if isinstance(contract, dict) and contract:
        null_fields = [k for k, v in contract.items() if v is None]
        _report_check(checks, "contract_fields", "actual", True,
                      "字段数=%d，null=%d：%s" % (len(contract), len(null_fields),
                                                 ",".join(null_fields) if null_fields else "无"))
        if null_fields:
            _report_check(checks, "contract_null_reason", "reliability",
                          bool(result.get("uncertainty") or result.get("pending_human")),
                          "null 字段需在 uncertainty/pending_human 说明")
    else:
        _report_check(checks, "contract_fields", "actual", False, "output_contract 缺失或为空")

    # 3. 元数据块
    _report_check(checks, "sources", "ecosystem", bool(result.get("sources")), "sources 存在")
    _report_check(checks, "uncertainty", "reliability",
                  isinstance(result.get("uncertainty"), list), "uncertainty 为列表")
    _report_check(checks, "pending_human", "reliability",
                  isinstance(result.get("pending_human"), list), "pending_human 为列表")
    log = result.get("change_log")
    _report_check(checks, "change_log", "ecosystem", isinstance(log, list) and len(log) > 0,
                  "change_log 条数=%d" % (len(log) if isinstance(log, list) else 0))
    if isinstance(log, list) and log:
        _report_check(checks, "change_log_time", "reliability",
                      all(isinstance(e.get("time_utc"), str) for e in log),
                      "change_log 含 time_utc")

    # 4. 产物存在 + 魔数
    artifacts = result.get("artifacts", [])
    _report_check(checks, "artifacts", "actual", isinstance(artifacts, list) and len(artifacts) > 0,
                  "artifacts 条数=%d" % (len(artifacts) if isinstance(artifacts, list) else 0))
    svg_paths = []
    for rel in artifacts:
        p = rel if os.path.isabs(rel) else os.path.join(out_dir, rel)
        exists = os.path.exists(p)
        _report_check(checks, "artifact_exists", "actual", exists, "%s" % rel)
        if not exists:
            continue
        if rel.lower().endswith(".svg"):
            svg_paths.append(p)
        elif rel.lower().endswith(".png"):
            _report_check(checks, "png_magic", "actual", _png_ok(p), "PNG 魔数 %s" % rel)
        elif rel.lower().endswith(".pdf"):
            _report_check(checks, "pdf_magic", "actual", _pdf_ok(p), "PDF 魔数 %s" % rel)

    # 5. SVG 可编辑性 + 证据属性
    svg_element_ids = set()
    for p in svg_paths:
        try:
            tree = ET.parse(p)
            root = tree.getroot()
        except ET.ParseError as e:
            _report_check(checks, "svg_parse", "actual", False, "%s 解析失败 %s" % (p, e))
            continue
        _report_check(checks, "svg_parse", "actual", True, "SVG 可解析 %s" % os.path.basename(p))
        texts = list(root.iter("{http://www.w3.org/2000/svg}text"))
        _report_check(checks, "svg_editable_text", "actual", len(texts) > 0,
                      "%s 含 %d 个 <text> 节点（未转曲）" % (os.path.basename(p), len(texts)))
        for el in root.iter():
            if "{" in (el.tag or ""):
                eid = el.get("data-element")
                if eid:
                    svg_element_ids.add(eid)
    _report_check(checks, "svg_data_attrs", "innovation", len(svg_element_ids) > 0,
                  "SVG 中带 data-element 的图元数=%d" % len(svg_element_ids))

    # 6. evidence_map 完整性 + 双向对应 + quote 校验
    em = result.get("evidence_map", [])
    _report_check(checks, "evidence_map", "innovation",
                  isinstance(em, list) and len(em) > 0, "evidence_map 条数=%d" % len(em))
    missing_keys = []
    quote_results = {"hit": 0, "miss": 0, "unverified": 0}
    for rec in em:
        for k in ("element_id", "locator", "quote", "source_file"):
            if not rec.get(k):
                missing_keys.append(rec.get("element_id", "?") + "." + k)
        q = rec.get("quote", "")
        sf = rec.get("source_file", "")
        r = _quote_check(q, sf, input_dir)
        quote_results[r] += 1
    _report_check(checks, "evidence_keys", "innovation", not missing_keys,
                  "缺键=%d %s" % (len(missing_keys), missing_keys[:5]))
    _report_check(checks, "evidence_quote", "innovation", quote_results["miss"] == 0,
                  "quote 命中=%d 未命中=%d 无法核验=%d" %
                  (quote_results["hit"], quote_results["miss"], quote_results["unverified"]))
    if svg_element_ids and em:
        em_ids = {r.get("element_id") for r in em}
        orphan_em = [e for e in em_ids if e and e not in svg_element_ids]
        orphan_svg = [e for e in svg_element_ids if e not in em_ids]
        _report_check(checks, "evidence_svg_binding", "innovation",
                      len(orphan_em) == 0,
                      "evidence 无对应 SVG 图元=%d（%s）；SVG 无 evidence=%d" %
                      (len(orphan_em), orphan_em[:3], len(orphan_svg)))

    n_ok = sum(1 for c in checks if c["ok"])
    n_total = len(checks)
    overall = "pass" if n_ok == n_total else "partial"
    report = {"overall": overall, "checks_total": n_total, "checks_ok": n_ok,
              "result_path": result_path, "checks": checks}
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
