#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the weekly-skill package (robustness + contract + determinism).

Runs the pipeline against simulated edge cases in temp directories and verifies
that every scenario degrades gracefully: a legal result.json is written, the SVG
is well-formed XML, and no exception escapes. Exit code 0 = all checks passed.

Run:  python scripts/selftest.py
"""

import argparse
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main  # noqa: E402
import build_svg  # noqa: E402


def _args(input_dir, output_dir, work_dir, mode="init"):
    return argparse.Namespace(input=input_dir, output=output_dir, work=work_dir,
                              mode=mode, max_file_mb=2.0)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _svg_ok(path):
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        return root.tag.endswith("svg")
    except Exception:
        return False


CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


@check
def c_parse_args_defaults():
    a = main.parse_args([])
    assert a.input == "/input", a.input
    assert a.output == "/app/output", a.output
    assert a.work == "/app/work", a.work
    assert a.mode == "init", a.mode


@check
def c_empty_input():
    with tempfile.TemporaryDirectory() as d:
        inp, out, work = os.path.join(d, "in"), os.path.join(d, "out"), os.path.join(d, "work")
        os.makedirs(inp)
        rc = main.run_init(_args(inp, out, work))
        assert rc == 0, rc
        r = _read_json(os.path.join(out, "result.json"))
        assert r["status"] in ("insufficient_evidence", "partial", "input_error"), r["status"]
        assert r["case_id"] is None or isinstance(r["case_id"], (str, int))
        assert _svg_ok(os.path.join(out, "mechanism.svg"))


@check
def c_bad_json_case():
    with tempfile.TemporaryDirectory() as d:
        inp, out, work = os.path.join(d, "in"), os.path.join(d, "out"), os.path.join(d, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        rc = main.run_init(_args(inp, out, work))
        assert rc == 0, rc
        r = _read_json(os.path.join(out, "result.json"))
        assert r["errors"], "expected an error entry for bad case.json"
        assert _svg_ok(os.path.join(out, "mechanism.svg"))


@check
def c_bad_encoding():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "case.json")
        with open(p, "wb") as f:
            f.write(b"\xef\xbb\xbf\xff\xfe{\"id\": \"x\"}")  # BOM + invalid bytes
        text, err, size = main.read_text_bytes(p)
        assert text is not None and err is None, err
        # decoded-with-replacement is acceptable only when truly undecodable
        assert size > 0


@check
def c_oversized_file():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "big.md")
        with open(p, "wb") as f:
            f.write(b"x" * (main.MAX_TEXT_BYTES + 10))
        text, err, _ = main.read_text_bytes(p)
        assert text is None and err and "oversized" in err, err


@check
def c_discover_oversized_degraded():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "materials"))
        small = os.path.join(d, "materials", "small.md")
        big = os.path.join(d, "materials", "big.md")
        with open(small, "w", encoding="utf-8") as f:
            f.write("hello")
        with open(big, "wb") as f:
            f.write(b"y" * (main.MAX_TEXT_BYTES + 1))
        disc = main.discover_inputs(d)
        assert small in disc["materials"]
        assert big not in disc["materials"]
        assert any(x.get("scenario") == "oversized_file" for x in disc["degradations"])


@check
def c_normal_spec_build_deterministic():
    spec = {
        "title": "demo",
        "entities": [
            {"id": "e1", "name": "EGFR", "type": "protein", "compartment": "membrane",
             "transmembrane": True, "source": [{"locator": "materials/paper.md#p3", "quote": "EGFR"}]},
            {"id": "e2", "name": "TP53", "type": "gene", "compartment": "nucleus",
             "source": [{"locator": "materials/paper.md#p5", "quote": "TP53"}]},
        ],
        "relations": [
            {"id": "r1", "source": "e1", "target": "e2", "type": "activation",
             "direction": "forward", "evidence": "high",
             "source_meta": [{"locator": "materials/paper.md#p4", "quote": "activates TP53"}]},
        ],
        "data_mappings": [{"channel": "stroke_width", "field": "evidence_strength", "scale": "high=2.8"}],
    }
    svg1, w1 = build_svg.build_svg(spec)
    svg2, _ = build_svg.build_svg(spec)
    assert svg1 == svg2, "build must be deterministic"
    assert "layer-nodes" in svg1 and "layer-edges" in svg1 and "layer-legend" in svg1
    assert "data-entity-id=\"e1\"" in svg1 and "data-relation-id=\"r1\"" in svg1
    ET.fromstring(svg1)


@check
def c_placeholder_empty_spec():
    svg, w = build_svg.build_svg({})
    ET.fromstring(svg)
    assert "insufficient evidence" in svg


@check
def c_dangling_relation_skipped():
    spec = {"entities": [{"id": "e1", "name": "A", "type": "protein", "compartment": "cytosol"}],
            "relations": [{"id": "r1", "source": "e1", "target": "missing", "type": "activation"}]}
    svg, w = build_svg.build_svg(spec)
    ET.fromstring(svg)
    assert any("missing entity" in x for x in w)


@check
def c_contract_check():
    with tempfile.TemporaryDirectory() as d:
        good = main.result_template("c1")
        good["editable_source_files"] = [{"path": "mechanism.svg"}]
        open(os.path.join(d, "mechanism.svg"), "w", encoding="utf-8").write("<svg/>")
        c = main.check_result(good, d)
        assert not c["errors"], c["errors"]
        bad = {"case_id": "x"}  # missing required keys
        c2 = main.check_result(bad, d)
        assert c2["errors"]


@check
def c_init_idempotent_change_log():
    with tempfile.TemporaryDirectory() as d:
        inp, out, work = os.path.join(d, "in"), os.path.join(d, "out"), os.path.join(d, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "case-1"}, f)
        main.run_init(_args(inp, out, work))
        r1 = _read_json(os.path.join(out, "result.json"))
        n1 = len(r1["change_log"])
        # second identical init should not duplicate the skeleton entry
        main.run_init(_args(inp, out, work))
        r2 = _read_json(os.path.join(out, "result.json"))
        assert len(r2["change_log"]) == n1, (r1["change_log"], r2["change_log"])


@check
def c_missing_params_defaults():
    # explicit defaults are already asserted in c_parse_args_defaults; also verify env-free CLI shape
    a = main.parse_args(["finalize"])
    assert a.mode == "finalize" and a.input == "/input"


def run_all():
    passed, failed = 0, []
    for fn in CHECKS:
        name = fn.__name__[2:]
        try:
            fn()
            passed += 1
            print("PASS  %s" % name)
        except Exception as e:
            failed.append((name, "%s: %s" % (type(e).__name__, e)))
            print("FAIL  %s -> %s: %s" % (name, type(e).__name__, e))
    print("-" * 60)
    print("selftest: %d passed, %d failed" % (passed, len(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(run_all())
