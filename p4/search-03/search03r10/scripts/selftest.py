"""selftest.py — full-pipeline self-test on a synthetic, clearly-labelled fixture.

The two candidate papers below exist only to exercise the pipeline's logic
(discover -> signal -> ledger -> merge -> finalize). They are NOT a formal
case and must not be mistaken for one; the platform's three real cases are run
separately by the official harness.
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core  # noqa: E402

FIXTURE = {
    "case_id": "synthetic-selftest",
    "research_question": "Effect of exercise on cognition in older adults",
    "inclusion_criteria": ["older adults", "cognition outcome"],
    "exclusion_criteria": ["animals"],
    "papers": [
        {
            "id": "p1",
            "title": "Aerobic exercise improves executive function in older adults",
            "authors": ["Smith J", "Lee A"],
            "year": 2020,
            "venue": "Journal of Gerontology",
            "doi": "10.1000/synthetic.p1",
            "abstract": "Randomized trial of aerobic exercise versus control on executive function in 200 older adults.",
        },
        {
            "id": "p2",
            "title": "Dietary protein intake in young athletes",
            "authors": ["Nguyen T"],
            "year": 2019,
            "venue": "Sports Nutrition",
            "doi": None,
            "abstract": "Survey of protein intake in adolescent athletes; no cognition outcome and no older adult population.",
        },
    ],
}


def main():
    tmp = tempfile.mkdtemp(prefix="screening-selftest-")
    inp = os.path.join(tmp, "input")
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "output")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(FIXTURE, fh, ensure_ascii=False)

    core.discover(inp, work, out)
    core.signal(work, out)

    ledger = os.path.join(work, "decisions.jsonl")
    decisions = [
        {"record_id": "p1", "relevance": "direct", "tier": "retain", "confidence": "high",
         "reasons": ["population and outcome match inclusion criteria"],
         "criteria_matched": ["older adults", "cognition outcome"],
         "evidence": [{"locator": "case.json#papers[0].abstract",
                       "quote": "Randomized trial of aerobic exercise versus control on executive function in 200 older adults."}]},
        {"record_id": "p2", "relevance": "irrelevant", "tier": "exclude", "confidence": "high",
         "reasons": ["young athletes, no cognition outcome"],
         "criteria_matched": [],
         "evidence": [{"locator": "case.json#papers[1].abstract",
                       "quote": "Survey of protein intake in adolescent athletes; no cognition outcome and no older adult population."}]},
    ]
    with open(ledger, "w", encoding="utf-8") as fh:
        for d in decisions:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")

    core.merge(work, out)
    sc = core.finalize(work, out)

    result_path = os.path.join(out, "result.json")
    with open(result_path, encoding="utf-8") as fh:
        result = json.load(fh)

    ok = (sc.get("pass") is True and result.get("case_id") == "synthetic-selftest"
          and len(result["results"]) == 2
          and result["results"][0]["record_id"] == "p1")
    if not ok:
        print("SELFTEST FAIL")
        print(json.dumps({"selfcheck_errors": sc.get("errors"), "top_results": result["results"]},
                         ensure_ascii=False, indent=2))
        return 1
    print("SELFTEST PASS: synthetic fixture, 2 candidates, errors=0, "
          "top rank=%s (relevance=%s, score=%s)" % (
              result["results"][0]["record_id"],
              result["results"][0]["relevance"],
              result["results"][0]["priority_score"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())