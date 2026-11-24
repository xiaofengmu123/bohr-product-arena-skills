"""main.py - thin entry wrapper; pipeline implementation lives in core_impl.py.

usage: main.py {probe|scaffold|validate|all} [--input DIR] [--output DIR] [--work DIR]
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py {probe|scaffold|validate|all} [--input DIR] [--output DIR] [--work DIR]")
    print("  probe    扫描输入、抽取 PDF/合同/候选方案，写 /app/work/ir.json")
    print("  scaffold 依据 IR 落盘 partial result.json + plan.md 骨架")
    print("  validate 结构/类型/六要素/算术/证据/一致性自检，退出码 0=可用 1=必须修复")
    print("  all      probe → scaffold → validate 一次性冒烟")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
