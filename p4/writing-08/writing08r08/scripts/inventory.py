#!/usr/bin/env python3
"""inventory.py — 执行流程第 1 步：扫描 /input，分类材料并抽取候选事实。

产出 /app/work/materials_inventory.json 与 extracted_facts.json（带
locator + 逐字 quote），并更新 state.json。候选事实供第 2 步核实，
本脚本只做"候选抽取"，不擅自认定结论。

只依赖 Python 标准库；单文件 2MB 上限；确定性执行。
"""
import argparse
import json
import os
import re
import subprocess
import sys

MAX_FILE_BYTES = 2 * 1024 * 1024
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

# 文件名/扩展名分类
TEXT_EXTS = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".csv", ".tsv",
             ".log", ".out", ".sh", ".rst", ".tex", ".bib", ".cfg", ".ini",
             ".toml", ".xml", ".html", ".ipynb"}
OFFICE_EXTS = {".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".tif", ".tiff"}

# 候选事实正则（仅抽取候选，供后续核实，不据此下结论）
RE_NUM_UNIT = re.compile(
    r'-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\s*(?:%|[A-Za-zμµ°][A-Za-z/]*|h|min|s|GB|MB|KB|GiB|MiB|epochs?|layers?|dims?|seeds?)')
RE_VERSION = re.compile(r'\b(?:[vV]?\d+\.\d+(?:\.\d+)?(?:[-_.][0-9A-Za-z]+)?)\b')
RE_SEED = re.compile(r'(?:seed|random_state|torch\.manual_seed|np\.random\.seed)\s*[=:]\s*(\d+)',
                     re.IGNORECASE)
RE_KV = re.compile(r'\b([A-Za-z_][A-Za-z0-9_\-\.]*)\s*[=:]\s*("[^"\n]{0,80}"|\'[^\'\n]{0,80}\'|[^\s,;}\n]{0,60})')
RE_PATH = re.compile(r'(?:[A-Za-z]:\\[^\s"\n]+|(?:\.{0,2}/)?[\w\-\.]+/[\w\-\./]+\.\w{1,5}|/[\w\-\./]{2,})')
RE_COMMAND = re.compile(r'^\s*(?:python3?|pip\d?|conda|git|bash|sh|docker)\s+[^\n]{0,200}', re.MULTILINE)
RE_DATASET = re.compile(r'\b(?:dataset|data_name|corpus|benchmark|data_dir)\s*[=:]\s*["\']?([A-Za-z0-9_\-\./]+)',
                        re.IGNORECASE)

CATEGORY_KEYWORDS = {
    "method_steps": ("step", "procedure", "输入", "输出", "操作", "train", "infer", "evaluate",
                     "预处理", "preprocess", "流程", "pipeline", "算法", "algorithm"),
    "experiment_setup": ("gpu", "cpu", "memory", "硬件", "环境", "python", "pytorch", "tensorflow",
                         "version", "seed", "epoch", "batch", "learning_rate", "lr", "optimizer",
                         "重复", "repetition", "replicate"),
    "parameters": ("param", "参数", "lr", "epoch", "batch", "dropout", "weight_decay", "temperature",
                   "threshold", "dim", "hidden"),
    "dataset_split": ("dataset", "corpus", "train", "val", "valid", "test", "split", "ratio",
                      "数据集", "划分", "规模", "sample", "预处理"),
    "baseline_control": ("baseline", "对照", "compare", "vs", "sota", "metric", "accuracy", "f1",
                         "auc", "bleu", "rouge", "统计", "signif", "budget", "基线"),
    "reproducibility_limits": ("repo", "github", "docker", "environment", "requirements", "license",
                               "download", "command", "复现", "限制", "limit", "assumption", "假设"),
}


def read_text(path):
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None
    return None


def read_bytes(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def try_pdftotext(path):
    """尽力用 pdftotext 抽取 PDF；不可用或失败返回 None，不崩溃。"""
    for cmd in ("pdftotext",):
        try:
            proc = subprocess.run([cmd, path, "-"], capture_output=True, timeout=30)
            if proc.returncode == 0 and proc.stdout:
                return proc.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            return None
    return None


def classify(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in TEXT_EXTS:
        return "text"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in IMAGE_EXTS:
        return "image"
    return "other"


def categorize(line):
    low = line.lower()
    hits = []
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(k in low for k in kws):
            hits.append(cat)
    return hits or ["unclassified"]


def extract_facts(text, relpath):
    """从单文件文本抽取候选事实，返回带 locator + quote 的列表。"""
    facts = []
    lines = text.splitlines()
    for idx, line in enumerate(lines, 1):
        locator = "%s#L%d" % (relpath, idx)
        stripped = line.strip()
        if not stripped:
            continue
        # 数值 + 单位
        for m in RE_NUM_UNIT.finditer(stripped):
            facts.append({"type": "number_unit", "locator": locator,
                          "quote": stripped[:200], "value": m.group(0)})
        # 版本号
        for m in RE_VERSION.finditer(stripped):
            facts.append({"type": "version", "locator": locator,
                          "quote": stripped[:200], "value": m.group(0)})
        # 随机种子
        for m in RE_SEED.finditer(stripped):
            facts.append({"type": "seed", "locator": locator,
                          "quote": stripped[:200], "value": m.group(1)})
        # 键值对（超参）
        for m in RE_KV.finditer(stripped):
            facts.append({"type": "kv", "locator": locator,
                          "quote": stripped[:200], "key": m.group(1),
                          "value": m.group(2).strip('"\'')})
        # 路径
        for m in RE_PATH.finditer(stripped):
            facts.append({"type": "path", "locator": locator,
                          "quote": stripped[:200], "value": m.group(0)})
        # 命令
        for m in RE_COMMAND.finditer(stripped):
            facts.append({"type": "command", "locator": locator,
                          "quote": stripped[:200], "value": m.group(0).strip()})
        # 数据集名
        for m in RE_DATASET.finditer(stripped):
            facts.append({"type": "dataset", "locator": locator,
                          "quote": stripped[:200], "value": m.group(1)})
        # 六类要素归类（候选，非结论）
        for cat in categorize(stripped):
            facts.append({"type": "category_hint", "locator": locator,
                          "quote": stripped[:200], "category": cat})
    return facts


def detect_conflicts(facts):
    """按 kv 键聚合候选，找出同键不同值的潜在冲突（供第 2 步核实）。"""
    by_key = {}
    for f in facts:
        if f.get("type") == "kv":
            key = f["key"].lower()
            by_key.setdefault(key, set()).add((f["value"], f["locator"]))
    conflicts = []
    for key, vals in by_key.items():
        if len({v for v, _ in vals}) > 1:
            conflicts.append({"key": key,
                              "seen": [{"value": v, "locator": loc} for v, loc in sorted(vals)]})
    return conflicts


def main(argv=None):
    ap = argparse.ArgumentParser(description="扫描输入目录并抽取候选事实")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    os.makedirs(args.work, exist_ok=True)

    inventory = {"files": [], "errors": [], "degradations": []}
    facts = []

    if not os.path.isdir(args.input):
        inventory["errors"].append("输入目录不存在: %s" % args.input)
    else:
        for root, _dirs, files in os.walk(args.input):
            for name in sorted(files):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, args.input).replace(os.sep, "/")
                size = 0
                try:
                    size = os.path.getsize(full)
                except OSError:
                    pass
                kind = classify(name)
                entry = {"path": rel, "kind": kind, "size": size}
                if size > MAX_FILE_BYTES:
                    entry["skipped"] = "size_over_2MB"
                    inventory["degradations"].append(
                        "跳过超限文件 %s (%d bytes)" % (rel, size))
                    inventory["files"].append(entry)
                    continue
                if kind == "text":
                    text = read_text(full)
                    if text is None:
                        entry["skipped"] = "undecodable"
                        inventory["degradations"].append("无法解码文件 %s" % rel)
                    else:
                        facts.extend(extract_facts(text, "materials/" + rel))
                elif kind == "pdf":
                    text = try_pdftotext(full)
                    if text is None:
                        entry["skipped"] = "pdf_unparsed"
                        inventory["degradations"].append(
                            "PDF 未提取文本，改由 pdf-text-tool/ocr-tool 处理: %s" % rel)
                    else:
                        facts.extend(extract_facts(text, "materials/" + rel))
                elif kind == "office":
                    entry["skipped"] = "office_unparsed"
                    inventory["degradations"].append(
                        "Office 文档未提取，改由 office-renderer 处理: %s" % rel)
                elif kind == "image":
                    entry["skipped"] = "image_unparsed"
                    inventory["degradations"].append(
                        "图像未提取，改由 ocr-tool 处理: %s" % rel)
                else:
                    entry["skipped"] = "other_binary"
                inventory["files"].append(entry)

    conflicts = detect_conflicts(facts)

    with open(os.path.join(args.work, "materials_inventory.json"), "w", encoding="utf-8") as fh:
        json.dump(inventory, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(args.work, "extracted_facts.json"), "w", encoding="utf-8") as fh:
        json.dump({"facts": facts, "conflicts": conflicts}, fh, ensure_ascii=False, indent=2)

    # 更新 state.json（若骨架已生成）
    state_path = os.path.join(args.work, "state.json")
    state = {}
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        except (ValueError, OSError):
            state = {}
    state["material_file_count"] = len(inventory["files"])
    state["candidate_fact_count"] = len(facts)
    state["candidate_conflict_count"] = len(conflicts)
    state["inventory_errors"] = inventory["errors"]
    state["inventory_degradations"] = inventory["degradations"]
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)

    print(json.dumps({
        "ok": True,
        "files": len(inventory["files"]),
        "facts": len(facts),
        "conflicts": len(conflicts),
        "degradations": len(inventory["degradations"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
