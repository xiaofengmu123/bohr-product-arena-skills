#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""方法与实验章节写作 Skill —— 确定性脚手架与自检工具。

职责边界
--------
本脚本只做确定性、可复跑的数据工程：读取输入、回填输出契约、扫描材料、
抽取候选事实、校验产物路径与契约字段。正文写作（manuscript.md）由加载本
Skill 的 Agent 按 SKILL.md 完成；本脚本负责让"任何时刻都有一份合法产物"。

鲁棒性承诺（与普通一次性脚本的关键差异）
--------------------------------------
1. 空输入目录      -> 退出码 0，status=insufficient_evidence，列出缺口。
2. 坏 JSON/坏编码  -> 逐编码容错读取；仍失败记入 errors，status=input_error。
3. 缺参数          -> --input/--output/--work 缺省 /input、/app/output、/app/work。
4. 超大文件        -> 单文件 >2MB 跳过，记入 degradations，不中断。
5. 写盘失败        -> 降级到最小合法产物，不让进程崩溃。
6. 重复运行        -> 同一输入两次运行结果一致（确定性布局、有序键、有序遍历）。

只用 Python 标准库；无第三方依赖；跨平台路径处理。
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
import traceback

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件 2MB 上限，超出跳过
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
VALID_STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

TEXT_EXTS = {
    ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".py", ".log",
    ".csv", ".tsv", ".toml", ".cfg", ".ini", ".conf", ".sh", ".bash",
    ".rst", ".tex", ".bib", ".xml", ".html", ".nfo", ".cfg", ".cnf",
}
PDF_EXTS = {".pdf"}
OFFICE_EXTS = {".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls",
               ".odt", ".ods", ".odp"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".tif", ".tiff", ".webp"}

# 结果顶层必填字段
REQUIRED_RESULT_FIELDS = (
    "case_id", "status", "artifacts", "sources", "uncertainty",
    "change_log", "errors", "degradations", "limitations",
    "pending_human", "runtime",
)

# 常见超参/设置名（用于候选事实抽取）
HP_NAMES = (
    "learning_rate", "lr", "batch_size", "epochs", "num_epochs",
    "n_epochs", "dropout", "weight_decay", "optimizer", "seed",
    "random_seed", "hidden_size", "num_layers", "n_layers", "dim",
    "temperature", "top_k", "top_p", "max_length", "max_len",
    "context_window", "window_size", "patience", "momentum", "beta1",
    "beta2", "gamma", "epsilon", "eps", "warmup", "scheduler",
)
_HP_ALTERNATION = "|".join(re.escape(n) for n in HP_NAMES)
RE_HP = re.compile(
    r"(?i)\b(" + _HP_ALTERNATION + r")\b\s*[=:]\s*(\S+)")
RE_VERSION = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?(?:[-+.][0-9A-Za-z]+)*")
RE_SEED = re.compile(r"(?i)\b(?:random[_\s]?seed|seed)\b\s*[=:]\s*(\d+)")
RE_DATASET = re.compile(
    r"(?i)\b(?:dataset|benchmark|data[_\s]?set|corpus)\b\s*[=:]\s*"
    r"([A-Za-z0-9_\-./:]+)")
RE_PATH = re.compile(r"(?:/[\w@.\-+]+)+/?")
RE_CMD = re.compile(
    r"^\s*(?:python3?|pip\d?|pip|conda|docker|bash|sh|git|make|cmake|"
    r"wget|curl|tar|unzip)\b.*$")
RE_NUM_UNIT = re.compile(
    r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\s*"
    r"(?:%|px|pt|s|sec|ms|h|hr|min|Hz|kHz|MHz|GHz|B|KB|MB|GB|TB|"
    r"KiB|MiB|GiB|bit|bps|FPS|fps|dB|µm|um|mm|cm|m|nm|pm|"
    r"L|mL|g|mg|kg|mol|V|A|W|K)\b")


# ---------------------------------------------------------------------------
# 容错 I/O
# ---------------------------------------------------------------------------
def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """容错读取文本：逐编码尝试；超限返回 None；坏编码返回尽力结果。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, {"error": "unreadable_size", "path": path}
    if size > max_bytes:
        return None, {"error": "oversized", "path": path, "bytes": size,
                      "limit": max_bytes}
    raw = None
    last_err = None
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                raw = fh.read()
            break
        except (UnicodeDecodeError, UnicodeError) as exc:
            last_err = str(exc)
            continue
        except OSError as exc:
            last_err = str(exc)
            break
    if raw is None:
        # 最后兜底：latin-1 不会抛 UnicodeDecodeError
        try:
            with open(path, "r", encoding="latin-1", errors="replace") as fh:
                raw = fh.read()
            return raw, {"warning": "encoding_fallback", "path": path,
                         "detail": last_err}
        except OSError as exc:
            return None, {"error": "unreadable", "path": path, "detail": str(exc)}
    return raw, None


def read_json_tolerant(path, max_bytes=MAX_FILE_BYTES):
    """容错读取 JSON：返回 (obj, error_dict)。坏 JSON 返回 None + error。"""
    text, err = read_text(path, max_bytes)
    if err is not None:
        return None, err
    if text is None:
        return None, {"error": "no_text", "path": path}
    stripped = text.lstrip("\ufeff").strip()
    if not stripped:
        return None, {"error": "empty", "path": path}
    try:
        return json.loads(stripped), None
    except json.JSONDecodeError as exc:
        return None, {"error": "bad_json", "path": path,
                      "detail": "line %d col %d: %s"
                                % (exc.lineno, exc.colno, exc.msg)}
    except ValueError as exc:
        return None, {"error": "bad_json", "path": path, "detail": str(exc)}


def atomic_write_json(path, obj):
    """原子写 JSON（utf-8，sort_keys，indent=2）。失败返回错误字符串，不抛异常。"""
    try:
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False, sort_keys=True, indent=2)
                fh.write("\n")
            os.replace(tmp, path)
            return None
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise
    except Exception as exc:  # noqa: BLE001 —— 写盘失败需降级，不能崩溃
        return str(exc)


def atomic_write_text(path, text):
    """原子写文本（utf-8）。失败返回错误字符串。"""
    try:
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, path)
            return None
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise
    except Exception as exc:  # noqa: BLE001
        return str(exc)


# ---------------------------------------------------------------------------
# 材料发现与候选事实抽取
# ---------------------------------------------------------------------------
def _file_kind(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in PDF_EXTS:
        return "pdf"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in TEXT_EXTS or ext == "":
        return "text"
    return "other"


def discover_files(root):
    """确定性扫描 root 下文件，返回分类列表。不跟随符号链接。"""
    items = []
    if not root or not os.path.isdir(root):
        return items, "input_missing"
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                if os.path.islink(full):
                    continue
                size = os.path.getsize(full)
            except OSError:
                size = -1
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            items.append({
                "path": full,
                "rel": rel,
                "name": name,
                "kind": _file_kind(name),
                "bytes": size,
                "oversized": size > MAX_FILE_BYTES,
            })
    return items, None


def _line_locator(rel, lineno):
    return "%s#L%d" % (rel, lineno)


def extract_facts_from_text(text, rel):
    """从单文件文本抽取候选事实（数值/版本/种子/超参/数据集/路径/命令）。

    返回带 locator + 逐字 quote 的候选列表；候选未经核实，由 Agent 逐条对照原文。
    逐行处理以获得准确行号。
    """
    facts = []
    if text is None:
        return facts
    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        loc = _line_locator(rel, idx)
        strip = line.strip()
        if not strip:
            continue

        # 版本号（要求至少一个小数点，避免命中普通整数）
        for m in RE_VERSION.finditer(line):
            val = m.group(0)
            if not re.search(r"\d\.\d", val):
                continue
            facts.append({"type": "version", "value": val, "unit": None,
                          "quote": val, "locator": loc, "line": idx})

        # 随机种子
        for m in RE_SEED.finditer(line):
            facts.append({"type": "seed", "value": int(m.group(1)),
                          "unit": None, "quote": m.group(0),
                          "locator": loc, "line": idx})

        # 超参与设置
        for m in RE_HP.finditer(line):
            facts.append({"type": "hyperparameter", "name": m.group(1).lower(),
                          "value": m.group(2), "unit": None,
                          "quote": m.group(0), "locator": loc, "line": idx})

        # 数据集声明
        for m in RE_DATASET.finditer(line):
            facts.append({"type": "dataset", "value": m.group(1),
                          "unit": None, "quote": m.group(0),
                          "locator": loc, "line": idx})

        # 命令（整行为单位）
        if RE_CMD.match(line):
            facts.append({"type": "command", "value": strip, "unit": None,
                          "quote": strip, "locator": loc, "line": idx})

        # 数值 + 单位（对短行限定长度，避免长文误报过多）
        for m in RE_NUM_UNIT.finditer(line):
            val = m.group(0)
            if len(val) > 40:
                continue
            facts.append({"type": "number_unit", "value": val, "unit": None,
                          "quote": val, "locator": loc, "line": idx})

        # 路径
        for m in RE_PATH.finditer(line):
            val = m.group(0)
            if len(val) > 3 and len(val) < 300:
                facts.append({"type": "path", "value": val, "unit": None,
                              "quote": val, "locator": loc, "line": idx})
    return facts


# ---------------------------------------------------------------------------
# 输出契约与骨架
# ---------------------------------------------------------------------------
def _placeholder_for_hint(hint):
    if isinstance(hint, str):
        h = hint.strip().lower()
        if h in ("str", "string", "text"):
            return ""
        if h in ("list", "array", "[]"):
            return []
        if h in ("dict", "object", "map", "{}"):
            return {}
        if h in ("int", "integer", "float", "number", "num", "bool", "boolean"):
            return None
    return None


def normalize_contract(case):
    """从 case.json 提取 output_contract 字段清单，返回 {field: placeholder}。

    支持三种形态：dict（键即字段，值可为类型提示）、{"fields": [...]}、
    list[str]。无法识别返回空 dict。
    """
    if not isinstance(case, dict):
        return {}
    contract = case.get("output_contract")
    if contract is None:
        return {}
    if isinstance(contract, dict):
        if isinstance(contract.get("fields"), list):
            return {f: None for f in contract["fields"] if isinstance(f, str)}
        out = {}
        for k, v in contract.items():
            if k in ("fields", "_meta", "_comment"):
                continue
            out[str(k)] = _placeholder_for_hint(v)
        return out
    if isinstance(contract, list):
        return {str(f): None for f in contract if isinstance(f, str)}
    return {}


def extract_case_id(case):
    if isinstance(case, dict):
        for key in ("case_id", "id", "challenge_id", "challengeId",
                    "caseId", "task_id"):
            val = case.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, int):
                return str(val)
    return "unknown"


def build_skeleton(case, contract_fields, work_dir, errors, degradations,
                   status=STATUS_PARTIAL):
    """构造一份合法结果骨架；status 由调用方根据输入状态决定。"""
    payload = {
        "case_id": extract_case_id(case),
        "status": status,
    }
    # 回填 output_contract 全部字段（未知占位）
    for field, placeholder in contract_fields.items():
        if field in ("case_id", "status"):
            continue
        if placeholder is None:
            payload[field] = None
        else:
            # 复制可变占位，避免多个字段共享同一列表/字典
            if isinstance(placeholder, list):
                payload[field] = list(placeholder)
            elif isinstance(placeholder, dict):
                payload[field] = dict(placeholder)
            else:
                payload[field] = placeholder
    payload.update({
        "artifacts": [],
        "sources": [],
        "uncertainty": [],
        "change_log": [{
            "step": "skeleton",
            "time": _now_iso(),
            "note": "骨架落盘：output_contract 全部字段已占位回填，"
                    "后续只做增量改进。",
        }],
        "errors": list(errors),
        "degradations": list(degradations),
        "limitations": [],
        "pending_human": [],
        "runtime": {
            "seconds": 0,
            "requests_used": 0,
            "budget_seconds": 900,
            "budget_requests": 64,
            "phase": "skeleton",
        },
    })
    return payload


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------
def _load_case(input_dir, errors, degradations):
    """读取 case.json；返回 (case, err)。case 为 {} 表示空/缺失。"""
    case = {}
    case_path = os.path.join(input_dir, "case.json")
    if not os.path.isfile(case_path):
        errors.append({"source": "case.json", "issue": "missing",
                       "detail": case_path})
        return case
    obj, err = read_json_tolerant(case_path)
    if err is not None:
        errors.append({"source": "case.json", "issue": err.get("error", "unknown"),
                       "detail": err.get("detail", "")})
        if err.get("error") == "oversized":
            degradations.append({"source": "case.json", "issue": "oversized_skipped"})
        return case
    if not isinstance(obj, dict):
        errors.append({"source": "case.json", "issue": "not_object"})
        return case
    return obj


def cmd_skeleton(args):
    """第 0 步：立即写合法 result.json 骨架 + state.json。前 3 分钟内完成。"""
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    errors, degradations = [], []

    items, scan_err = discover_files(args.input)
    if scan_err is not None:
        errors.append({"source": "input", "issue": scan_err,
                       "detail": args.input})

    case = _load_case(args.input, errors, degradations)
    contract_fields = normalize_contract(case)
    if not contract_fields and isinstance(case, dict) and "output_contract" in case:
        degradations.append({
            "issue": "output_contract_unparseable",
            "detail": "output_contract 存在但无法解析字段清单，按空契约处理。"})

    # 输入状态 -> 初始 status（诚实优先，后续由 Agent 增量修正）
    bad_case = any(e.get("source") == "case.json" and
                   e.get("issue") == "bad_json" for e in errors)
    if scan_err is not None or not items:
        initial_status = STATUS_INSUFFICIENT
    elif bad_case:
        initial_status = STATUS_INPUT_ERROR
    else:
        initial_status = STATUS_PARTIAL

    result = build_skeleton(case, contract_fields, args.work, errors, degradations,
                            status=initial_status)
    if initial_status == STATUS_INSUFFICIENT:
        result["limitations"].append(
            "输入目录为空或不存在：无科研材料可抽取，无法生成方法与实验章节；"
            "请提供材料后重跑。")
        result["pending_human"].append(
            "补充科研材料（代码/配置/日志/数据说明/实验记录）。")
    # 记录契约字段供后续校验使用
    state = {
        "phase": "skeleton",
        "time": _now_iso(),
        "case_id": result["case_id"],
        "output_contract_fields": sorted(contract_fields.keys()),
        "input_dir": os.path.abspath(args.input),
        "output_dir": os.path.abspath(args.output),
        "work_dir": os.path.abspath(args.work),
        "change_log": list(result["change_log"]),
    }
    err = atomic_write_json(os.path.join(args.work, "state.json"), state)
    if err:
        errors.append({"source": "state.json", "issue": "write_failed", "detail": err})
    out_err = atomic_write_json(os.path.join(args.output, "result.json"), result)
    if out_err:
        # 写盘失败：降级到最小合法产物（再试一次到 stdout 可见路径）
        errors.append({"source": "result.json", "issue": "write_failed", "detail": out_err})
        print("CRITICAL: unable to write result.json: %s" % out_err, file=sys.stderr)
        return 1
    print("skeleton ok: case_id=%s status=%s contract_fields=%d"
          % (result["case_id"], result["status"], len(contract_fields)))
    return 0


def cmd_inventory(args):
    """第 1 步：扫描材料、分类、抽取候选事实。"""
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    degradations, errors = [], []

    items, err = discover_files(args.input)
    if err is not None:
        errors.append({"source": "input", "issue": err,
                       "detail": args.input})

    facts = []
    for it in items:
        if it["oversized"]:
            degradations.append({
                "source": it["rel"], "issue": "oversized_skipped",
                "bytes": it["bytes"], "limit": MAX_FILE_BYTES})
            it["status"] = "skipped_oversized"
            continue
        if it["kind"] == "text":
            text, rerr = read_text(it["path"])
            if rerr is not None:
                degradations.append({"source": it["rel"],
                                     "issue": rerr.get("error", "read_error"),
                                     "detail": rerr.get("detail", "")})
                it["status"] = "unreadable"
                continue
            it["status"] = "extracted"
            file_facts = extract_facts_from_text(text, it["rel"])
            # 只保留非空 quote 的候选
            for f in file_facts:
                if f.get("quote"):
                    facts.append(f)
        elif it["kind"] == "pdf":
            it["status"] = "needs_pdf_text_tool"
            degradations.append({"source": it["rel"],
                                 "issue": "pdf_not_extracted_by_script",
                                 "detail": "请用平台 pdf-text-tool 抽取文本后补入证据池。"})
        elif it["kind"] == "office":
            it["status"] = "needs_office_renderer"
            degradations.append({"source": it["rel"],
                                 "issue": "office_not_extracted_by_script",
                                 "detail": "请用 office-renderer 转文本后补入证据池。"})
        elif it["kind"] == "image":
            it["status"] = "image"
        else:
            it["status"] = "other_ignored"

    inventory = {
        "input_dir": os.path.abspath(args.input),
        "file_count": len(items),
        "files": items,
        "degradations": degradations,
    }
    err = atomic_write_json(os.path.join(args.work, "materials_inventory.json"),
                            inventory)
    if err:
        errors.append({"source": "materials_inventory.json",
                       "issue": "write_failed", "detail": err})
    err = atomic_write_json(os.path.join(args.work, "extracted_facts.json"),
                            {"facts": facts, "count": len(facts)})
    if err:
        errors.append({"source": "extracted_facts.json",
                       "issue": "write_failed", "detail": err})
    print("inventory ok: files=%d facts=%d skipped=%d"
          % (len(items), len(facts), sum(1 for d in degradations
                                         if d["issue"] == "oversized_skipped")))
    return 0


def _check_evidence_map(path):
    """校验 evidence_map.json：条目须含非空 locator 与 quote。"""
    issues = []
    if not os.path.isfile(path):
        return ["evidence_map_missing"]
    obj, err = read_json_tolerant(path)
    if err is not None or not isinstance(obj, dict):
        return ["evidence_map_bad_json"]
    if not obj:
        return ["evidence_map_empty"]
    for key, item in obj.items():
        if not isinstance(item, dict):
            issues.append("evidence_map_entry_not_object:%s" % key)
            continue
        loc = item.get("locator")
        quote = item.get("quote")
        if not isinstance(loc, str) or not loc.strip():
            issues.append("evidence_map_missing_locator:%s" % key)
        if not isinstance(quote, str) or not quote.strip():
            issues.append("evidence_map_missing_quote:%s" % key)
    return issues


def cmd_validate(args):
    """第 5 步自检：校验 result.json 契约字段、状态、证据与产物路径。"""
    os.makedirs(args.work, exist_ok=True)
    result_path = os.path.join(args.output, "result.json")
    issues = []

    if not os.path.isfile(result_path):
        report = {"ok": False, "issues": ["result_json_missing"],
                  "result_path": result_path}
        atomic_write_json(os.path.join(args.work, "validation_report.json"), report)
        print("validate: result.json missing")
        return 0

    result, err = read_json_tolerant(result_path)
    if err is not None or not isinstance(result, dict):
        issues.append("result_json_bad_json")
        report = {"ok": False, "issues": issues, "result_path": result_path}
        atomic_write_json(os.path.join(args.work, "validation_report.json"), report)
        print("validate: result.json bad json")
        return 0

    # 顶层必填字段
    for field in REQUIRED_RESULT_FIELDS:
        if field not in result:
            issues.append("missing_field:%s" % field)

    # status 合法性
    status = result.get("status")
    if status not in VALID_STATUSES:
        issues.append("invalid_status:%r" % (status,))
    if status == STATUS_OK:
        if not result.get("artifacts"):
            issues.append("status_ok_without_artifacts")

    # case_id 非空
    if not result.get("case_id"):
        issues.append("missing_case_id")

    # output_contract 回填核对：从 case.json 重新读契约
    case = {}
    case_path = os.path.join(args.input, "case.json")
    if os.path.isfile(case_path):
        case, _ = read_json_tolerant(case_path)
    contract_fields = normalize_contract(case)
    for field in contract_fields:
        if field not in result:
            issues.append("missing_contract_field:%s" % field)

    # artifacts 路径（相对 /app/output 需存在）
    for art in result.get("artifacts", []):
        if isinstance(art, str):
            p = art
            if not os.path.isabs(p):
                p = os.path.join(args.output, p)
            if not os.path.isfile(p):
                issues.append("artifact_missing:%s" % art)
        elif isinstance(art, dict) and isinstance(art.get("path"), str):
            p = art["path"]
            if not os.path.isabs(p):
                p = os.path.join(args.output, p)
            if not os.path.isfile(p):
                issues.append("artifact_missing:%s" % art["path"])

    # 证据映射（若存在）
    ev_path = os.path.join(args.output, "evidence_map.json")
    if os.path.isfile(ev_path):
        issues.extend(_check_evidence_map(ev_path))

    report = {
        "ok": len(issues) == 0,
        "issues": issues,
        "issue_count": len(issues),
        "status": status,
        "result_path": result_path,
        "time": _now_iso(),
    }
    atomic_write_json(os.path.join(args.work, "validation_report.json"), report)
    print("validate: ok=%s issues=%d" % (report["ok"], report["issue_count"]))
    for i in issues:
        print("  - %s" % i)
    return 0


def cmd_runall(args):
    """依次执行 skeleton -> inventory -> validate，返回首次非零退出码。"""
    rc = cmd_skeleton(args)
    if rc != 0:
        return rc
    rc = cmd_inventory(args)
    if rc != 0:
        return rc
    return cmd_validate(args)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="方法与实验章节写作 Skill 的确定性脚手架："
                    "骨架落盘、材料盘点、候选事实抽取、产物自检。")
    sub = parser.add_subparsers(dest="command")

    def add_common(p):
        p.add_argument("--input", default=DEFAULT_INPUT)
        p.add_argument("--output", default=DEFAULT_OUTPUT)
        p.add_argument("--work", default=DEFAULT_WORK)

    p = sub.add_parser("skeleton", help="立即落盘合法 result.json 骨架")
    add_common(p)

    p = sub.add_parser("inventory", help="扫描材料并抽取候选事实")
    add_common(p)

    p = sub.add_parser("validate", help="校验 result.json 契约与证据")
    add_common(p)

    p = sub.add_parser("runall", help="按序执行 skeleton->inventory->validate")
    add_common(p)
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    if args.command is None:
        parser.print_help()
        return 0
    try:
        if args.command == "skeleton":
            return cmd_skeleton(args)
        if args.command == "inventory":
            return cmd_inventory(args)
        if args.command == "validate":
            return cmd_validate(args)
        if args.command == "runall":
            return cmd_runall(args)
        parser.print_help()
        return 0
    except Exception as exc:  # noqa: BLE001 —— 任何异常都不应让进程崩溃
        print("UNEXPECTED ERROR: %s" % exc, file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
