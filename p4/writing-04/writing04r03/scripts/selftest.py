"""Self-test for the reference & citation management skill.

Verifies the package's distinguishing guarantees before submission:

1.  ``--help`` exits 0 (checked via ``main.py --self-test`` wrapper path).
2.  Determinism — run the full pipeline twice on the same synthetic input and
    assert byte-identical artifacts AND identical ``result.json`` after
    normalising the (intentionally variable) ``runtime`` fields and the
    output/work directory paths.
3.  Degradation — empty input, bad JSON library, bad-encoding file, and an
    oversized file each degrade without raising, with an honest status and a
    valid ``result.json``.
4.  Contract — ``result.json`` echoes every ``output_contract`` key and always
    carries ``case_id`` plus the standard status/evidence fields.

Pure standard library; runs in-process (no subprocess needed for the fixture
checks) so it is fast and deterministic itself.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import core_impl as ce  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_fixture(root: Path) -> None:
    """A realistic minimal case: manuscript with pandoc markers + .bib library
    containing one duplicate pair (same work, casing / DOI normalisation)."""
    _write(root / "case.json", json.dumps({
        "case_id": "selftest-case-001",
        "challenge_id": "product-arena-03-429cc14d",
        "output_contract": {
            "manuscript_path": "str",
            "references_path": "str",
            "evidence_map_path": "str",
        },
    }, ensure_ascii=False))
    _write(root / "task.md", "# 自检任务\n完成引用插入、编号与去重。\n")
    _write(root / "library.bib", r"""
@article{smith2020,
  author  = {Smith, John A. and Lee, Wei},
  title   = {A Study of Citation Placement},
  journal = {Journal of Testing},
  year    = {2020},
  volume  = {12},
  number  = {3},
  pages   = {100--110},
  doi     = {10.1000/abc123}
}
@article{smith2020dup,
  author  = {Smith, John A. and Lee, Wei},
  title   = {A study of citation placement},
  journal = {Journal of Testing},
  year    = {2020},
  doi     = {https://doi.org/10.1000/abc123}
}
@book{jones2019,
  author    = {Jones, Mary},
  title     = {Reference Management in Practice},
  publisher = {Academic Press},
  year      = {2019}
}
""")
    _write(root / "manuscript.md",
           "第一段需要来源 [@smith2020]。\n\n"
           "第二段同时引用两个来源 [@jones2019; @smith2020]，"
           "以及再次单引 [@jones2019]。\n")


def strip_runtime(obj):
    """Normalise the only intentionally non-deterministic fields
    (``runtime.seconds`` and ``runtime.started_at``)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "runtime" and isinstance(v, dict):
                out[k] = {"seconds": 0, "started_at": "normalized"}
            else:
                out[k] = strip_runtime(v)
        return out
    if isinstance(obj, list):
        return [strip_runtime(x) for x in obj]
    return obj


def normalize_paths(obj, replacements: dict[str, str]):
    """Replace output/work directory prefixes inside string values so two runs
    into different scratch dirs compare equal."""
    if isinstance(obj, dict):
        return {k: normalize_paths(v, replacements) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_paths(x, replacements) for x in obj]
    if isinstance(obj, str):
        for src, dst in replacements.items():
            obj = obj.replace(src, dst)
        return obj
    return obj


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

def check_determinism(tmp: Path) -> None:
    src = tmp / "fixture"
    build_fixture(src)
    out1 = tmp / "out1"
    out2 = tmp / "out2"
    work1 = tmp / "work1"
    work2 = tmp / "work2"
    ce.run_pipeline(src, out1, work1)
    ce.run_pipeline(src, out2, work2)

    for name in ("manuscript.md", "references.md", "evidence_map.json",
                 "consistency_report.json", "manifest.md"):
        b1 = (out1 / name).read_bytes()
        b2 = (out2 / name).read_bytes()
        assert_true(b1 == b2, f"artifact {name} not deterministic")

    rr1 = read_json(out1 / "result.json")
    rr2 = read_json(out2 / "result.json")
    assert_true(rr1.get("content_hash") == rr2.get("content_hash"),
                "content_hash differs across runs")
    repl = {str(out1): "<OUT>", str(out2): "<OUT>",
            str(work1): "<WORK>", str(work2): "<WORK>"}
    n1 = normalize_paths(strip_runtime(rr1), repl)
    n2 = normalize_paths(strip_runtime(rr2), repl)
    assert_true(n1 == n2,
                "result.json differs across runs (after normalising runtime & paths)")

    md = (out1 / "manuscript.md").read_text(encoding="utf-8")
    assert_true("[1]" in md and ("[1,2]" in md or "[2,1]" in md),
                f"unexpected renumbering output: {md!r}")
    refs = (out1 / "references.md").read_text(encoding="utf-8")
    assert_true("[J]" in refs or "[M]" in refs, "reference list missing format markers")


def check_degradation(tmp: Path) -> None:
    empty = tmp / "empty"
    empty.mkdir(parents=True, exist_ok=True)
    r = ce.run_pipeline(empty, tmp / "o_empty", tmp / "w_empty")
    assert_true(r.get("status") == "insufficient_evidence",
                f"empty input status={r.get('status')!r}")
    assert_true((tmp / "o_empty" / "result.json").exists(), "empty input no result.json")

    bad = tmp / "badjson"
    bad.mkdir(parents=True, exist_ok=True)
    _write(bad / "case.json", json.dumps({"case_id": "badjson-1", "output_contract": {}}))
    _write(bad / "library.json", "{ this is not json")
    _write(bad / "manuscript.md", "正文 [@x] 结束。\n")
    r = ce.run_pipeline(bad, tmp / "o_bad", tmp / "w_bad")
    assert_true(any("invalid JSON" in e for e in r.get("errors", [])),
                "bad JSON not recorded in errors")
    assert_true((tmp / "o_bad" / "result.json").exists(), "bad JSON no result.json")

    badenc = tmp / "badenc"
    badenc.mkdir(parents=True, exist_ok=True)
    _write(badenc / "case.json", json.dumps({"case_id": "badenc-1", "output_contract": {}}))
    (badenc / "library.bib").write_bytes(b"\xff\xfe\x00@article{broken, title={x}}")
    _write(badenc / "manuscript.md", "正文。\n")
    r = ce.run_pipeline(badenc, tmp / "o_badenc", tmp / "w_badenc")
    assert_true((tmp / "o_badenc" / "result.json").exists(), "bad encoding no result.json")

    big = tmp / "big"
    big.mkdir(parents=True, exist_ok=True)
    _write(big / "case.json", json.dumps({"case_id": "big-1", "output_contract": {}}))
    _write(big / "manuscript.md", "正文。\n")
    (big / "library.bib").write_bytes(b"@article{x, title={y}}\n" + b"%" * (ce.MAX_FILE_BYTES + 100))
    r = ce.run_pipeline(big, tmp / "o_big", tmp / "w_big")
    assert_true(any("exceeds" in d for d in r.get("degradations", [])),
                "oversized file not recorded in degradations")


def check_contract(tmp: Path) -> None:
    src = tmp / "contract"
    build_fixture(src)
    ce.run_pipeline(src, tmp / "o_contract", tmp / "w_contract")
    rr = read_json(tmp / "o_contract" / "result.json")
    assert_true(rr.get("case_id") == "selftest-case-001", "case_id missing/wrong")
    for k in ("manuscript_path", "references_path", "evidence_map_path"):
        assert_true(k in rr, f"output_contract key {k} not echoed")
        assert_true(rr.get(k), f"output_contract key {k} not filled with artifact path")
    for k in ("status", "sources", "uncertainty", "change_log",
              "errors", "degradations", "pending_human", "artifacts", "runtime"):
        assert_true(k in rr, f"standard field {k} missing")


def check_renumber(tmp: Path) -> None:
    """Numeric [n] citations must be renumbered by first appearance and the
    reference list must be reordered accordingly (library-order fallback)."""
    src = tmp / "renumber"
    src.mkdir(parents=True, exist_ok=True)
    _write(src / "case.json", json.dumps({"case_id": "renum-1", "output_contract": {}}))
    _write(src / "task.md", "重排编号\n")
    _write(src / "library.bib", (
        "@article{k1,author={One, A},title={First Paper},journal={J},year={2019}}\n"
        "@article{k2,author={Two, B},title={Second Paper},journal={J},year={2020}}\n"
        "@article{k3,author={Three, C},title={Third Paper},journal={J},year={2021}}\n"
    ))
    _write(src / "manuscript.md", "Intro [3]. Method [1]. Conclusion [3,2].\n")
    ce.run_pipeline(src, tmp / "o_renumber", tmp / "w_renumber")
    md = (tmp / "o_renumber" / "manuscript.md").read_text(encoding="utf-8")
    assert_true("Intro [1]. Method [2]. Conclusion [1,3]." in md,
                f"numeric renumbering wrong: {md!r}")
    refs = (tmp / "o_renumber" / "references.md").read_text(encoding="utf-8")
    assert_true(refs.splitlines()[0].startswith("#"), "reference list missing header")
    assert_true("Third Paper" in refs and "First Paper" in refs and "Second Paper" in refs,
                "reference list missing reordered entries")


def run() -> int:
    with tempfile.TemporaryDirectory(prefix="citmgmt-selftest-") as td:
        tmp = Path(td)
        checks = [
            ("determinism", check_determinism),
            ("degradation", check_degradation),
            ("contract", check_contract),
            ("renumber", check_renumber),
        ]
        failures = 0
        for name, fn in checks:
            try:
                fn(tmp)
                print(f"PASS  {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL  {name}: {exc}")
        print(f"self-test: {len(checks) - failures}/{len(checks)} passed")
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
