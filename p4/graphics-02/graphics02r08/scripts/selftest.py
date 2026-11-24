#!/usr/bin/env python3
"""scix-graphics 自检：契约完整性、产物存在、SVG 文本可编辑性、PNG/PDF 魔数与 DPI。

纯标准库。始终退出码 0（异常只降级记录，不崩溃），报告写入 work/check_report.json。
"""
import argparse
import json
import os
import struct
import sys
import xml.etree.ElementTree as ET

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

SVG_NS = "{http://www.w3.org/2000/svg}"

VALID_STATUS = {"complete", "partial", "failed"}


def read_json_robust(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def check_svg(path):
    """SVG 存在、可解析、含 <text> 节点（未转曲）。"""
    if not os.path.exists(path):
        return {"path": path, "ok": False, "issue": "missing"}
    try:
        with open(path, "rb") as f:
            head = f.read(64)
        if b"<svg" not in head[:64].lower():
            # 尝试全文确认（BOM/注释前缀情况）
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(4096)
            if "<svg" not in text.lower():
                return {"path": path, "ok": False, "issue": "not_svg"}
        tree = ET.parse(path)
        root = tree.getroot()
        texts = list(root.iter(SVG_NS + "text"))
        if not texts:
            texts = list(root.iter("text"))
        return {"path": path, "ok": True, "text_nodes": len(texts)}
    except (ET.ParseError, OSError, ValueError) as e:
        return {"path": path, "ok": False, "issue": "parse_error: %s" % e}


def check_png(path):
    if not os.path.exists(path):
        return {"path": path, "ok": False, "issue": "missing"}
    with open(path, "rb") as f:
        sig = f.read(8)
    if sig != b"\x89PNG\r\n\x1a\n":
        return {"path": path, "ok": False, "issue": "bad_magic"}
    dpi = None
    try:
        with open(path, "rb") as f:
            data = f.read()
        pos = 8
        while pos + 12 <= len(data):
            length = struct.unpack(">I", data[pos:pos + 4])[0]
            ctype = data[pos + 4:pos + 8].decode("latin-1")
            if ctype == "pHYs":
                xppu = struct.unpack(">I", data[pos + 8:pos + 12])[0]
                unit = data[pos + 16]
                if unit == 1:
                    dpi = round(xppu * 0.0254)
                break
            pos += 12 + length
    except (OSError, struct.error):
        pass
    return {"path": path, "ok": True, "dpi": dpi}


def check_pdf(path):
    if not os.path.exists(path):
        return {"path": path, "ok": False, "issue": "missing"}
    with open(path, "rb") as f:
        head = f.read(5)
    if head != b"%PDF-":
        return {"path": path, "ok": False, "issue": "bad_magic"}
    return {"path": path, "ok": True}


def resolve(out_dir, rel):
    if not rel:
        return None
    if os.path.isabs(rel):
        return rel
    return os.path.join(out_dir, rel)


def main():
    ap = argparse.ArgumentParser(description="scix-graphics 自检")
    ap.add_argument("--result", default=os.environ.get("OUTPUT_DIR", "/app/output") + "/result.json")
    ap.add_argument("--out", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    args = ap.parse_args()

    result = read_json_robust(args.result)
    checks = []
    problems = []

    if result is None:
        problems.append("result.json 不存在或不可解析")
        report = {"overall": "fail", "checks": [], "problems": problems}
    else:
        # 1) case_id
        if result.get("case_id"):
            checks.append({"id": "case_id", "ok": True, "value": result.get("case_id")})
        else:
            checks.append({"id": "case_id", "ok": False, "issue": "empty"})
            problems.append("case_id 为空")
        # 2) status
        st = result.get("status")
        if st in VALID_STATUS:
            checks.append({"id": "status", "ok": True, "value": st})
        else:
            checks.append({"id": "status", "ok": False, "value": st})
            problems.append("status=%r 非最终状态" % st)
        # 3) output_contract 字段覆盖
        contract = result.get("output_contract")
        if isinstance(contract, dict):
            filled = sum(1 for v in contract.values() if v not in (None, [], "", {}))
            missing = [k for k, v in contract.items() if v in (None, [], "", {})]
            checks.append({"id": "contract", "ok": True, "total": len(contract),
                           "filled": filled, "missing": missing})
            if missing:
                problems.append("contract 未填字段 %d 个（需在 uncertainty 说明）: %s" %
                                (len(missing), ", ".join(missing[:8])))
        else:
            checks.append({"id": "contract", "ok": False, "issue": "output_contract 非对象"})
            problems.append("output_contract 缺失或非对象")
        # 4) 元数据字段（uncertainty/pending_human 允许为空列表；sources/change_log 须非空）
        for k in ("sources", "change_log"):
            if result.get(k) not in (None, [], {}):
                checks.append({"id": k, "ok": True})
            else:
                checks.append({"id": k, "ok": False, "issue": "missing_or_empty"})
                problems.append("%s 缺失或为空" % k)
        for k in ("uncertainty", "pending_human"):
            if isinstance(result.get(k), list):
                checks.append({"id": k, "ok": True, "count": len(result[k])})
            else:
                checks.append({"id": k, "ok": False, "issue": "missing"})
                problems.append("%s 缺失（应至少为空列表）" % k)
        # 5) 产物文件检查
        products = result.get("products") or []
        svg_checked, png_checked, pdf_checked = False, False, False
        # 收集所有产物路径（products + contract 字段中的路径值）
        paths = list(products)
        if isinstance(contract, dict):
            for v in contract.values():
                if isinstance(v, str):
                    paths.append(v)
                elif isinstance(v, dict):
                    for vv in v.values():
                        if isinstance(vv, str):
                            paths.append(vv)
        for rel in paths:
            p = resolve(args.out, rel)
            ext = os.path.splitext(rel)[1].lower()
            if ext == ".svg":
                r = check_svg(p)
                checks.append({"id": "svg", "check": r})
                svg_checked = True
                if not r["ok"]:
                    problems.append("SVG 问题: %s" % r)
                elif r.get("text_nodes", 0) == 0:
                    problems.append("SVG 无 <text> 节点（文字可能转曲）")
            elif ext == ".png":
                r = check_png(p)
                checks.append({"id": "png", "check": r})
                png_checked = True
                if not r["ok"]:
                    problems.append("PNG 问题: %s" % r)
                elif r.get("dpi") is not None and r["dpi"] < 300:
                    problems.append("PNG dpi=%s < 300" % r["dpi"])
            elif ext == ".pdf":
                r = check_pdf(p)
                checks.append({"id": "pdf", "check": r})
                pdf_checked = True
                if not r["ok"]:
                    problems.append("PDF 问题: %s" % r)
        # 6) 可编辑源文件与导出件齐全性（仅提示，不硬失败）
        if not svg_checked:
            problems.append("未找到 SVG 产物（可编辑源文件缺失）")
        if not png_checked:
            problems.append("未找到 PNG 导出件（需真实渲染或如实 null+说明）")
        if not pdf_checked:
            problems.append("未找到 PDF 导出件")
        report = {
            "overall": "ok" if not problems else "issues",
            "case_id": result.get("case_id"),
            "status": result.get("status"),
            "checks": checks,
            "problems": problems,
        }
    os.makedirs(args.work, exist_ok=True)
    with open(os.path.join(args.work, "check_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
