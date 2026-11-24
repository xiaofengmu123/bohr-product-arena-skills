# -*- coding: utf-8 -*-
"""Core implementation for the methods-experiments-writing skill.

Standard library only. All output is deterministic given identical input:
canonical JSON (sort_keys, ensure_ascii=False), fixed key insertion order,
step labels instead of timestamps in change_log, no hostname/PID/timestamps
in content. The single non-deterministic field is `runtime` (elapsed wall
clock), which is excluded from `content_sha256`.
"""

import hashlib
import json
import os
import re
import subprocess
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024

ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")

TEXT_EXTS = {
    ".py", ".ipynb", ".yaml", ".yml", ".json", ".cfg", ".ini", ".toml",
    ".txt", ".md", ".rst", ".csv", ".tsv", ".sh", ".log", ".out", ".err",
    ".tex", ".bib", ".xml", ".html", ".config", ".properties", ".env",
    ".slurm", ".sbatch",
}
PDF_EXTS = {".pdf"}
OFFICE_EXTS = {".docx", ".doc", ".pptx", ".xlsx", ".xls", ".odt", ".ods", ".odp"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".tif", ".tiff", ".bmp"}
BINARY_EXTS = {".zip", ".tar", ".gz", ".npy", ".npz", ".pkl", ".pt", ".pth",
               ".ckpt", ".bin", ".h5", ".hdf5", ".pb", ".onnx"}

# Candidate-fact extractors: (kind, compiled regex).  Group 0 is the quote
# fragment; where a value group exists we keep the full line as the verbatim
# quote so the agent can verify against surrounding context.
_VERSION_RE = re.compile(
    r"(?i)\b(version|ver)\s*[=:]\s*"
    r"([0-9]+(?:\.[0-9]+){1,3}(?:[-_.]?(?:rc|alpha|beta|dev|post|final)\d*)?)")
_VERSION_PIN_RE = re.compile(
    r"(?i)([A-Za-z0-9_.\-]+)\s*==\s*"
    r"([0-9]+(?:\.[0-9]+){1,3}(?:[-_.]?(?:rc|alpha|beta|dev|post)\d*)?)")
_SEED_RE = re.compile(
    r"(?i)\b(seed|random_state|torch\.manual_seed|np\.random\.seed|"
    r"set_seed|random_seed)\b\s*[=(]\s*([0-9]+)")
_HYPER_RE = re.compile(
    r"(?i)\b(learning_?rate|lr|batch_?size|epochs?|dropout|weight_?decay|"
    r"hidden_?size|num_?layers|temperature|max_?length|top_k|top_p|"
    r"warmup_?steps|max_?steps|gradient_?accumulation_?steps)\b"
    r"\s*[=:]\s*([^\s,;}\])]+)")
_NUM_UNIT_RE = re.compile(
    r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\s*"
    r"(%|ms|s\b|GB|MB|KB|GiB|MiB|KiB|h\b|hours?|min\b|epochs?|steps?|"
    r"layers?|dim\b|batch|workers?|GPUs?|cores?)(?![\w])")
_DATASET_RE = re.compile(
    r"(?i)\b(dataset|data_?dir|data_?path|corpus|benchmark|datadir|"
    r"train_?file|valid?_?file|test_?file)\b\s*[=:(]\s*[\"']?"
    r"([A-Za-z0-9_\-./~]+)")
_PATH_RE = re.compile(
    r"(\b[A-Za-z0-9_\-./]+\.(?:py|yaml|yml|json|csv|tsv|txt|md|sh|cfg|ini|"
    r"toml|log)\b|(?:/|\./)[A-Za-z0-9_\-./]+)")
_COMMAND_RE = re.compile(
    r"^\s*(python3?|pip3?|conda|docker|bash|sh|make|git|sbatch|srun|"
    r"torchrun|deepspeed|nvcc)\b.*")
_METRIC_RE = re.compile(
    r"(?i)\b(accuracy|acc|f1|precision|recall|auc|roc|mse|mae|rmse|bleu|"
    r"rouge|perplexity|ppl|loss)\b\s*[=:]\s*"
    r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")

_FACT_KINDS = ("version", "version_pin", "seed", "hyperparam", "number_unit",
               "dataset", "path", "command", "metric")
_EXTRACTORS = [
    ("version", _VERSION_RE),
    ("version_pin", _VERSION_PIN_RE),
    ("seed", _SEED_RE),
    ("hyperparam", _HYPER_RE),
    ("number_unit", _NUM_UNIT_RE),
    ("dataset", _DATASET_RE),
    ("path", _PATH_RE),
    ("command", _COMMAND_RE),
    ("metric", _METRIC_RE),
]


def _u(s):
    """Coerce to str for py2/py3 style safety (py3 only in practice)."""
    return s if isinstance(s, str) else s.decode("utf-8", "replace")


def dumps_canonical(obj):
    """Deterministic JSON text: sorted keys, no ASCII escaping, tight separators."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def content_hash(obj):
    """sha256 over the canonical form of a dict (order-insensitive)."""
    return hashlib.sha256(dumps_canonical(obj).encode("utf-8")).hexdigest()


def read_text_safe(path):
    """Read text tolerating BOM/bad encodings. Returns (text, encoding, error)."""
    attempts = ["utf-8-sig", "utf-8", "latin-1"]
    last_err = None
    for enc in attempts:
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            try:
                text = raw.decode(enc)
            except UnicodeDecodeError:
                last_err = "decode(%s) failed" % enc
                continue
            if enc == "utf-8-sig":
                text = text.lstrip("\ufeff")
            return text, enc, None
        except (OSError, IOError) as exc:  # pragma: no cover
            last_err = str(exc)
            break
    return "", "latin-1", last_err


def load_json_safe(path):
    """Load JSON tolerating BOM/bad content. Returns (obj, error)."""
    text, _enc, err = read_text_safe(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as exc:
        return None, "bad_json: %s" % exc


def classify(path):
    """Classify a file by extension; returns (kind, is_text)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXTS:
        return "text", True
    if ext in PDF_EXTS:
        return "pdf", False
    if ext in OFFICE_EXTS:
        return "office", False
    if ext in IMAGE_EXTS:
        return "image", False
    if ext in BINARY_EXTS:
        return "binary", False
    return "unknown", False


def find_case_id(case):
    """Resolve case_id from the common key alternatives."""
    if not isinstance(case, dict):
        return "unknown"
    for key in ("case_id", "id", "challenge_id"):
        val = case.get(key)
        if isinstance(val, str) and val:
            return val
        if isinstance(val, (int, float)):
            return str(val)
    return "unknown"


def backfill_contract(case):
    """Return the output_contract keys (original order) with placeholder values."""
    if not isinstance(case, dict):
        return {}, []
    contract = case.get("output_contract")
    if not isinstance(contract, dict):
        return {}, []
    filled = {}
    keys = list(contract.keys())
    for key in keys:
        val = contract[key]
        if isinstance(val, list):
            filled[key] = []
        elif isinstance(val, dict):
            filled[key] = {}
        elif isinstance(val, bool):
            filled[key] = None
        else:
            filled[key] = None
    return filled, keys


def _ordered_result(case_id, contract_filled, contract_keys, status, runtime):
    """Build result dict with a fixed, deterministic key insertion order."""
    result = {}
    result["case_id"] = case_id
    for key in contract_keys:
        result[key] = contract_filled.get(key)
    result["status"] = status
    result["artifacts"] = []
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["errors"] = []
    result["degradations"] = []
    result["limitations"] = []
    result["change_log"] = [{
        "step": "skeleton",
        "action": "落盘合法结果骨架，回填 output_contract 占位值",
        "status": status,
    }]
    result["runtime"] = runtime
    return result


def _attach_determinism(result):
    """Compute content hash over every field except runtime and determinism."""
    subset = {k: v for k, v in result.items()
              if k not in ("runtime", "determinism")}
    h = content_hash(subset)
    result["determinism"] = {
        "policy": ("same input -> byte-identical result.json except runtime; "
                   "canonical JSON (sort_keys, ensure_ascii=False) hashed"),
        "content_sha256": h,
        "excluded_fields": ["runtime"],
    }
    return result


def write_json(path, obj):
    """Write JSON with a stable, readable key order (insertion order)."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def ensure_dirs(output_dir, work_dir):
    for d in (output_dir, work_dir):
        if d:
            os.makedirs(d, exist_ok=True)


def _rel_posix(path, base):
    """Path relative to base, normalized to forward slashes."""
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        rel = path
    return rel.replace(os.sep, "/")


# ---------------------------------------------------------------------------
# skeleton
# ---------------------------------------------------------------------------

def run_skeleton(input_dir, output_dir, work_dir, runtime=0):
    """Read case/task, write a legal result.json skeleton and state.json.

    Returns (result, state, report).  Never raises on bad input.
    """
    ensure_dirs(output_dir, work_dir)
    errors, degradations = [], []

    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")

    case, case_err = load_json_safe(case_path)
    task_text = ""
    if os.path.isfile(task_path):
        task_text, _enc, task_err = read_text_safe(task_path)
        if task_err:
            degradations.append("task.md 读取降级: %s" % task_err)

    status = "partial"
    case_id = "unknown"
    contract_filled, contract_keys = {}, []

    if case_err or not os.path.isfile(case_path):
        status = "input_error"
        case_id = "unknown"
        errors.append("case.json 不可读或缺失: %s" % (case_err or "not found"))
    elif case is None:
        status = "input_error"
        errors.append("case.json 解析为空对象")
    else:
        case_id = find_case_id(case)
        contract_filled, contract_keys = backfill_contract(case)

    # If input dir has no usable material files, this is honest evidence gap.
    materials = _scan_materials(input_dir)
    if status != "input_error" and not any(m["kind"] in ("text", "pdf", "office")
                                           for m in materials):
        status = "insufficient_evidence"

    result = _ordered_result(case_id, contract_filled, contract_keys,
                             status, runtime)
    if errors:
        result["errors"] = errors
    if degradations:
        result["degradations"] = degradations
    if status == "insufficient_evidence":
        result["limitations"] = ["输入目录无可提取的科研材料（text/pdf/office）。"]
        result["pending_human"] = ["请确认 /input 下是否已提供代码、配置、日志或数据说明。"]
    elif status == "input_error":
        result["limitations"] = ["case.json 不可读，无法确定 output_contract 与 case_id。"]
    _attach_determinism(result)

    state = {
        "case_id": case_id,
        "input_dir": input_dir,
        "output_dir": output_dir,
        "work_dir": work_dir,
        "contract_keys": contract_keys,
        "step": "skeleton",
    }

    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(os.path.join(work_dir, "state.json"), state)
    return result, state, {"errors": list(errors), "degradations": list(degradations)}


# ---------------------------------------------------------------------------
# inventory / material scan
# ---------------------------------------------------------------------------

CONTROL_FILES = {"case.json", "task.md"}


def _scan_materials(input_dir):
    """Walk input dir, classify files, respect the 2MB cap. Deterministic order."""
    entries = []
    if not os.path.isdir(input_dir):
        return entries
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = _rel_posix(full, input_dir)
            # Skip top-level control files: they are the task interface, not
            # research materials, so their presence must not mask an empty
            # material set (insufficient_evidence detection).
            if rel in CONTROL_FILES and "/" not in rel:
                continue
            if rel.startswith("work/") or rel.startswith("output/"):
                continue
            size = os.path.getsize(full)
            kind, is_text = classify(full)
            entry = {
                "path": rel,
                "kind": kind,
                "size": size,
                "skipped": size > MAX_FILE_BYTES,
                "needs_extraction": kind in ("pdf", "office") and size <= MAX_FILE_BYTES,
            }
            entries.append(entry)
    return entries


def _extract_candidates(text, locator, max_per_file=500):
    """Extract candidate facts with locator + verbatim line quote."""
    candidates = []
    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        quote = stripped if len(stripped) <= 240 else stripped[:237] + "..."
        loc = "%s#L%d" % (locator, lineno)
        for kind, regex in _EXTRACTORS:
            if regex.search(stripped):
                candidates.append({"kind": kind, "locator": loc, "quote": quote})
        if len(candidates) >= max_per_file:
            break
    return candidates


def run_inventory(input_dir, output_dir, work_dir, runtime=0):
    """Scan /input, classify files, extract candidate facts. Deterministic."""
    ensure_dirs(output_dir, work_dir)
    entries = _scan_materials(input_dir)
    inventory = {"materials": entries}
    facts = []
    degradations = []
    skipped = [e for e in entries if e["skipped"]]

    for entry in entries:
        if entry["skipped"]:
            degradations.append("跳过超限文件(>2MB): %s" % entry["path"])
            continue
        if entry["kind"] != "text":
            continue
        full = os.path.join(input_dir, entry["path"])
        text, _enc, err = read_text_safe(full)
        if err:
            degradations.append("读取失败: %s (%s)" % (entry["path"], err))
            continue
        locator = "materials/%s" % entry["path"]
        facts.extend(_extract_candidates(text, locator))

    # Deterministic ordering: by locator then kind then quote.
    facts.sort(key=lambda f: (f["locator"], f["kind"], f["quote"]))

    inventory["fact_candidates_count"] = len(facts)
    inventory["skipped_count"] = len(skipped)
    write_json(os.path.join(work_dir, "materials_inventory.json"), inventory)
    write_json(os.path.join(work_dir, "extracted_facts.json"),
               {"facts": facts, "count": len(facts)})

    # Refresh sources/degradations in result.json without disturbing model work.
    result_path = os.path.join(output_dir, "result.json")
    result, err = load_json_safe(result_path)
    if isinstance(result, dict):
        result["sources"] = [
            {"path": e["path"], "kind": e["kind"], "size": e["size"]}
            for e in entries if not e["skipped"]
        ]
        if degradations:
            existing = result.get("degradations") or []
            merged = sorted(set(existing) | set(degradations))
            result["degradations"] = merged
        _attach_determinism(result)
        write_json(result_path, result)
    return inventory, {"facts": facts}, degradations


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def run_validate(input_dir, output_dir, work_dir, runtime=0):
    """Check result.json contract, evidence, and artifact paths. Exit code 0=pass."""
    issues = []
    result_path = os.path.join(output_dir, "result.json")
    state_path = os.path.join(work_dir, "state.json")

    result, err = load_json_safe(result_path)
    if err or not isinstance(result, dict):
        issues.append("result.json 不可读或非对象: %s" % (err or "missing"))
        report = {"ok": False, "issues": issues}
        write_json(os.path.join(work_dir, "validation_report.json"), report)
        return report

    required = ["case_id", "status", "artifacts", "sources", "uncertainty",
                "pending_human", "change_log"]
    for key in required:
        if key not in result:
            issues.append("缺少顶层字段: %s" % key)
    if result.get("status") not in ALLOWED_STATUS:
        issues.append("非法 status: %r" % result.get("status"))
    if result.get("case_id") in (None, "", "unknown") and result.get("status") != "input_error":
        issues.append("case_id 缺失或为 unknown")

    state, _err = load_json_safe(state_path)
    contract_keys = []
    if isinstance(state, dict):
        contract_keys = state.get("contract_keys") or []
    for key in contract_keys:
        if key not in result:
            issues.append("output_contract 字段未回填: %s" % key)

    # artifacts must exist relative to output_dir
    for rel in result.get("artifacts", []):
        if not isinstance(rel, str):
            continue
        full = os.path.join(output_dir, rel.replace("/", os.sep))
        if not os.path.isfile(full):
            issues.append("artifacts 路径不存在: %s" % rel)

    # determinism hash must match stored value
    stored = None
    det = result.get("determinism")
    if isinstance(det, dict):
        stored = det.get("content_sha256")
    subset = {k: v for k, v in result.items()
              if k not in ("runtime", "determinism")}
    recomputed = content_hash(subset)
    if stored and stored != recomputed:
        issues.append("content_sha256 不一致（请重跑 finalize）")

    # evidence checks only apply to a finished ok/partial manuscript
    if result.get("status") in ("ok", "partial"):
        ev_path = os.path.join(output_dir, "evidence_map.json")
        if not os.path.isfile(ev_path):
            issues.append("缺少 evidence_map.json")
        else:
            ev, ev_err = load_json_safe(ev_path)
            if ev_err or not isinstance(ev, dict):
                issues.append("evidence_map.json 非法")
            elif len(ev) == 0:
                issues.append("evidence_map.json 为空")

    ok = len(issues) == 0
    report = {"ok": ok, "issues": issues, "status": result.get("status")}
    write_json(os.path.join(work_dir, "validation_report.json"), report)
    return report


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

def run_finalize(input_dir, output_dir, work_dir, status="ok", runtime=0):
    """Set final status, append change_log, recompute determinism hash."""
    result_path = os.path.join(output_dir, "result.json")
    result, err = load_json_safe(result_path)
    if err or not isinstance(result, dict):
        return {"ok": False, "error": err or "result.json 不可读"}
    if status in ALLOWED_STATUS:
        result["status"] = status
    else:
        result["status"] = "partial"
    result["runtime"] = runtime
    log = result.get("change_log")
    if not isinstance(log, list):
        log = []
    log.append({
        "step": "finalize",
        "action": "收口：重算 content_sha256，status=%s" % result["status"],
    })
    result["change_log"] = log
    _attach_determinism(result)
    write_json(result_path, result)
    return {"ok": True, "status": result["status"]}


# ---------------------------------------------------------------------------
# selftest (used by selftest.py and `main.py selftest`)
# ---------------------------------------------------------------------------

def _write(path, text):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def make_synthetic_input(root):
    """Create a deterministic synthetic case with code/config/log materials."""
    os.makedirs(root, exist_ok=True)
    _write(os.path.join(root, "case.json"), json.dumps({
        "case_id": "synthetic-001",
        "id": "synthetic-001",
        "output_contract": {
            "title": "A synthetic method",
            "abstract": "placeholder",
            "keywords": ["methods"],
            "target_file": "manuscript.md",
        },
    }, ensure_ascii=False, indent=2))
    _write(os.path.join(root, "task.md"),
           "# 任务\n写方法与实验章节，六类要素齐全，证据可溯源。\n")
    mat = os.path.join(root, "materials")
    os.makedirs(mat, exist_ok=True)
    _write(os.path.join(mat, "config.yaml"),
           "model: bert-base-uncased\nseed: 42\n"
           "learning_rate: 3e-5\nbatch_size: 16\nepochs: 3\n")
    _write(os.path.join(mat, "train.log"),
           "python3 train.py --epochs 3 --lr 3e-5 --seed 42\n"
           "train loss 0.231 acc 0.912\nval loss 0.411 acc 0.872\n")
    _write(os.path.join(mat, "notes.txt"),
           "dataset: PubMedQA v2, train/val/test split 80/10/10.\n")


def run_pipeline(input_dir, output_dir, work_dir, runtime=0):
    """skeleton -> inventory -> validate. The script-only, model-free pipeline."""
    run_skeleton(input_dir, output_dir, work_dir, runtime=runtime)
    run_inventory(input_dir, output_dir, work_dir, runtime=runtime)
    report = run_validate(input_dir, output_dir, work_dir, runtime=runtime)
    return report


def run_selftest(work_root):
    """Determinism + robustness self-check. Returns (passed, report_lines)."""
    import shutil
    import tempfile
    lines = []
    ok = True

    base = work_root if work_root else tempfile.mkdtemp(prefix="msk-selftest-")
    os.makedirs(base, exist_ok=True)

    def check(name, cond):
        nonlocal ok
        lines.append("%s %s" % ("PASS" if cond else "FAIL", name))
        if not cond:
            ok = False

    # Phase 1: synthetic normal input -> two identical runs.
    inp = os.path.join(base, "in")
    make_synthetic_input(inp)
    out_a = os.path.join(base, "out_a")
    out_b = os.path.join(base, "out_b")
    work_a = os.path.join(base, "work_a")
    work_b = os.path.join(base, "work_b")

    run_pipeline(inp, out_a, work_a, runtime=3)
    run_pipeline(inp, out_b, work_b, runtime=5)

    ra, err_a = load_json_safe(os.path.join(out_a, "result.json"))
    rb, err_b = load_json_safe(os.path.join(out_b, "result.json"))
    check("正常输入两次运行均产出合法 result.json", err_a is None and err_b is None)
    ha = (ra or {}).get("determinism", {}).get("content_sha256")
    hb = (rb or {}).get("determinism", {}).get("content_sha256")
    check("两次运行 content_sha256 一致（确定性）", bool(ha) and ha == hb)
    check("contract 字段全部回填",
          all(k in (ra or {}) for k in ("title", "abstract", "keywords", "target_file")))
    check("inventory 产出候选事实", os.path.isfile(os.path.join(work_a, "extracted_facts.json")))

    # Phase 2: empty input -> insufficient_evidence, valid JSON.
    empty = os.path.join(base, "empty_in")
    os.makedirs(empty, exist_ok=True)
    _write(os.path.join(empty, "case.json"), '{"case_id":"empty-1","output_contract":{"title":"t"}}')
    run_skeleton(empty, os.path.join(base, "empty_out"), os.path.join(base, "empty_work"))
    re_, _ = load_json_safe(os.path.join(base, "empty_out", "result.json"))
    check("空材料 -> insufficient_evidence", (re_ or {}).get("status") == "insufficient_evidence")

    # Phase 3: bad JSON -> input_error, valid JSON.
    bad = os.path.join(base, "bad_in")
    os.makedirs(bad, exist_ok=True)
    _write(os.path.join(bad, "case.json"), "{not valid json")
    run_skeleton(bad, os.path.join(base, "bad_out"), os.path.join(base, "bad_work"))
    rb_, _ = load_json_safe(os.path.join(base, "bad_out", "result.json"))
    check("坏 JSON -> input_error 且不崩溃", (rb_ or {}).get("status") == "input_error")

    if work_root is None:
        try:
            shutil.rmtree(base, ignore_errors=True)
        except OSError:
            pass
    return ok, lines
