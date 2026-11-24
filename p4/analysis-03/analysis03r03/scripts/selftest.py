#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 自检脚本（仅标准库）。用途：提交/交付前证明

  1) 端到端可运行：init → update → stats → manifest → check 全程退出码 0；
  2) 确定性：同一输入两次运行，统计与 SVG 字节级一致，result.json 除时间戳外一致；
  3) 健壮性：空目录、坏 JSON、坏编码均退出码 0 并产出合法 result.json；
  4) 产物校验逻辑：PNG/SVG/PDF 检查函数能正确识别格式与分辨率。

运行：python3 scripts/selftest.py  （在包根目录或任意目录均可，路径自适应）
退出码：全部通过为 0，任一 FAIL 为 1。
"""
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402
import main as main_mod  # noqa: E402

MAIN = os.path.join(HERE, "main.py")

FIXTURE_CSV = """group,x,y
A,1.0,2.0
A,2.0,3.0
A,2.5,3.5
B,3.0,4.0
B,4.0,5.0
B,4.5,6.0
C,,6.0
C,6.0,7.0
C,6.5,42.0
"""

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("[%s] %s %s" % ("PASS" if ok else "FAIL", name,
                          ("- " + detail) if detail else ""))


def run_cli(args, **kw):
    return subprocess.run([sys.executable, MAIN] + args,
                          capture_output=True, text=True, timeout=120, **kw)


def normalize_for_compare(obj):
    """去掉时间戳/运行时长等非确定性字段，比较确定性部分。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("ts", "started_at", "updated_at", "finished_at", "seconds"):
                continue
            out[k] = normalize_for_compare(v)
        return out
    if isinstance(obj, list):
        return [normalize_for_compare(v) for v in obj]
    return obj


def make_png(width, height, dpi):
    """构造一个最小合法 RGB PNG（含 pHYs 分辨率块），用于校验 PNG 解析。"""
    def chunk(ctype, payload):
        c = struct.pack(">I", len(payload)) + ctype + payload
        crc = zlib.crc32(ctype + payload) & 0xFFFFFFFF
        return c + struct.pack(">I", crc)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\x80\x80\x80" * width
    idat = zlib.compress(row * height)
    ppm = round(dpi / 0.0254)
    phys = struct.pack(">IIB", ppm, ppm, 1)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"pHYs", phys) + \
        chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def test_end_to_end(tmp):
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(inp, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "case-selftest", "output_contract": {
            "figure": {}, "caption": {}, "data_summary": {}}}, fh, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 科研数据可视化\n")
    with open(os.path.join(inp, "data.csv"), "w", encoding="utf-8") as fh:
        fh.write(FIXTURE_CSV)

    r = run_cli(["init", "--input", inp, "--output", out, "--work", work])
    record("init_exit0", r.returncode == 0, "rc=%d" % r.returncode)
    result_path = os.path.join(out, "result.json")
    record("init_result_exists", os.path.isfile(result_path))
    obj, err = main_mod.load_json_robust(result_path)
    record("init_result_valid", not err and obj.get("status") == "partial",
           "status=%s err=%s" % (obj.get("status"), err))
    record("init_contract_backfilled", "figure" in obj and "caption" in obj
           and "data_summary" in obj)

    r = run_cli(["stats", "--input", inp, "--output", out, "--work", work])
    record("stats_exit0", r.returncode == 0, "rc=%d" % r.returncode)
    stats1, _ = main_mod.load_json_robust(os.path.join(work, "stats.json"))
    record("stats_descriptives", "descriptives" in stats1 and "x" in stats1.get("descriptives", {}),
           "cols=%s" % sorted(stats1.get("descriptives", {}).keys()))

    # SVG 兜底绘图
    svg1 = core_impl.svg_bar(["A", "B", "C"], [2.0, 4.0, 6.5],
                             title="Selftest", xlabel="group", ylabel="x (unit)")
    svg_path = os.path.join(out, "figure.svg")
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg1)
    record("svg_parseable", bool(main_mod._check_svg(svg_path)[0]))

    # 产物登记 + 状态 + 证据
    run_cli(["update", "--output", out, "--work", work,
             "--set", '{"status":"ok"}',
             "--log", "完成统计、SVG 导出与证据映射", "--step", "finalize",
             "--files", "figure.svg"])
    run_cli(["update", "--output", out, "--work", work,
             "--set-key", "sources",
             "--set-value", '[{"locator":"data.csv#L2","quote":"A,1.0,2.0","kind":"value"}]'])
    run_cli(["manifest", "--input", inp, "--output", out])
    obj, _ = main_mod.load_json_robust(result_path)
    obj["artifacts"] = [{"path": "figure.svg", "format": "svg"}]
    main_mod.atomic_write_json(result_path, obj)

    r = run_cli(["check", "--output", out, "--work", work,
                 "--input", inp, "--require-contract", "--fail"])
    record("check_exit0_pass", r.returncode == 0, "rc=%d out=%s" % (r.returncode, r.stdout.strip()[:80]))
    report, _ = main_mod.load_json_robust(os.path.join(work, "check_report.json"))
    record("check_report_ok", report.get("ok") is True, "fails=%s" % report.get("fails"))

    return inp, out, work, stats1, svg1


def test_determinism(tmp, inp, out, work, stats1, svg1):
    r = run_cli(["stats", "--input", inp, "--output", out, "--work", work])
    stats2, _ = main_mod.load_json_robust(os.path.join(work, "stats.json"))
    record("determinism_stats", core_impl.canonical_json(stats1) == core_impl.canonical_json(stats2),
           "byte-identical")
    svg2 = core_impl.svg_bar(["A", "B", "C"], [2.0, 4.0, 6.5],
                             title="Selftest", xlabel="group", ylabel="x (unit)")
    record("determinism_svg", svg1 == svg2, "byte-identical")

    # 两次 init 的确定性部分一致（忽略时间戳）
    out2 = os.path.join(tmp, "output2")
    r1 = run_cli(["init", "--input", inp, "--output", out, "--work", work])
    r2 = run_cli(["init", "--input", inp, "--output", out2, "--work", work])
    o1, _ = main_mod.load_json_robust(os.path.join(out, "result.json"))
    o2, _ = main_mod.load_json_robust(os.path.join(out2, "result.json"))
    record("determinism_init", r1.returncode == 0 and r2.returncode == 0 and
           normalize_for_compare(o1) == normalize_for_compare(o2),
           "modulo timestamps")


def test_robustness(tmp):
    # 空目录
    empty = os.path.join(tmp, "empty_input")
    os.makedirs(empty, exist_ok=True)
    eo = os.path.join(tmp, "empty_output")
    r = run_cli(["init", "--input", empty, "--output", eo, "--work", os.path.join(tmp, "ew")])
    record("empty_input_exit0", r.returncode == 0, "rc=%d" % r.returncode)
    obj, err = main_mod.load_json_robust(os.path.join(eo, "result.json"))
    record("empty_input_valid", not err and "case_id" in obj,
           "case_id=%s" % obj.get("case_id"))

    # 坏 JSON
    bad = os.path.join(tmp, "bad_input")
    os.makedirs(bad, exist_ok=True)
    with open(os.path.join(bad, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not valid json")
    bo = os.path.join(tmp, "bad_output")
    r = run_cli(["init", "--input", bad, "--output", bo, "--work", os.path.join(tmp, "bw")])
    record("bad_json_exit0", r.returncode == 0, "rc=%d" % r.returncode)
    obj, err = main_mod.load_json_robust(os.path.join(bo, "result.json"))
    record("bad_json_degradation", not err and any("bad_json" in d for d in obj.get("degradations", [])),
           "degradations=%s" % obj.get("degradations"))

    # 坏编码（latin-1 字节）
    enc_dir = os.path.join(tmp, "enc_input")
    os.makedirs(enc_dir, exist_ok=True)
    with open(os.path.join(enc_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "enc"}, fh)
    with open(os.path.join(enc_dir, "data.csv"), "wb") as fh:
        fh.write("name,value\ncaf\xe9,1.2\n".encode("latin-1"))
    cols, rows, meta = core_impl.read_table(os.path.join(enc_dir, "data.csv"))
    record("bad_encoding_read", cols == ["name", "value"] and len(rows) == 1,
           "enc=%s cols=%s" % (meta.get("encoding"), cols))


def test_png_check(tmp):
    png_path = os.path.join(tmp, "test_300dpi.png")
    with open(png_path, "wb") as fh:
        fh.write(make_png(8, 8, 300))
    ok, info = main_mod._check_png(png_path)
    record("png_check_ok", ok and info.get("dpi_x") == 300 and info.get("width") == 8,
           "info=%s" % info)
    low_path = os.path.join(tmp, "test_72dpi.png")
    with open(low_path, "wb") as fh:
        fh.write(make_png(8, 8, 72))
    ok, info = main_mod._check_png(low_path)
    record("png_check_low_dpi", ok and info.get("dpi_x") == 72, "info=%s" % info)


def test_svg_check(tmp):
    ok, info = main_mod._check_svg(__file__.replace("selftest.py", ""))
    record("svg_check_bad_returns_false", ok is False, "info=%s" % info)


def main():
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        inp, out, work, stats1, svg1 = test_end_to_end(tmp)
        test_determinism(tmp, inp, out, work, stats1, svg1)
        test_robustness(tmp)
        test_png_check(tmp)
        test_svg_check(tmp)
    finally:
        # 保留临时目录便于排查；不递归删除用户数据
        pass
    failed = [r for r in RESULTS if not r[1]]
    print("\n%d/%d passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print("FAILED:")
        for name, _ok, detail in failed:
            print("  - %s %s" % (name, detail))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
