#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py —— 面向鲁棒性变体的自动化自检（纯标准库）。

覆盖场景：
  1. --help 退出码 0
  2. 空输入目录 -> status=insufficient_evidence，不崩溃
  3. 坏 JSON / 坏编码 -> 降级到 input_error/partial，不崩溃
  4. 缺 case.json / task.md -> 仍产出合法 result.json
  5. 超大文件（>2MB）-> 跳过并记录 degradation，不崩溃
  6. output_contract 字段原样回填（null/[]），未知键不丢
  7. 候选导入去重（同 DOI 只留一条）
  8. 组装确定性（同状态两次 assemble 主内容 fingerprint 一致）
  9. 结果逐字证据缺失 -> 标记 unverified，不冒充已核验

用法：python3 selftest.py   （从 scripts/ 目录运行）
退出码 0 = 全部通过；非 0 = 有失败。
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(*argv):
    return subprocess.run(
        [sys.executable, MAIN] + list(argv),
        cwd=HERE, capture_output=True, text=True, timeout=60)


def make_case(base, name):
    d = os.path.join(base, name)
    os.makedirs(os.path.join(d, "input"), exist_ok=True)
    os.makedirs(os.path.join(d, "output"), exist_ok=True)
    os.makedirs(os.path.join(d, "work"), exist_ok=True)
    return d


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  -> " + detail) if detail and not ok else ""))


def main():
    # 1. help
    r = run("--help")
    check("main.py --help 退出码 0", r.returncode == 0, "rc=%d" % r.returncode)

    # 2. 空输入目录
    d = make_case(tempfile.mkdtemp(prefix="skt_"), "empty")
    r = run("run", "--input", os.path.join(d, "input"),
            "--output", os.path.join(d, "output"),
            "--work", os.path.join(d, "work"))
    res = read_json(os.path.join(d, "output", "result.json"))
    check("空输入目录不崩溃且状态诚实", r.returncode == 0 and res.get("status") == "insufficient_evidence",
          "rc=%d status=%s" % (r.returncode, res.get("status")))

    # 3. 坏 JSON + 坏编码
    d = make_case(tempfile.mkdtemp(prefix="skt_"), "badjson")
    write(os.path.join(d, "input", "case.json"), "{not valid json")
    write(os.path.join(d, "input", "task.md"), "研究问题：测试课题\n".encode("utf-8").decode("utf-8"))
    # 坏编码：写非法 UTF-8 字节
    with open(os.path.join(d, "input", "notes.md"), "wb") as f:
        f.write(b"\xff\xfe\x00broken")
    r = run("run", "--input", os.path.join(d, "input"),
            "--output", os.path.join(d, "output"),
            "--work", os.path.join(d, "work"))
    res = read_json(os.path.join(d, "output", "result.json"))
    check("坏 JSON/坏编码不崩溃并落盘", r.returncode == 0 and res.get("case_id"),
          "rc=%d case_id=%s" % (r.returncode, res.get("case_id")))

    # 4. 缺 case.json/task.md
    d = make_case(tempfile.mkdtemp(prefix="skt_"), "noinput")
    r = run("run", "--input", os.path.join(d, "input"),
            "--output", os.path.join(d, "output"),
            "--work", os.path.join(d, "work"))
    res = read_json(os.path.join(d, "output", "result.json"))
    check("缺 case.json/task.md 仍产出合法结果", r.returncode == 0 and res.get("status") in
          ("insufficient_evidence", "input_error", "partial"),
          "rc=%d status=%s" % (r.returncode, res.get("status")))

    # 5. 超大文件
    d = make_case(tempfile.mkdtemp(prefix="skt_"), "bigfile")
    write(os.path.join(d, "input", "case.json"), json.dumps({"case_id": "big", "research_question": "x"}))
    write(os.path.join(d, "input", "task.md"), "研究问题：x")
    with open(os.path.join(d, "input", "huge.txt"), "wb") as f:
        f.write(b"A" * (3 * 1024 * 1024))
    r = run("run", "--input", os.path.join(d, "input"),
            "--output", os.path.join(d, "output"),
            "--work", os.path.join(d, "work"))
    res = read_json(os.path.join(d, "output", "result.json"))
    has_deg = any("size_exceeded" in x for x in (res.get("degradations") or []))
    check("超大文件被跳过并记录降级", r.returncode == 0 and has_deg,
          "rc=%d degradations=%d" % (r.returncode, len(res.get("degradations") or [])))

    # 6. output_contract 回填
    d = make_case(tempfile.mkdtemp(prefix="skt_"), "contract")
    contract = {"fields": ["paper_list", "queries"], "required_output": {"hits": "int"},
                "unknown_custom": ["a", "b"]}
    case = {"case_id": "contract-1", "research_question": "主题X",
            "output_contract": contract}
    write(os.path.join(d, "input", "case.json"), json.dumps(case, ensure_ascii=False))
    write(os.path.join(d, "input", "task.md"), "研究问题：主题X")
    r = run("run", "--input", os.path.join(d, "input"),
            "--output", os.path.join(d, "output"),
            "--work", os.path.join(d, "work"))
    res = read_json(os.path.join(d, "output", "result.json"))
    backfilled = all(k in res for k in ("fields", "required_output", "unknown_custom"))
    check("output_contract 全部字段回填且不改名不删键",
          r.returncode == 0 and backfilled and res.get("fields") == [] and
          res.get("unknown_custom") == [],
          "rc=%d" % r.returncode)

    # 7. 去重
    d = make_case(tempfile.mkdtemp(prefix="skt_"), "dedup")
    write(os.path.join(d, "input", "case.json"),
          json.dumps({"case_id": "d", "research_question": "R"}, ensure_ascii=False))
    write(os.path.join(d, "input", "task.md"), "研究问题：R")
    cands = {"results": [
        {"title": "Alpha Paper", "doi": "10.1000/xyz", "year": 2020, "relevance": 0.9,
         "evidence": [{"locator": "t", "quote": "Alpha"}]},
        {"title": "alpha paper", "doi": "10.1000/xyz", "year": 2021, "relevance": 0.8,
         "evidence": [{"locator": "t", "quote": "Alpha"}]},
    ]}
    write(os.path.join(d, "cands.json"), json.dumps(cands, ensure_ascii=False))
    r0 = run("run", "--input", os.path.join(d, "input"),
             "--output", os.path.join(d, "output"),
             "--work", os.path.join(d, "work"))
    r = run("import", "--file", os.path.join(d, "cands.json"), "--label", "test",
            "--work", os.path.join(d, "work"),
            "--output", os.path.join(d, "output"),
            "--input", os.path.join(d, "input"))
    check("import 退出码 0", r.returncode == 0, "rc=%d" % r.returncode)
    ra = run("assemble", "--input", os.path.join(d, "input"),
             "--output", os.path.join(d, "output"),
             "--work", os.path.join(d, "work"))
    res = read_json(os.path.join(d, "output", "result.json"))
    n = len(res.get("results") or [])
    check("同 DOI 去重后仅一条", r.returncode == 0 and n == 1, "results=%d" % n)

    # 8. 确定性
    fp1 = read_json(os.path.join(d, "output", "result.json")).get("reproducibility", {}).get("fingerprint")
    run("assemble", "--input", os.path.join(d, "input"),
        "--output", os.path.join(d, "output"),
        "--work", os.path.join(d, "work"))
    fp2 = read_json(os.path.join(d, "output", "result.json")).get("reproducibility", {}).get("fingerprint")
    check("同状态两次 assemble 主内容 fingerprint 一致", fp1 and fp1 == fp2,
          "fp1=%s fp2=%s" % (fp1, fp2))

    # 9. 证据缺失标记
    d2 = make_case(tempfile.mkdtemp(prefix="skt_"), "unverified")
    write(os.path.join(d2, "input", "case.json"),
          json.dumps({"case_id": "u", "research_question": "Q"}, ensure_ascii=False))
    write(os.path.join(d2, "input", "task.md"), "研究问题：Q")
    cands = {"results": [{"title": "No Evidence Paper", "year": 2019, "relevance": 0.9}]}
    write(os.path.join(d2, "cands.json"), json.dumps(cands, ensure_ascii=False))
    run("run", "--input", os.path.join(d2, "input"),
        "--output", os.path.join(d2, "output"),
        "--work", os.path.join(d2, "work"))
    run("import", "--file", os.path.join(d2, "cands.json"), "--label", "t",
        "--work", os.path.join(d2, "work"), "--output", os.path.join(d2, "output"),
        "--input", os.path.join(d2, "input"))
    run("assemble", "--input", os.path.join(d2, "input"),
        "--output", os.path.join(d2, "output"),
        "--work", os.path.join(d2, "work"))
    res2 = read_json(os.path.join(d2, "output", "result.json"))
    unv = [r for r in (res2.get("results") or []) if not r.get("verified")]
    check("无逐字证据的结果被标记 unverified", len(unv) == 1, "unverified=%d" % len(unv))

    # 10. selfcheck 子命令
    r = run("selfcheck", "--output", os.path.join(d, "output"),
            "--work", os.path.join(d, "work"))
    check("selfcheck 子命令可运行", r.returncode == 0, "rc=%d" % r.returncode)

    failed = sum(1 for _, ok, _ in CHECKS if not ok)
    print("\nselftest: %d/%d PASS" % (len(CHECKS) - failed, len(CHECKS)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
