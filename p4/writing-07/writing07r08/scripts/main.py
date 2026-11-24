#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""写作08｜引言与相关工作写作 Skill 的运行时脚本。

只依赖 Python 3.9+ 标准库；无第三方包；无网络访问；无随机成分（确定性输出）。

子命令（相对路径调用，见 SKILL.md）：
  scan      发现输入材料，抽取 output_contract 字段与 case_id，写 work/ 清单
  skeleton  落盘第一版合法 result.json 骨架（partial / insufficient_evidence / input_error）
  validate  校验 result.json / manuscript.md / evidence_map.json 的一致性
  finalize  确定性重排 JSON 键序并复校（消除手写键序差异）
  selftest  运行内置自检（等价于 scripts/selftest.py 的核心断言）
  help      打印帮助

约定（缺省指向运行时目录）：
  --input   /input
  --output  /app/output
  --work    /app/work
"""

import copy
import hashlib
import io
import json
import os
import re
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB：单文件上限，超限跳过并记 degradations

BUDGET_SECONDS = 900
BUDGET_MODEL_REQUESTS = 64
BUDGET_TOKENS_PER_RESPONSE = 16384

STATUS_ALLOWED = ("ok", "partial", "insufficient_evidence", "input_error")

# 标准字段：除 case_id 与合同字段外，result.json 必须保留的诚实性字段。
STANDARD_FIELDS = (
    "status",
    "sources",
    "uncertainty",
    "pending_human",
    "change_log",
    "artifacts",
    "errors",
    "degradations",
    "runtime",
)

# 合同字段发现优先级：先命中即返回。
CONTRACT_KEYS = (
    "output_contract",
    "outputContract",
    "output",
    "expected_output",
    "fields",
    "required_fields",
)

# 四段结构检测词（中英双语）。validate 用其判定稿件是否四段齐全。
SECTION_MARKERS = {
    "background": ("研究背景", "背景", "background"),
    "related_work": ("相关工作", "相关工作", "related work", "prior work"),
    "gap": ("研究空白", "空白", "研究缺口", "research gap", "gap"),
    "contributions": ("贡献", "本文贡献", "主要贡献", "contributions", "contribution"),
}

# 稿件正文中事实句锚点的形态：[S1] [S2] …（可用带 # 或 / 的定位，如 [S1#p3]）。
ANCHOR_RE = re.compile(r"\[S\s*\d+(?:[#/][^\]]+)?\]", re.IGNORECASE)


def _deep_sort(obj):
    """递归按键序重排 dict，用于确定性写出（不引入任何时间戳/顺序噪声）。"""
    if isinstance(obj, dict):
        return {k: _deep_sort(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_deep_sort(x) for x in obj]
    return obj


def _json_dumps(obj):
    return json.dumps(_deep_sort(obj), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def read_text_safe(path, max_bytes=MAX_FILE_BYTES):
    """按 utf-8-sig → utf-8 → latin-1 容错读取文本；超限/二进制/NUL 记录降级。

    返回 (text, degradations)，其中 degradations 为 list[dict]。
    """
    degradations = []
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, [{"file": os.path.basename(path), "issue": "stat_failed", "detail": str(exc)}]

    if size > max_bytes:
        return None, [{
            "file": os.path.basename(path),
            "issue": "oversize",
            "detail": f"{size} bytes > {max_bytes} limit",
        }]

    raw = None
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, [{"file": os.path.basename(path), "issue": "read_failed", "detail": str(exc)}]

    if b"\x00" in raw[:4096]:
        return None, [{
            "file": os.path.basename(path),
            "issue": "binary_nul",
            "detail": "NUL byte detected in first 4KB",
        }]

    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            return text, degradations
        except UnicodeDecodeError:
            continue
    return None, [{
        "file": os.path.basename(path),
        "issue": "undecodable",
        "detail": "could not decode as utf-8-sig/utf-8/latin-1",
    }]


def _is_text_ext(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in (".md", ".txt", ".json", ".bib", ".tex", ".csv", ".tsv", ".yaml", ".yml"):
        return True
    if ext == "":
        return True
    return False


def _rel_path(root, path):
    try:
        return os.path.relpath(path, root).replace(os.sep, "/")
    except ValueError:
        return os.path.basename(path)


def discover_contract(case_obj):
    """从 case.json 抽取 output_contract 字段，返回 (source_key, field_defaults)。

    field_defaults 为保序 dict：字段名 → 合法缺省值（None/[]/{} /嵌套 dict）。
    """
    if not isinstance(case_obj, dict):
        return None, {}
    for key in CONTRACT_KEYS:
        if key in case_obj and case_obj[key] is not None:
            return key, _spec_to_fields(case_obj[key])
    return None, {}


def _spec_to_fields(spec):
    fields = {}
    if isinstance(spec, dict):
        for k, v in spec.items():
            fields[str(k)] = _field_default(v)
    elif isinstance(spec, list):
        for item in spec:
            if isinstance(item, str):
                fields[item] = None
            elif isinstance(item, dict):
                for k, v in item.items():
                    fields[str(k)] = _field_default(v)
    return fields


def _field_default(v):
    """给定合同字段规格，返回合法缺省值。

    - 带 type 的 schema：array/list → []，object/dict/map → {}，其余 None。
    - 无 type 的对象：按子键结构递归回填。
    - 列表：视为字段名列表，缺省 []。
    - 其他（字符串/数字/布尔）：None（待 Agent 回填）。
    """
    if isinstance(v, dict):
        if isinstance(v.get("type"), str):
            t = v["type"].lower()
            if t in ("array", "list"):
                return []
            if t in ("object", "dict", "map"):
                return {}
        out = {}
        for k2, v2 in v.items():
            out[str(k2)] = _field_default(v2)
        return out
    if isinstance(v, list):
        return []
    return None


def scan(input_dir, work_dir):
    """发现输入材料并抽取合同，写 work/input_manifest.json 与 work/case_normalized.json。

    返回 manifest dict。
    """
    os.makedirs(work_dir, exist_ok=True)
    degradations = []
    files = []
    case_id = None
    case_error = None
    case_obj = None

    if not os.path.isdir(input_dir):
        degradations.append({"file": input_dir, "issue": "input_dir_missing"})
        case_error = "input_dir_missing"

    if case_error is None:
        try:
            entries = sorted(os.listdir(input_dir))
        except OSError as exc:
            entries = []
            degradations.append({"file": input_dir, "issue": "listdir_failed", "detail": str(exc)})
            case_error = "input_dir_unreadable"

        for name in entries:
            full = os.path.join(input_dir, name)
            if os.path.isfile(full):
                rel = name
                if name in ("case.json", "case.json.txt"):
                    text, dg = read_text_safe(full)
                    degradations.extend(dg)
                    if text is None:
                        case_error = "case_unreadable"
                        files.append({"rel": rel, "kind": "json", "readable": False, "locator": rel})
                        continue
                    try:
                        case_obj = json.loads(text)
                    except (json.JSONDecodeError, ValueError) as exc:
                        case_error = "case_bad_json"
                        case_obj = None
                        files.append({"rel": rel, "kind": "json", "readable": True, "parsed": False, "locator": rel})
                        continue
                    files.append({"rel": rel, "kind": "json", "readable": True, "parsed": True, "locator": rel})
                    continue
                if _is_text_ext(name):
                    text, dg = read_text_safe(full)
                    degradations.extend(dg)
                    files.append({
                        "rel": rel,
                        "kind": "text",
                        "readable": text is not None,
                        "locator": rel,
                        "bytes": os.path.getsize(full),
                    })
                else:
                    files.append({
                        "rel": rel,
                        "kind": "binary",
                        "readable": False,
                        "locator": rel,
                        "bytes": os.path.getsize(full),
                        "note": "binary asset; parsed via platform tool, not this script",
                    })
            elif os.path.isdir(full):
                files.append({"rel": rel + "/", "kind": "dir", "locator": rel + "/"})

    # 抽取 case_id 与合同字段
    contract_source_key, field_defaults = None, {}
    if isinstance(case_obj, dict):
        case_id = case_obj.get("case_id") or case_obj.get("id") or case_obj.get("caseId")
        if not isinstance(case_id, str):
            case_id = None
        contract_source_key, field_defaults = discover_contract(case_obj)

    manifest = {
        "input_dir": input_dir,
        "file_count": len(files),
        "total_bytes": sum(f.get("bytes", 0) for f in files),
        "files": files,
        "degradations": degradations,
        "case_id": case_id,
        "case_error": case_error,
        "contract_source_key": contract_source_key,
    }
    normalized = {
        "case_id": case_id,
        "contract_fields": field_defaults,
        "contract_source_key": contract_source_key,
        "input_status": ("ok" if case_error is None and case_obj is not None else
                         "empty" if (case_obj is None and case_error is None and not files) else
                         "bad_case_json" if case_error in ("case_bad_json", "case_unreadable") else
                         "no_case_json" if case_obj is None and case_error is None else
                         "error"),
    }

    with open(os.path.join(work_dir, "input_manifest.json"), "w", encoding="utf-8") as fh:
        fh.write(_json_dumps(manifest))
    with open(os.path.join(work_dir, "case_normalized.json"), "w", encoding="utf-8") as fh:
        fh.write(_json_dumps(normalized))
    return manifest


def _load_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def _load_json_optional(path):
    if not os.path.isfile(path):
        return None
    try:
        return _load_json(path)
    except (json.JSONDecodeError, ValueError, OSError):
        return None


def build_skeleton(work_dir, output_dir):
    """根据 scan 结果写第一版合法 result.json；幂等：已存在时不覆盖增量成果。

    返回 (status, result dict)。
    """
    os.makedirs(output_dir, exist_ok=True)
    normalized = _load_json_optional(os.path.join(work_dir, "case_normalized.json"))
    manifest = _load_json_optional(os.path.join(work_dir, "input_manifest.json"))

    case_id = (normalized or {}).get("case_id")
    contract_fields = (normalized or {}).get("contract_fields") or {}
    input_status = (normalized or {}).get("input_status", "error")
    case_error = (manifest or {}).get("case_error")
    degradations = (manifest or {}).get("degradations") or []

    if input_status == "empty":
        status = "insufficient_evidence"
    elif case_error in ("case_bad_json", "case_unreadable", "input_dir_missing", "input_dir_unreadable"):
        status = "input_error"
    elif input_status == "no_case_json":
        status = "insufficient_evidence"
    else:
        status = "partial"

    errors = []
    if status == "input_error":
        errors.append({"stage": "scan", "detail": case_error or input_status})

    result = {}
    result["case_id"] = case_id
    # 合同字段原样回填为顶层键（不更名、不删键）；合同字段名与标准字段冲突时合同名优先。
    for name, default in contract_fields.items():
        result[name] = copy.deepcopy(default)
    # 标准字段：不覆盖已存在的同名合同字段。
    std = {
        "status": status,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "artifacts": [],
        "errors": errors,
        "degradations": list(degradations),
        "runtime": {
            "budget_seconds": BUDGET_SECONDS,
            "budget_model_requests": BUDGET_MODEL_REQUESTS,
            "budget_tokens_per_response": BUDGET_TOKENS_PER_RESPONSE,
        },
    }
    for k, v in std.items():
        if k not in result:
            result[k] = v

    out_path = os.path.join(output_dir, "result.json")
    if os.path.isfile(out_path):
        # 已存在：不覆盖，避免抹掉后续增量改进（幂等骨架）。
        return status, result
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(_json_dumps(result))
    return status, result


def _find_sections(text):
    """返回四段结构命中的 dict；以标题行优先，标题缺失时退化到全文关键词。"""
    found = {k: False for k in SECTION_MARKERS}
    headings = re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)
    low_text = text.lower()
    for key, markers in SECTION_MARKERS.items():
        if headings:
            for h in headings:
                hl = h.lower()
                if any(m.lower() in hl for m in markers):
                    found[key] = True
                    break
        else:
            if any(m.lower() in low_text for m in markers):
                found[key] = True
    return found


def _extract_anchors(text):
    return [m.group(0) for m in ANCHOR_RE.finditer(text)]


def validate(work_dir, output_dir):
    """校验产物一致性。硬错误退出码 1；警告只提示（Agent 修警告后仍可交付）。

    返回 (errors, warnings)。
    """
    errors = []
    warnings = []

    result_path = os.path.join(output_dir, "result.json")
    result = _load_json_optional(result_path)
    if result is None:
        return [{"check": "result_json", "detail": "missing or invalid result.json"}], []

    # 1. result.json 基本契约
    if not isinstance(result.get("case_id"), str) and result.get("case_id") is not None:
        errors.append({"check": "case_id", "detail": "case_id must be string or null"})
    if result.get("status") not in STATUS_ALLOWED:
        errors.append({"check": "status", "detail": f"status must be one of {STATUS_ALLOWED}"})

    normalized = _load_json_optional(os.path.join(work_dir, "case_normalized.json"))
    contract_fields = (normalized or {}).get("contract_fields") or {}
    for name in contract_fields:
        if name not in result:
            errors.append({"check": "contract_field", "detail": f"missing contract field {name!r}"})

    for name in STANDARD_FIELDS:
        if name not in result:
            errors.append({"check": "standard_field", "detail": f"missing standard field {name!r}"})

    # 2. 证据溯源结构
    for src in (result.get("sources") or []):
        if not isinstance(src, dict):
            errors.append({"check": "source_shape", "detail": "sources[] item must be object"})
            continue
        for key in ("locator", "quote"):
            if not src.get(key):
                errors.append({"check": "source_shape", "detail": f"sources[] item missing {key!r}"})
        if "key" not in src:
            warnings.append({"check": "source_shape", "detail": "sources[] item missing 'key' (recommended)"})

    # 3. 主产物 manuscript.md：ok/partial 状态必须交付成稿；insufficient_evidence/input_error 可缺。
    manuscript_path = os.path.join(output_dir, "manuscript.md")
    manuscript_required = result.get("status") in ("ok", "partial")
    if manuscript_required and not os.path.isfile(manuscript_path):
        errors.append({"check": "manuscript", "detail": "missing manuscript.md"})
    elif os.path.isfile(manuscript_path):
        text, dg = read_text_safe(manuscript_path)
        if text is None or not text.strip():
            errors.append({"check": "manuscript", "detail": "manuscript.md empty or unreadable"})
        else:
            sections = _find_sections(text)
            missing = [k for k, v in sections.items() if not v]
            if missing:
                warnings.append({
                    "check": "four_sections",
                    "detail": f"manuscript may lack sections: {missing}",
                })

    # 4. evidence_map.json：正文每个 [S#] 都有 locator + quote
    evidence_path = os.path.join(output_dir, "evidence_map.json")
    evidence = _load_json_optional(evidence_path)
    if os.path.isfile(manuscript_path):
        text, _ = read_text_safe(manuscript_path)
        anchors = _extract_anchors(text or "")
        if anchors:
            if evidence is None:
                errors.append({"check": "evidence_map", "detail": "missing/invalid evidence_map.json while manuscript has anchors"})
            else:
                ev_keys = set()
                for k, v in evidence.items():
                    if isinstance(v, dict):
                        ev_keys.add(k)
                        for req in ("locator", "quote"):
                            if not v.get(req):
                                errors.append({
                                    "check": "evidence_entry",
                                    "detail": f"evidence_map[{k!r}] missing {req!r}",
                                })
                covered = set()
                for a in anchors:
                    base = a.strip("[]")
                    if base in ev_keys:
                        covered.add(a)
                    else:
                        # 允许 [S#locator] 形式：拆出 S 编号
                        mnum = re.match(r"[Ss]\s*(\d+)", base)
                        if mnum:
                            key = "S" + mnum.group(1)
                            if key in ev_keys:
                                covered.add(a)
                                continue
                        warnings.append({"check": "anchor_coverage", "detail": f"anchor {a!r} not in evidence_map"})
                if len(covered) != len(anchors):
                    warnings.append({"check": "anchor_coverage", "detail": "some manuscript anchors lack evidence entries"})

    # 5. 确定性卫生：runtime 不得含墙钟时间戳
    runtime = result.get("runtime")
    if isinstance(runtime, dict):
        for bad in ("wall_seconds", "timestamp", "started_at", "finished_at"):
            if bad in runtime:
                warnings.append({"check": "determinism", "detail": f"runtime contains non-deterministic field {bad!r}"})

    return errors, warnings


def finalize(work_dir, output_dir):
    """确定性重排 result/evidence_map/change_log 的键序并复校。"""
    for name in ("result.json", "evidence_map.json", "change_log.json"):
        path = os.path.join(output_dir, name)
        obj = _load_json_optional(path)
        if obj is not None:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_json_dumps(obj))
    errors, warnings = validate(work_dir, output_dir)
    return errors, warnings


def _print_report(errors, warnings):
    if errors:
        print("ERRORS:")
        for e in errors:
            print(" -", json.dumps(e, ensure_ascii=False))
    if warnings:
        print("WARNINGS:")
        for w in warnings:
            print(" -", json.dumps(w, ensure_ascii=False))
    if not errors and not warnings:
        print("ok")


def _selftest():
    """内置自检（与 scripts/selftest.py 等价的核心断言）。"""
    import tempfile

    failures = []

    def check(name, cond):
        print(("PASS  " if cond else "FAIL  ") + name)
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as td:
        root = td
        in_dir = os.path.join(root, "in")
        work_dir = os.path.join(root, "work")
        out_dir = os.path.join(root, "out")
        os.makedirs(in_dir)

        # 1. 空目录：scan → skeleton → validate，退出 0，insufficient_evidence
        m = scan(in_dir, work_dir)
        status, result = build_skeleton(work_dir, out_dir)
        check("empty_dir_status", status == "insufficient_evidence")
        errs, warns = validate(work_dir, out_dir)
        check("empty_dir_validate_no_hard_errors", len(errs) == 0)

        # 2. 坏 JSON：继续处理，status=input_error，errors 记录
        os.makedirs(out_dir, exist_ok=True)
        os.path.isfile(os.path.join(out_dir, "result.json")) and os.remove(os.path.join(out_dir, "result.json"))
        with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        m = scan(in_dir, work_dir)
        status, result = build_skeleton(work_dir, out_dir)
        check("bad_json_status", status == "input_error")
        check("bad_json_errors_recorded", len(result.get("errors") or []) >= 1)

        # 3. 正常 case.json + output_contract：合同字段回填
        os.path.isfile(os.path.join(out_dir, "result.json")) and os.remove(os.path.join(out_dir, "result.json"))
        case = {
            "case_id": "demo-001",
            "output_contract": {
                "introduction": "string",
                "related_work": "string",
                "references": {"type": "array"},
                "metadata": {"authors": "string", "venue": "string"},
            },
        }
        with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(case, ensure_ascii=False))
        m = scan(in_dir, work_dir)
        status, result = build_skeleton(work_dir, out_dir)
        check("case_id_passthrough", result.get("case_id") == "demo-001")
        check("contract_leaf", result.get("introduction") is None)
        check("contract_array", result.get("references") == [])
        check("contract_nested", result.get("metadata") == {"authors": None, "venue": None})
        check("status_partial", status == "partial")
        check("standard_fields_present", all(k in result for k in STANDARD_FIELDS))

        # 4. 确定性：删除 result.json 后重建两次，字节一致
        os.remove(os.path.join(out_dir, "result.json"))
        _, r1 = build_skeleton(work_dir, out_dir)
        b1 = _json_dumps(r1)
        os.remove(os.path.join(out_dir, "result.json"))
        _, r2 = build_skeleton(work_dir, out_dir)
        b2 = _json_dumps(r2)
        check("determinism", hashlib.sha256(b1.encode()).hexdigest() == hashlib.sha256(b2.encode()).hexdigest())

        # 5. validate 捕获坏 result.json
        with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        errs, warns = validate(work_dir, out_dir)
        check("validate_catches_broken_result", len(errs) >= 1)

    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    return 1 if failures else 0


def _usage():
    return """usage: python3 -B scripts/main.py <subcommand> [--input DIR] [--work DIR] [--output DIR]

subcommands:
  scan      discover input materials and extract output_contract fields
  skeleton  write first valid result.json skeleton (idempotent)
  validate  check result.json / manuscript.md / evidence_map.json consistency
  finalize  deterministically re-sort JSON keys and re-validate
  selftest  run built-in self checks
  help      show this help

defaults: --input /input --work /app/work --output /app/output
"""


def _parse_args(argv):
    args = {"input": DEFAULT_INPUT, "work": DEFAULT_WORK, "output": DEFAULT_OUTPUT}
    i = 0
    sub = None
    if argv:
        if argv[0] in ("-h", "--help"):
            return "help", args
        sub = argv[0]
        i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("--input", "--output", "--work", "--out", "--in"):
            key = {"--in": "input", "--out": "output"}.get(a, a[2:])
            if i + 1 < len(argv):
                args[key] = argv[i + 1]
                i += 2
                continue
        i += 1
    return sub or "help", args


def dispatch(argv):
    sub, args = _parse_args(argv)
    if sub == "help":
        sys.stdout.write(_usage())
        return 0
    if sub == "scan":
        scan(args["input"], args["work"])
        print(f"scan done: manifest -> {args['work']}")
        return 0
    if sub == "skeleton":
        status, _ = build_skeleton(args["work"], args["output"])
        print(f"skeleton done: status={status}")
        return 0
    if sub == "validate":
        errors, warnings = validate(args["work"], args["output"])
        _print_report(errors, warnings)
        return 1 if errors else 0
    if sub == "finalize":
        errors, warnings = finalize(args["work"], args["output"])
        _print_report(errors, warnings)
        return 1 if errors else 0
    if sub == "selftest":
        return _selftest()
    sys.stderr.write(_usage())
    return 2


if __name__ == "__main__":
    sys.exit(dispatch(sys.argv[1:]))
