#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence.py — P1/P2 取料与证据脚手架：材料归一、定位切分、证据原子抽取。

职责（Agent 调用）
  - 把 /input 下材料（txt/md/json/csv；PDF 用 pdftotext 兜底）统一转成
    /app/work/materials/<name>.txt，并保留相对路径来源标识。
  - 按 S{section}.P{para}.s{sentence} 定位切分；无章节时退化为 P{para}.s{sentence}。
  - 抽取数字（含单位）、术语、引用标记、DOI 等“证据原子”，带 60 字符原文上下文。
  - 写出 /app/work/analysis.json（定位清单与证据原子）与
    /app/work/evidence_template.json（论断—证据表模板，Agent 填表）。
  - 写出 /app/output/evidence_writing.md 三段交付模板（论断清单/证据表/成稿），
    保证任意时刻都存在可编辑产物；模板明确标注为模板，不冒充正式结果。

注意：本脚本只做确定性抽取与定位，不生成论断、不给证据强度定级；
     那些必须由 Agent 依据实际读到的材料完成，且逐条指回 locator+quote。
只依赖 Python 标准库；pdftotext 缺失时记录降级，不崩溃。
"""
import argparse
import json
import os
import re
import subprocess
import sys

MAX_FILE_BYTES = 2 * 1024 * 1024
TEXT_EXT = {".txt", ".md", ".markdown", ".rst", ".tex", ".csv", ".tsv", ".json", ".xml", ".html"}
PDF_EXT = ".pdf"
DOCX_EXT = ".docx"

SENT_SPLIT = re.compile(
    r"(?<=[。！？!?])\s*|(?<=\.)\s+(?=[A-Z0-9])|(?<=;)\s*|\n+"
)
SECTION_RE = re.compile(
    r"^\s*(#{1,6}\s+|\d+(?:\.\d+)*[\.、)\s]+"
    r"|\b(?:abstract|introduction|background|methods?|materials?\b"
    r"|results?|discussion|conclusions?|limitations?|references|acknowledg"
    r"|appendix|supplement\w*|摘要|引言|前言|方法|材料|结果|讨论|结论|局限"
    r"|参考文献|致谢|附录)\s*[:：]?)",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(
    r"(?<![\w.])[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\b[-+]?\d+(?:\.\d+)?"
    r"(?:\s*(?:%|‰|mg|kg|μg|ug|ng|pg|g|ml|l|μl|ul|nl|pl|mm|cm|nm|µm|μm|"
    r"Hz|kHz|MHz|kDa|Da|°C|℃|°F|min|h|hr|s|years?|yrs?|days?|d|weeks?|wks?|"
    r"months?|mo|patients?|subjects?|participants?|samples?|cells?|mice|"
    r"±\s*\d+(?:\.\d+)?|×\s*\d+|CI|95\s*%|OR|RR|HR|HR\b|p\s*[<>=]\s*0?\.?\d+)?)"
)
CITE_RE = re.compile(
    r"\[\d+(?:\s*[-,–]\s*\d+)*\]"
    r"|\([A-Z][A-Za-zÀ-ÿ'\-]+(?:\s+(?:et\s+al\.?|and|&|、)\s*[A-Z][A-Za-zÀ-ÿ'\-]+)?,\s*\d{4}[a-z]?\)"
    r"|\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+"
)


def read_text_bytes(path, max_bytes=MAX_FILE_BYTES):
    try:
        with open(path, "rb") as f:
            raw = f.read(max_bytes)
    except OSError as e:
        return None, False, None, "read_error: %s" % e
    truncated = len(raw) >= max_bytes
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), truncated, enc, None
        except UnicodeDecodeError:
            continue
    return None, truncated, None, "undecodable_text"


def pdf_to_text(pdf_path, out_txt):
    try:
        r = subprocess.run(
            ["pdftotext", "-layout", pdf_path, out_txt],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
    except FileNotFoundError:
        return False, "pdftotext_missing"
    except Exception as e:
        return False, "pdftotext_error: %s" % e
    if r.returncode != 0 or not os.path.isfile(out_txt) or os.path.getsize(out_txt) == 0:
        return False, "pdftotext_failed_rc=%s" % r.returncode
    return True, None


def is_task_meta(rel):
    """case.json / task.md 是任务说明，不是证据材料。"""
    base = os.path.basename(rel).lower()
    return base in ("case.json", "task.md")


def normalize_material(path, rel, work):
    """把单个材料转成 UTF-8 文本，写入 work/materials/。返回 (txt, err, note)。"""
    os.makedirs(os.path.join(work, "materials"), exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]", "_", rel)
    out_txt = os.path.join(work, "materials", stem + ".txt")
    ext = os.path.splitext(path)[1].lower()
    note = None

    if ext == PDF_EXT:
        ok, err = pdf_to_text(path, out_txt)
        if not ok:
            return None, err, None
        txt, truncated, enc, derr = read_text_bytes(out_txt)
        return txt, derr, "pdf_via_pdftotext" + ("_truncated" if truncated else "")

    if ext in TEXT_EXT:
        txt, truncated, enc, derr = read_text_bytes(path)
        if derr:
            return None, derr, None
        try:
            with open(out_txt, "w", encoding="utf-8") as f:
                f.write(txt or "")
        except OSError as e:
            return txt, "write_material_error: %s" % e, None
        note = "text(%s)" % enc + ("_truncated" if truncated else "")
        return txt, None, note

    return None, "unsupported_ext:%s" % ext, None


def split_sentences(paragraph):
    parts = re.split(SENT_SPLIT, paragraph)
    out = []
    for p in parts:
        p = p.strip()
        if p:
            out.append(p)
    return out


def segment(text, source_doc):
    """切分为带 locator 的句子列表。"""
    lines = text.split("\n") if text else []
    paras = []          # (section, para_index, para_text)
    cur_section = "S1"
    para_idx = 1
    buf = []
    for ln in lines:
        if SECTION_RE.match(ln) and len(ln.strip()) <= 120:
            if buf:
                paras.append((cur_section, para_idx, "\n".join(buf)))
                para_idx += 1
                buf = []
            cur_section = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", ln.strip())[:40] or cur_section
            cur_section = "S%d.%s" % (para_idx, cur_section)
            continue
        if ln.strip() == "" and buf:
            paras.append((cur_section, para_idx, "\n".join(buf)))
            para_idx += 1
            buf = []
        else:
            buf.append(ln)
    if buf:
        paras.append((cur_section, para_idx, "\n".join(buf)))

    sentences = []
    for section, pidx, ptext in paras:
        sents = split_sentences(ptext)
        for si, s in enumerate(sents, 1):
            sentences.append({
                "source_doc": source_doc,
                "locator": "S%s.P%d.s%d" % (section, pidx, si),
                "section": section,
                "paragraph": pidx,
                "sentence": si,
                "text": s,
            })
    return sentences


def ctx(s, m):
    a = max(0, m.start() - 40)
    b = min(len(s), m.end() + 40)
    return s[a:b]


def extract_atoms(sentences):
    atoms = []
    for st in sentences:
        for m in NUMBER_RE.finditer(st["text"]):
            atoms.append({"type": "number", "value": m.group(0),
                          "locator": st["locator"], "source_doc": st["source_doc"],
                          "quote": ctx(st["text"], m)})
        for m in CITE_RE.finditer(st["text"]):
            atoms.append({"type": "citation", "value": m.group(0),
                          "locator": st["locator"], "source_doc": st["source_doc"],
                          "quote": ctx(st["text"], m)})
    return atoms


def evidence_template():
    return {
        "_comment": "论断—证据表模板（示例，非正式案例结果）。Agent 依据实际材料填表，"
                    "每行必须指回 locator+逐字 quote；无据可循的行不得保留。",
        "claims": [
            {
                "claim_id": "C1",
                "claim_text": None,
                "source_ids": [],
                "wording_grade": None,
                "uncertainty_ids": [],
                "verdict": None
            }
        ],
        "sources": [
            {
                "id": "S1",
                "key": None,
                "value": None,
                "kind": "number|term|citation|figure|claim",
                "source_doc": None,
                "locator": None,
                "quote": None,
                "support_scope": "full|partial|weak|conflict|none",
                "strength": "S1|S2|S3|S4",
                "design": None,
                "sample_size": None,
                "change": None
            }
        ]
    }


def write_manuscript_template(out_dir):
    md = """# 文献证据驱动写作：三段交付（模板）

> 说明：本文件为运行模板，内容由 Agent 依据 /input 材料填写的实际结果替换。
> 未替换前不构成正式案例结果；空白处不得虚构。

## 1. 论断清单（Claim List）
| 论断编号 | 论断原文（应可直接判定真伪） | 支撑来源编号 | 强度级 | 表述是否与强度相称 |
|-|-|-|-|-|
| C1 | （待填） | S1 | S? | 待核 |

## 2. 证据表（Evidence Map）
| 来源编号 | 键 | 值 | 类型 | 来源文档 | 定位 | 原文摘录（逐字） | 支撑范围 | 证据强度 | 研究设计 | 样本量 |
|-|-|-|-|-|-|-|-|-|-|-|
| S1 | （待填） | （待填） | number/term/citation/figure/claim | （待填） | S?.P?.s? | （逐字引文） | full/partial/weak/conflict/none | S1–S4 | （待填） | （待填） |

## 3. 成稿（Manuscript）
（待写：满足题目指定体裁、受众与格式；正文每句实质性论断以 [C#] 或 [S#] 指回上文，
不新增材料外事实。）

## 4. 不确定性、人工确认项与材料缺口
（待填：uncertainty / pending_human / limitations 的对应条目。）
"""
    p = os.path.join(out_dir, "evidence_writing.md")
    if not os.path.isfile(p):
        with open(p, "w", encoding="utf-8") as f:
            f.write(md)
    return p


def main():
    ap = argparse.ArgumentParser(description="P1/P2 取料与证据脚手架")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--materials", nargs="*", default=None,
                    help="可选：显式材料路径（覆盖自动发现）")
    a = ap.parse_args()

    os.makedirs(a.output, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)

    # 复用 scaffold 的发现结果，或自行发现。
    disc_path = os.path.join(a.work, "discovery.json")
    inventory, errors = [], []
    if os.path.isfile(disc_path):
        try:
            with open(disc_path, "r", encoding="utf-8") as f:
                disc = json.load(f)
            inventory = disc.get("inventory", [])
            errors = list(disc.get("errors", []))
        except (OSError, json.JSONDecodeError):
            inventory, errors = [], []
    # 无论来自发现结果还是自动发现，都排除 case.json / task.md。
    inventory = [it for it in inventory if not is_task_meta(it.get("rel", ""))]

    if not inventory:
        for root, dirs, files in os.walk(a.input):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in sorted(files):
                p = os.path.join(root, fn)
                rel = os.path.relpath(p, a.input)
                if is_task_meta(rel):
                    continue
                inventory.append({"path": p, "rel": rel,
                                  "size": os.path.getsize(p), "over_limit": False})

    # 显式材料优先。
    if a.materials:
        inventory = [{"path": p, "rel": os.path.basename(p), "size": -1, "over_limit": False}
                     for p in a.materials]

    sentences, atoms, mats, degrades = [], [], [], []
    for item in inventory:
        if item.get("over_limit"):
            degrades.append({"kind": "file_over_limit", "file": item["rel"]})
            continue
        if not os.path.isfile(item["path"]):
            degrades.append({"kind": "missing_file", "file": item["rel"]})
            continue
        txt, err, note = normalize_material(item["path"], item["rel"], a.work)
        if err:
            degrades.append({"kind": "material_error", "file": item["rel"], "detail": err})
            continue
        mats.append({"source_doc": item["rel"], "note": note, "chars": len(txt or "")})
        if txt:
            sents = segment(txt, item["rel"])
            sentences.extend(sents)
            atoms.extend(extract_atoms(sents))

    analysis = {
        "materials": mats,
        "degradations": degrades,
        "sentence_count": len(sentences),
        "sentences": sentences,
        "atoms": atoms,
        "numbers": [a for a in atoms if a["type"] == "number"],
        "citations": [a for a in atoms if a["type"] == "citation"],
    }
    with open(os.path.join(a.work, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)
    with open(os.path.join(a.work, "evidence_template.json"), "w", encoding="utf-8") as f:
        json.dump(evidence_template(), f, ensure_ascii=False, indent=2)

    artifact = write_manuscript_template(a.output)

    print(json.dumps({
        "ok": True,
        "materials_loaded": len(mats),
        "sentences": len(sentences),
        "numbers": len(analysis["numbers"]),
        "citations": len(analysis["citations"]),
        "degradations": degrades,
        "artifact": artifact,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
