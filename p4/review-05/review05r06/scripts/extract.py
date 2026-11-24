#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic evidence extraction for the source-bound compliance audit.

Reads every material under the input root, parses PDFs to text when a PDF text
tool is available, and writes a stable evidence ledger to <work>/evidence.json:

  files       discovered inputs with role/size/parse status
  paragraphs  stable paragraph index (id, file, locator, length, text)
  facets      candidate observations per facet (id, facet, trigger, locator,
              quote, match). A trigger is a *candidate*, never a verdict:
              adjudication is performed later by the audit agent.

Standard library only. Identical input produces identical output (stable IDs,
stable ordering), so a re-run yields the same evidence basis.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".tex", ".csv", ".tsv", ".html",
    ".xml", ".json", ".jsonl", ".yaml", ".yml", ".org",
}
PDF_SUFFIX = ".pdf"
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_PARAGRAPH_CHARS = 1600
MAX_PARAGRAPHS = 8000
MAX_OBS_PER_FACET = 80
MAX_QUOTE_CHARS = 240

# ---------------------------------------------------------------------------
# Facet trigger rules. Each rule points the audit agent at locations worth
# adjudicating. Verdicts (fact_error / insufficient_evidence / norm_violation /
# suggestion) are made by the agent and recorded in findings.json.
# ---------------------------------------------------------------------------
FACET_RULES = [
    {
        "facet": "academic_integrity",
        "label": "学术诚信",
        "patterns": [
            ("conflict_interest", r"利益冲突|conflict\s+of\s+interest|competing\s+interest"),
            ("funding", r"基金|资助|funding|grant\s+(?:number|no\.?|编号)|acknowledg"),
            ("authorship", r"作者贡献|author\s+contribution|contributed\s+equally|通讯作者|corresponding\s+author"),
            ("reuse", r"重复发表|一稿多投|duplicate\s+publication|数据复用|reuse|repurpos"),
            ("data_integrity", r"图像|image\s+analysis|原始数据|raw\s+data|data\s+availability|数据可(?:得|获取)"),
        ],
    },
    {
        "facet": "citation",
        "label": "引用规范",
        "patterns": [
            ("numeric_cite", r"\[\d+(?:\s*[-,，]\s*\d+)*\]"),
            ("author_year", r"\([A-Z][A-Za-z\u00C0-\u024F'’\-]+(?:\s+(?:et\s+al\.?|and|&))?\s*,?\s*(?:19|20)\d{2}[a-z]?"),
            ("ref_section", r"参考文献|references|bibliography|works\s+cited"),
            ("doi", r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+"),
        ],
    },
    {
        "facet": "privacy",
        "label": "隐私与数据合规",
        "patterns": [
            ("email", r"[\w.+-]+@[\w-]+\.[\w.-]+"),
            ("phone_cn", r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)"),
            ("id_cn", r"\b\d{17}[\dXx]\b"),
            ("person_field", r"姓名|身份证|家庭住址|电话号码|出生日期|患者编号|participant\s+(?:id|number)|patient\s+(?:id|number)"),
            ("restricted", r"未公开|机密|confidential|内部数据|restricted|脱敏|anonymi[sz]|de-identif"),
        ],
    },
    {
        "facet": "copyright",
        "label": "版权与许可",
        "patterns": [
            ("figure_reuse", r"(?:reproduced|adapted|modified)\s+(?:from|after)|转自|来源[:：]|引自"),
            ("license", r"creative\s+commons|CC[- ]?BY|许可|版权|copyright|©|\(c\)|license"),
            ("code_data", r"code\s+availability|data\s+availability|软件许可|开源|open\s+source|github\.com"),
        ],
    },
    {
        "facet": "ethics",
        "label": "伦理审查",
        "patterns": [
            ("consent", r"知情同意|informed\s+consent|consent\s+(?:form|was\s+obtained)"),
            ("approval", r"伦理委员会|ethics\s+(?:committee|board|approval)|IRB|IACUC|批件|approval\s+number"),
            ("animal", r"动物实验|animal\s+(?:experiment|study)|ARRIVE|3R\s+principle"),
            ("human_subject", r"人[体群]\S*研究|human\s+subject|participants?|受试者|recruit"),
        ],
    },
    {
        "facet": "submission",
        "label": "投稿要求",
        "patterns": [
            ("word_count", r"字数|word\s+count|字符数"),
            ("keywords", r"关键词|keywords?\s*[:：]"),
            ("abstract", r"摘要|abstract\s*[:：]?"),
            ("affiliation", r"作者单位|affiliation|通讯地址|corresponding\s+author"),
        ],
    },
    {
        "facet": "quality",
        "label": "通用质量",
        "patterns": [
            ("structure", r"引言|方法|结果|讨论|结论|introduction|methods?|results?|discussion|conclusion"),
            ("abbreviation", r"\b[A-Z]{2,10}\s*\([^)]{2,80}\)"),
        ],
    },
    {
        "facet": "fact",
        "label": "事实核查",
        "patterns": [
            ("numeric_claim", r"(?:\b(?:19|20)\d{2}\b|\b\d+(?:\.\d+)?\s*%|\b\d+(?:\.\d+)?\s*(?:mg|g|ml|mol|µg|nm|mm|cm|kg|s|min|h|day|year)s?\b)"),
            ("self_reference", r"我们先前|our\s+(?:previous|prior)\s+(?:work|study)|课题组此前"),
        ],
    },
    {
        "facet": "method",
        "label": "方法完整性",
        "patterns": [
            ("method_section", r"材料与方法|方法学|实验设计|protocol|experimental\s+design|方法"),
            ("sample_size", r"样本量|sample\s+size|\bn\s*=\s*\d+"),
            ("replicate", r"重复[次数组]?|replicate|重复实验"),
        ],
    },
    {
        "facet": "statistics",
        "label": "统计报告",
        "patterns": [
            ("p_value", r"\b[pP]\s*[<>=]\s*0?\.\d+|\bp\s*=\s*0?\.\d+"),
            ("ci", r"95\s*%\s*CI|confidence\s+interval|置信区间|可信区间"),
            ("sd_sem", r"\b(?:SD|SEM|SE)\b|标准差|标准误"),
            ("test", r"\bt\s*[-–]?\s*test\b|ANOVA|方差分析|卡方|chi[\s-]*square|回归|regression|校正|correction"),
        ],
    },
    {
        "facet": "logic",
        "label": "逻辑与论证链",
        "patterns": [
            ("connective", r"因此|然而|但是|此外|综上|结论|therefore|however|moreover|in\s+conclusion|thus|hence"),
            ("claim", r"表明|证实|证明|发现|suggest|demonstrate|indicate|confirm"),
            ("hedge", r"可能|或许|似乎|大概|约\s*\d|possibly|probably|likely|approximately"),
        ],
    },
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def normalize(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", text).strip()


def read_json_robust(path: Path):
    """Return (obj, error). Tries several encodings and degrades gracefully."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, str(exc)
    last = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            return json.loads(text), None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            last = str(exc)
    return None, last or "cannot decode"


def read_text_robust(path: Path, max_bytes: int = MAX_TEXT_BYTES):
    """Return (text, error)."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return "", str(exc)
    if size > max_bytes:
        return "", f"file exceeds {max_bytes} bytes, skipped"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return "", str(exc)
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), None


def discover(inp: Path):
    """Classify files under the input root. Deterministic ordering."""
    items = []
    if not inp.exists():
        return items
    for p in sorted(inp.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        name = p.name.lower()
        if name == "case.json" or (name.startswith("case") and name.endswith(".json")):
            role = "case"
        elif name == "task.md" or (name.startswith("task") and name.endswith(".md")):
            role = "task"
        elif p.suffix.lower() in TEXT_SUFFIXES:
            role = "material"
        elif p.suffix.lower() == PDF_SUFFIX:
            role = "pdf"
        else:
            role = "artifact"
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        items.append({"path": p, "name": p.name, "role": role, "size": size})
    return items


def extract_pdf(pdf_path: Path, parsed_dir: Path):
    """Return (txt_path_or_None, error_or_None).

    Writes output to a file and does not capture stdout/stderr, so it also
    works in sandboxes where named-pipe capture is unavailable.
    """
    out_path = parsed_dir / (pdf_path.stem + ".txt")
    candidates = [
        ["pdftotext", "-layout", str(pdf_path), str(out_path)],
        ["pdftotext", str(pdf_path), str(out_path)],
        ["pdf-text-tool", "--input", str(pdf_path), "--output", str(out_path)],
    ]
    last_err = None
    for cmd in candidates:
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120
            )
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            last_err = f"timeout: {cmd[0]}"
            continue
        if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
            return out_path, None
        last_err = f"{cmd[0]} exited {proc.returncode}"
    return None, (last_err or "no PDF text tool available")


def build_paragraphs(text: str, rel_path: str, start_index: int = 1):
    """Split text into stable, bounded paragraphs with globally unique IDs."""
    paragraphs = []
    blocks = re.split(r"\n\s*\n", text)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if len(block) <= MAX_PARAGRAPH_CHARS:
            chunks = [block]
        else:
            sentences = re.split(r"(?<=[。！？.!?；;])\s*", block)
            chunks, cur = [], ""
            for sentence in sentences:
                if not sentence.strip():
                    continue
                if len(cur) + len(sentence) > MAX_PARAGRAPH_CHARS and cur:
                    chunks.append(cur)
                    cur = sentence
                else:
                    cur += sentence
            if cur:
                chunks.append(cur)
            final = []
            for chunk in chunks:
                while len(chunk) > MAX_PARAGRAPH_CHARS:
                    final.append(chunk[:MAX_PARAGRAPH_CHARS])
                    chunk = chunk[MAX_PARAGRAPH_CHARS:]
                if chunk:
                    final.append(chunk)
            chunks = final
        for chunk in chunks:
            pid = f"P{start_index + len(paragraphs):05d}"
            paragraphs.append({
                "id": pid,
                "file": rel_path,
                "locator": f"{rel_path}#{pid}",
                "length": len(chunk),
                "text": chunk,
            })
        if len(paragraphs) >= MAX_PARAGRAPHS:
            break
    return paragraphs


def scan_facets(paragraphs):
    """Produce candidate observations for each facet (no verdicts)."""
    facets = []
    for rule in FACET_RULES:
        observations = []
        for key, pat in rule["patterns"]:
            rx = re.compile(pat)
            for para in paragraphs:
                if len(observations) >= MAX_OBS_PER_FACET:
                    break
                for match in rx.finditer(para["text"]):
                    if len(observations) >= MAX_OBS_PER_FACET:
                        break
                    start = max(0, match.start() - 60)
                    end = min(len(para["text"]), match.end() + 60)
                    quote = normalize(para["text"][start:end])
                    observations.append({
                        "id": f"O-{rule['facet']}-{len(observations) + 1:04d}",
                        "facet": rule["facet"],
                        "trigger": key,
                        "locator": para["locator"],
                        "quote": quote[:MAX_QUOTE_CHARS],
                        "match": match.group(0)[:120],
                    })
                if len(observations) >= MAX_OBS_PER_FACET:
                    break
        facets.append({
            "facet": rule["facet"],
            "label": rule["label"],
            "observations": observations,
        })
    return facets


def build_evidence(inp: Path, work: Path):
    """Discover, parse, index and scan; return the evidence ledger dict."""
    files = discover(inp)
    parsed_dir = work / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    errors, degradations, paragraphs, file_records = [], [], [], []
    for item in files:
        rel = item["path"].relative_to(inp).as_posix()
        record = {
            "path": rel,
            "name": item["name"],
            "role": item["role"],
            "size": item["size"],
            "chars": 0,
            "paragraph_count": 0,
            "parsed": False,
            "parsed_path": None,
        }
        text = None
        if item["role"] == "pdf":
            txt_path, err = extract_pdf(item["path"], parsed_dir)
            if txt_path is not None:
                text, rerr = read_text_robust(txt_path)
                if rerr:
                    errors.append({"file": rel, "error": rerr})
                    text = None
                else:
                    record["parsed"] = True
                    record["parsed_path"] = txt_path.as_posix()
            else:
                degradations.append({"file": rel, "issue": err})
        elif item["role"] in ("material", "task"):
            text, rerr = read_text_robust(item["path"])
            if rerr:
                errors.append({"file": rel, "error": rerr})
                text = None
            else:
                record["parsed"] = True
        if text:
            record["chars"] = len(text)
            paras = build_paragraphs(text, rel, start_index=len(paragraphs) + 1)
            record["paragraph_count"] = len(paras)
            paragraphs.extend(paras)
        file_records.append(record)
    return {
        "generated_at": utcnow(),
        "files": file_records,
        "paragraphs": paragraphs,
        "facets": scan_facets(paragraphs),
        "errors": errors,
        "degradations": degradations,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/input")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args(argv)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    evidence = build_evidence(Path(args.root), work)
    atomic_write_json(work / "evidence.json", evidence)
    summary = {
        "status": "ok",
        "files": len(evidence["files"]),
        "paragraphs": len(evidence["paragraphs"]),
        "facets": len(evidence["facets"]),
        "errors": len(evidence["errors"]),
        "degradations": len(evidence["degradations"]),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
