#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- 交付前自检 + 健壮性自测（仅标准库，确定性，无网络）。

两种模式：
  python3 -B scripts/selftest.py --input /input --output /app/output
      -> 校验真实运行的 result.json（退出码 0=全 PASS）
  python3 -B scripts/selftest.py --self-test
      -> 用临时合成输入跑六类异常场景（空目录/坏 JSON/缺 case/坏编码/超大文件/重复运行确定性）

自检逐条对应评分维度（结构完整性、追溯性、异常处理、确定性、无伪造）。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import fedsearch as fs  # noqa: E402
import main as pipeline  # noqa: E402


def _read_json(path: Path) -> Tuple[Any, List[str]]:
    if not path.exists():
        return None, ["missing:%s" % path]
    return fs.load_json(path)


def contract_keys_from_case(input_dir: Path) -> List[str]:
    case_path = input_dir / "case.json"
    if not case_path.exists():
        return list(fs.DEFAULT_CONTRACT)
    obj, _ = fs.load_json(case_path)
    if not isinstance(obj, dict):
        return list(fs.DEFAULT_CONTRACT)
    names, _ = pipeline.resolve_contract(obj)
    return names if names else list(fs.DEFAULT_CONTRACT)


def check_delivery(input_dir: Path, output_dir: Path) -> Tuple[int, List[str]]:
    """真实运行自检，返回 (失败数, 详细行)。"""
    failures: List[str] = []
    result_path = output_dir / "result.json"

    data, errs = _read_json(result_path)
    if errs or not isinstance(data, dict):
        return 1, ["FAIL: result.json 不存在或不是合法 JSON"] + errs

    # 1) 契约完整
    if not data.get("case_id"):
        failures.append("FAIL: result.json 缺 case_id")
    if data.get("status") not in fs.STATUSES:
        failures.append("FAIL: status 非法 %r" % data.get("status"))
    for k in contract_keys_from_case(input_dir):
        if k not in data:
            failures.append("FAIL: output_contract 缺键 %r" % k)

    # 2) 固定字段
    for k in fs.FIXED_KEYS:
        if k not in data:
            failures.append("FAIL: 固定字段缺 %r" % k)
    if not isinstance(data.get("change_log"), list) or not data["change_log"]:
        failures.append("FAIL: change_log 为空或非列表")

    # 3) 候选可追溯
    for c in data.get("candidates", []):
        if not c.get("record_id"):
            failures.append("FAIL: 候选缺 record_id")
            break
        if not c.get("provenance"):
            failures.append("FAIL: 候选 %s 缺 provenance" % c.get("record_id"))
            break
        if not c.get("field_sources"):
            failures.append("FAIL: 候选 %s 缺 field_sources" % c.get("record_id"))
            break

    # 4) 证据非空（有候选时）
    if data.get("candidates") and not data.get("evidence_records"):
        failures.append("FAIL: 有候选但 evidence_records 为空")

    # 5) 降级/状态一致性（粗略）
    if data.get("status") == "insufficient_evidence" and data.get("candidates"):
        failures.append("FAIL: status=insufficient_evidence 但存在候选")

    return len(failures), failures


def check_determinism(input_dir: Path) -> Tuple[bool, str]:
    """同一输入跑两次，比较 result.json 内容确定。

    结果内容（候选/排序/覆盖/证据/合并）必须完全一致；
    runtime 墙钟时间戳与 change_log 的 at 属于运行时记账，排除后比较。
    """
    def _normalize(obj: Any) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k == "at":
                    continue
                if k == "runtime":
                    v = {kk: vv for kk, vv in v.items()
                         if kk not in ("started_at", "finished_at", "seconds")}
                out[k] = _normalize(v)
            return out
        if isinstance(obj, list):
            return [_normalize(x) for x in obj]
        return obj

    with tempfile.TemporaryDirectory() as td:
        out1 = Path(td) / "out1"
        out2 = Path(td) / "out2"
        work = Path(td) / "work"
        pipeline.run(Path(input_dir), out1, work)
        pipeline.run(Path(input_dir), out2, work)
        j1 = json.loads((out1 / "result.json").read_text(encoding="utf-8"))
        j2 = json.loads((out2 / "result.json").read_text(encoding="utf-8"))
        same = _normalize(j1) == _normalize(j2)
        return (same, "两次运行 result.json 内容确定（已排除 runtime 墙钟）" if same
                else "两次运行 result.json 内容不一致（非确定）")


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def scenario_empty() -> Tuple[bool, str]:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "case_empty" / "input"
        out = root / "out"
        inp.mkdir(parents=True)
        _write(inp / "case.json", b'{"case_id": "t-empty", "task": "q"}')
        res = pipeline.run(inp, out, root / "work")
        return (res.get("status") in ("insufficient_evidence", "partial"),
                "空输入 => status=%s" % res.get("status"))


def scenario_bad_json() -> Tuple[bool, str]:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "case_bad" / "input"
        out = root / "out"
        inp.mkdir(parents=True)
        _write(inp / "case.json", b"{not valid json")
        _write(inp / "notes.txt", b"some text")
        res = pipeline.run(inp, out, root / "work")
        ok = (out / "result.json").exists() and res.get("status") in fs.STATUSES
        return (ok, "坏 JSON => 仍产出合法骨架 status=%s" % res.get("status"))


def scenario_missing_case() -> Tuple[bool, str]:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "case_miss" / "input"
        out = root / "out"
        inp.mkdir(parents=True)
        _write(inp / "material.json", b'{"message":{"items":[{"DOI":"10.1/x","title":["T"],"year":2020,"author":[{"family":"A","given":"B"}]}]}}')
        res = pipeline.run(inp, out, root / "work")
        ok = (out / "result.json").exists() and res.get("status") in fs.STATUSES \
            and res.get("errors")
        return (ok, "缺 case.json => status=%s 且 errors 记录" % res.get("status"))


def scenario_bad_encoding() -> Tuple[bool, str]:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "case_enc" / "input"
        out = root / "out"
        inp.mkdir(parents=True)
        _write(inp / "case.json", b'{"case_id": "t-enc", "task": "q"}')
        _write(inp / "paper.txt", b"\xff\xfe\x00bad\xff\x00")
        res = pipeline.run(inp, out, root / "work")
        ok = (out / "result.json").exists() and res.get("status") in fs.STATUSES
        return (ok, "坏编码文本 => 不崩溃 status=%s" % res.get("status"))


def scenario_too_large() -> Tuple[bool, str]:
    big = b"x" * (fs.MAX_FILE_BYTES + 10)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "case_big" / "input"
        out = root / "out"
        inp.mkdir(parents=True)
        _write(inp / "case.json", b'{"case_id": "t-big", "task": "q"}')
        _write(inp / "huge.json", b'{"items":[]}')
        # 直接造一个超大的 txt 文件
        _write(inp / "huge.txt", big)
        res = pipeline.run(inp, out, root / "work")
        ok = (out / "result.json").exists() and any(
            d.startswith(("too_large", "huge")) for d in res.get("degradations", []))
        return (ok, "超大文件 => 跳过并记 degradation")


def scenario_determinism() -> Tuple[bool, str]:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "case_det" / "input"
        inp.mkdir(parents=True)
        payload = {
            "case_id": "t-det", "task": "CRISPR gene editing",
            "output_contract": ["candidates", "coverage", "note"],
            "databases": ["crossref", "pubmed"],
        }
        _write(inp / "case.json", json.dumps(payload).encode("utf-8"))
        _write(inp / "crossref.json", json.dumps({
            "message": {"items": [
                {"DOI": "10.1/a", "title": ["CRISPR Editing"], "issued":
                 {"date-parts": [[2020, 5]]},
                 "author": [{"family": "Smith", "given": "J"}],
                 "container-title": ["Nat"], "volume": "1", "page": "1-9",
                 "type": "journal-article"}]}}).encode("utf-8"))
        _write(inp / "pubmed.json", json.dumps({
            "result": {"uids": ["1"], "1": {"uid": "1", "pubdate": "2020 May",
                                            "title": "CRISPR Editing",
                                            "authors": [{"name": "Smith J"}],
                                            "source": "Nature", "volume": "1",
                                            "pages": "1-9",
                                            "articleids": [{"idtype": "doi",
                                                            "value": "10.1/a"}]}}
        }).encode("utf-8"))
        return check_determinism(inp)


def run_self_test() -> int:
    scenarios = [
        ("空输入目录", scenario_empty),
        ("坏 JSON", scenario_bad_json),
        ("缺 case.json", scenario_missing_case),
        ("坏编码", scenario_bad_encoding),
        ("超大文件", scenario_too_large),
        ("重复运行确定性", scenario_determinism),
    ]
    fails = 0
    for name, fn in scenarios:
        try:
            ok, msg = fn()
        except Exception as e:  # noqa: BLE001
            ok, msg = False, "异常: %r" % e
        print("[%s] %s: %s" % ("PASS" if ok else "FAIL", name, msg))
        if not ok:
            fails += 1
    return 1 if fails else 0


def main(argv: List[str] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="selftest.py", description="多库检索 Skill 自检与自测")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--self-test", action="store_true", help="跑异常场景自测")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    n, lines = check_delivery(Path(args.input), Path(args.output))
    for line in lines:
        print(line)
    if n == 0:
        print("ALL CHECKS PASSED")
    else:
        print("%d CHECK(S) FAILED" % n)
    return 0 if n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())