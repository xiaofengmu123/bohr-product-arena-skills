# -*- coding: utf-8 -*-
"""CLI entry point for the academic-polish-invariants Skill.

Subcommands
-----------
scaffold   Discover /input, write a *partial* result.json plus the invariant
           registry skeleton (plan.json) and edit ledger skeleton (ledger.json)
           immediately -- the first-3-minutes partial-deliverable requirement.
verify     Run the deterministic conservation checks from invariants.py
           against /app/work/plan.json and /app/work/ledger.json and write
           check_report.json.
finalize   Assemble editable Markdown artifacts and the full result.json.
selfcheck  Validate the final result.json and referenced artifacts.

Default paths follow the runtime contract:
    --input /input   --output /app/output   --work /app/work
Run from the Skill root or anywhere; the import path is resolved relative to
this file, so the working directory does not matter. Standard library only;
no network; no model calls; deterministic layout.
"""

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import invariants  # noqa: E402

DEFAULT_CONTRACT = OrderedDict([
    # target editable artifact, relative to /app/output
    ('deliverable', None),
    # list of {fact, locator, quote} -- every fact/number/citation traceable
    ('fact_source_map', []),
    # list of {type, detail, status} -- diffs, human confirmations, gaps
    ('version_boundary', []),
])

STATUSES = {'ok', 'partial', 'insufficient_evidence', 'input_error'}
TEXT_EXT = {'.md', '.txt', '.tex', '.json', '.csv', '.tsv', '.yaml', '.yml',
            '.rst', '.xml', '.html', '.bib'}
MAX_TEXT_BYTES = 2 * 1024 * 1024
SCHEMA_VERSION = '1.0'


# ---------------------------------------------------------------------------
# Robust file I/O
# ---------------------------------------------------------------------------

def read_text(path, limit=MAX_TEXT_BYTES):
    """Read UTF-8 text with BOM/bad-encoding tolerance. Returns (text, enc, size)."""
    size = os.path.getsize(path)
    if size > limit:
        raise ValueError('file too large (%d bytes > %d)' % (size, limit))
    raw = open(path, 'rb').read()
    last_err = None
    for enc in ('utf-8-sig', 'utf-8', 'gb18030', 'latin-1'):
        try:
            return raw.decode(enc), enc, size
        except (UnicodeDecodeError, ValueError) as e:
            last_err = e
            continue
    raise last_err or ValueError('cannot decode ' + path)


def write_json(path, obj):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write('\n')


def read_json(path):
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return {'_read_error': str(e)}


def write_text(path, content):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


def file_digest(path, n=16):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()[:n]


def now_ts():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def discover(input_dir):
    """Return (files, case, task_text, errors). Never raises."""
    files, errors = [], []
    if not os.path.isdir(input_dir):
        return files, None, '', ['input directory missing: ' + input_dir]
    for root, _dirs, names in os.walk(input_dir):
        for n in sorted(names):
            if n.startswith('.'):
                continue
            p = os.path.join(root, n)
            if os.path.isfile(p):
                files.append(p)
    case = None
    task_text = ''
    for p in files:
        base = os.path.basename(p).lower()
        if base == 'case.json':
            parsed = read_json(p)
            if isinstance(parsed, dict) and '_read_error' in parsed:
                errors.append('case.json unreadable: %s' % parsed['_read_error'])
                case = None
            else:
                case = parsed
        elif base == 'task.md':
            try:
                task_text, _, _ = read_text(p)
            except Exception as e:
                errors.append('task.md unreadable: %s' % e)
    return files, case, task_text, errors


def _looks_like_draft_name(base):
    keys = ('manuscript', 'draft', 'abstract', 'grant', 'reply', 'response',
            'paper', 'text', 'article', 'proposal', 'summary', '原文', '底稿',
            '草稿', '稿件', '摘要')
    return any(k in base.lower() for k in keys)


def find_draft(case, files, task_text):
    """Locate the manuscript to polish. Returns (path, text) or (None, '')."""
    candidates = []
    if isinstance(case, dict):
        for key in ('text', 'draft', 'document', 'manuscript', 'source_text',
                    'passage', 'body', 'content'):
            v = case.get(key)
            if isinstance(v, str) and len(v.strip()) > 40:
                candidates.append(('case.json.' + key, v))
    contract_bases = {'case.json', 'task.md', 'challenge.json'}
    for p in files:
        base = os.path.basename(p).lower()
        if base in contract_bases:
            continue
        if os.path.splitext(base)[1] not in TEXT_EXT:
            continue
        try:
            txt, _enc, _size = read_text(p)
        except Exception:
            continue
        if _looks_like_draft_name(base) or len(txt) > 200:
            candidates.append((p, txt))
    if not candidates:
        best, best_len = None, -1
        for p in files:
            base = os.path.basename(p).lower()
            if base in contract_bases:
                continue
            if os.path.splitext(p)[1] not in TEXT_EXT:
                continue
            try:
                txt, _e, _s = read_text(p)
            except Exception:
                continue
            if len(txt) > best_len:
                best, best_len = p, len(txt)
        if best:
            try:
                txt, _e, _s = read_text(best)
                candidates.append((best, txt))
            except Exception:
                pass
    if not candidates:
        return None, ''
    # Prefer a real file over inline case.json fields.
    for p, txt in candidates:
        if not p.startswith('case.json.'):
            return p, txt
    return candidates[0]


def get_contract(case):
    """Return output_contract fields, preserving unknown keys, with defaults."""
    base = OrderedDict(DEFAULT_CONTRACT)
    if isinstance(case, dict) and isinstance(case.get('output_contract'), dict):
        for k, v in case['output_contract'].items():
            base[k] = v
    return base


def describe_sources(files, input_dir):
    srcs = []
    for p in files:
        rel = os.path.relpath(p, input_dir) if os.path.isdir(input_dir) else p
        ext = os.path.splitext(p)[1].lower()
        stype = 'text' if ext in TEXT_EXT else 'opaque'
        item = {'path': rel, 'type': stype, 'bytes': os.path.getsize(p),
                'sha': file_digest(p)}
        if stype == 'opaque':
            item['notes'] = 'binary; requires parser/OCR before content is trusted'
        srcs.append(item)
    return srcs


def case_id_of(case):
    if isinstance(case, dict):
        for k in ('case_id', 'id', 'caseId'):
            v = case.get(k)
            if v not in (None, ''):
                return str(v)
    return 'unknown'


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------

def scaffold(args):
    input_dir, output_dir, work_dir = args.input, args.output, args.work
    files, case, task_text, errors = discover(input_dir)
    draft_path, draft_text = find_draft(case, files, task_text)
    case_id = case_id_of(case)
    contract = get_contract(case)

    started = time.time()
    sources = describe_sources(files, input_dir)
    degradations = []
    status = 'partial'

    if not files or not os.path.isdir(input_dir):
        status = 'insufficient_evidence'
    elif not draft_text:
        status = 'insufficient_evidence'
        degradations.append('no manuscript draft located in /input')
    if errors:
        degradations.extend(errors)

    plan = {
        'schema_version': SCHEMA_VERSION,
        'case_id': case_id,
        'target_lang': None,
        'genre': None,
        'audience_notes': None,
        'style_rules': [],
        'sections': [],
        'invariants': [],
        'terminology': [],
        'gaps': [],
        'conflicts': [],
        'draft_locator': draft_path,
        '_scaffold': True,
    }
    ledger = {
        'schema_version': SCHEMA_VERSION,
        'case_id': case_id,
        'units': [],
        '_scaffold': True,
    }

    # partial result.json immediately (first-3-minutes requirement).
    result = OrderedDict()
    result['case_id'] = case_id
    for k in contract:
        result[k] = contract[k]
    result['deliverable'] = 'manuscript.md'
    result['status'] = status
    result['sources'] = sources
    result['uncertainty'] = []
    result['pending_human'] = []
    result['change_log'] = [{'phase': 'scaffold', 'action': 'partial skeleton written',
                             'elapsed_s': round(time.time() - started, 2)}]
    result['errors'] = list(errors)
    result['degradations'] = degradations
    result['runtime'] = {'started_at': now_ts(), 'seconds': round(time.time() - started, 2),
                         'note': 'scaffold only; finalize updates this'}

    write_json(os.path.join(output_dir, 'result.json'), result)
    write_json(os.path.join(work_dir, 'plan.json'), plan)
    write_json(os.path.join(work_dir, 'ledger.json'), ledger)
    return 0


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def verify(args):
    work_dir = args.work
    plan = read_json(os.path.join(work_dir, 'plan.json')) or {}
    ledger = read_json(os.path.join(work_dir, 'ledger.json')) or {}

    unit_results = {}
    for u in ledger.get('units', []):
        unit_results[u.get('id')] = invariants.verify_unit(u, plan)

    all_revised = '\n'.join(u.get('revised', '') for u in ledger.get('units', []))
    term_errors = invariants.verify_terminology(plan, all_revised)
    report = invariants.summarize_report(unit_results, term_errors, plan, ledger)
    report['schema_version'] = SCHEMA_VERSION
    report['case_id'] = plan.get('case_id') or ledger.get('case_id') or 'unknown'
    write_json(os.path.join(work_dir, 'check_report.json'), report)
    return 0


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

def finalize(args):
    input_dir, output_dir, work_dir = args.input, args.output, args.work
    files, case, task_text, discover_errors = discover(input_dir)
    case_id = case_id_of(case)
    contract = get_contract(case)
    plan = read_json(os.path.join(work_dir, 'plan.json')) or {}
    ledger = read_json(os.path.join(work_dir, 'ledger.json')) or {}
    report = read_json(os.path.join(work_dir, 'check_report.json')) or {}

    started = time.time()
    units = ledger.get('units', [])
    revised_all = '\n\n'.join(u.get('revised', '') for u in units)
    # Fall back to the original draft when no edits were recorded (skeleton path).
    if not revised_all.strip():
        _dp, draft_text = find_draft(case, files, task_text)
        revised_all = draft_text

    # --- manuscript.md ---
    lines = ['# 润色后稿件（可编辑）', '',
             '> 本文件为可编辑正稿；修改明细与证据见下方逐句表与 result.json。', '',
             '## 润色全文', '', revised_all.strip() or '（无正文：材料不足，未生成内容）', '']
    if units:
        lines += ['', '## 逐句修改表', '',
                  '| # | 原文 | 改文 | 修改类型 | 理由 | 触碰不变量 |', '|-|-|-|-|-|-|']
        for u in units:
            invs = ', '.join(u.get('touched_invariants', []) or ['-'])
            lines.append('| %s | %s | %s | %s | %s | %s |' % (
                u.get('id', ''), _cell(u.get('original', '')),
                _cell(u.get('revised', '')), u.get('edit_type', ''),
                _cell(u.get('reason', '')), invs))
    terms = plan.get('terminology', [])
    if terms:
        lines += ['', '## 术语表', '',
                  '| 概念 | 规范写法 | 禁用的变体 | 首次出现 |', '|-|-|-|-|']
        for t in terms:
            lines.append('| %s | %s | %s | %s |' % (
                _cell(t.get('concept', '')), _cell(t.get('canonical', '')),
                _cell('; '.join(t.get('variants', []))), _cell(t.get('first_occurrence', ''))))
    gaps = plan.get('gaps', [])
    if gaps:
        lines += ['', '## 材料缺口（未编造）', ''] + ['- MATERIAL GAP / not_reported: %s' % g for g in gaps]
    conflicts = plan.get('conflicts', [])
    if conflicts:
        lines += ['', '## 来源冲突', ''] + ['- source_conflict: %s' % c for c in conflicts]
    write_text(os.path.join(output_dir, 'manuscript.md'), '\n'.join(lines) + '\n')

    # --- glossary.md ---
    if terms:
        gl = ['# 术语表', '', '| 概念 | 规范写法 | 变体 | 首次出现 |', '|-|-|-|-|']
        for t in terms:
            gl.append('| %s | %s | %s | %s |' % (
                _cell(t.get('concept', '')), _cell(t.get('canonical', '')),
                _cell('; '.join(t.get('variants', []))), _cell(t.get('first_occurrence', ''))))
        write_text(os.path.join(output_dir, 'glossary.md'), '\n'.join(gl) + '\n')

    # --- result.json ---
    errors = list(report.get('errors', [])) + list(discover_errors)
    warnings = list(report.get('warnings', []))
    fact_map = invariants.build_fact_source_map(plan)
    boundary = invariants.build_version_boundary(plan, ledger)
    pending_human = [b for b in boundary if b.get('status') == 'pending']

    if errors:
        status = 'partial'
    elif pending_human or warnings or gaps or conflicts:
        status = 'partial'
    elif not units:
        status = 'insufficient_evidence' if not revised_all.strip() else 'partial'
    else:
        status = 'ok'

    sources = describe_sources(files, input_dir)
    sources.append({'path': 'work/plan.json', 'type': 'intermediate',
                    'bytes': os.path.getsize(os.path.join(work_dir, 'plan.json')),
                    'sha': file_digest(os.path.join(work_dir, 'plan.json')),
                    'notes': 'invariant registry (auditable)'})
    sources.append({'path': 'work/ledger.json', 'type': 'intermediate',
                    'bytes': os.path.getsize(os.path.join(work_dir, 'ledger.json')),
                    'sha': file_digest(os.path.join(work_dir, 'ledger.json')),
                    'notes': 'per-sentence edit ledger (auditable)'})

    result = OrderedDict()
    result['case_id'] = case_id
    for k in contract:
        result[k] = contract[k]
    result['deliverable'] = 'manuscript.md'
    result['fact_source_map'] = fact_map or []
    result['version_boundary'] = boundary or []
    result['status'] = status
    result['sources'] = sources
    result['uncertainty'] = plan.get('uncertainty', []) or []
    result['pending_human'] = pending_human
    result['change_log'] = list(plan.get('change_log', []))
    result['change_log'].append({
        'phase': 'finalize', 'action': 'full result assembled',
        'elapsed_s': round(time.time() - started, 2)})
    result['errors'] = errors
    result['degradations'] = plan.get('degradations', []) or []
    result['warnings'] = warnings
    result['runtime'] = {'finished_at': now_ts(), 'seconds': round(time.time() - started, 2),
                         'schema_version': SCHEMA_VERSION}
    write_json(os.path.join(output_dir, 'result.json'), result)
    return 0


def _cell(s):
    return str(s or '').replace('|', '\\|').replace('\n', ' ')


# ---------------------------------------------------------------------------
# selfcheck
# ---------------------------------------------------------------------------

def selfcheck(args):
    input_dir, output_dir, work_dir = args.input, args.output, args.work
    problems = []
    result = read_json(os.path.join(output_dir, 'result.json'))
    if result is None:
        problems.append('result.json missing')
        print('SELFCHECK FAIL: ' + '; '.join(problems))
        return 1
    if isinstance(result, dict) and '_read_error' in result:
        problems.append('result.json unreadable: %s' % result['_read_error'])
    if not result.get('case_id'):
        problems.append('case_id empty')
    if result.get('status') not in STATUSES:
        problems.append('invalid status: %r' % result.get('status'))

    files, case, _task, _errs = discover(input_dir)
    contract = get_contract(case)
    for k in contract:
        if k not in result:
            problems.append('output_contract key missing from result.json: %s' % k)

    deliverable = result.get('deliverable')
    if deliverable:
        dp = os.path.join(output_dir, deliverable)
        if not os.path.isfile(dp):
            problems.append('deliverable file missing: %s' % deliverable)
    for item in result.get('fact_source_map', []):
        if not all(item.get(k) for k in ('fact', 'locator', 'quote')):
            problems.append('fact_source_map item incomplete: %r' % item)

    if problems:
        print('SELFCHECK FAIL')
        for p in problems:
            print('  -', p)
        return 1
    print('SELFCHECK OK  status=%s  facts=%d  boundary=%d' % (
        result.get('status'), len(result.get('fact_source_map', [])),
        len(result.get('version_boundary', []))))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(description='Academic polish + invariant conservation Skill')
    p.add_argument('--input', default='/input', help='runtime input directory')
    p.add_argument('--output', default='/app/output', help='final artifacts directory')
    p.add_argument('--work', default='/app/work', help='intermediate artifacts directory')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('scaffold', help='write partial result.json + intermediate skeletons')
    sub.add_parser('verify', help='run deterministic conservation checks')
    sub.add_parser('finalize', help='assemble editable artifacts and full result.json')
    sub.add_parser('selfcheck', help='validate final result.json and artifacts')
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {
        'scaffold': scaffold,
        'verify': verify,
        'finalize': finalize,
        'selfcheck': selfcheck,
    }
    return handlers[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
