#!/usr/bin/env python3
"""Consistency checker for a revision-response result.json.

Validates the output contract and the machine-checkable invariants that turn
"答得全、改得准、证得住、对得上" into concrete checks:

  1. contract fields present and correctly typed;
  2. traced claims carry a verbatim quote; assumptions are surfaced;
  3. every change has a searchable anchor and the anchor exists in the revised
     manuscript when one is supplied;
  4. every response entry resolves its change_ids to real changes and no change
     is orphaned;
  5. over-claiming ("已修改" with no change or evidence) is flagged;
  6. version numbers agree across changes, deliverable and response.

Never crashes on bad input; always exits 0 and prints a JSON report so the
harness can treat this as a diagnostic, not a failure. Standard library only.
"""
import argparse
import json
import os
import re
import sys

MAX_FILE_BYTES = 2 * 1024 * 1024
ALLOWED_STATUS = {"in_progress", "partial", "complete", "failed", "insufficient_evidence"}
ALLOWED_CLAIM_STATUS = {"traced", "derived", "assumption", "unverified", "source_conflict"}
ALLOWED_DECISION = {"accept", "partial-accept", "clarify", "rebut", "appeal"}
ALLOWED_TONE = {"accept", "clarify", "rebut", "appeal"}
ALLOWED_CHANGE_STATUS = {"applied", "pending", "reverted", "unapplied"}
OVERCLAIM_RE = re.compile(r"已修改|已完成|已补做|已补充|已完成实验", re.IGNORECASE)


def read_text(path):
    if not path or not os.path.exists(path):
        return None
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, OSError, LookupError):
            continue
    return None


def load_json(path):
    text = read_text(path)
    if text is None:
        return None, "unreadable: %s" % path
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, "invalid JSON: %s" % exc


def report(pass_count, warn_count, fail_count, items):
    return {
        "ok": fail_count == 0,
        "pass": pass_count,
        "warn": warn_count,
        "fail": fail_count,
        "items": items,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--manuscript", default=None)
    ap.add_argument("--response", default=None)
    ap.add_argument("--work", default="/app/work")
    a = ap.parse_args(argv)

    items = []
    pass_count = warn_count = fail_count = 0

    def add(level, name, detail):
        nonlocal pass_count, warn_count, fail_count
        items.append({"check": name, "status": level, "detail": detail})
        if level == "pass":
            pass_count += 1
        elif level == "warn":
            warn_count += 1
        else:
            fail_count += 1

    result, err = load_json(a.result)
    if result is None or not isinstance(result, dict):
        add("fail", "result-load", err or "result missing")
        print(json.dumps(report(pass_count, warn_count, fail_count, items), ensure_ascii=False, indent=2))
        return 0

    # 1. contract fields
    for field in ("case_id", "deliverable", "fact_source_mapping", "version_boundary"):
        if field in result:
            add("pass", "contract-" + field, "present")
        else:
            add("fail", "contract-" + field, "missing required field")

    if isinstance(result.get("case_id"), str) and result["case_id"].strip():
        add("pass", "case-id", result["case_id"])
    else:
        add("fail", "case-id", "empty or non-string")

    status = result.get("status")
    if status in ALLOWED_STATUS:
        add("pass", "status", status)
    else:
        add("fail", "status", "invalid status: %r" % status)

    # 2. fact_source_mapping
    mapping = result.get("fact_source_mapping", [])
    if not isinstance(mapping, list):
        add("fail", "mapping-type", "fact_source_mapping must be a list")
        mapping = []
    assumptions = []
    traced_no_quote = []
    for i, item in enumerate(mapping):
        if not isinstance(item, dict):
            add("fail", "mapping-item-%d" % i, "not an object")
            continue
        cid = item.get("claim_id", "<none>")
        st = item.get("status")
        if st not in ALLOWED_CLAIM_STATUS:
            add("fail", "mapping-status-%s" % cid, "invalid status: %r" % st)
        if st == "traced" and not (item.get("quote") or "").strip():
            traced_no_quote.append(cid)
        if st == "assumption":
            assumptions.append(cid)
        for key in ("claim_id", "claim", "source_ref"):
            if not item.get(key):
                add("warn", "mapping-field-%s" % cid, "missing %s" % key)
    if traced_no_quote:
        add("fail", "traced-quote", "traced claims missing verbatim quote: %s" % ", ".join(traced_no_quote))
    else:
        add("pass", "traced-quote", "all traced claims carry a quote")

    # assumptions surfaced in pending_human or uncertainty
    pending_items = result.get("pending_human", []) + result.get("uncertainty", [])
    surfaced = set()
    for it in pending_items:
        if isinstance(it, dict) and it.get("item"):
            surfaced.add(it["item"])
    unsurfaced = [c for c in assumptions if c not in surfaced]
    if unsurfaced:
        add("warn", "assumption-surfaced", "assumptions not surfaced in pending_human/uncertainty: %s" % ", ".join(unsurfaced))
    else:
        add("pass", "assumption-surfaced", "all assumptions surfaced")

    # 3. version_boundary changes + anchors
    vb = result.get("version_boundary")
    if not isinstance(vb, dict):
        add("fail", "vb-type", "version_boundary must be an object")
        vb = {}
    changes = vb.get("changes", [])
    if not isinstance(changes, list):
        add("fail", "changes-type", "changes must be a list")
        changes = []
    manuscript_text = read_text(a.manuscript) if a.manuscript else None
    change_ids = set()
    missing_anchor = []
    anchor_missing_from_ms = []
    same_before_after = []
    for i, ch in enumerate(changes):
        if not isinstance(ch, dict):
            add("fail", "change-%d" % i, "not an object")
            continue
        cid = ch.get("id", "C%02d" % i)
        change_ids.add(cid)
        anchor = (ch.get("anchor") or "").strip()
        if not anchor:
            missing_anchor.append(cid)
        elif manuscript_text is not None and anchor not in manuscript_text:
            anchor_missing_from_ms.append("%s:%s" % (cid, anchor[:20]))
        before = ch.get("before") or ""
        after = ch.get("after") or ""
        if before and before == after:
            same_before_after.append(cid)
        if ch.get("status") not in ALLOWED_CHANGE_STATUS:
            add("fail", "change-status-%s" % cid, "invalid status: %r" % ch.get("status"))
    if missing_anchor:
        add("fail", "change-anchor", "changes missing anchor: %s" % ", ".join(missing_anchor))
    else:
        add("pass", "change-anchor", "all changes have an anchor")
    if anchor_missing_from_ms:
        add("fail", "anchor-in-manuscript", "anchors not found in revised manuscript: %s" % ", ".join(anchor_missing_from_ms))
    elif manuscript_text is not None:
        add("pass", "anchor-in-manuscript", "all anchors found in revised manuscript")
    else:
        add("warn", "anchor-in-manuscript", "no manuscript supplied; anchor presence not checked")
    if same_before_after:
        add("warn", "change-diff", "before==after for: %s" % ", ".join(same_before_after))

    # 4. response_letter entries vs changes
    rl = result.get("response_letter")
    if not isinstance(rl, dict):
        add("fail", "response-type", "response_letter must be an object")
        rl = {}
    entries = rl.get("entries", [])
    if not isinstance(entries, list):
        add("fail", "entries-type", "response_letter.entries must be a list")
        entries = []
    referenced = set()
    bad_change_refs = []
    overclaims = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            add("fail", "entry-%d" % i, "not an object")
            continue
        cid = e.get("comment_id", "R%d" % i)
        if e.get("decision") not in ALLOWED_DECISION:
            add("fail", "entry-decision-%s" % cid, "invalid decision: %r" % e.get("decision"))
        if e.get("tone") not in ALLOWED_TONE:
            add("fail", "entry-tone-%s" % cid, "invalid tone: %r" % e.get("tone"))
        eids = e.get("change_ids", [])
        if not isinstance(eids, list):
            eids = []
        for rid in eids:
            if rid in change_ids:
                referenced.add(rid)
            else:
                bad_change_refs.append("%s->%s" % (cid, rid))
        summary = (e.get("summary") or "")
        if OVERCLAIM_RE.search(summary) and not eids and not e.get("evidence"):
            overclaims.append(cid)
    if bad_change_refs:
        add("fail", "change-ref", "response references missing change ids: %s" % ", ".join(bad_change_refs))
    else:
        add("pass", "change-ref", "all response change_ids resolve")
    orphans = sorted(change_ids - referenced)
    if orphans:
        add("warn", "orphan-change", "changes not referenced by any response entry: %s" % ", ".join(orphans))
    else:
        add("pass", "orphan-change", "no orphan changes")
    if overclaims:
        add("fail", "overclaim", "summary claims revised/completed but has no change_ids/evidence: %s" % ", ".join(overclaims))
    else:
        add("pass", "overclaim", "no over-claiming detected")

    # 5. version consistency
    versions = set()
    for ch in changes:
        if isinstance(ch, dict) and ch.get("manuscript_version"):
            versions.add(ch["manuscript_version"])
    dv = (result.get("deliverable") or {}).get("version")
    if dv:
        versions.add(dv)
    if len(versions) > 1:
        add("fail", "version-consistency", "conflicting versions: %s" % ", ".join(sorted(str(v) for v in versions)))
    elif versions:
        add("pass", "version-consistency", "single version: %s" % next(iter(versions)))
    else:
        add("warn", "version-consistency", "no version recorded")

    # 6. uncertainty/pending_human have reason+disposition
    incomplete = []
    for lst_name in ("uncertainty", "pending_human"):
        for it in result.get(lst_name, []):
            if isinstance(it, dict) and (not it.get("reason") or not it.get("disposition")):
                incomplete.append("%s:%s" % (lst_name, it.get("item", "<none>")))
    if incomplete:
        add("warn", "uncertainty-fields", "reason/disposition missing in: %s" % ", ".join(incomplete))
    else:
        add("pass", "uncertainty-fields", "uncertainty/pending_human entries complete")

    rep = report(pass_count, warn_count, fail_count, items)
    if a.work:
        try:
            os.makedirs(a.work, exist_ok=True)
            with open(os.path.join(a.work, "check_revision_report.json"), "w", encoding="utf-8") as f:
                json.dump(rep, f, ensure_ascii=False, indent=2)
                f.write("\n")
        except OSError:
            pass
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
