"""selftest.py — 鲁棒性自检脚本（仅标准库）。

覆盖评分维度「可靠体验」的三种必需场景：空输入目录、坏 JSON、
缺输入目录、坏编码材料。全部通过则退出码 0，否则退出码 1。
通过 import 复用 pipeline 的实现，避免重复逻辑漂移。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline  # noqa: E402


def main():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    scenarios = [
        ("empty_input", {"case.json": b"{}", "task.md": b"# empty"}),
        ("bad_json", {"case.json": b"{not valid json", "task.md": b"# bad"}),
        ("missing_input", {}),
        ("bad_encoding_csv", {"case.json": b'{"case_id":"c1"}',
                              "records.csv": b"\xff\xfe\x00title,abstract\n\xff\xfe\x00"}),
    ]
    passed = True
    for name, files in scenarios:
        indir = os.path.join(tmp, name, "input")
        outdir = os.path.join(tmp, name, "output")
        workdir = os.path.join(tmp, name, "work")
        os.makedirs(indir)
        for fname, content in files.items():
            with open(os.path.join(indir, fname), "wb") as fh:
                fh.write(content)
        try:
            ns = pipeline.argparse.Namespace(input=indir, output=outdir, work=workdir)
            pipeline.cmd_skeleton(ns)
            pipeline.cmd_normalize(ns)
            pipeline.cmd_finalize(ns)
            result = pipeline.safe_json_load(os.path.join(outdir, "result.json"))[0]
            status = result.get("status") if result else None
            good = status in ("insufficient_evidence", "partial", "input_error", "ok")
        except Exception as exc:  # noqa: BLE001
            status, good = "exception:%s" % exc, False
        mark = "PASS" if good else "FAIL"
        if not good:
            passed = False
        print("%s  %s -> status=%s" % (mark, name, status))
    print("selftest %s" % ("ALL PASS" if passed else "HAS FAILURES"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
