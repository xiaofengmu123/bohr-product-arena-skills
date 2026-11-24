#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence: 证据目录（evidence catalog）记录与证据映射校验。

设计取舍（相对常见"先写稿后检查"的流程）：
- 以 /app/work/evidence_catalog.json 为唯一中间表示（IR），
  每条断言同时挂 locator + 逐字 quote + citation_keys；
- 校验放在撰写过程中持续运行，而不是最后一次性补救；
- 提供 add-claim / add-citation 子命令，避免 Agent 手工改 JSON 引入字段漂移。

CLI:
  python3 scripts/evidence.py add-claim  --catalog C --claim-id K01 --section results \
      --kind number --text "..." --locator "materials/a.pdf#p3" --quote "3.2 mg/L" \
      --citation-keys C01 --confidence verbatim
  python3 scripts/evidence.py add-citation --catalog C --key C01 --authors "..." ...
  python3 scripts/evidence.py check --catalog C [--draft /app/output/manuscript.md] \
      --out /app/work/evidence_report.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

RE_SOURCE = re.compile(r"\[S:([A-Za-z0-9][A-Za-z0-9._-]*)\]")
RE_CITE_USE = re.compile(r"\[C:([A-Za-z0-9][A-Za-z0-9._-]*)\](?!\s*:)")
RE_CITE_DEF = re.compile(r"^\s*\[C:([A-Za-z0-9][A-Za-z0-9._-]*)\]:\s*(.+?)\s*$")

CLAIM_KINDS = {"fact", "number", "figure", "table", "citation_claim", "term", "method"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json_safe(path: str):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except FileNotFoundError:
        return None, "文件不存在: %s" % path
    except json.JSONDecodeError as exc:
        return None, "JSON 解析失败 %s: %s" % (path, exc)
    except OSError as exc:
        return None, "读取失败 %s: %s" % (path, exc)


def load_catalog(path: str):
    obj, err = load_json_safe(path)
    if err is not None:
        # 容错：缺失时返回空目录结构，不崩溃
        return {"schema": "evidence-catalog/v1", "materials": [], "claims": [],
                "citations": [], "conflicts": [], "gaps": []}, err
    for k in ("materials", "claims", "citations", "conflicts", "gaps"):
        if k not in obj or not isinstance(obj.get(k), list):
            obj[k] = []
    return obj, None


def save_catalog(path: str, catalog: dict):
    catalog["updated_at"] = now_iso()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def material_ids(catalog: dict):
    ids = set()
    for m in catalog.get("materials", []):
        if isinstance(m, dict):
            ids.add(m.get("id"))
            ids.add(m.get("path"))
    return {i for i in ids if i}


def add_claim(catalog: dict, **kw):
    claim = {
        "id": kw.get("claim_id"),
        "kind": kw.get("kind") or "fact",
        "section": kw.get("section") or "unspecified",
        "text": kw.get("text") or "",
        "locator": kw.get("locator") or "",
        "quote": kw.get("quote") or "",
        "citation_keys": [c for c in (kw.get("citation_keys") or "").split(",") if c.strip()],
        "confidence": kw.get("confidence") or "verbatim",
        "flags": kw.get("flags") or [],
        "updated_at": now_iso(),
    }
    claims = catalog.setdefault("claims", [])
    for i, c in enumerate(claims):
        if isinstance(c, dict) and c.get("id") == claim["id"]:
            claims[i] = claim
            return claim
    claims.append(claim)
    return claim


def add_citation(catalog: dict, **kw):
    cit = {
        "key": kw.get("key"),
        "authors": kw.get("authors") or "unknown",
        "year": kw.get("year") or "unknown",
        "title": kw.get("title") or "unknown",
        "journal": kw.get("journal") or "unknown",
        "volume": kw.get("volume"),
        "issue": kw.get("issue"),
        "pages": kw.get("pages"),
        "doi": kw.get("doi"),
        "status": kw.get("status") or "metadata_only",
        "raw": kw.get("raw") or "",
        "updated_at": now_iso(),
    }
    cits = catalog.setdefault("citations", [])
    for i, c in enumerate(cits):
        if isinstance(c, dict) and c.get("key") == cit["key"]:
            cits[i] = cit
            return cit
    cits.append(cit)
    return cit


def check_catalog(catalog: dict, draft_text=None):
    """返回 findings 列表：{'severity': 'error'|'warn', 'code', 'message', 'ref'}。"""
    findings = []
    mids = material_ids(catalog)
    claims = catalog.get("claims", [])
    cits = catalog.get("citations", [])
    cit_keys = {c.get("key") for c in cits if isinstance(c, dict) and c.get("key")}
    used_cit = set()

    for c in claims:
        if not isinstance(c, dict):
            continue
        cid = c.get("id") or "?"
        loc = (c.get("locator") or "").strip()
        quote = (c.get("quote") or "").strip()
        keys = c.get("citation_keys") or []

        if not quote:
            findings.append({"severity": "error", "code": "E_CLAIM_NO_QUOTE",
                             "message": "断言 %s 缺少逐字 quote" % cid, "ref": cid})
        if not loc:
            findings.append({"severity": "error", "code": "E_CLAIM_NO_LOCATOR",
                             "message": "断言 %s 缺少 locator" % cid, "ref": cid})
        if loc and mids and not any(loc.startswith(m) or loc == m for m in mids):
            findings.append({"severity": "error", "code": "E_LOCATOR_UNKNOWN",
                             "message": "断言 %s 的 locator %r 未命中任何材料" % (cid, loc), "ref": cid})
        for k in keys:
            if not k:
                continue
            used_cit.add(k)
            if k not in cit_keys:
                findings.append({"severity": "error", "code": "E_CIT_UNDEFINED",
                                 "message": "断言 %s 引用了未定义的文献键 %s" % (cid, k), "ref": cid})

    for c in cits:
        key = c.get("key")
        if key and key not in used_cit and draft_text is not None and ("[C:%s]" % key) not in (draft_text or ""):
            findings.append({"severity": "warn", "code": "W_CIT_UNUSED",
                             "message": "文献 %s 已定义但未被任何断言/正文使用" % key, "ref": key})

    if draft_text is not None:
        findings.extend(_check_draft(draft_text, mids, cit_keys))
    return findings


def _check_draft(text: str, mids, cit_keys):
    findings = []
    used_src = set(RE_SOURCE.findall(text))
    used_cit = set(RE_CITE_USE.findall(text))
    defined_cit = set()
    for line in text.splitlines():
        m = RE_CITE_DEF.match(line)
        if m:
            defined_cit.add(m.group(1))

    for sid in used_src:
        if mids and not any(sid == m or sid.startswith(m) for m in mids):
            findings.append({"severity": "error", "code": "E_DANGLING_SOURCE",
                             "message": "正文引用来源 [S:%s] 未在材料清单中定义" % sid, "ref": sid})
    for cid in used_cit:
        if cid not in cit_keys and cid not in defined_cit:
            findings.append({"severity": "error", "code": "E_DANGLING_CITATION",
                             "message": "正文引用 [C:%s] 无定义" % cid, "ref": cid})
    for cid in defined_cit:
        if cid not in used_cit:
            findings.append({"severity": "warn", "code": "W_CIT_DEFINED_UNUSED",
                             "message": "参考文献定义 [C:%s] 未被正文使用" % cid, "ref": cid})

    # 软启发：含数字但无 [S:]/[C:] 标记的行 -> 提示核对是否材料外补写
    reported = 0
    for line in text.splitlines():
        if not re.search(r"\d", line):
            continue
        if "[S:" in line or "[C:" in line or "]" not in line:
            continue
        if RE_CITE_DEF.match(line):
            continue
        if line.lstrip().startswith(("#", ">", "|", "- ", "* ")):
            continue
        if reported < 40:
            findings.append({"severity": "warn", "code": "W_NUMERIC_UNMARKED",
                             "message": "含数字但未挂 [S:]/[C:] 标记，请核对是否来自材料：%s" % line.strip()[:120],
                             "ref": None})
            reported += 1
    return findings


def _add_claim_cmd(args):
    catalog, err = load_catalog(args.catalog)
    if err and not os.path.exists(args.catalog):
        catalog["case_id"] = "unknown"
    add_claim(catalog, claim_id=args.claim_id, kind=args.kind, section=args.section,
              text=args.text, locator=args.locator, quote=args.quote,
              citation_keys=args.citation_keys, confidence=args.confidence, flags=args.flags)
    os.makedirs(os.path.dirname(os.path.abspath(args.catalog)), exist_ok=True)
    save_catalog(args.catalog, catalog)
    print(json.dumps({"ok": True, "claim_id": args.claim_id}, ensure_ascii=False))
    return 0


def _add_citation_cmd(args):
    catalog, err = load_catalog(args.catalog)
    if err and not os.path.exists(args.catalog):
        catalog["case_id"] = "unknown"
    add_citation(catalog, key=args.key, authors=args.authors, year=args.year,
                 title=args.title, journal=args.journal, volume=args.volume,
                 issue=args.issue, pages=args.pages, doi=args.doi, status=args.status,
                 raw=args.raw)
    os.makedirs(os.path.dirname(os.path.abspath(args.catalog)), exist_ok=True)
    save_catalog(args.catalog, catalog)
    print(json.dumps({"ok": True, "citation_key": args.key}, ensure_ascii=False))
    return 0


def _check_cmd(args):
    catalog, _ = load_catalog(args.catalog)
    draft_text = None
    if args.draft and os.path.isfile(args.draft):
        with open(args.draft, "r", encoding="utf-8-sig") as fh:
            draft_text = fh.read()
    findings = check_catalog(catalog, draft_text)
    errors = [f for f in findings if f["severity"] == "error"]
    report = {
        "catalog": args.catalog,
        "draft": args.draft,
        "finding_count": len(findings),
        "error_count": len(errors),
        "findings": findings,
        "checked_at": now_iso(),
    }
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    print(json.dumps(report, ensure_ascii=False))
    return 1 if errors else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="证据目录记录与证据映射校验")
    sub = ap.add_subparsers(dest="command", required=True)

    pc = sub.add_parser("add-claim")
    pc.add_argument("--catalog", required=True)
    pc.add_argument("--claim-id", required=True)
    pc.add_argument("--section", default="unspecified")
    pc.add_argument("--kind", default="fact", choices=sorted(CLAIM_KINDS))
    pc.add_argument("--text", required=True)
    pc.add_argument("--locator", required=True)
    pc.add_argument("--quote", required=True)
    pc.add_argument("--citation-keys", default="")
    pc.add_argument("--confidence", default="verbatim")
    pc.add_argument("--flags", action="append", default=[])

    pi = sub.add_parser("add-citation")
    pi.add_argument("--catalog", required=True)
    pi.add_argument("--key", required=True)
    pi.add_argument("--authors", default="unknown")
    pi.add_argument("--year", default="unknown")
    pi.add_argument("--title", default="unknown")
    pi.add_argument("--journal", default="unknown")
    pi.add_argument("--volume")
    pi.add_argument("--issue")
    pi.add_argument("--pages")
    pi.add_argument("--doi")
    pi.add_argument("--status", default="metadata_only")
    pi.add_argument("--raw", default="")

    pch = sub.add_parser("check")
    pch.add_argument("--catalog", required=True)
    pch.add_argument("--draft")
    pch.add_argument("--out")

    args = ap.parse_args(argv)
    if args.command == "add-claim":
        return _add_claim_cmd(args)
    if args.command == "add-citation":
        return _add_citation_cmd(args)
    return _check_cmd(args)


if __name__ == "__main__":
    sys.exit(main())
