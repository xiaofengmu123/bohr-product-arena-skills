"""core_impl — 科研数据分析 Skill 的共享实现（仅标准库）。

本模块不含任何具体案例的答案或预置数值。它只做五件事：
  1. 容错发现输入材料（case.json / task.md / csv / tsv / json / sqlite）；
  2. 生成合法的 result.json 骨架（case_id + output_contract 全字段 + status/sources/uncertainty/change_log）；
  3. 生成 data_profile.json（编码、行列数、字段、缺失、重复、数值摘要、SQLite 表结构）；
  4. 校验 result.json 与产物（契约字段、证据溯源、图表格式、产物存在性）；
  5. 自检（空输入 / 坏 JSON / 坏编码 / 缺参数四种场景退出码 0 且产出合法结果）。

所有函数都是防御式的：坏 JSON、坏编码、空目录、缺列都不能让进程崩溃。
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import sqlite3
import statistics
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

VERSION = "2.0.0"

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime",
}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_PROFILE_ROWS = 100_000
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MISSING_TOKENS = {"", "na", "n/a", "null", "none", "nan", "-", "missing", "?", "nil", "n.d."}
ARTIFACT_EXTS = (
    ".json", ".csv", ".tsv", ".md", ".markdown", ".svg", ".pdf", ".png",
    ".jpg", ".jpeg", ".txt", ".docx", ".xlsx", ".pptx", ".py", ".log",
)


# --------------------------------------------------------------------------- #
# 时间与日志
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def append_change_log(result: dict, action: str, detail: str) -> None:
    """按时间序追加一条变更记录（动作 / 细节 / 时间）。"""
    if not isinstance(result, dict):
        return
    log = result.get("change_log")
    if not isinstance(log, list):
        log = []
        result["change_log"] = log
    log.append({"ts": now_iso(), "action": action, "detail": detail})


# --------------------------------------------------------------------------- #
# 文件读取：编码与 JSON 容错
# --------------------------------------------------------------------------- #
def _size_of(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


def read_text_flexible(path: str, max_bytes: int = MAX_FILE_BYTES):
    """返回 (text, encoding) 或 (None, reason)。优先 utf-8-sig / utf-8，其次 gb18030，最后 latin-1。"""
    size = _size_of(path)
    if size < 0:
        return None, f"stat failed: {path}"
    if size > max_bytes:
        return None, f"size {size} > cap {max_bytes}"
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, f"read failed: {exc}"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def load_json_flexible(path: str):
    """返回 (obj, error) 。error 为 None 表示解析成功；坏 JSON 返回 (None, reason)。"""
    text, enc = read_text_flexible(path)
    if text is None:
        return None, enc  # enc 处承载失败原因
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError) as exc:
        stripped = text.lstrip("\ufeff \t\r\n")
        try:
            return json.loads(stripped), None
        except Exception as exc2:
            return None, f"json parse error ({enc}): {exc2}"


def write_json(path: str, obj) -> str:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def iter_input_files(root: str):
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            yield os.path.join(dirpath, fn)


def classify(name: str) -> str:
    low = name.lower()
    if low.endswith((".csv", ".tsv", ".txt", ".data", ".dat")):
        return "table"
    if low.endswith((".db", ".sqlite", ".sqlite3")):
        return "sqlite"
    if low.endswith(".json"):
        return "json"
    if low.endswith((".md", ".markdown")):
        return "markdown"
    if low.endswith((".yaml", ".yml")):
        return "yaml"
    if low.endswith(".py"):
        return "code"
    return "other"


def find_case_file(input_dir: str):
    files = list(iter_input_files(input_dir))
    for p in files:
        if os.path.basename(p).lower() == "case.json":
            return p
    for p in files:
        if classify(p) == "json":
            obj, err = load_json_flexible(p)
            if not err and isinstance(obj, dict) and extract_case_id(obj):
                return p
    return None


# --------------------------------------------------------------------------- #
# case.json 语义抽取
# --------------------------------------------------------------------------- #
def extract_case_id(obj) -> str | None:
    if not isinstance(obj, dict):
        return None
    for k in ("case_id", "caseId", "challenge_id", "challengeId", "id"):
        v = obj.get(k)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None


def extract_contract(obj):
    """从 case.json 发现 output_contract。返回 dict / list / None。"""
    if not isinstance(obj, dict):
        return None
    for k in ("output_contract", "outputContract", "output_contract_fields"):
        v = obj.get(k)
        if isinstance(v, dict):
            return v
        if isinstance(v, list):
            return {str(f) if isinstance(f, str) else json.dumps(f, ensure_ascii=False): None for f in v}
    for k in ("contract", "task", "runtime_contract"):
        v = obj.get(k)
        if isinstance(v, dict):
            inner = extract_contract(v)
            if inner is not None:
                return inner
    return None


def contract_field_names(contract) -> list:
    if isinstance(contract, dict):
        return list(contract.keys())
    if isinstance(contract, list):
        return [str(x) for x in contract]
    return []


def extract_dictionary(obj):
    """尽力发现字段字典（字段名 -> 语义说明），供字段对齐使用。"""
    if not isinstance(obj, dict):
        return None
    for k in ("data_dictionary", "data_dict", "field_semantics", "dictionary",
              "columns", "schema", "fields", "field_descriptions", "metadata"):
        v = obj.get(k)
        d = _as_field_map(v)
        if d:
            return d
    for v in obj.values():
        if isinstance(v, dict):
            d = extract_dictionary(v)
            if d:
                return d
    return None


def _as_field_map(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, list):
        d = {}
        for item in v:
            if isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("column")
                if name:
                    d[str(name)] = item
            elif isinstance(item, str):
                d[item] = None
        return d or None
    return None


# --------------------------------------------------------------------------- #
# 结果骨架
# --------------------------------------------------------------------------- #
def build_skeleton(case_id, contract, errors=None) -> dict:
    result = {"case_id": case_id}
    for name in contract_field_names(contract):
        if name in RESERVED_KEYS:
            continue
        result.setdefault(name, None)
    result.setdefault("status", "partial")
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    result.setdefault("change_log", [])
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("runtime", {})
    if errors:
        if isinstance(errors, list):
            result["errors"].extend(errors)
        else:
            result["errors"].append(str(errors))
    return result


def write_result(output_dir: str, result: dict) -> str:
    return write_json(os.path.join(output_dir, "result.json"), result)


# --------------------------------------------------------------------------- #
# 数据画像
# --------------------------------------------------------------------------- #
def detect_delimiter(sample: str) -> str:
    best, best_n = ",", -1
    for d in (",", "\t", ";", "|"):
        n = sample.count(d)
        if n > best_n:
            best, best_n = d, n
    return best


def _is_missing(v) -> bool:
    s = (v or "").strip().lower()
    return s in MISSING_TOKENS


def _clean_cell(v) -> str:
    return "" if v is None else str(v).strip()


def column_stats(values) -> dict:
    n = len(values)
    missing = 0
    nums = []
    for v in values:
        if _is_missing(v):
            missing += 1
            continue
        try:
            nums.append(float(v))
        except (ValueError, TypeError):
            pass
    non_numeric = n - missing - len(nums)
    out = {"n": n, "missing": missing, "numeric": len(nums), "non_numeric": non_numeric}
    if nums:
        out["min"] = min(nums)
        out["max"] = max(nums)
        out["mean"] = round(sum(nums) / len(nums), 8)
        out["stdev"] = round(statistics.stdev(nums), 8) if len(nums) > 1 else 0.0
        out["distinct"] = len(set(nums))
    return out


def profile_csv(path: str) -> dict:
    info = {"path": path, "kind": "table", "size": _size_of(path)}
    text, enc = read_text_flexible(path)
    if text is None:
        info["error"] = enc
        return info
    info["encoding"] = enc
    delim = detect_delimiter(text[:4000])
    info["delimiter"] = delim
    rows = []
    for r in csv.reader(io.StringIO(text), delimiter=delim):
        rows.append(r)
        if len(rows) > MAX_PROFILE_ROWS + 1:
            info["truncated"] = True
            break
    if not rows:
        info["n_rows"] = 0
        info["error"] = "empty file"
        return info
    header = rows[0]
    body = rows[1:]
    info["n_rows"] = len(body)
    info["n_cols"] = len(header)
    info["columns"] = [_clean_cell(c) for c in header]
    # 逐列统计
    cols = []
    for ci, name in enumerate(header):
        vals = [r[ci] if ci < len(r) else "" for r in body]
        st = column_stats(vals)
        st["column"] = _clean_cell(name)
        cols.append(st)
    info["column_stats"] = cols
    # 整行重复检测
    seen, dup = set(), 0
    for r in body:
        key = (len(r),) + tuple(_clean_cell(c) for c in r)
        if key in seen:
            dup += 1
        else:
            seen.add(key)
    info["n_unique_rows"] = len(body) - dup
    info["duplicate_rows"] = dup
    return info


def profile_json(path: str) -> dict:
    info = {"path": path, "kind": "json", "size": _size_of(path)}
    obj, err = load_json_flexible(path)
    if err:
        info["error"] = err
        return info
    if isinstance(obj, list):
        info["n_records"] = len(obj)
        if obj and isinstance(obj[0], dict):
            keys = list(obj[0].keys())
            info["fields"] = keys
            info["missing"] = {}
            for k in keys:
                info["missing"][k] = sum(
                    1 for r in obj if not isinstance(r, dict) or k not in r or _is_missing(r.get(k))
                )
    elif isinstance(obj, dict):
        info["keys"] = list(obj.keys())
        info["n_records"] = None
    return info


def profile_sqlite(path: str) -> dict:
    info = {"path": path, "kind": "sqlite", "size": _size_of(path), "tables": []}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        info["error"] = f"open failed: {exc}"
        return info
    try:
        cur = con.cursor()
        try:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [r[0] for r in cur.fetchall()]
        except sqlite3.Error as exc:
            info["error"] = f"schema read failed: {exc}"
            tables = []
        for t in tables:
            safe = t.replace('"', '""')
            n = None
            try:
                cur.execute(f'SELECT COUNT(*) FROM "{safe}"')
                n = cur.fetchone()[0]
            except sqlite3.Error as exc:
                n = f"error: {exc}"
            cols = []
            try:
                cur.execute(f'PRAGMA table_info("{safe}")')
                cols = [{"name": r[1], "type": r[2]} for r in cur.fetchall()]
            except sqlite3.Error:
                pass
            info["tables"].append({"name": t, "n_rows": n, "columns": cols})
    finally:
        con.close()
    return info


def profile_directory(input_dir: str, output_dir: str, work_dir: str = DEFAULT_WORK) -> dict:
    files = list(iter_input_files(input_dir))
    case_file = find_case_file(input_dir)
    case_obj, case_id, contract, dictionary = None, None, None, None
    if case_file:
        case_obj, err = load_json_flexible(case_file)
        if not err and case_obj:
            case_id = extract_case_id(case_obj)
            contract = extract_contract(case_obj)
            dictionary = extract_dictionary(case_obj)
    report = {
        "generated_at": now_iso(),
        "input_dir": input_dir,
        "case_file": case_file,
        "case_id": case_id,
        "contract_fields": contract_field_names(contract) if contract else [],
        "files": [],
    }
    for p in files:
        if p == case_file:
            continue
        kind = classify(p)
        if kind == "table":
            report["files"].append(profile_csv(p))
        elif kind == "json":
            report["files"].append(profile_json(p))
        elif kind == "sqlite":
            report["files"].append(profile_sqlite(p))
        else:
            report["files"].append({"path": p, "kind": kind, "size": _size_of(p)})
    if dictionary is not None:
        report["dictionary"] = dictionary
        report["field_alignment"] = align_fields(dictionary, report)
    write_json(os.path.join(output_dir, "data_profile.json"), report)
    return report


def align_fields(dictionary: dict, report: dict) -> list:
    """把数据字典字段名与实际列名做规范化模糊对齐（不参与打分，仅供 Agent 参考）。"""
    actual = []
    for f in report.get("files", []):
        for c in f.get("columns", []):
            actual.append(c)
        for t in f.get("tables", []):
            for c in t.get("columns", []):
                actual.append(c.get("name", ""))
    actual = sorted({a for a in actual if a})
    pairs = []
    for field in dictionary:
        target = norm_name(field)
        best, best_score = None, -1
        for col in actual:
            s = norm_name(col)
            if s == target:
                score = 2
            elif s and target and (s in target or target in s):
                score = 1
            else:
                score = 0
            if score > best_score:
                best, best_score = col, score
        pairs.append({"dictionary_field": field, "matched_column": best, "score": best_score})
    return pairs


def norm_name(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
def looks_like_artifact(v: str) -> bool:
    return isinstance(v, str) and v.lower().endswith(ARTIFACT_EXTS) and "\n" not in v


def _check_artifact_format(path: str, issues: list) -> None:
    low = path.lower()
    try:
        if low.endswith(".svg"):
            ET.parse(path)
        elif low.endswith(".pdf"):
            with open(path, "rb") as fh:
                if not fh.read(5).startswith(b"%PDF-"):
                    issues.append(f"bad PDF header: {path}")
        elif low.endswith(".png"):
            with open(path, "rb") as fh:
                if not fh.read(8).startswith(b"\x89PNG\r\n\x1a\n"):
                    issues.append(f"bad PNG magic: {path}")
    except Exception as exc:
        issues.append(f"cannot parse {path}: {exc}")


def validate_result(input_dir: str, output_dir: str) -> dict:
    result_path = os.path.join(output_dir, "result.json")
    report = {
        "generated_at": now_iso(),
        "result_path": result_path,
        "checks": [],
        "issues": [],
    }

    def check(name: str, cond: bool, msg: str):
        report["checks"].append({"name": name, "ok": bool(cond), "message": msg})
        if not cond:
            report["issues"].append(f"{name}: {msg}")

    if not os.path.exists(result_path):
        check("result.json exists", False, "missing")
        report["valid"] = False
        write_json(os.path.join(output_dir, "validate_report.json"), report)
        return report

    obj, err = load_json_flexible(result_path)
    if err is not None or not isinstance(obj, dict):
        check("result.json parses", False, str(err))
        report["valid"] = False
        write_json(os.path.join(output_dir, "validate_report.json"), report)
        return report

    check("case_id", bool(obj.get("case_id")), "case_id must be non-empty")
    check("status", obj.get("status") in ALLOWED_STATUS, f"status in {sorted(ALLOWED_STATUS)}")
    check("sources list", isinstance(obj.get("sources"), list), "sources must be a list")
    if isinstance(obj.get("sources"), list):
        missing_q = [
            i for i, s in enumerate(obj["sources"])
            if not isinstance(s, dict) or not s.get("locator") or not s.get("quote")
        ]
        check("sources locator+quote", not missing_q, f"every source needs locator+quote (bad indexes {missing_q})")
    check("change_log list", isinstance(obj.get("change_log"), list), "change_log must be a list")
    check("uncertainty present", obj.get("uncertainty") not in (None, ""), "uncertainty must be present")
    check("pending_human present", obj.get("pending_human") is not None, "pending_human must be present")

    # 契约字段回填检查
    case_file = find_case_file(input_dir)
    if case_file:
        case_obj, _e = load_json_flexible(case_file)
        contract = extract_contract(case_obj) if case_obj else None
        missing = [f for f in contract_field_names(contract) if f not in obj]
        check("contract fields filled", not missing, f"missing contract fields: {missing}")
    else:
        check("contract fields filled", True, "no case.json to compare against")

    # 产物存在性与格式（跳过 sources/change_log 中的 locator/quote，避免误报）
    artifact_issues = []
    missing_artifacts = []
    _collect(obj, missing_artifacts)
    for p in sorted(set(missing_artifacts)):
        full = p if os.path.isabs(p) else os.path.join(output_dir, p)
        if os.path.exists(full):
            _check_artifact_format(full, artifact_issues)
        else:
            artifact_issues.append(f"missing artifact: {p}")
    check("artifacts exist", not artifact_issues,
          "; ".join(artifact_issues[:5]) if artifact_issues else "all referenced artifacts found")

    report["valid"] = not report["issues"]
    write_json(os.path.join(output_dir, "validate_report.json"), report)
    return report


def _collect(node, out: list) -> None:
    """收集结果里引用到的产物路径；跳过 sources/change_log 与 locator/quote。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("sources", "change_log", "locator", "quote"):
                continue
            if looks_like_artifact(v):
                out.append(v)
            elif isinstance(v, (dict, list)):
                _collect(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect(v, out)


# --------------------------------------------------------------------------- #
# 顶层流水线步骤（main.py 分发到这里）
# --------------------------------------------------------------------------- #
def run_init(input_dir: str, output_dir: str, work_dir: str) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    errors, case_id, contract = [], None, None
    case_file = find_case_file(input_dir)
    if case_file:
        obj, err = load_json_flexible(case_file)
        if err is not None:
            errors.append(f"case.json unreadable: {err}")
        elif not isinstance(obj, dict):
            errors.append("case.json is not a JSON object")
        else:
            case_id = extract_case_id(obj)
            contract = extract_contract(obj)
            if not case_id:
                errors.append("case_id not found in case.json")
            if contract is None:
                errors.append("output_contract not found in case.json; default contract fields used")
    else:
        errors.append("no case.json found in input")

    result = build_skeleton(case_id, contract, errors=errors)
    if not case_file:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append("No case.json in input; contract unknown.")
        result["pending_human"].append("provide /input/case.json and task.md")
    elif not case_id or contract is None:
        result["status"] = "partial"
    else:
        result["status"] = "partial"

    append_change_log(
        result, "init",
        f"scanned {input_dir}; case_file={os.path.basename(case_file) if case_file else None}; "
        f"case_id={case_id}; contract_fields={contract_field_names(contract) if contract else []}",
    )
    write_result(output_dir, result)
    return result


def run_profile(input_dir: str, output_dir: str, work_dir: str) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    report = profile_directory(input_dir, output_dir, work_dir)
    # 把画像关键信息追加进 change_log（不覆盖任何已有内容）
    result_path = os.path.join(output_dir, "result.json")
    if os.path.exists(result_path):
        obj, err = load_json_flexible(result_path)
        if not err and isinstance(obj, dict):
            n_tables = sum(1 for f in report.get("files", []) if f.get("kind") == "table")
            n_sqlite = sum(1 for f in report.get("files", []) if f.get("kind") == "sqlite")
            append_change_log(
                obj, "profile",
                f"data_profile.json written; tables={n_tables}, sqlite={n_sqlite}, "
                f"files={len(report.get('files', []))}",
            )
            write_result(output_dir, obj)
    return report


def run_validate(input_dir: str, output_dir: str, work_dir: str) -> dict:
    report = validate_result(input_dir, output_dir)
    ok = "VALID" if report.get("valid") else "ISSUES"
    print(f"[validate] {ok} issues={len(report.get('issues', []))}")
    for i in report.get("issues", [])[:10]:
        print(f"  - {i}")
    return report


# --------------------------------------------------------------------------- #
# 自检
# --------------------------------------------------------------------------- #
def _load_result(output_dir: str):
    p = os.path.join(output_dir, "result.json")
    if not os.path.exists(p):
        return None
    obj, err = load_json_flexible(p)
    return obj if not err else None


def run_selftest() -> bool:
    """四种失败场景验证：空输入 / 坏 JSON / 坏编码 / 缺参数。返回是否全部通过。"""
    base = tempfile.mkdtemp(prefix="sci_selftest_")
    results = []
    try:
        # A. 空输入目录
        a_in = os.path.join(base, "a_in"); os.makedirs(a_in)
        a_out = os.path.join(base, "a_out"); a_work = os.path.join(base, "a_work")
        try:
            run_init(a_in, a_out, a_work)
            run_profile(a_in, a_out, a_work)
            run_validate(a_in, a_out, a_work)
            r = _load_result(a_out)
            results.append(("empty_input", bool(r) and r.get("status") == "insufficient_evidence",
                            r.get("status") if r else None))
        except Exception as exc:
            results.append(("empty_input", False, f"raised {exc}"))

        # B. 坏 JSON
        b_in = os.path.join(base, "b_in"); os.makedirs(b_in)
        b_out = os.path.join(base, "b_out"); b_work = os.path.join(base, "b_work")
        with open(os.path.join(b_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not valid json ")
        try:
            run_init(b_in, b_out, b_work)
            r = _load_result(b_out)
            ok = bool(r) and isinstance(r.get("sources"), list) and r.get("status") in ALLOWED_STATUS
            results.append(("bad_json", ok, r.get("status") if r else None))
        except Exception as exc:
            results.append(("bad_json", False, f"raised {exc}"))

        # C. 坏编码（latin-1 编码的 csv）
        c_in = os.path.join(base, "c_in"); os.makedirs(c_in)
        c_out = os.path.join(base, "c_out"); c_work = os.path.join(base, "c_work")
        with open(os.path.join(c_in, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "selftest-c", "output_contract": {"summary": None}}, fh)
        with open(os.path.join(c_in, "data.csv"), "wb") as fh:
            fh.write("caf\xe9,value\n1,2.5\n".encode("latin-1"))
        try:
            run_init(c_in, c_out, c_work)
            run_profile(c_in, c_out, c_work)
            r = _load_result(c_out)
            prof_p = os.path.join(c_out, "data_profile.json")
            prof = load_json_flexible(prof_p)[0] if os.path.exists(prof_p) else None
            enc = None
            for f in (prof or {}).get("files", []):
                if f.get("kind") == "table":
                    enc = f.get("encoding")
            results.append(("bad_encoding", bool(r) and r.get("case_id") == "selftest-c" and enc is not None,
                            f"encoding={enc}"))
        except Exception as exc:
            results.append(("bad_encoding", False, f"raised {exc}"))

        # D. 缺参数：默认指向 /input /app/output /app/work
        from core_impl import DEFAULT_INPUT, DEFAULT_OUTPUT, DEFAULT_WORK  # noqa: F401
        defaults_ok = (DEFAULT_INPUT == "/input" and DEFAULT_OUTPUT == "/app/output"
                       and DEFAULT_WORK == "/app/work")
        results.append(("missing_params_defaults", defaults_ok,
                        f"{DEFAULT_INPUT},{DEFAULT_OUTPUT},{DEFAULT_WORK}"))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    n_pass = sum(1 for _, ok, _ in results if ok)
    print(f"[selftest] {n_pass}/{len(results)} scenarios passed")
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {detail}")
    return n_pass == len(results)
