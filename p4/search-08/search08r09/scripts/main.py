"""main.py — thin entry wrapper（实现见 pipeline.py，仅标准库）。"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print("usage: main.py <skeleton|normalize|finalize|selfcheck|selftest> "
          "[--input DIR] [--output DIR] [--work DIR]")
    print("  skeleton  扫描 /input，落盘 partial 骨架（前 3 分钟必做）")
    print("  normalize 去重并生成判定模板")
    print("  finalize  合并判定、校验证据、计数闭合，写最终产物")
    print("  selfcheck 逐项自检")
    print("  selftest  空输入/坏JSON/缺输入鲁棒性自检")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h", "help"):
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "pipeline.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
