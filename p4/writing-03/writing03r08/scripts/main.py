#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学术格式与模板适配 Skill 的核心脚本（仅依赖 Python 标准库）。

提供两个子命令，均可被 Agent 以相对路径直接调用：
  scaffold  读取 /input/case.json，写出 /app/output/result.json 的 partial 骨架。
            幂等：已存在合法结果时只增量补齐缺失键，不覆盖已落盘内容。
  verify    校验 result.json 的契约完整性、摘录可定位性、占位符与内容单元，
            输出 verification_report.json；退出码 0=通过，1=警告，2=硬性失败。
"""
import argparse
import datetime
import json
import os
import re
import sys

UTF8_SIG = "utf-8-sig"
MAX_TEXT_FILE = 2 * 1024 * 1024          # 单文件 2MB 上限，超限跳过
TEXT_EXTS = {".md", ".txt", ".tex", ".json", ".csv", ".yaml", ".yml", ".rst", ".html", ".xml"}
PLACEHOLDER_RE = re.compile(
    r"(TODO|TBD|FIXME|XXX|PLACEHOLDER|lorem\s+ipsum|待补|占位|待填|此处填写|示例填充|待插入)",
    re.IGNORECASE,
)
VALID_STATUS = {"partial", "complete", "failed", "insufficient_evidence", "input_error"}

# 题目 output_contract 缺失时的三段式兜底契约
FALLBACK_CONTRACT = {
    "deliverable": {},
    "fact_source_map": [],
    "version_boundary": {},
}
RESERVED_FIELDS = ("sources", "uncertainty", "pending_human", "change_log")


def now_ts():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    """按 utf-8-sig 读取并容错坏编码，失败返回空串。"""
    for enc in (UTF8_SIG, "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except UnicodeDecodeError:
            continue
        except OSError:
            return ""
    return ""


def load_json(path):
    """读取 JSON，容错 BOM、坏编码、坏 JSON；不可读时返回 None。"""
    if not path or not os.path.isfile(path):
        return None
    raw = read_text(path)
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 尝试剥离 BOM 后重读
        try:
            return json.loads(raw.lstrip("\ufeff"))
        except json.JSONDecodeError:
            return None


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def resolve_case_id(case):
    """从 case.json 的常见键中解析 case_id。"""
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "caseId", "id", "armiId", "challenge_id"):
        if isinstance(case.get(key), str) and case.get(key).strip():
            return case[key].strip()
    return None


def resolve_output_contract(case):
    """解析 output_contract；缺失时用三段式兜底。返回 dict 字段名 -> 默认值。"""
    if not isinstance(case, dict):
        return dict(FALLBACK_CONTRACT)
    for key in ("output_contract", "outputContract", "output_contract_fields", "output_fields"):
        val = case.get(key)
        if isinstance(val, dict) and val:
            contract = {k: default_for_field(k) for k in val}
            return contract
        if isinstance(val, list) and val and all(isinstance(x, str) for x in val):
            return {k: default_for_field(k) for k in val}
    return dict(FALLBACK_CONTRACT)


def default_for_field(field):
    name = str(field).lower()
    if "map" in name or "list" in name or "items" in name:
        return []
    if "deliverable" in name or "boundary" in name or "meta" in name or "info" in name or "spec" in name:
        return {}
    return None


def build_skeleton(case):
    contract = resolve_output_contract(case)
    skeleton = {
        "case_id": resolve_case_id(case),
        "status": "partial",
        "sources": [],
        "uncertainty": {
            "claims_requiring_confirmation": [],
            "conflicting_evidence": [],
            "material_gaps": [],
            "limitations": [],
        },
        "pending_human": [],
        "change_log": [],
    }
    for field, default in contract.items():
        skeleton[field] = default
    return skeleton


def merge_result(existing, skeleton):
    """幂等合并：已存在的结果不被清空，只补齐缺失/为空的键。"""
    if not isinstance(existing, dict) or not existing:
        return skeleton
    result = dict(existing)
    changed = []
    for key, value in skeleton.items():
        if key == "change_log":
            continue
        if key not in result or result[key] is None or (result[key] in ("", [], {}) and value not in ("", [], {})):
            result[key] = value
            changed.append(key)
    for key in RESERVED_FIELDS:
        if key not in result or result[key] is None:
            default = [] if key in ("sources", "pending_human", "change_log") else {}
            result[key] = skeleton.get(key, default)
            changed.append(key)
    if changed:
        log = result.setdefault("change_log", [])
        if isinstance(log, list):
            log.append({"ts": now_ts(), "action": "scaffold_merge", "detail": "补齐缺失键: " + ", ".join(changed)})
    return result


def cmd_scaffold(args):
    case = load_json(args.case)
    skeleton = build_skeleton(case)
    out_path = args.out or "/app/output/result.json"
    existing = load_json(out_path)
    final = merge_result(existing, skeleton)
    write_json(out_path, final)
    contract = resolve_output_contract(case)
    print(json.dumps({
        "ok": True,
        "case_id": final.get("case_id"),
        "contract_fields": list(contract),
        "status": final.get("status"),
        "path": out_path,
    }, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def normalize_text(s):
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def collect_corpus(roots):
    """收集给定根目录下的可定位文本，返回 {文件路径: 归一化全文}。"""
    corpus = {}
    seen = set()
    for root in (roots or []):
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() not in TEXT_EXTS:
                    continue
                fp = os.path.join(dirpath, fn)
                if fp in seen:
                    continue
                seen.add(fp)
                try:
                    if os.path.getsize(fp) > MAX_TEXT_FILE:
                        continue
                    text = read_text(fp)
                except OSError:
                    continue
                if text.strip():
                    corpus[fp] = normalize_text(text)
    return corpus


EVIDENCE_KEYS = {"fact_source_map", "content_units", "claims", "evidence_map", "style_spec", "version_boundary"}


def iter_evidence_entries(result):
    """从证据映射字段中抽取证据条目；排除 sources/change_log/pending_human 等元数据字段。"""
    entries = []
    if not isinstance(result, dict):
        return entries
    for key, val in result.items():
        if key not in EVIDENCE_KEYS:
            continue
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and ("quote" in item or "locator" in item):
                    entries.append((key, item))
        elif isinstance(val, dict) and key == "version_boundary":
            for sub in val.values():
                if isinstance(sub, list):
                    for item in sub:
                        if isinstance(item, dict) and ("quote" in item or "locator" in item):
                            entries.append((key, item))
    return entries


def verify(args):
    case = load_json(args.case)
    result = load_json(args.result)
    checks = []
    errors = []
    warnings = []

    # 1. result.json 存在且合法
    if result is None:
        errors.append("result.json 缺失或 JSON 无效")
        checks.append({"id": "result_exists", "ok": False, "detail": "无法解析 result.json"})
        report = {"ok": False, "errors": errors, "warnings": warnings, "checks": checks,
                  "ts": now_ts(), "case": args.case, "result": args.result}
        if args.report:
            write_json(args.report, report)
        return report, 2
    checks.append({"id": "result_exists", "ok": True, "detail": "result.json 可解析"})

    # 2. case_id 一致
    case_id = resolve_case_id(case)
    rid = result.get("case_id")
    if case_id is None:
        warnings.append("case.json 中未解析到 case_id，无法比对")
        checks.append({"id": "case_id_match", "ok": None, "detail": "case.json 无 case_id"})
    elif rid == case_id:
        checks.append({"id": "case_id_match", "ok": True, "detail": f"case_id={rid}"})
    else:
        errors.append(f"case_id 不一致: result={rid!r} vs case={case_id!r}")
        checks.append({"id": "case_id_match", "ok": False, "detail": f"{rid!r} != {case_id!r}"})

    # 3. 契约字段齐全
    contract = resolve_output_contract(case)
    missing_contract = [f for f in contract if f not in result or result[f] is None]
    if missing_contract:
        errors.append("output_contract 字段缺失或为 null: " + ", ".join(missing_contract))
        checks.append({"id": "contract_fields", "ok": False, "detail": "缺失: " + ", ".join(missing_contract)})
    else:
        checks.append({"id": "contract_fields", "ok": True, "detail": f"{len(contract)} 个契约字段齐全"})

    # 4. 保留字段
    missing_reserved = [f for f in RESERVED_FIELDS if f not in result or result[f] is None]
    if missing_reserved:
        errors.append("保留字段缺失: " + ", ".join(missing_reserved))
        checks.append({"id": "reserved_fields", "ok": False, "detail": "缺失: " + ", ".join(missing_reserved)})
    else:
        checks.append({"id": "reserved_fields", "ok": True, "detail": "status/sources/uncertainty/pending_human/change_log 齐全"})

    # 5. status 合法
    status = result.get("status")
    if status in VALID_STATUS:
        checks.append({"id": "status_valid", "ok": True, "detail": f"status={status}"})
    else:
        errors.append(f"status 非法: {status!r}")
        checks.append({"id": "status_valid", "ok": False, "detail": f"status={status!r}"})

    # 6. 证据条目字段 + 摘录可定位
    entries = iter_evidence_entries(result)
    roots = []
    if getattr(args, "material_root", None):
        roots.extend(args.material_root)
    for attr in ("work", "input", "case"):
        v = getattr(args, attr, None)
        if v:
            roots.append(v)
    roots.append("/input")
    corpus = collect_corpus([r for r in roots if r])

    missing_locatable = []
    missing_quote_field = []
    for key, item in entries:
        if "quote" not in item or not str(item.get("quote", "")).strip():
            missing_quote_field.append((key, item.get("id") or item.get("claim_id") or item.get("unit_id")))
            continue
        q = normalize_text(item["quote"])
        found = any(q in text for text in corpus.values())
        if not found and q:
            missing_locatable.append((key, item.get("id") or item.get("claim_id") or item.get("unit_id"), item.get("locator", "")))
    if missing_quote_field:
        errors.append("证据条目缺 quote 字段: " + str(missing_quote_field))
        checks.append({"id": "evidence_quote_field", "ok": False, "detail": f"{len(missing_quote_field)} 条缺 quote"})
    else:
        checks.append({"id": "evidence_quote_field", "ok": True, "detail": "所有证据条目含 quote"})
    if corpus:
        if missing_locatable:
            warnings.append("有摘录无法在材料文本中定位（可能材料未抽取或摘录有改动）: " + str(missing_locatable[:5]))
            checks.append({"id": "quote_locatable", "ok": False, "detail": f"{len(missing_locatable)} 条无法定位，已入 warnings"})
        else:
            checks.append({"id": "quote_locatable", "ok": True, "detail": f"全部摘录可在 {len(corpus)} 个文本文件中定位"})
    else:
        warnings.append("未找到可扫描的材料文本文件（/app/work、/input），摘录可定位性未验证")
        checks.append({"id": "quote_locatable", "ok": None, "detail": "材料文本缺失，未验证"})

    # 7. 占位符检查
    def find_placeholders(obj, path=""):
        hits = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                hits.extend(find_placeholders(v, f"{path}.{k}" if path else k))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                hits.extend(find_placeholders(v, f"{path}[{i}]"))
        elif isinstance(obj, str) and PLACEHOLDER_RE.search(obj):
            hits.append(path)
        return hits

    ph = find_placeholders(result)
    if ph:
        errors.append("发现占位符/TODO 标记: " + ", ".join(ph[:8]))
        checks.append({"id": "no_placeholders", "ok": False, "detail": f"{len(ph)} 处占位符"})
    else:
        checks.append({"id": "no_placeholders", "ok": True, "detail": "无 TODO/占位符"})

    ok = len(errors) == 0
    exit_code = 0 if ok else 2
    report = {"ok": ok, "errors": errors, "warnings": warnings, "checks": checks,
              "ts": now_ts(), "case": args.case, "result": args.result}
    if args.report:
        write_json(args.report, report)
    print(json.dumps({"ok": ok, "errors": len(errors), "warnings": len(warnings),
                      "checks": len(checks), "exit": exit_code}, ensure_ascii=False))
    return report, exit_code


def build_parser():
    ap = argparse.ArgumentParser(prog="main.py", description="学术格式与模板适配 Skill 核心脚本")
    sub = ap.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("scaffold", help="生成 partial 结果骨架")
    sp.add_argument("--case", default="/input/case.json")
    sp.add_argument("--out", default="/app/output/result.json")
    sp.set_defaults(func=cmd_scaffold)

    vp = sub.add_parser("verify", help="校验契约与证据可核验性")
    vp.add_argument("--case", default="/input/case.json")
    vp.add_argument("--result", default="/app/output/result.json")
    vp.add_argument("--report", default="/app/output/verification_report.json")
    vp.add_argument("--work", default="/app/work")
    vp.add_argument("--input", default="/input")
    vp.add_argument("--material-root", action="append", default=[])
    vp.set_defaults(func=None)

    return ap


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "scaffold":
        return args.func(args)
    if args.command == "verify":
        _, code = verify(args)
        return code
    return 2


if __name__ == "__main__":
    sys.exit(main())
