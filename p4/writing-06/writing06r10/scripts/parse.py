#!/usr/bin/env python3
"""Build a three-level document model for a manuscript and emit deterministic
structural signals (claim / causal / correlation / hedge / contribution
sentences and suspected duplicate paragraph pairs) that the model must verify
against the source text before writing any diagnosis.

Pure Python standard library only. Locators are stable across repeated runs.
"""
import argparse
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

MAX_FILE_BYTES = 2 * 1024 * 1024  # per-file size cap; larger files are skipped
QUOTE_LIMIT = 240                 # max verbatim quote length stored per anchor
MAX_SIGNALS = 600                 # cap on total candidate signals to keep model small
MAX_DUP_PAIRS = 80

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")

TEXT_EXTS = {".md", ".markdown", ".txt", ".tex", ".latex", ".org", ".rst"}
DATA_EXTS = {".csv", ".tsv", ".json", ".yaml", ".yml", ".xml"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp", ".svg"}
OFFICE_EXTS = {".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"}

TASK_NAMES = {"case.json", "task.md", "task.txt", "challenge.json"}

HEADING_LINE_RE = re.compile(
    r"^(#{1,6}\s+\S)"
    r"|^(\d{1,3}(\.\d{1,3}){0,4})[\.、)\s]+\S"
    r"|^(abstract|introduction|background|related\s+work|method|methods|methodology|"
    r"materials\s+and\s+methods|experimental\s+setup|experiments?|results?|findings|"
    r"discussion|limitations|implications|conclusion|conclusions|concluding\s+remarks|"
    r"summary|future\s+work|references|bibliography|acknowledg?ments?|"
    r"摘要|引言|绪论|背景|方法|材料与方法|实验|结果|讨论|结论|总结|展望|参考文献|致谢)\s*$",
    re.IGNORECASE,
)

ROLE_RULES = [
    ("abstract", re.compile(r"abstract|摘要", re.I)),
    ("introduction", re.compile(r"introduction|intro|background|related\s+work|引言|绪论|背景|研究现状|相关(研究|工作)", re.I)),
    ("methods", re.compile(r"method|methodology|materials\s+and\s+methods|experiment|implementation|algorithm|design|setup|方法|实验|试验|算法|设计|材料与方法", re.I)),
    ("results", re.compile(r"result|findings|结果|实证", re.I)),
    ("discussion", re.compile(r"discussion|limitation|implication|讨论|局限|启示|分析", re.I)),
    ("conclusion", re.compile(r"conclusion|concluding|summary|future\s+work|结论|总结|展望", re.I)),
    ("references", re.compile(r"references?|bibliography|参考文献", re.I)),
    ("acknowledgements", re.compile(r"acknowledg|致谢", re.I)),
]

SENT_SPLIT_RE = re.compile(r"(?<=[.!?。！？；;])(?:\s+|(?=[A-Z\u4e00-\u9fff]))")

CAUSAL_RE = re.compile(
    r"caus(e|ed|es|ing|al|ality)|due\s+to|because|lead(s|ing)?\s+to|result(s|ed)?\s+in|"
    r"induc(e|es|ed|ing)|driv(en|es|e)|trigger(s|ed)?|consequently|therefore|"
    r"导致|引起|致使|使得|由于|因此|从而|诱发|驱动|促成",
    re.I,
)
CORREL_RE = re.compile(
    r"correlat(e|ion|ional|ions|ed)|associat(e|ion|ions|ed)|related\s+to|linked\s+to|"
    r"connected\s+to|相关|关联|联系",
    re.I,
)
HEDGE_RE = re.compile(
    r"\b(may|might|could|possibly|likely|probably|suggest(s|ed)?|appears?\s+to|"
    r"seems?\s+to|tends?\s+to|indicates?\s+that)\b|"
    r"可能|或许|大概|似乎|表明|提示|倾向于",
    re.I,
)
CLAIM_RE = re.compile(
    r"we\s+found|we\s+show|we\s+demonstrate|our\s+results?\s+(show|demonstrate|indicate|reveal)|"
    r"this\s+(study|work|paper)\s+(shows?|demonstrates?|reveals?|confirms?|finds?)|"
    r"results?\s+(show|demonstrate|indicate|reveal|confirm|support)|"
    r"结果表明|结果显示|本(研究|文|工作)|我们(发现|证明|证实|提出)|证实|验证|表明",
    re.I,
)
CONTRIB_RE = re.compile(
    r"contribution|novel(t|ty)|first\s+to|this\s+paper\s+(proposes?|introduces?|presents?)|"
    r"we\s+(propose|introduce|present|develop|design)|"
    r"本文(提出|介绍|设计|开发)|本(研究|工作)(提出|设计|开发)|提出|贡献|首次|创新",
    re.I,
)


def read_text(path):
    """Return (text, encoding, degraded). Never raises on decode."""
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, False
        except UnicodeDecodeError:
            continue
        except OSError:
            return None, None, False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(), "utf-8", True
    except OSError:
        return None, None, False


def split_sentences(text):
    parts = SENT_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def normalize(text):
    t = re.sub(r"[\W_]+", " ", text.lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def token_bigrams(text):
    toks = normalize(text).split()
    return set(zip(toks, toks[1:]))


def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def classify_role(heading):
    for role, regex in ROLE_RULES:
        if regex.search(heading):
            return role
    return "body"


def split_sections(text):
    """Split raw text into sections using heading-like lines."""
    lines = text.splitlines()
    sections = []
    cur_heading = "(no headings)"
    cur_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and len(stripped) <= 120 and HEADING_LINE_RE.match(stripped):
            if cur_lines:
                sections.append((cur_heading, "\n".join(cur_lines)))
            cur_heading = stripped.lstrip("#").strip()
            cur_lines = []
        else:
            cur_lines.append(line)
    if cur_lines:
        sections.append((cur_heading, "\n".join(cur_lines)))
    return sections


def split_paragraphs(text):
    blocks = re.split(r"\n\s*\n", text)
    return [re.sub(r"\s+", " ", b).strip() for b in blocks if b.strip()]


def build_document_model(input_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    inventory = []
    documents = []
    unreadable = []
    signals = []

    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, input_dir).replace(os.sep, "/")
            if name.lower() in TASK_NAMES:
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                size = -1
            ext = os.path.splitext(name)[1].lower()
            if size > MAX_FILE_BYTES:
                unreadable.append({"file": rel, "reason": "oversized", "size": size})
                inventory.append({"file": rel, "kind": "oversized", "size": size})
                continue

            if ext in TEXT_EXTS:
                text, enc, degraded = read_text(path)
                if text is None:
                    unreadable.append({"file": rel, "reason": "unreadable"})
                    inventory.append({"file": rel, "kind": "text", "readable": False})
                    continue
                inventory.append({"file": rel, "kind": "text", "size": size, "encoding": enc, "degraded": degraded})
                if degraded:
                    unreadable.append({"file": rel, "reason": "encoding_fallback", "encoding": enc})
                doc = {"file": rel, "kind": "text", "sections": []}
                for sec_idx, (heading, body) in enumerate(split_sections(text), start=1):
                    sec_id = "sec%d" % sec_idx
                    role = classify_role(heading)
                    section = {
                        "id": sec_id,
                        "heading": heading[:120],
                        "role": role,
                        "char_count": len(body),
                        "paragraphs": [],
                    }
                    for para_idx, para_text in enumerate(split_paragraphs(body), start=1):
                        sents = split_sentences(para_text)
                        para_id = "%s.para%d" % (sec_id, para_idx)
                        sentence_ids = ["%s.sent%d" % (para_id, k) for k in range(1, len(sents) + 1)]
                        section["paragraphs"].append({
                            "id": para_id,
                            "text": para_text[:2000],
                            "sentences": [s[:QUOTE_LIMIT] for s in sents],
                            "sentence_ids": sentence_ids,
                        })
                        for k, sent in enumerate(sents, start=1):
                            if len(signals) >= MAX_SIGNALS:
                                continue
                            locator = "doc://%s#%s.sent%d" % (rel, para_id, k)
                            q = sent[:QUOTE_LIMIT]
                            if CAUSAL_RE.search(sent):
                                signals.append({"type": "causal", "locator": locator, "quote": q, "role": role})
                            if CORREL_RE.search(sent):
                                signals.append({"type": "correlation", "locator": locator, "quote": q, "role": role})
                            if HEDGE_RE.search(sent):
                                signals.append({"type": "hedge", "locator": locator, "quote": q, "role": role})
                            if CLAIM_RE.search(sent):
                                signals.append({"type": "claim", "locator": locator, "quote": q, "role": role})
                            if CONTRIB_RE.search(sent):
                                signals.append({"type": "contribution", "locator": locator, "quote": q, "role": role})
                    doc["sections"].append(section)
                documents.append(doc)
            elif ext in PDF_EXTS:
                inventory.append({"file": rel, "kind": "pdf", "size": size, "readable": False})
                unreadable.append({"file": rel, "reason": "pdf_needs_text_extraction"})
            elif ext in IMAGE_EXTS:
                inventory.append({"file": rel, "kind": "image", "size": size, "readable": False})
                unreadable.append({"file": rel, "reason": "image_needs_ocr_or_render"})
            elif ext in OFFICE_EXTS:
                inventory.append({"file": rel, "kind": "office", "size": size, "readable": False})
                unreadable.append({"file": rel, "reason": "office_needs_render"})
            elif ext in DATA_EXTS:
                inventory.append({"file": rel, "kind": "data", "size": size})
            else:
                inventory.append({"file": rel, "kind": "other", "size": size})

    # Suspected duplicate paragraphs across all text documents (deterministic signal).
    duplicate_candidates = []
    para_pool = []
    for doc in documents:
        for sec in doc["sections"]:
            for para in sec["paragraphs"]:
                para_pool.append(("doc://%s#%s" % (doc["file"], para["id"]), para["text"], token_bigrams(para["text"])))
    for i in range(len(para_pool)):
        if len(duplicate_candidates) >= MAX_DUP_PAIRS:
            break
        la, ta, ba = para_pool[i]
        for j in range(i + 1, len(para_pool)):
            lb, tb, bb = para_pool[j]
            if len(ta) < 60 or len(tb) < 60:
                continue
            sim = jaccard(ba, bb)
            if sim >= 0.6:
                duplicate_candidates.append({"a": la, "b": lb, "similarity": round(sim, 3)})

    model = {
        "schema": "document-model-v1",
        "inventory": inventory,
        "documents": documents,
        "unreadable": unreadable,
        "signals": signals[:MAX_SIGNALS],
        "duplicate_candidates": duplicate_candidates[:MAX_DUP_PAIRS],
    }
    out_path = os.path.join(work_dir, "document_model.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(model, fh, ensure_ascii=False, indent=2)
    return out_path, model


def main():
    ap = argparse.ArgumentParser(description="Build document structure model for /input materials.")
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    args = ap.parse_args()
    out_path, model = build_document_model(args.input, args.work)
    print(json.dumps({
        "ok": True,
        "model": out_path,
        "documents": len(model["documents"]),
        "signals": len(model["signals"]),
        "duplicate_candidates": len(model["duplicate_candidates"]),
        "unreadable": len(model["unreadable"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
