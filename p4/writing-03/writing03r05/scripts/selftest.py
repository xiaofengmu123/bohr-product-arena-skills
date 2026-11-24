#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 内置自测（仅标准库，临时 fixture 空跑，不写正式案例产物）。

验证：JSON/文本容错读取、契约键解析、段落 locator、逐字引用命中、
材料发现与通用校验。通过则退出码 0；失败则退出码 1。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core_impl import (  # noqa: E402
    contract_keys, discover, fill_contract, norm, paragraphs, quote_hit, read_json,
    read_text, validate_common, write_json, write_text,
)


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}".rstrip())
    return ok


def main() -> int:
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # 文本与 JSON 容错读取
        write_text(root / "a.txt", "第一段内容。\n\n第二段有数值 87.4%。")
        text, err = read_text(root / "a.txt")
        results.append(check("read_text", err == "" and "87.4%" in text, err))
        bad = root / "bad.json"
        write_text(bad, "{not json")
        _obj, err = read_json(bad)
        results.append(check("read_json_bad", err != "", err))

        # 段落 locator
        paras = paragraphs(text)
        results.append(check("paragraphs", len(paras) == 2 and paras[1][0] == "para.2",
                             f"{paras}"))

        # 契约键解析：dict 与 list 两种形态 + 兜底
        keys1, fb1 = contract_keys({"output_contract": {"case_id": "str", "result": "obj"}})
        results.append(check("contract_dict", keys1 == ["case_id", "result"] and fb1 is False, str(keys1)))
        keys2, fb2 = contract_keys({"output_contract": [{"name": "deliverable"}, "fact_source_map"]})
        results.append(check("contract_list", keys2 == ["deliverable", "fact_source_map"] and fb2 is False, str(keys2)))
        keys3, fb3 = contract_keys({})
        results.append(check("contract_fallback", fb3 is True and len(keys3) >= 3, str(keys3)))

        # 契约回填
        filled, missing = fill_contract(["case_id", "result", "claims"], {"case_id": "x"})
        results.append(check("fill_contract", filled["case_id"] == "x" and "result" in filled
                             and missing == ["result", "claims"], str(missing)))

        # 逐字引用命中（忽略大小写与空白）
        corpus = {"S1": norm(text)}
        results.append(check("quote_hit", quote_hit(corpus["S1"], "第二段有数值 87.4%") is True))
        results.append(check("quote_miss", quote_hit(corpus["S1"], "不存在的内容") is False))

        # 材料发现（跳过 case.json/task.md，按文本优先排序编号；用子目录隔离噪声文件）
        mats_root = root / "mats"
        mats_root.mkdir()
        write_json(mats_root / "case.json", {"case_id": "t"})
        write_text(mats_root / "task.md", "# task")
        write_text(mats_root / "paper.md", "sample A reached 87.4%")
        write_text(mats_root / "table.csv", "sample,value\nA,87.4\n")
        mats = discover(mats_root)
        source_ids = [m["source_id"] for m in mats]
        results.append(check("discover", "S1" in source_ids and "S2" in source_ids
                             and len(mats) == 2, str(source_ids)))

        # 通用校验（语料指向材料目录；S1=paper.md, S2=table.csv）
        corpus = {"S1": norm("sample A reached 87.4%"), "S2": norm("sample,value\nA,87.4")}
        draft = {"deliverable_text": "样品 A 转化率 87.4%。",
                 "preserved_facts": [{"fact_id": "F1", "value": "87.4", "source_id": "S1",
                                      "locator": "para.1", "quote": "sample A reached 87.4%"}],
                 "evidence_map": [{"claim_id": "C1", "source_id": "S1", "locator": "para.1",
                                   "quote": "sample A reached 87.4%"}],
                 "change_log": [{"id": "1", "action": "rewrite"}]}
        items, fail, warn = validate_common(draft, corpus, root)
        results.append(check("validate_common", fail == 0, str(items)))

    failed = sum(1 for ok in results if not ok)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
