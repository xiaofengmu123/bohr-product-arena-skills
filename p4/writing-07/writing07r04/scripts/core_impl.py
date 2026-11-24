"""core_impl.py — 引言与相关工作写作 Skill 的核心实现（纯标准库）。

职责：输入扫描、输出契约抽取、骨架落盘、产物校验、合同回填辅助。
设计原则：
- 确定性：无随机数，目录/键/材料均固定排序，重复运行结果一致。
- 容错：坏 JSON、坏编码、超大文件、空输入均降级处理并记录，不抛异常崩溃。
- 诚实：status 只取 ok/partial/insufficient_evidence/input_error；缺材料不编造。
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

CONTRACT_KEYS = (
    "output_contract",
    "output",
    "expected_output",
    "fields",
    "required_fields",
)

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件 2MB 上限，超限跳过并记 degradations
MAX_DEPTH = 8                      # 契约递归发现深度上限

BUDGET_SECONDS = 900               # 官方执行预算
BUDGET_REQUESTS = 64               # 官方模型请求预算
SKELETON_DEADLINE_SECONDS = 180    # 前 3 分钟内必须先落盘 partial 骨架

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
ALLOWED_STATUS = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

SUPPORTED_TEXT_EXTS = (".md", ".txt", ".json", ".bib", ".tex", ".csv", ".tsv")

# result.json 除合同字段外的标准附加字段
STANDARD_FIELDS = (
    "status",
    "main_product",
    "sources",
    "uncertainty",
    "limitations",
    "pending_human",
    "change_log",
    "artifacts",
    "errors",
    "degradations",
    "runtime",
)

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #

def now_iso():
    """UTC 时间戳字符串（runtime 记录用，非内容依赖）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def read_json(path):
    """读取 JSON 文件；失败返回 None（调用方自行记录错误）。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def write_json(path, obj):
    """确定性写 JSON：固定排序、保留非 ASCII、缩进 2。"""
    ensure_dir(os.path.dirname(path) if os.path.dirname(path) else ".")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def read_text_safe(path):
    """容错读取文本：BOM / utf-8 / gb18030 / latin-1 依次尝试。

    返回 (text, errors)。errors 记录降级编码，供 degradations 使用。
    """
    errors = []
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception as exc:  # noqa: BLE001
        return "", ["cannot_read:%s:%s" % (path, exc)]

    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            if enc not in ("utf-8-sig", "utf-8"):
                errors.append("fallback_encoding:%s:%s" % (path, enc))
            return text, errors
        except (UnicodeDecodeError, LookupError):
            continue
    return "", ["undecodable:%s" % path]


def load_json_safe(path):
    """容错读取 JSON：返回 (data, errors)。"""
    if not os.path.isfile(path):
        return None, ["json_missing:%s" % path]
    if os.path.getsize(path) > MAX_FILE_BYTES:
        return None, ["json_oversized:%s" % path]
    text, errs = read_text_safe(path)
    if not text.strip():
        return None, errs + ["json_empty:%s" % path]
    try:
        return json.loads(text), errs
    except Exception as exc:  # noqa: BLE001
        return None, errs + ["bad_json:%s:%s" % (path, exc)]


# --------------------------------------------------------------------------- #
# 输出契约发现与规范化
# --------------------------------------------------------------------------- #

def _nonempty(val):
    return val is not None and val != {} and val != [] and val != ""


def discover_contract(obj, depth=0):
    """在 case.json 中递归发现 output_contract（按 CONTRACT_KEYS 优先级）。

    返回 (contract_value, source_key)；找不到返回 (None, None)。
    先沿包装键（output_contract/output/expected_output/fields/required_fields）
    逐层下钻到终止契约，找不到包装键时再回退到任意嵌套值中搜索。
    """
    if depth > MAX_DEPTH or obj is None:
        return None, None
    if isinstance(obj, dict):
        # 1) 本层出现包装键：沿其值继续下钻，或把其值本身当作终止契约
        for key in CONTRACT_KEYS:
            val = obj.get(key)
            if _nonempty(val):
                inner, src = discover_contract(val, depth + 1)
                if inner is not None:
                    return inner, src if src else key
                return val, key
        # 2) 本层无包装键：回退到任意嵌套值中搜索
        for val in obj.values():
            found, k = discover_contract(val, depth + 1)
            if found is not None:
                return found, k
    elif isinstance(obj, list):
        for item in obj:
            found, k = discover_contract(item, depth + 1)
            if found is not None:
                return found, k
    return None, None


def normalize_contract_fields(contract):
    """把任意形态的契约归一化为字段名列表（保持首次出现顺序、去重）。"""
    fields = []
    if isinstance(contract, dict):
        fields = list(contract.keys())
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                fields.append(item)
            elif isinstance(item, dict):
                for k in item.keys():
                    fields.append(k)
    elif isinstance(contract, str) and contract:
        fields = [contract]
    # 去重且保持顺序
    seen = set()
    out = []
    for f in fields:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def null_value_for(field, contract_raw):
    """按契约子结构推断缺省值：对象->{}，数组->[]，其余->null。"""
    spec = None
    if isinstance(contract_raw, dict):
        spec = contract_raw.get(field)
    if isinstance(spec, dict):
        t = str(spec.get("type", "")).lower()
        if t in ("array", "list"):
            return []
        if t in ("object", "dict", "map"):
            return {}
        if "items" in spec or "element" in spec:
            return []
    elif isinstance(spec, list):
        return []
    return None


# --------------------------------------------------------------------------- #
# 材料扫描
# --------------------------------------------------------------------------- #

def classify_kind(name):
    low = name.lower()
    if low.endswith(".pdf"):
        return "pdf"
    if low.endswith(".bib"):
        return "bib"
    if low.endswith(".json"):
        return "json"
    if low.endswith(SUPPORTED_TEXT_EXTS):
        return "text"
    if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".tif", ".tiff")):
        return "image"
    if low.endswith((".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls")):
        return "office"
    return "other"


def _record_material(root, rel, abs_path, materials, degradations, prefix_rel):
    """记录单个材料文件；超限跳过并写 degradations。"""
    try:
        size = os.path.getsize(abs_path)
    except OSError:
        size = -1
    kind = classify_kind(rel)
    entry = {
        "path": rel,
        "size": size,
        "kind": kind,
        "locator_prefix": rel,
        "skipped": False,
    }
    if size > MAX_FILE_BYTES:
        entry["skipped"] = True
        entry["reason"] = "oversized_gt_2mb"
        degradations.append({"item": rel, "reason": "oversized_gt_2mb", "bytes": size})
        materials.append(entry)
        return
    if size < 0:
        entry["skipped"] = True
        entry["reason"] = "stat_failed"
        degradations.append({"item": rel, "reason": "stat_failed"})
    # 编码探针：文本类材料提前检测可解码性，降级编码记入 degradations
    if kind in ("text", "bib", "json") and not entry["skipped"]:
        try:
            _, enc_errs = read_text_safe(abs_path)
            for e in enc_errs:
                if ("fallback_encoding" in e or "undecodable" in e
                        or "cannot_read" in e):
                    degradations.append({"item": rel, "reason": e})
        except Exception as exc:  # noqa: BLE001
            degradations.append({"item": rel, "reason": "encoding_probe_failed:%s" % exc})
    materials.append(entry)


def scan_input(input_dir, work_dir):
    """扫描输入目录，产出 input_manifest.json 与 case_normalized.json。

    返回 manifest dict。绝不抛异常。
    """
    ensure_dir(work_dir)
    manifest = {
        "input_dir": input_dir,
        "scanned_at": now_iso(),
        "case_id": None,
        "contract_source_key": None,
        "contract_raw": None,
        "contract_fields": [],
        "materials": [],
        "errors": [],
        "degradations": [],
        "input_empty": False,
    }

    if not os.path.isdir(input_dir):
        manifest["errors"].append("input_dir_missing:%s" % input_dir)
        manifest["input_empty"] = True
        _write_manifest(work_dir, manifest)
        return manifest

    try:
        entries = sorted(os.listdir(input_dir))
    except OSError as exc:  # noqa: BLE001
        manifest["errors"].append("listdir_failed:%s:%s" % (input_dir, exc))
        manifest["input_empty"] = True
        _write_manifest(work_dir, manifest)
        return manifest

    if not entries:
        manifest["input_empty"] = True
        _write_manifest(work_dir, manifest)
        return manifest

    # case.json
    case_path = os.path.join(input_dir, "case.json")
    if os.path.isfile(case_path):
        data, errs = load_json_safe(case_path)
        manifest["errors"].extend(errs)
        if isinstance(data, dict):
            manifest["case_id"] = (
                data.get("case_id")
                or data.get("id")
                or data.get("challenge_id")
                or data.get("task_id")
            )
            contract, src_key = discover_contract(data)
            if contract is not None:
                manifest["contract_source_key"] = src_key
                manifest["contract_raw"] = contract
                manifest["contract_fields"] = normalize_contract_fields(contract)
            else:
                manifest["errors"].append("contract_not_found:checked=%s" % ",".join(CONTRACT_KEYS))
        elif data is not None:
            manifest["errors"].append("case_json_not_dict")
    else:
        manifest["errors"].append("case_json_missing:%s" % case_path)

    # task.md 单独标记（正文事实依据之一）
    task_path = os.path.join(input_dir, "task.md")
    if os.path.isfile(task_path):
        _record_material(input_dir, "task.md", task_path,
                         manifest["materials"], manifest["degradations"], input_dir)
    else:
        manifest["errors"].append("task_md_missing")

    # 其余材料：顶层文件 + materials/ 等子目录递归
    for name in entries:
        if name in ("case.json", "task.md"):
            continue
        p = os.path.join(input_dir, name)
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for fn in sorted(files):
                    abs_path = os.path.join(root, fn)
                    rel = os.path.relpath(abs_path, input_dir).replace(os.sep, "/")
                    _record_material(input_dir, rel, abs_path,
                                     manifest["materials"], manifest["degradations"], input_dir)
        elif os.path.isfile(p):
            rel = name.replace(os.sep, "/")
            _record_material(input_dir, rel, p,
                             manifest["materials"], manifest["degradations"], input_dir)

    _write_manifest(work_dir, manifest)
    return manifest


def _write_manifest(work_dir, manifest):
    write_json(os.path.join(work_dir, "input_manifest.json"), manifest)
    normalized = {
        "case_id": manifest["case_id"],
        "contract_source_key": manifest["contract_source_key"],
        "contract_raw": manifest["contract_raw"],
        "contract_fields": manifest["contract_fields"],
        "input_empty": manifest["input_empty"],
    }
    write_json(os.path.join(work_dir, "case_normalized.json"), normalized)


# --------------------------------------------------------------------------- #
# 骨架落盘（前 3 分钟内必须完成）
# --------------------------------------------------------------------------- #

def emit_skeleton(work_dir, output_dir):
    """落盘一版合法 partial result.json；任何中途异常都不会留下空产物。

    返回写入的 result dict。
    """
    ensure_dir(output_dir)
    normalized_path = os.path.join(work_dir, "case_normalized.json")
    cn = read_json(normalized_path) or {}
    manifest_path = os.path.join(work_dir, "input_manifest.json")
    manifest = read_json(manifest_path) or {}

    case_id = cn.get("case_id")
    contract_raw = cn.get("contract_raw")
    contract_fields = cn.get("contract_fields", []) or []
    input_empty = bool(cn.get("input_empty") or manifest.get("input_empty"))

    if input_empty:
        status = STATUS_INSUFFICIENT
    elif case_id is None:
        status = STATUS_INPUT_ERROR
    else:
        status = STATUS_PARTIAL

    result = {
        "case_id": case_id,
        "status": status,
        "runtime": {
            "start_iso": now_iso(),
            "phase": "skeleton",
            "budget_seconds": BUDGET_SECONDS,
            "budget_requests": BUDGET_REQUESTS,
            "skeleton_deadline_seconds": SKELETON_DEADLINE_SECONDS,
        },
    }

    # 合同字段：字段名原样回填为顶层键，不改名、不删键
    for field in contract_fields:
        if field not in result:
            result[field] = null_value_for(field, contract_raw)

    # 标准附加字段
    result["main_product"] = "manuscript.md"
    result["sources"] = []
    result["uncertainty"] = []
    result["limitations"] = []
    result["pending_human"] = []
    result["change_log"] = []
    result["artifacts"] = {
        "manuscript.md": "manuscript.md",
        "evidence_map.json": "evidence_map.json",
        "change_log.json": "change_log.json",
    }
    result["errors"] = list(manifest.get("errors", []))
    result["degradations"] = list(manifest.get("degradations", []))

    write_json(os.path.join(output_dir, "result.json"), result)
    return result


# --------------------------------------------------------------------------- #
# 产物校验
# --------------------------------------------------------------------------- #

def validate_output(work_dir, output_dir):
    """校验 result.json 与主产物；返回 report dict（passed/failed/warnings）。"""
    report = {"passed": [], "failed": [], "warnings": []}

    result_path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(result_path):
        report["failed"].append("result_json_missing")
        return report

    result = read_json(result_path)
    if result is None:
        report["failed"].append("result_json_unparseable")
        return report

    # case_id
    if result.get("case_id") is None:
        report["failed"].append("case_id_missing_or_null")

    # status 合法
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        report["failed"].append("status_invalid:%s" % status)

    # 合同字段全部回填
    cn = read_json(os.path.join(work_dir, "case_normalized.json")) or {}
    contract_fields = cn.get("contract_fields", []) or []
    missing_fields = [f for f in contract_fields if f not in result]
    if missing_fields:
        report["failed"].append("contract_fields_missing:%s" % ",".join(missing_fields))
    else:
        report["passed"].append("contract_fields_complete:%d" % len(contract_fields))

    # 标准字段
    missing_std = [f for f in STANDARD_FIELDS if f not in result]
    if missing_std:
        report["failed"].append("standard_fields_missing:%s" % ",".join(missing_std))
    else:
        report["passed"].append("standard_fields_complete")

    # status 与证据一致性：ok 不得空 sources 且合同字段仍为 null
    sources = result.get("sources", [])
    if status == STATUS_OK and not sources and contract_fields:
        report["warnings"].append("status_ok_but_sources_empty")
    null_contract = [f for f in contract_fields if result.get(f) is None]
    if status == STATUS_OK and null_contract:
        report["failed"].append("status_ok_but_contract_null:%s" % ",".join(null_contract))

    # 主产物
    main = result.get("main_product") or "manuscript.md"
    main_path = os.path.join(output_dir, os.path.basename(main))
    if os.path.isfile(main_path) and os.path.getsize(main_path) > 0:
        report["passed"].append("main_product_exists:%s" % main)
    else:
        report["failed"].append("main_product_missing_or_empty:%s" % main)

    # evidence_map 校验（存在则逐条检查 locator + quote）
    evidence_path = os.path.join(output_dir, "evidence_map.json")
    if os.path.isfile(evidence_path):
        em = read_json(evidence_path)
        if isinstance(em, dict):
            bad = []
            for key, val in em.items():
                if not isinstance(val, dict) or not val.get("locator") or not val.get("quote"):
                    bad.append(str(key))
            if bad:
                report["failed"].append("evidence_map_bad_entries:%s" % ",".join(bad[:5]))
            else:
                report["passed"].append("evidence_map_ok:%d_entries" % len(em))
        else:
            report["failed"].append("evidence_map_not_dict")
    else:
        report["warnings"].append("evidence_map_missing")

    # 编造防火墙：主产物不得出现显式占位空话
    if os.path.isfile(main_path):
        try:
            text = read_text_safe(main_path)[0]
        except Exception:  # noqa: BLE001
            text = ""
        placeholders = ("Lorem ipsum", "TODO", "此处插入", "示例数据", "占位")
        hits = [p for p in placeholders if p in text]
        if hits:
            report["warnings"].append("placeholder_text_found:%s" % ",".join(hits))

    return report


def run_validate(work_dir, output_dir, stream=None):
    """执行校验并打印报告；返回退出码 0/1。"""
    stream = stream or sys.stdout
    report = validate_output(work_dir, output_dir)
    stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if not report["failed"] else 1
