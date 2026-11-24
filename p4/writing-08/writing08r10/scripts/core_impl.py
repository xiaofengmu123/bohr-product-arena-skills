# -*- coding: utf-8 -*-
"""core_impl.py -- 类型化事实台账 + 双序校验的实现模块（纯标准库）。

架构说明（与“三脚本顺序流水线”不同）：
1. 契约优先（preflight）：先解析 output_contract 并立即落盘合法 result.json 骨架，
   任何后续异常都不会留下不完整产物。
2. 台账作为唯一中间表示（fact ledger）：扫描材料抽取候选事实，每条记录含
   facet/value/unit/locator/quote/strength/conflicts，成稿骨架、证据映射、数值保全
   表均由同一台账渲染，避免“正文-证据”漂移。
3. 证据后置（validate）：收口时再做证据完整性后检，输出 validation_report.json。

本模块刻意保持确定性：所有遍历与排序均使用 sorted()，无随机性。
"""

import json
import os
import re
import sys
import time

# ---------------------------------------------------------------------------
# 常量与默认值
# ---------------------------------------------------------------------------

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB 单文件上限

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"

# 六类要素：facet key -> 中文标签
FACETS = {
    "steps": "方法步骤",
    "setup": "实验设置",
    "params": "参数与来源",
    "data": "数据集与划分",
    "baselines": "对照实验",
    "repro": "可复现与限制",
}

# 标准字段（result.json 顶层，output_contract 字段之外）
STANDARD_FIELDS = [
    "case_id",
    "status",
    "artifacts",
    "sources",
    "uncertainty",
    "pending_human",
    "change_log",
    "errors",
    "degradations",
    "limitations",
    "runtime",
]


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def _read_bytes(path):
    """读取文件字节，超限返回 None。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size > MAX_FILE_BYTES:
        return None
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def read_text_safe(path):
    """utf-8-sig 优先，容忍 BOM/坏编码；失败返回 (None, error)。"""
    raw = _read_bytes(path)
    if raw is None:
        return None, "read_failed_or_oversize"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "bad_encoding_replaced"


def read_json_safe(path):
    """读取 JSON，容错；失败返回 (None, error)。"""
    text, err = read_text_safe(path)
    if err == "read_failed_or_oversize":
        return None, "read_failed_or_oversize"
    if text is None:
        return None, "read_failed"
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as exc:
        return None, "bad_json:%s" % type(exc).__name__


def write_json(path, obj):
    """写 JSON（utf-8，稳定缩进，键排序确保确定性）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def write_text(path, content):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def rel_to_output(path, output_dir):
    """把绝对路径转成相对 /app/output/ 的相对路径。"""
    try:
        rel = os.path.relpath(path, output_dir)
    except ValueError:
        rel = os.path.basename(path)
    return rel.replace(os.sep, "/")


# ---------------------------------------------------------------------------
# 契约解析
# ---------------------------------------------------------------------------

def extract_case_id(case):
    """从 case.json 取 case_id，依序尝试常见键。"""
    if not isinstance(case, dict):
        return "unknown"
    for key in ("case_id", "id", "challenge_id", "task_id"):
        val = case.get(key)
        if isinstance(val, (str, int)) and str(val).strip():
            return str(val)
    return "unknown"


def extract_output_contract(case):
    """从 case.json 中提取 output_contract 字段。"""
    if not isinstance(case, dict):
        return {}
    # 直接字段
    for key in ("output_contract", "outputContract", "contract"):
        val = case.get(key)
        if isinstance(val, dict):
            return val
    # 嵌套在 task / spec 内
    for key in ("task", "spec", "runtime"):
        val = case.get(key)
        if isinstance(val, dict):
            inner = extract_output_contract(val)
            if inner:
                return inner
    return {}


def placeholder_for(value):
    """为契约字段生成类型化占位：dict 递归、list 空、其余 null。"""
    if isinstance(value, dict):
        return {k: placeholder_for(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


# ---------------------------------------------------------------------------
# 契约预检（preflight）：立即落盘合法 result.json 骨架
# ---------------------------------------------------------------------------

def preflight(input_dir, output_dir, work_dir):
    """读取 case.json/task.md，落盘 result.json 骨架 + state.json + 六节 manuscript 骨架。

    契约优先：无论后续发生什么，本步骤都先落盘一版合法 result.json，
    并把 errors/degradations/change_log 同步写入 result 与 state。
    """
    started = time.time()
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")

    case_exists = os.path.isfile(case_path)
    case, case_err = read_json_safe(case_path)
    task_text, task_err = read_text_safe(task_path)

    errors = []
    degradations = []

    if not case_exists:
        errors.append("case.json 缺失")
    elif case_err:
        errors.append("case.json: %s" % case_err)
    if task_err and task_err not in ("read_failed_or_oversize", "bad_encoding_replaced"):
        errors.append("task.md: %s" % task_err)
    elif task_err == "bad_encoding_replaced":
        degradations.append("task.md 编码已替换解码")

    case = case if isinstance(case, dict) else {}
    case_id = extract_case_id(case)
    contract = extract_output_contract(case)

    # 状态初判：case.json 存在但坏 → input_error；否则从骨架（insufficient）起步
    if case_exists and case_err:
        status = STATUS_INPUT_ERROR
    else:
        status = STATUS_INSUFFICIENT

    change_log = [{
        "step": "preflight",
        "action": "落盘 result.json 骨架与 state.json（契约字段已占位回填）",
    }]

    result = {
        "case_id": case_id,
        "status": status,
        "artifacts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": change_log,
        "errors": errors,
        "degradations": degradations,
        "limitations": [],
        "runtime": {"seconds": round(time.time() - started, 3), "phase": "preflight"},
    }
    # 契约字段原样回填（类型化占位）
    for key, value in contract.items():
        result[key] = placeholder_for(value)

    state = {
        "phase": "preflight",
        "case_id": case_id,
        "contract_keys": sorted(contract.keys()),
        "errors": list(errors),
        "degradations": list(degradations),
        "change_log": list(change_log),
        "started_at": started,
    }

    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(os.path.join(work_dir, "state.json"), state)

    # 六节 manuscript 骨架（供模型填充，不含示例答案）
    _write_manuscript_skeleton(os.path.join(output_dir, "manuscript.md"), contract)

    return result, state


def _write_manuscript_skeleton(path, contract):
    """写六节骨架，只给节标题与填写要求，不给示例答案。"""
    lines = [
        "# Methods / Experiments（初稿骨架）",
        "",
        "> 本文件由 preflight 生成，六节均为待填状态；请按 `/input/task.md` 的体裁、受众与格式要求逐节填充，",
        "> 每个事实性句子挂 `[S#]`，并在 `evidence_map.json` 与 `preserved_facts.json` 中同步。",
        "",
    ]
    for key, label in FACETS.items():
        lines += ["## %s" % label, "", "<!-- TODO: 依据材料填充，缺口写 MATERIAL GAP 或 not_reported -->", ""]
    lines += [
        "## 人工确认项",
        "",
        "<!-- 缺口、冲突、无来源参数逐条列出，对应 pending_human -->",
        "",
    ]
    write_text(path, "\n".join(lines))


# ---------------------------------------------------------------------------
# 材料扫描与事实抽取（inventory）
# ---------------------------------------------------------------------------

TEXT_EXTENSIONS = {
    ".md", ".txt", ".json", ".csv", ".tsv", ".py", ".yaml", ".yml", ".toml",
    ".cfg", ".ini", ".log", ".sh", ".rst", ".tex", ".bib", ".xml", ".html",
    ".ipynb", ".njk", ".jsonl",
}
BINARY_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg",
                     ".svg", ".pdf", ".zip", ".gz", ".pt", ".pth", ".ckpt", ".h5", ".npy"}

# 数值+单位（逐字片段）
RE_NUMBER_UNIT = re.compile(r"-?\d+(?:\.\d+)?\s*(?:%|[A-Za-zµμ/·]{1,12}(?:/[A-Za-zµμ]{1,12})?)")
# key=value 或 key: value
RE_KV = re.compile(r"^\s*(?:[-*#\d.]{0,6}\s*)?([A-Za-z_][A-Za-z0-9_.\-/]*)\s*[:=]\s*(.+?)\s*$")
# 种子
RE_SEED = re.compile(r"\b(?:random_?)?seed\s*[:=]\s*(-?\d+)", re.IGNORECASE)
# 版本号
RE_VERSION = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?(?:[a-z0-9.\-+]*)\b", re.IGNORECASE)
# 路径/文件
RE_PATH = re.compile(r"(?:/[\w.\-/]+|[\w\-]+\.(?:py|yaml|yml|json|csv|toml|cfg|ini|log|md|sh|ipynb))")
# 命令
RE_COMMAND = re.compile(r"^\s*(?:\$|>|(?:python\d?(?:\.\d+)?|pip\d?|conda|bash|sh|docker)\s+)", re.IGNORECASE)

# facet 关键词猜测
FACET_KEYWORDS = {
    "steps": ["step", "procedure", "protocol", "first", "then", "run", "execute", "train", "evaluate", "推断", "步骤", "流程"],
    "setup": ["epoch", "batch", "lr", "learning rate", "gpu", "seed", "cuda", "device", "训练", "验证", "测试", "训练轮", "随机种子", "重复"],
    "params": ["parameter", "hyperparameter", "threshold", "temperature", "weight", "参数", "阈值", "系数", "超参"],
    "data": ["dataset", "corpus", "split", "samples", "images", "tokens", "数据", "样本", "划分", "子集", "预处理"],
    "baselines": ["baseline", "compare", "versus", "vs", "sota", "ablation", "基线", "对照", "消融"],
    "repro": ["environment", "requirements", "docker", "conda", "python", "version", "dependency", "复现", "环境", "依赖", "版本"],
}


def classify_material(path):
    """按扩展名分类。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXTENSIONS:
        return "text"
    if ext in BINARY_EXTENSIONS:
        return "binary"
    return "other"


def scan_materials(input_dir, work_dir):
    """扫描 /input，返回 (inventory, candidates, degradations)。"""
    inventory = []
    candidates = []
    degradations = []

    if not os.path.isdir(input_dir):
        return inventory, candidates, ["input dir missing: %s" % input_dir]

    names = sorted(os.listdir(input_dir))
    for name in names:
        full = os.path.join(input_dir, name)
        if not os.path.isfile(full):
            continue
        # 跳过 case.json/task.md 本身（已单独读）
        if name in ("case.json", "task.md"):
            continue
        kind = classify_material(name)
        size = os.path.getsize(full)
        item = {"file": name, "path": full, "kind": kind, "size": size}
        if kind == "text":
            text, err = read_text_safe(full)
            if text is None:
                item["note"] = err or "read_failed"
                degradations.append("跳过 %s: %s" % (name, item["note"]))
            else:
                item["note"] = "extracted"
                item["lines"] = text.count("\n") + 1
                rel = "materials/" + name if "materials" not in full else name
                cands = extract_candidates(text, rel)
                for c in cands:
                    c["file"] = name
                candidates.extend(cands)
        else:
            item["note"] = "binary: 交由 pdf-text-tool/ocr-tool/office-renderer 处理（如题面提供）"
        inventory.append(item)

    return inventory, candidates, degradations


def extract_candidates(text, locator_base):
    """从文本抽取候选事实，返回类型化记录列表。每条含 locator+quote。"""
    out = []
    lines = text.split("\n")
    for idx, line in enumerate(lines, 1):
        locator = "%s#L%d" % (locator_base, idx)
        stripped = line.strip()
        if not stripped:
            continue
        # 命令
        if RE_COMMAND.match(line):
            out.append(_cand("command", stripped, locator, stripped))
        # 种子
        for m in RE_SEED.finditer(line):
            out.append(_cand("seed", m.group(1), locator, stripped))
        # key=value
        if RE_KV.match(stripped) and len(stripped) < 200:
            m = RE_KV.match(stripped)
            out.append(_cand("kv", {"key": m.group(1), "value": m.group(2)}, locator, stripped))
        # 数值+单位
        for m in RE_NUMBER_UNIT.finditer(line):
            tok = m.group(0)
            if len(tok) <= 40:
                out.append(_cand("number", tok, locator, stripped))
        # 版本
        for m in RE_VERSION.finditer(line):
            out.append(_cand("version", m.group(0), locator, stripped))
        # 路径/文件
        for m in RE_PATH.finditer(line):
            out.append(_cand("path", m.group(0), locator, stripped))

    # 去重（同 locator+kind+value），保持顺序稳定
    seen = set()
    dedup = []
    for c in out:
        key = (c["locator"], c["kind"], json.dumps(c["value"], ensure_ascii=False, sort_keys=True))
        if key not in seen:
            seen.add(key)
            dedup.append(c)
    return dedup


def _cand(kind, value, locator, quote):
    return {
        "kind": kind,
        "value": value,
        "locator": locator,
        "quote": quote,
        "facet_guess": guess_facet(locator, quote),
        "strength": "candidate",
    }


def guess_facet(locator, quote):
    text = (locator + " " + quote).lower()
    scores = {f: 0 for f in FACETS}
    for facet, kws in FACET_KEYWORDS.items():
        for kw in kws:
            if kw.lower() in text:
                scores[facet] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "params"


def inventory(input_dir, output_dir, work_dir):
    """扫描材料并落盘 fact_candidates.json + materials_inventory.json。"""
    inventory_list, candidates, degradations = scan_materials(input_dir, work_dir)
    write_json(os.path.join(work_dir, "materials_inventory.json"),
               {"files": inventory_list, "degradations": degradations})
    write_json(os.path.join(work_dir, "fact_candidates.json"),
               {"candidates": candidates, "count": len(candidates)})

    # 更新 state
    state = _load_state(work_dir)
    state["phase"] = "inventory"
    state["change_log"].append({
        "step": "inventory",
        "action": "扫描材料，抽取候选事实 %d 条" % len(candidates),
    })
    write_json(os.path.join(work_dir, "state.json"), state)
    return inventory_list, candidates, degradations


def _load_state(work_dir):
    path = os.path.join(work_dir, "state.json")
    obj, err = read_json_safe(path)
    return obj if isinstance(obj, dict) else {"change_log": [], "errors": [], "degradations": []}


# ---------------------------------------------------------------------------
# 台账构建（模型确认后调用；此处提供从候选构建的确定性基座）
# ---------------------------------------------------------------------------

def build_ledger_from_candidates(work_dir):
    """把候选事实归并为六类台账（未确认候选，模型需逐条核实）。"""
    cand_path = os.path.join(work_dir, "fact_candidates.json")
    cands, err = read_json_safe(cand_path)
    if err or not isinstance(cands, dict):
        return {}, "no_candidates:%s" % (err or "missing")
    ledger = {f: [] for f in FACETS}
    for c in cands.get("candidates", []):
        facet = c.get("facet_guess", "params")
        if facet not in ledger:
            facet = "params"
        ledger[facet].append(c)
    # 每类按 locator 排序，稳定输出
    for f in ledger:
        ledger[f].sort(key=lambda x: x.get("locator", ""))
    return ledger, None


# ---------------------------------------------------------------------------
# 渲染（从台账渲染成稿骨架、证据映射、数值保全表）
# ---------------------------------------------------------------------------

def render_from_ledger(output_dir, work_dir, ledger):
    """从台账渲染 manuscript 骨架、evidence_map.json、preserved_facts.json。"""
    # evidence_map：以候选的 locator 排序编号 S#
    evidence_map = {}
    preserved = []
    counter = [0]
    for facet in FACETS:
        for rec in ledger.get(facet, []):
            counter[0] += 1
            sid = "S%d" % counter[0]
            evidence_map[sid] = {
                "facet": facet,
                "locator": rec.get("locator", ""),
                "quote": rec.get("quote", ""),
            }
            if rec.get("kind") == "number":
                preserved.append({
                    "id": sid,
                    "value": rec.get("value"),
                    "locator": rec.get("locator", ""),
                    "quote": rec.get("quote", ""),
                })
    write_json(os.path.join(output_dir, "evidence_map.json"), evidence_map)
    write_json(os.path.join(output_dir, "preserved_facts.json"), {"facts": preserved})
    write_json(os.path.join(work_dir, "fact_ledger.json"), {"ledger": ledger, "counts": {f: len(ledger[f]) for f in FACETS}})
    return evidence_map, preserved


# ---------------------------------------------------------------------------
# 证据后检（validate）
# ---------------------------------------------------------------------------

def validate(input_dir, output_dir, work_dir):
    """校验 result.json 契约字段、状态、产物路径、证据映射，输出 validation_report.json。"""
    issues = []
    warnings = []

    result, err = read_json_safe(os.path.join(output_dir, "result.json"))
    if err or not isinstance(result, dict):
        return {"ok": False, "issues": ["result.json 缺失或非法: %s" % (err or "not dict")],
                "warnings": warnings}

    # 契约字段回填检查
    case, _ = read_json_safe(os.path.join(input_dir, "case.json"))
    case = case if isinstance(case, dict) else {}
    contract = extract_output_contract(case)
    for key in contract:
        if key not in result:
            issues.append("output_contract 字段缺失: %s" % key)

    # 标准字段检查
    for field in STANDARD_FIELDS:
        if field not in result:
            issues.append("标准字段缺失: %s" % field)

    if not result.get("case_id"):
        issues.append("case_id 为空")

    status = result.get("status")
    if status not in (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR):
        issues.append("status 非法: %r" % status)

    if not isinstance(result.get("change_log"), list) or not result["change_log"]:
        warnings.append("change_log 为空，建议记录 骨架→初稿→改进→收口")

    if not isinstance(result.get("sources"), list):
        warnings.append("sources 应为列表")
    if not isinstance(result.get("uncertainty"), list):
        warnings.append("uncertainty 应为列表")
    if not isinstance(result.get("pending_human"), list):
        warnings.append("pending_human 应为列表")

    # 产物路径存在性
    for art in result.get("artifacts", []):
        if isinstance(art, str):
            full = os.path.join(output_dir, art) if not os.path.isabs(art) else art
            if not os.path.isfile(full):
                warnings.append("artifacts 指向不存在文件: %s" % art)

    # 证据映射完整性
    em_path = os.path.join(output_dir, "evidence_map.json")
    em, em_err = read_json_safe(em_path)
    if em_err or not isinstance(em, dict):
        issues.append("evidence_map.json 缺失或非法")
    else:
        for sid, entry in em.items():
            if not (isinstance(entry, dict) and entry.get("locator") and entry.get("quote")):
                issues.append("evidence_map[%s] 缺 locator/quote" % sid)

    # manuscript 是否存在且非空骨架
    ms_path = os.path.join(output_dir, "manuscript.md")
    if not os.path.isfile(ms_path):
        issues.append("manuscript.md 缺失")
    else:
        ms_text, _ = read_text_safe(ms_path)
        if ms_text is not None and "<!-- TODO" in ms_text:
            warnings.append("manuscript.md 仍含 TODO 骨架占位，可能未完成填充")

    report = {"ok": len(issues) == 0, "issues": issues, "warnings": warnings}
    write_json(os.path.join(work_dir, "validation_report.json"), report)
    return report


_STATUS_RANK = {
    STATUS_INSUFFICIENT: 0,
    STATUS_PARTIAL: 1,
    STATUS_OK: 2,
}


def finalize_result(output_dir, work_dir, status=None, extra=None):
    """收口：更新 status、artifacts、runtime；只升级、不降级。

    status=None 表示保持现状（由调用方决定是否升级）；input_error 仅能被
    显式目标覆盖（例如模型修复后指定 ok）。
    """
    result, err = read_json_safe(os.path.join(output_dir, "result.json"))
    if err or not isinstance(result, dict):
        return None
    if status:
        cur = result.get("status")
        if cur == STATUS_INPUT_ERROR:
            result["status"] = status  # 显式覆盖
        else:
            new_rank = _STATUS_RANK.get(status, _STATUS_RANK.get(cur, 0))
            old_rank = _STATUS_RANK.get(cur, 0)
            result["status"] = status if new_rank >= old_rank else cur
    if extra:
        for k, v in extra.items():
            if k not in result or not result[k]:
                result[k] = v
    # 归一化 artifacts：列出可编辑产物
    result["artifacts"] = sorted({
        f for f in ("manuscript.md", "evidence_map.json", "preserved_facts.json")
        if os.path.isfile(os.path.join(output_dir, f))
    })
    rt = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
    rt["phase"] = "finalize"
    result["runtime"] = rt
    write_json(os.path.join(output_dir, "result.json"), result)
    return result


# ---------------------------------------------------------------------------
# 编排入口
# ---------------------------------------------------------------------------

def run(args):
    """按 --step 分派。args: namespace(input, output, work, step)."""
    input_dir = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK

    step = getattr(args, "step", "all") or "all"

    if step in ("preflight", "all"):
        preflight(input_dir, output_dir, work_dir)
    if step in ("inventory", "all"):
        inventory(input_dir, output_dir, work_dir)
    if step in ("render", "all"):
        ledger, _ = build_ledger_from_candidates(work_dir)
        render_from_ledger(output_dir, work_dir, ledger)
        total = sum(len(v) for v in ledger.values())
        # 有候选才升级到 partial；无候选（空输入/无材料）保持 insufficient
        target = STATUS_PARTIAL if total > 0 else None
        finalize_result(output_dir, work_dir, status=target)
    if step in ("validate", "all"):
        report = validate(input_dir, output_dir, work_dir)
        print("validation ok=%s issues=%d warnings=%d" %
              (report["ok"], len(report["issues"]), len(report["warnings"])))
        return 0 if report["ok"] else 1

    return 0
