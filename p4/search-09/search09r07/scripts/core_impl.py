#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litsearch-audit 核心实现（确定性、仅标准库）。

职责：
  1. 容错地发现 /input 下的 case.json、task.md 与材料文件；
  2. 从 case.json 中递归定位 output_contract（任意命名/嵌套层级）；
  3. 立即写出合法的 partial 骨架 result.json（含 case_id、原样回填的
     output_contract 字段、status/sources/uncertainty/change_log 等）；
  4. 生成稳定段落/句子级 locator，并做逐字引文核验；
  5. 按阈值对维度判定聚合风险等级；
  6. 校验最终 result.json 的结构与证据完整性；
  7. 运行鲁棒性自检（空输入 / 坏 JSON / 缺参数 / 超大文件 / 坏编码）。

所有函数确定性：无随机数，同一输入结果一致（runtime 时间戳除外）。
"""

import json
import os
import re
import sys
import time
import math
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB，超限跳过并记录
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CONTRACT_KEY_RE = re.compile(r"^(output_?contract|output_?schema|contract|result_?schema)$", re.I)
CASEFILE_RE = re.compile(r"^case\.json$", re.I)
TASKFILE_RE = re.compile(r"^task\.md$", re.I)

# 判定值受限词表：约束 verdict 只能取这些值，保证双人复核可比。
QUALITY_VERDICTS = {
    "satisfied", "not_satisfied", "partially_satisfied",
    "insufficient_evidence", "not_applicable",
}
BIAS_VERDICTS = {"present", "possible", "absent", "insufficient_evidence"}
ALL_VERDICTS = QUALITY_VERDICTS | BIAS_VERDICTS

# 聚合分级默认阈值（可在 rubric 中覆盖；grade() 接受显式参数）。
DEFAULT_GRADE_RULES = {
    "critical_fail_verdicts": ["not_satisfied", "present"],
    "high_signal_verdicts": ["not_satisfied", "present"],
    "moderate_signal_verdicts": ["possible", "partially_satisfied"],
    "noncritical_high_for_moderate": 1,   # 非 critical 高风险信号数 >= 该值 -> moderate
    "moderate_for_moderate": 2,           # 中风险信号数 >= 该值 -> moderate
    "partial_for_moderate": 3,            # partially_satisfied 数 >= 该值 -> moderate
    "insufficient_ratio": 0.5,            # 证据不足占比 >= 该值 -> unclear
}


# ---------------------------------------------------------------------------
# 基础 I/O（容错）
# ---------------------------------------------------------------------------


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """以 utf-8-sig 优先读取文本，坏编码/超大文件降级并返回 (text, errors)。

    返回值： (str | None, [str])。绝不抛异常。
    """
    errors = []
    p = Path(path)
    if not p.exists():
        return None, ["missing: %s" % p]
    try:
        size = p.stat().st_size
    except OSError as exc:
        return None, ["stat_failed: %s: %s" % (p, exc)]
    if size > max_bytes:
        return None, ["oversize(%d>%d): %s" % (size, max_bytes, p)]
    raw = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            raw = p.read_bytes().decode(enc)
            break
        except (UnicodeDecodeError, OSError) as exc:
            errors.append("decode(%s)_failed: %s: %s" % (enc, p, exc))
    if raw is None:
        return None, errors
    return raw, errors


def load_json_lenient(path):
    """读 JSON；坏 JSON 返回 (None, errors)，绝不抛异常。"""
    text, errors = read_text(path)
    if text is None:
        return None, errors
    try:
        return json.loads(text), errors
    except json.JSONDecodeError as exc:
        return None, errors + ["bad_json: %s: %s" % (path, exc)]


def atomic_write_json(path, data):
    """先写临时文件再 rename，避免留下半截 JSON。返回写入路径。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(p))
    return str(p)


def atomic_write_text(path, text):
    """原子写文本（md 等）。返回写入路径。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(str(tmp), str(p))
    return str(p)


# ---------------------------------------------------------------------------
# 输入发现与契约定位
# ---------------------------------------------------------------------------


def discover_input(input_dir):
    """扫描输入目录，返回 (found, errors)。不假设固定文件名/键名。"""
    root = Path(input_dir)
    found = {"case_json": [], "task_md": [], "materials": [], "other": []}
    if not root.exists():
        return found, ["input_dir_missing: %s" % root]
    if not root.is_dir():
        return found, ["input_path_not_dir: %s" % root]
    try:
        entries = sorted(p for p in root.rglob("*") if p.is_file())
    except OSError as exc:
        return found, ["input_dir_read_failed: %s: %s" % (root, exc)]
    for p in entries:
        name = p.name
        if CASEFILE_RE.match(name):
            found["case_json"].append(p)
        elif TASKFILE_RE.match(name):
            found["task_md"].append(p)
        elif name.lower() in ("materials",):
            continue
        elif ("material" in name.lower()
              or p.suffix.lower() in (".pdf", ".txt", ".md", ".json", ".xml", ".csv", ".html")):
            found["materials"].append(p)
        else:
            found["other"].append(p)
    return found, []


def find_contract(obj, depth=0):
    """递归查找 output_contract（大小写不敏感，任意嵌套）。返回 dict 或 None。"""
    if depth > 8:
        return None
    if isinstance(obj, dict):
        for key, value in obj.items():
            if CONTRACT_KEY_RE.match(str(key)):
                return value
            hit = find_contract(value, depth + 1)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for value in obj:
            hit = find_contract(value, depth + 1)
            if hit is not None:
                return hit
    return None


def _default_for(value):
    """根据契约类型描述给默认值；无法判断则 None。"""
    if isinstance(value, dict):
        typ = str(value.get("type", "")).lower()
        if "array" in typ:
            return []
        if "object" in typ:
            return {}
        if "null" in typ:
            return None
        return None
    if isinstance(value, list):
        return []
    return None


def contract_defaults(contract):
    """把契约字段原样回填为顶层默认值；不改名、不删未知键。"""
    if isinstance(contract, dict):
        props = contract.get("properties")
        source = props if isinstance(props, dict) else contract
        if isinstance(source, dict):
            return {str(k): _default_for(v) for k, v in source.items()}
        return {}
    if isinstance(contract, list):
        return {str(k): None for k in contract if isinstance(k, str)}
    return {}


def extract_case_id(data, input_dir):
    """从 case.json 取 case_id，缺失则退到目录名。"""
    if isinstance(data, dict):
        for key in ("case_id", "caseId", "id"):
            val = data.get(key)
            if isinstance(val, (str, int)):
                return str(val)
    return Path(input_dir).name or "unknown"


# ---------------------------------------------------------------------------
# 骨架构建
# ---------------------------------------------------------------------------


def build_skeleton(case_id, contract, notes=None):
    """构造一份合法 partial 骨架，回填契约字段。"""
    result = {"case_id": case_id, "status": "partial"}
    # 契约字段先回填（原样键名、缺值 null/[]）
    result.update(contract_defaults(contract))
    # 我方结构字段随后追加（同名时以我方为准，冲突记录进 change_log）
    result.update({
        "research_question": None,
        "search": {"scope": None, "query": None, "date_range": None,
                   "language": None, "sources_queried": []},
        "candidates": [],
        "screening": [],
        "assessment": [],
        "evidence_records": [],
        "omissions": [],
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "limitations": [],
        "runtime": {"start_epoch": time.time(),
                    "skeleton_epoch": time.time(),
                    "finish_epoch": None},
    })
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result["change_log"].append({
        "time_utc": now, "event": "skeleton", "detail": "partial 骨架落盘",
    })
    if notes:
        result["change_log"].append({
            "time_utc": now, "event": "skeleton_notes", "detail": "; ".join(notes),
        })
    return result


def run_skeleton(input_dir, output_dir, work_dir):
    """发现输入、定位契约、写骨架。返回 (result_path, result, errors)。"""
    errors = []
    degradations = []
    # 确保工作目录存在（失败降级到临时目录，但不阻塞写结果）
    try:
        Path(work_dir).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        degradations.append("work_dir_create_failed: %s: %s" % (work_dir, exc))

    found, derrs = discover_input(input_dir)
    errors.extend(derrs)

    case_data = None
    contract = None
    case_id = "unknown"
    status = "partial"

    if not found["case_json"]:
        if not derrs:  # 目录存在但无 case.json
            errors.append("no_case_json_found_in: %s" % input_dir)
            status = "insufficient_evidence"
        else:
            status = "input_error"
    else:
        cp = found["case_json"][0]
        case_data, jerrs = load_json_lenient(cp)
        errors.extend(jerrs)
        if case_data is None:
            status = "input_error" if jerrs else "partial"
        else:
            case_id = extract_case_id(case_data, input_dir)
            contract = find_contract(case_data)
            if contract is None:
                errors.append("output_contract_not_found_in: %s" % cp)

    result = build_skeleton(case_id, contract,
                            notes=["input_dir=%s" % input_dir])
    result["status"] = status
    result["errors"].extend(errors)
    result["degradations"].extend(degradations)

    out_dir = Path(output_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # 写盘失败：降级到 work_dir 下，仍尝试写出最小合法产物
        out_dir = Path(work_dir)
        degradations.append("output_dir_create_failed: %s: %s" % (output_dir, exc))
        result["degradations"] = degradations
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc2:
            errors.append("fallback_output_dir_create_failed: %s: %s" % (out_dir, exc2))

    result_path = str(out_dir / "result.json")
    try:
        atomic_write_json(result_path, result)
    except OSError as exc:
        # 最后兜底：写当前目录
        result_path = "result.json"
        errors.append("write_failed: %s: %s" % (out_dir / "result.json", exc))
        try:
            atomic_write_json(result_path, result)
        except OSError as exc2:
            errors.append("final_write_failed: %s" % exc2)
    result["errors"] = errors
    return result_path, result, errors


# ---------------------------------------------------------------------------
# Locator：段落/句子级定位与逐字核验
# ---------------------------------------------------------------------------


def _split_sentences(text):
    """按句末标点 + 空白切分，保留原句内容。"""
    parts = re.split(r"(?<=[.!?。！？；;])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def make_locators(text, source_id):
    """把纯文本切成段落与句子，生成稳定 locator。

    返回 (records, locator_map)：
      records: [{locator, text, char_start, char_end}]
      locator_map: {locator: normalized_text}
    locator 形如 `src#p3`（段落）、`src#p3s2`（段落内第 2 句）。
    """
    records = []
    locator_map = {}
    paragraphs = re.split(r"\n\s*\n", text)
    cursor = 0
    for pidx, para in enumerate(paragraphs, 1):
        para = para.strip()
        if not para:
            continue
        start = text.find(para, cursor)
        if start < 0:
            start = cursor
        end = start + len(para)
        cursor = max(cursor, end)
        ploc = "%s#p%d" % (source_id, pidx)
        records.append({"locator": ploc, "text": para,
                        "char_start": start, "char_end": end})
        locator_map[ploc] = _normalize_ws(para)
        for sidx, sent in enumerate(_split_sentences(para), 1):
            sloc = "%s#p%ds%d" % (source_id, pidx, sidx)
            records.append({"locator": sloc, "text": sent,
                            "char_start": start, "char_end": end})
            locator_map[sloc] = _normalize_ws(sent)
    return records, locator_map


def _normalize_ws(s):
    """把任意空白压缩为单个空格，便于逐字核验时容忍换行差异。"""
    return re.sub(r"\s+", " ", (s or "")).strip()


def verify_quote(quote, locator, locator_map):
    """核验 quote 是否逐字存在于 locator 所指文本。

    返回 (ok: bool, detail: str)。先做原文字符串包含，再做空白归一化包含。
    """
    if not quote:
        return False, "empty_quote"
    norm = locator_map.get(locator)
    if norm is None:
        return False, "locator_not_found: %s" % locator
    q = _normalize_ws(quote)
    if quote in norm:
        return True, "verbatim_exact"
    if q in norm:
        return True, "verbatim_normalized"
    return False, "quote_not_found_in_locator"


# ---------------------------------------------------------------------------
# 分级聚合（阈值可解释）
# ---------------------------------------------------------------------------


def grade(assessments, rules=None):
    """按阈值聚合维度判定为风险等级。

    返回 {"grade","signals","thresholds","basis"}，确定性、可解释。
    grade ∈ {"low","moderate","high","unclear"}。
    """
    rules = dict(DEFAULT_GRADE_RULES, **(rules or {}))
    high_verdicts = set(rules["high_signal_verdicts"])
    moderate_verdicts = set(rules["moderate_signal_verdicts"])
    critical_fail_verdicts = set(rules["critical_fail_verdicts"])

    applicable = 0
    insufficient = 0
    critical_fail = 0
    noncritical_high = 0
    moderate_signal = 0
    partial = 0
    for item in assessments or []:
        v = item.get("verdict")
        if v not in ALL_VERDICTS:
            continue
        if v in ("insufficient_evidence",):
            insufficient += 1
            continue
        if v == "not_applicable":
            continue
        applicable += 1
        is_critical = bool(item.get("critical"))
        if is_critical and v in critical_fail_verdicts:
            critical_fail += 1
        if not is_critical and v in high_verdicts:
            noncritical_high += 1
        if v in moderate_verdicts:
            moderate_signal += 1
        if v == "partially_satisfied":
            partial += 1

    signals = {
        "applicable": applicable,
        "insufficient": insufficient,
        "critical_fail": critical_fail,
        "noncritical_high": noncritical_high,
        "moderate_signal": moderate_signal,
        "partial": partial,
    }
    thresholds = dict(rules)

    if critical_fail > 0:
        g = "high"
    elif (noncritical_high >= rules["noncritical_high_for_moderate"]
          or moderate_signal >= rules["moderate_for_moderate"]
          or partial >= rules["partial_for_moderate"]):
        g = "moderate"
    elif applicable == 0:
        g = "unclear"
    elif insufficient >= max(1, math.ceil(applicable * rules["insufficient_ratio"])):
        g = "unclear"
    else:
        g = "low"

    basis = ("critical_fail={cf} noncritical_high={nh} moderate_signal={ms} "
             "partial={pa} applicable={ap} insufficient={ins} -> {grade}").format(
                 cf=critical_fail, nh=noncritical_high, ms=moderate_signal,
                 pa=partial, ap=applicable, ins=insufficient, grade=g)
    return {"grade": g, "signals": signals, "thresholds": thresholds, "basis": basis}


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


REQUIRED_TOP_KEYS = {"case_id", "status", "sources", "uncertainty", "change_log"}


def validate_result(result, input_dir=None, output_dir=None):
    """校验 result.json 结构与证据完整性。返回 (errors, warnings)。"""
    errors = []
    warnings = []
    if not isinstance(result, dict):
        return ["result_not_dict"], []
    for key in REQUIRED_TOP_KEYS:
        if key not in result:
            errors.append("missing_required_key: %s" % key)
    if "case_id" in result and not isinstance(result["case_id"], str):
        errors.append("case_id_not_string")
    if "status" in result and result["status"] not in ALLOWED_STATUS:
        errors.append("invalid_status: %s" % result.get("status"))
    if "runtime" not in result:
        warnings.append("missing_runtime_block")
    # 证据核验抽查：每条 assessment 有 locator 且 quote 非空
    assessment = result.get("assessment") or []
    if isinstance(assessment, list):
        for i, item in enumerate(assessment):
            if not isinstance(item, dict):
                errors.append("assessment[%d]_not_dict" % i)
                continue
            if not item.get("dimension") or not item.get("verdict"):
                errors.append("assessment[%d]_missing_dimension_or_verdict" % i)
            if item.get("verdict") not in ALL_VERDICTS:
                errors.append("assessment[%d]_invalid_verdict: %s"
                              % (i, item.get("verdict")))
            if not item.get("locator"):
                errors.append("assessment[%d]_missing_locator" % i)
            if not item.get("quote"):
                warnings.append("assessment[%d]_empty_quote" % i)
    else:
        errors.append("assessment_not_list")
    return errors, warnings


# ---------------------------------------------------------------------------
# 鲁棒性自检
# ---------------------------------------------------------------------------


def _write_bytes(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def run_self_test():
    """运行鲁棒性自检，返回 (passed, failed, details)。退出码按失败数。"""
    import tempfile
    details = []
    passed = 0
    failed = 0

    def check(name, cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append("PASS %s" % name)
        else:
            failed += 1
            details.append("FAIL %s: %s" % (name, msg))

    base = Path(tempfile.mkdtemp(prefix="litsk_"))

    # 场景 1：空输入目录
    d = base / "empty_input"
    o = base / "out_empty"
    d.mkdir(parents=True)
    path, result, errs = run_skeleton(str(d), str(o), str(base / "w_empty"))
    ok1 = Path(path).exists()
    loaded, lerrs = load_json_lenient(path)
    check("empty_input_writes_result", ok1 and loaded is not None, "path=%s errs=%s" % (path, errs))
    check("empty_input_status", loaded and loaded.get("status") == "insufficient_evidence",
          "status=%s" % (loaded.get("status") if loaded else None))
    check("empty_input_has_case_id", loaded and "case_id" in loaded, "no case_id")

    # 场景 2：坏 JSON
    d = base / "bad_json"
    d.mkdir(parents=True)
    _write_bytes(d / "case.json", b"{ not valid json !!!")
    path, result, errs = run_skeleton(str(d), str(base / "out_bad"), str(base / "w_bad"))
    loaded, _ = load_json_lenient(path)
    check("bad_json_writes_result", loaded is not None and loaded.get("case_id") == "unknown",
          "loaded=%s" % (loaded is not None))
    check("bad_json_errors_recorded", loaded and any("bad_json" in e for e in loaded.get("errors", [])),
          "errors=%s" % (loaded.get("errors") if loaded else None))

    # 场景 3：缺输入目录
    path, result, errs = run_skeleton(str(base / "no_such_input"),
                                      str(base / "out_missing"), str(base / "w_missing"))
    loaded, _ = load_json_lenient(path)
    check("missing_input_writes_result", loaded is not None, "errs=%s" % errs)
    check("missing_input_status", loaded and loaded.get("status") == "input_error",
          "status=%s" % (loaded.get("status") if loaded else None))

    # 场景 4：超大文件（>2MB）跳过
    d = base / "oversize"
    d.mkdir(parents=True)
    _write_bytes(d / "case.json", json.dumps({"case_id": "ov", "output_contract": {"properties": {"summary": {"type": "string"}}}}).encode("utf-8"))
    big = b"x" * (MAX_FILE_BYTES + 1024)
    _write_bytes(d / "materials" / "big.txt", big)
    path, result, errs = run_skeleton(str(d), str(base / "out_ov"), str(base / "w_ov"))
    loaded, _ = load_json_lenient(path)
    check("oversize_contract_echoed", loaded and loaded.get("summary") is None,
          "summary=%r" % (loaded.get("summary") if loaded else None))
    check("oversize_case_id", loaded and loaded.get("case_id") == "ov", "case_id=%s" % (loaded.get("case_id") if loaded else None))

    # 场景 5：坏编码（latin-1 字节）不崩溃
    d = base / "bad_encoding"
    d.mkdir(parents=True)
    _write_bytes(d / "case.json", b'{"case_id": "enc"}')
    _write_bytes(d / "materials" / "note.txt", b"caf\xe9 latin1 bytes")
    path, result, errs = run_skeleton(str(d), str(base / "out_enc"), str(base / "w_enc"))
    loaded, _ = load_json_lenient(path)
    check("bad_encoding_writes_result", loaded is not None, "errs=%s" % errs)

    # 场景 6：缺参数（env 默认 + 显式覆盖）不崩溃
    os.environ["SKILL_OUTPUT_DIR"] = str(base / "out_env")
    os.environ["SKILL_INPUT_DIR"] = str(base / "empty_input")
    path, result, errs = run_skeleton(os.environ["SKILL_INPUT_DIR"],
                                      os.environ["SKILL_OUTPUT_DIR"],
                                      str(base / "w_env"))
    loaded, _ = load_json_lenient(path)
    check("env_defaults_work", loaded is not None, "errs=%s" % errs)

    # 场景 7：locator 与逐字核验
    text = "First paragraph here. Second sentence here.\n\nSecond paragraph. Another sentence."
    records, lmap = make_locators(text, "src")
    check("locator_paragraphs", any(r["locator"] == "src#p1" for r in records), "no p1")
    check("locator_sentences", any(r["locator"] == "src#p1s2" for r in records), "no p1s2")
    okq, _ = verify_quote("Second sentence here.", "src#p1s2", lmap)
    check("verify_exact", okq, "exact quote failed")
    okn, _ = verify_quote("Second\nsentence here.", "src#p1s2", lmap)
    check("verify_normalized", okn, "normalized quote failed")
    bad, _ = verify_quote("not in text", "src#p1s2", lmap)
    check("verify_rejects", not bad, "should reject")

    # 场景 8：分级聚合
    assm = [
        {"dimension": "study_design", "checkpoint": "allocation", "critical": True,
         "verdict": "not_satisfied", "judgment": "无随机", "locator": "x#p1", "quote": "q"},
    ]
    g = grade(assm)
    check("grade_high", g["grade"] == "high", "grade=%s" % g)
    g2 = grade([{"critical": False, "verdict": "satisfied"}])
    check("grade_low", g2["grade"] == "low", "grade=%s" % g2)

    # 场景 9：校验
    errs, warns = validate_result({"case_id": "c", "status": "ok",
                                   "sources": [], "uncertainty": [], "change_log": [],
                                   "assessment": [{"dimension": "d", "verdict": "satisfied",
                                                    "locator": "s#p1", "quote": "q"}]})
    check("validate_clean", not errs, "errs=%s" % errs)
    errs2, _ = validate_result({"status": "weird"})
    check("validate_detects_bad", any("invalid_status" in e for e in errs2), "errs=%s" % errs2)

    return passed, failed, details


if __name__ == "__main__":
    passed, failed, details = run_self_test()
    for line in details:
        print(line)
    print("self-test: %d passed, %d failed" % (passed, failed))
    sys.exit(0 if failed == 0 else 1)
