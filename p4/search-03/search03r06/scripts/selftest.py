"""selftest.py — built-in self-test for the literature-screening skill.

Builds synthetic fixtures (clearly labelled as fixtures, never formal answers)
and exercises the full deterministic pipeline plus the model-fill path:

  1. normal case (4 candidates incl. a preprint/published duplicate pair)
  2. determinism (same input, twice, same ranking/decisions)
  3. empty input directory -> insufficient_evidence, exit code 0
  4. bad JSON -> graceful degradation, no crash, partial result
  5. model-filled ledger -> finalize validates and ranks

Exits 0 when all assertions pass; non-zero otherwise.
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as impl  # noqa: E402

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  [ok] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, detail))


def build_fixture_case(root):
    """Synthetic fixture input; explicitly labelled as fixture, not a formal answer."""
    case = {
        "case_id": "selftest_case",
        "research_question": "Effect of structured physical activity programs on glycemic control in adults with type 2 diabetes",
        "inclusion_criteria": [
            "randomized or non-randomized intervention studies in adults with type 2 diabetes",
            "reports HbA1c as an outcome",
        ],
        "exclusion_criteria": [
            "animal or in vitro studies",
            "editorials and commentaries",
        ],
        "papers": [
            {
                "title": "Structured exercise improves HbA1c in adults with type 2 diabetes: a randomized trial",
                "authors": ["A. Smith", "B. Jones"],
                "year": 2021,
                "venue": "Journal of Diabetes",
                "doi": "10.1000/selftest.001",
                "abstract": "We randomized 240 adults with type 2 diabetes to structured exercise or usual care and measured HbA1c at 12 months.",
                "source_type": "journal_article",
            },
            {
                "title": "Structured exercise improves HbA1c in adults with type 2 diabetes: a randomized trial (preprint)",
                "authors": ["A. Smith", "B. Jones"],
                "year": 2020,
                "venue": "",
                "doi": "",
                "abstract": "Preprint of the randomized trial reporting HbA1c outcomes in adults with type 2 diabetes.",
                "source_type": "preprint",
            },
            {
                "title": "Dietary patterns and cardiovascular risk",
                "authors": ["C. Lee"],
                "year": 2019,
                "venue": "Nutrition Reviews",
                "doi": "10.1000/selftest.002",
                "abstract": "A narrative review of diet and cardiovascular disease in the general population.",
                "source_type": "journal_article",
            },
            {
                "title": "Mouse model of pancreatic beta-cell function",
                "authors": ["D. Chen"],
                "year": 2018,
                "venue": "Cell Metabolism",
                "doi": "10.1000/selftest.003",
                "abstract": "In vitro study of beta-cell insulin secretion in mice.",
                "source_type": "journal_article",
            },
        ],
    }
    with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh)
    with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# selftest fixture\n\nchallenge_id: `challenge-selftest`\n")


def projection(result):
    """Deterministic projection ignoring timestamps/runtime."""
    recs = sorted(result.get("results", []), key=lambda r: str(r.get("record_id")))
    return {
        "status": result.get("status"),
        "records": [
            (r.get("record_id"), r.get("relevance"), r.get("tier"),
             r.get("priority_score"), r.get("rank"))
            for r in recs
        ],
        "dedupe_groups": sorted(
            [tuple(sorted(g.get("record_ids", []))) for g in result.get("dedupe_groups", [])]
        ),
    }


def run_normal_case():
    print("== 1. normal fixture: discover/scaffold/finalize ==")
    tmp = tempfile.mkdtemp(prefix="litskill_selftest_")
    try:
        inp = os.path.join(tmp, "input")
        work = os.path.join(tmp, "work")
        out = os.path.join(tmp, "output")
        os.makedirs(inp)
        build_fixture_case(inp)

        result = impl.full_pipeline(inp, work, out)
        check("pipeline ran", result["status"] in ("ok", "partial"), str(result))
        check("no structural errors", not result["errors"], str(result["errors"]))

        doc_path = os.path.join(out, "result.json")
        check("result.json exists", os.path.isfile(doc_path))
        with open(doc_path, encoding="utf-8") as fh:
            doc = json.load(fh)

        check("case_id present", doc.get("case_id") == "selftest_case")
        check("status present", doc.get("status") in impl.STATUS_ENUM, doc.get("status"))
        for field in ("sources", "uncertainty", "change_log", "results", "evidence"):
            check("field %s present" % field, field in doc)
        check("all 4 candidates covered", len(doc.get("results", [])) == 4,
              "got %d" % len(doc.get("results", [])))

        ids = {r["record_id"] for r in doc.get("results", [])}
        check("record_ids unique", len(ids) == 4)

        groups = doc.get("dedupe_groups", [])
        check("duplicate pair clustered", any(len(g.get("record_ids", [])) == 2 for g in groups),
              str(groups))

        # All records start relevance=unknown (honest, no fabrication).
        all_unknown = all(r["relevance"] == "unknown" for r in doc["results"])
        check("unfilled records stay relevance=unknown", all_unknown)

        return doc, work, out, tmp
    except Exception as exc:  # noqa: BLE001
        check("normal fixture ran without exception", False, repr(exc))
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_determinism():
    print("== 2. determinism: same input twice ==")
    base = tempfile.mkdtemp(prefix="litskill_det_")
    try:
        inp = os.path.join(base, "input")
        os.makedirs(inp)
        build_fixture_case(inp)
        outs = []
        for k in (1, 2):
            work = os.path.join(base, "work%d" % k)
            out = os.path.join(base, "output%d" % k)
            impl.full_pipeline(inp, work, out)
            with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
                outs.append(projection(json.load(fh)))
        check("deterministic ranking/decisions", outs[0] == outs[1])
    finally:
        shutil.rmtree(base, ignore_errors=True)


def run_empty_input():
    print("== 3. empty input -> insufficient_evidence, exit 0 ==")
    base = tempfile.mkdtemp(prefix="litskill_empty_")
    try:
        inp = os.path.join(base, "input")
        os.makedirs(inp)
        result = impl.full_pipeline(inp, os.path.join(base, "work"), os.path.join(base, "output"))
        check("status insufficient_evidence", result["status"] == "insufficient_evidence",
              result["status"])
        with open(os.path.join(base, "output", "result.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        check("legal result written for empty input", doc.get("status") == "insufficient_evidence")
        check("no fabricated candidates", doc.get("results") == [])
    finally:
        shutil.rmtree(base, ignore_errors=True)


def run_bad_json():
    print("== 4. bad JSON -> graceful degradation ==")
    base = tempfile.mkdtemp(prefix="litskill_bad_")
    try:
        inp = os.path.join(base, "input")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write('{"research_question": "x", "papers": [ {"title": "T"')
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# task\n")
        result = impl.full_pipeline(inp, os.path.join(base, "work"), os.path.join(base, "output"))
        with open(os.path.join(base, "output", "result.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        check("degradation recorded", bool(doc.get("degradations")) or bool(doc.get("errors")),
              str(doc.get("degradations")) + str(doc.get("errors")))
        check("still writes a legal result", doc.get("status") in impl.STATUS_ENUM)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def run_model_fill():
    print("== 5. model-filled ledger -> finalize validates and ranks ==")
    base = tempfile.mkdtemp(prefix="litskill_fill_")
    try:
        inp = os.path.join(base, "input")
        os.makedirs(inp)
        build_fixture_case(inp)
        work = os.path.join(base, "work")
        out = os.path.join(base, "output")
        impl.discover(inp, work, out)
        impl.scaffold(work, out)

        ledger_path = os.path.join(work, "evidence_ledger.json")
        with open(ledger_path, encoding="utf-8") as fh:
            ledger = json.load(fh)
        # Deterministic semantic fill for the fixture (fixture evidence, not a real case).
        rec_map = {}
        with open(os.path.join(work, "case_normalized.json"), encoding="utf-8") as fh:
            norm = json.load(fh)
        for cand in norm["candidates"]:
            t = (cand.get("title") or "").lower()
            if "dietary" in t:
                rel, score = "irrelevant", 0.1
            elif "mouse" in t or "beta-cell" in t:
                rel, score = "irrelevant", 0.0
            elif "preprint" in t:
                rel, score = "direct", 0.88
            else:
                rel, score = "direct", 0.95
            rec_map[cand["record_id"]] = (rel, score)
        for rec in ledger["records"]:
            rel, score = rec_map[rec["record_id"]]
            rec["relevance"] = rel
            rec["tier"] = "exclude" if rel == "irrelevant" else "retain"
            rec["priority_score"] = score
            rec["reasons"] = ["fixture reason"]
            rec["evidence"] = [{"locator": "case.json#papers[].abstract", "quote": "fixture quote"}]
            rec["confidence"] = "high"
            rec["criteria_matched"] = ["c01", "c02"]
        ledger["filled"] = True
        with open(ledger_path, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh)

        errors, warnings, status = impl.finalize(work, out)
        with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        check("status ok after full fill", status == "ok", status)
        check("no finalize errors", not errors, str(errors))
        # Tier gate: direct -> retain must be respected.
        direct = [r for r in doc["results"] if r["relevance"] == "direct"]
        check("direct records all retain", all(r["tier"] == "retain" for r in direct))
        # Top rank should be the highest-score direct record.
        ranked = sorted(doc["results"], key=lambda r: r["rank"])
        check("rank 1 is highest priority direct", ranked[0]["priority_score"] == 0.95,
              str(ranked[0]))
        # Duplicate variant demoted.
        variants = [r for r in doc["results"] if r.get("dedupe") and r["dedupe"].get("role") == "version_variant"]
        check("version variant demoted", len(variants) >= 1)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main(_argv):
    print("literature-screening-ranking selftest (core_impl v%s)" % impl.VERSION)
    run_normal_case()
    run_determinism()
    run_empty_input()
    run_bad_json()
    run_model_fill()
    print("---")
    print("selftest result: %d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
