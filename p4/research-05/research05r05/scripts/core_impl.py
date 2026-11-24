#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研选题与创新性评估 Skill 的核心实现。

只依赖 Python 标准库。所有输入按扩展名识别、按字节真实读取，坏 JSON / 坏编码 /
缺软件 / 缺文件 / 超 2MB 文件 / 写盘失败都记录为 errors / degradations 而不是崩溃。
输出契约：result.json 逐字段对齐 case.json 的 output_contract，并维护 change_log。
"""
import os
import sys
import json
import math
import shutil
import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024

TEXT_EXTS = {'.md', '.txt', '.json', '.csv', '.tsv', '.xml', '.html', '.htm',
             '.yaml', '.yml', '.tex', '.bib', '.ris', '.rst', '.log', '.m'}
PDF_EXTS = {'.pdf'}
OFFICE_EXTS = {'.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls', '.odt', '.ods', '.odp'}
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.tif', '.tiff'}

# 这些顶层字段由脚本/契约保留，output_contract 同名键不被回填覆盖。
RESERVED_KEYS = {
    'case_id', 'status', 'output_contract', 'contract_alignment',
    'research_question', 'search_protocol', 'prior_work', 'novelty',
    'feasibility', 'evidence_matrix', 'conflicts', 'synthesis', 'sources',
    'uncertainty', 'pending_human', 'errors', 'degradations', 'runtime',
    'change_log',
}

CORE_DELIVERABLES = ('research_question', 'prior_work', 'novelty', 'feasibility')


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _ensure_dirs(*paths):
    for p in paths:
        Path(p).mkdir(parents=True, exist_ok=True)


def _read_text(path):
    """按 utf-8-sig → utf-8 → latin-1 容错读取，返回 (text, encoding, error)。"""
    path = Path(path)
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        return None, None, {'code': 'file_too_large',
                            'detail': '%d bytes > %d' % (size, MAX_FILE_BYTES)}
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, None, {'code': 'read_error', 'detail': str(exc)}
    for enc in ('utf-8-sig', 'utf-8'):
        try:
            return raw.decode(enc), enc, None
        except UnicodeDecodeError:
            continue
    try:
        return raw.decode('latin-1'), 'latin-1', {'code': 'fallback_encoding',
                                                  'detail': 'latin-1'}
    except Exception as exc:  # pragma: no cover - 极端情况
        return None, None, {'code': 'decode_error', 'detail': str(exc)}


def _load_json(path):
    text, enc, err = _read_text(path)
    if text is None:
        return None, err
    try:
        return json.loads(text), err
    except json.JSONDecodeError as exc:
        return None, {'code': 'bad_json', 'detail': str(exc)}
    except Exception as exc:  # pragma: no cover
        return None, {'code': 'json_error', 'detail': str(exc)}


def _write_json(obj, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True,
                  allow_nan=False)
    os.replace(tmp, str(path))


def _write_text(text, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), 'w', encoding='utf-8') as fh:
        fh.write(text)


def _sanitize_nan(obj):
    """把 float 的 NaN/Inf 换成 None，保证输出是合法 JSON。"""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_nan(v) for v in obj]
    return obj


def _find_nan(obj, path='$'):
    out = []
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            out.append(path)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_find_nan(v, '%s.%s' % (path, k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_find_nan(v, '%s[%d]' % (path, i)))
    return out


def derive_case_id(case_data, input_dir):
    for key in ('case_id', 'id', 'armiId', 'challenge_id'):
        val = (case_data or {}).get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    base = Path(input_dir)
    names = []
    if base.is_dir():
        for p in base.rglob('*'):
            try:
                if p.is_file():
                    names.append(p.relative_to(base).as_posix())
            except OSError:
                continue
        names.sort()
    if names:
        digest = hashlib.sha256('\n'.join(names).encode('utf-8')).hexdigest()[:12]
        return 'unresolved-' + digest
    return 'unresolved-input'


def contract_of(case_data):
    c = (case_data or {}).get('output_contract')
    return c if isinstance(c, dict) else {}


def _scan_input(input_dir):
    base = Path(input_dir)
    records = []
    if not base.is_dir():
        return records
    for p in base.rglob('*'):
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        records.append({'path': p.relative_to(base).as_posix(), 'abs': str(p),
                        'size': p.stat().st_size, 'ext': p.suffix.lower()})
    records.sort(key=lambda r: r['path'])
    return records


def _kind_of(ext):
    if ext in PDF_EXTS:
        return 'pdf'
    if ext in TEXT_EXTS:
        return 'text'
    if ext in OFFICE_EXTS:
        return 'office'
    if ext in IMAGE_EXTS:
        return 'image'
    return 'unknown'


# ---------------------------------------------------------------------------
# Phase 0: skeleton
# ---------------------------------------------------------------------------
def run_skeleton(input_dir, output_dir, work_dir):
    input_dir, output_dir, work_dir = Path(input_dir), Path(output_dir), Path(work_dir)
    _ensure_dirs(output_dir, work_dir)
    ts = now_iso()
    errors, degradations = [], []

    case_data, case_json_err = None, None
    if not input_dir.is_dir():
        errors.append({'code': 'missing_input_dir', 'detail': str(input_dir)})
    else:
        case_path = input_dir / 'case.json'
        if case_path.is_file():
            case_data, case_json_err = _load_json(case_path)
            if case_json_err:
                errors.append({'code': case_json_err['code'],
                               'detail': case_json_err['detail'], 'file': 'case.json'})
                case_data = None
        else:
            case_json_err = {'code': 'missing_case_json',
                             'detail': 'case.json not found'}
            errors.append(dict(case_json_err))

    contract = contract_of(case_data)
    task_md = {'present': False}
    if input_dir.is_dir():
        tp = input_dir / 'task.md'
        if tp.is_file():
            task_md = {'present': True, 'path': 'task.md', 'size': tp.stat().st_size}

    result = {
        'case_id': derive_case_id(case_data, input_dir),
        'status': 'partial',
        'output_contract': contract,
        'research_question': None,
        'search_protocol': None,
        'prior_work': None,
        'novelty': None,
        'feasibility': None,
        'evidence_matrix': [],
        'conflicts': [],
        'synthesis': None,
        'sources': [],
        'uncertainty': None,
        'pending_human': [],
        'errors': errors,
        'degradations': degradations,
        'runtime': {'started_at': ts, 'phase': 'skeleton', 'updated_at': ts},
        'change_log': [],
    }

    # 契约逐字段对齐：原样回显 + 回填到顶层（缺值 null），并记录对齐状态。
    alignment = {}
    for key in sorted(contract.keys()):
        collides = key in RESERVED_KEYS
        alignment[key] = {
            'contract_value': contract[key],
            'top_level': None,
            'filled': False,
            'collides_with_reserved': collides,
            'source': 'reserved' if collides else None,
        }
        if not collides:
            result[key] = None
    result['contract_alignment'] = alignment

    result['change_log'].append({
        'at': ts, 'actor': 'skeleton', 'action': 'create_skeleton',
        'file': 'result.json', 'status': 'partial',
        'detail': 'contract keys flattened: ' + (', '.join(sorted(contract)) or '(none)'),
    })

    _write_json(result, output_dir / 'result.json')
    _write_json({
        'case_id': result['case_id'],
        'input_dir': str(input_dir),
        'case_json': {'present': case_data is not None, 'error': case_json_err},
        'task_md': task_md,
        'contract_keys': sorted(contract),
        'created_at': ts,
    }, work_dir / 'input_manifest.json')
    return 0


# ---------------------------------------------------------------------------
# Phase 1: evidence
# ---------------------------------------------------------------------------
def _handle_pdf(rel, abs_path, work_dir, degradations):
    size = Path(abs_path).stat().st_size
    extracted_dir = Path(work_dir) / 'extracted'
    _ensure_dirs(extracted_dir)
    out_txt = extracted_dir / (rel.replace('/', '__') + '.txt')
    tool = shutil.which('pdftotext')
    if not tool:
        degradations.append({'code': 'pdftotext_missing', 'file': rel,
                             'detail': 'pdftotext not on PATH'})
        return {'source_id': rel, 'kind': 'pdf', 'size': size, 'encoding': None,
                'text_status': 'text_unavailable',
                'error': {'code': 'pdftotext_missing',
                          'detail': 'pdftotext not on PATH'}}
    err_log = str(out_txt) + '.err.txt'
    try:
        with open(err_log, 'w', encoding='utf-8') as ef:
            proc = subprocess.run([tool, '-layout', abs_path, str(out_txt)],
                                  stdout=subprocess.DEVNULL, stderr=ef, timeout=120)
        if proc.returncode != 0 or not out_txt.is_file():
            detail = _read_error_tail(err_log)
            degradations.append({'code': 'pdf_extract_failed', 'file': rel,
                                 'detail': detail})
            return {'source_id': rel, 'kind': 'pdf', 'size': size, 'encoding': None,
                    'text_status': 'text_unavailable',
                    'error': {'code': 'pdf_extract_failed', 'detail': detail}}
        text, enc, rd_err = _read_text(out_txt)
        if text is None:
            degradations.append({'code': 'pdf_extract_failed', 'file': rel,
                                 'detail': (rd_err or {}).get('detail', 'read failed')})
            return {'source_id': rel, 'kind': 'pdf', 'size': size, 'encoding': enc,
                    'text_status': 'text_unavailable', 'error': rd_err}
        return {'source_id': rel, 'kind': 'pdf', 'size': size, 'encoding': enc,
                'text_status': 'readable',
                'extracted_text': 'extracted/' + (rel.replace('/', '__') + '.txt'),
                'line_count': text.count('\n') + 1}
    except Exception as exc:
        degradations.append({'code': 'pdf_extract_failed', 'file': rel,
                             'detail': str(exc)})
        return {'source_id': rel, 'kind': 'pdf', 'size': size, 'encoding': None,
                'text_status': 'text_unavailable',
                'error': {'code': 'pdf_extract_failed', 'detail': str(exc)}}


def _read_error_tail(path):
    try:
        data = Path(path).read_text(encoding='utf-8', errors='replace').strip()
        return data[:300] if data else 'pdftotext returned non-zero'
    except OSError:
        return 'pdftotext returned non-zero'


def run_evidence(input_dir, output_dir, work_dir):
    input_dir, output_dir, work_dir = Path(input_dir), Path(output_dir), Path(work_dir)
    _ensure_dirs(output_dir, work_dir)
    ts = now_iso()

    result_path = output_dir / 'result.json'
    if not result_path.is_file():
        run_skeleton(input_dir, output_dir, work_dir)
    result, _err = _load_json(result_path)
    if result is None or not isinstance(result, dict):
        run_skeleton(input_dir, output_dir, work_dir)
        result, _err = _load_json(result_path)
        if result is None or not isinstance(result, dict):
            result = {}

    errors = list(result.get('errors') or [])
    degradations = list(result.get('degradations') or [])

    inventory = {'created_at': ts, 'input_dir': str(input_dir), 'materials': []}
    sources = []
    readable_count = 0

    for rec in _scan_input(input_dir):
        rel = rec['path']
        if rel == 'case.json':
            continue  # 已在 skeleton 阶段作为元信息解析
        abs_path = rec['abs']
        size = rec['size']
        ext = rec['ext']

        if size > MAX_FILE_BYTES:
            item = {'source_id': rel, 'kind': _kind_of(ext), 'size': size,
                    'encoding': None, 'text_status': 'skipped_too_large',
                    'error': {'code': 'file_too_large', 'detail': '%d bytes' % size}}
            degradations.append({'code': 'file_too_large', 'file': rel,
                                 'detail': '%d bytes' % size})
            sources.append(item)
            inventory['materials'].append({'path': rel, 'abs': abs_path, **item})
            continue

        if ext in PDF_EXTS:
            item = _handle_pdf(rel, abs_path, work_dir, degradations)
        elif ext in TEXT_EXTS or rel == 'task.md':
            text, enc, rd_err = _read_text(abs_path)
            if text is None:
                item = {'source_id': rel, 'kind': _kind_of(ext), 'size': size,
                        'encoding': enc, 'text_status': 'text_unavailable',
                        'error': rd_err}
                degradations.append({'code': (rd_err or {}).get('code', 'read_error'),
                                     'file': rel, 'detail': (rd_err or {}).get('detail', '')})
            else:
                item = {'source_id': rel, 'kind': _kind_of(ext), 'size': size,
                        'encoding': enc, 'text_status': 'readable',
                        'line_count': text.count('\n') + 1}
                readable_count += 1
        else:
            item = {'source_id': rel, 'kind': _kind_of(ext), 'size': size,
                    'encoding': None, 'text_status': 'text_unavailable',
                    'error': {'code': 'unsupported_type',
                              'detail': ext or '(no extension)'}}
            degradations.append({'code': 'unsupported_type', 'file': rel,
                                 'detail': ext or '(no extension)'})

        sources.append(item)
        inventory['materials'].append({'path': rel, 'abs': abs_path, **item})

    result['sources'] = sources
    result['errors'] = errors
    result['degradations'] = degradations
    runtime = result.get('runtime')
    if not isinstance(runtime, dict):
        runtime = {}
    runtime['phase'] = 'evidence'
    runtime['updated_at'] = ts
    result['runtime'] = runtime

    has_task = any(s['source_id'] == 'task.md' and s.get('text_status') == 'readable'
                   for s in sources)
    if not input_dir.is_dir():
        result['status'] = 'input_error'
    elif readable_count == 0 and not has_task:
        result['status'] = 'insufficient_evidence'

    log = result.get('change_log')
    if not isinstance(log, list):
        log = []
        result['change_log'] = log
    log.append({
        'at': ts, 'actor': 'evidence', 'action': 'scan_materials',
        'file': 'result.json', 'status': result.get('status'),
        'detail': '%d sources, %d readable; pdf text under extracted/' % (len(sources), readable_count),
    })

    _write_json(result, result_path)
    _write_json(inventory, work_dir / 'material_inventory.json')
    return 0


# ---------------------------------------------------------------------------
# Phase 4: finalize
# ---------------------------------------------------------------------------
def _deep_merge(base, patch):
    """深合并 fragment 到 result；list 拼接，dict 递归，标量替换。返回顶层键列表。"""
    touched = []
    for k, v in patch.items():
        touched.append(k)
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        elif k in base and isinstance(base[k], list) and isinstance(v, list):
            base[k] = base[k] + v
        else:
            base[k] = v
    return touched


def _recompute_status(result):
    codes = {e.get('code') for e in (result.get('errors') or []) if isinstance(e, dict)}
    if 'missing_input_dir' in codes:
        return 'input_error'
    filled = [result.get(k) for k in CORE_DELIVERABLES if result.get(k) is not None]
    readable = [s for s in (result.get('sources') or []) if s.get('text_status') == 'readable']
    if not filled and not readable:
        return 'insufficient_evidence'
    if len(filled) == len(CORE_DELIVERABLES):
        return 'ok'
    return 'partial'


def _brief(v):
    if v is None:
        return '未完成'
    if isinstance(v, list):
        return '共 %d 条' % len(v)
    if isinstance(v, dict):
        for key in ('summary', 'conclusion', 'verdict', 'status', 'testable_statement'):
            if v.get(key):
                s = str(v[key])
                return (s[:120] + '…') if len(s) > 120 else s
        return '已完成（%d 个字段）' % len(v)
    s = str(v)
    return (s[:120] + '…') if len(s) > 120 else s


def _render_report(result):
    L = ['# 科研选题与创新性评估报告', '',
         '> 本报告由 `scripts/main.py finalize` 自动生成，只汇总 result.json 已有内容，不新增事实。', '']
    L.append('- case_id：`%s`' % result.get('case_id', ''))
    L.append('- status：`%s`' % result.get('status', ''))
    L.append('- generated_at：`%s`' % now_iso())
    L.append('')
    L.append('## 1. 契约对齐')
    align = result.get('contract_alignment') or {}
    if not align:
        L.append('（case.json 未提供 output_contract）')
    for k, v in sorted(align.items()):
        mark = '已填' if v.get('filled') else '待填'
        src = v.get('source') or '—'
        L.append('- `%s`：%s，来源 %s' % (k, mark, src))
    L.append('')
    L.append('## 2. 四项交付')
    for k, label in (('research_question', '研究问题定义'), ('prior_work', '已有工作比较'),
                     ('novelty', '创新点识别'), ('feasibility', '可行性判断')):
        L.append('- %s（`%s`）：%s' % (label, k, _brief(result.get(k))))
    L.append('- 检索协议（`search_protocol`）：%s' % _brief(result.get('search_protocol')))
    L.append('')
    L.append('## 3. 证据与冲突')
    L.append('- 证据矩阵条目：%d' % len(result.get('evidence_matrix') or []))
    L.append('- 冲突证据：%d' % len(result.get('conflicts') or []))
    L.append('- 来源文件：%d' % len(result.get('sources') or []))
    L.append('')
    L.append('## 4. 综合结论与边界')
    syn = result.get('synthesis')
    if isinstance(syn, dict):
        for k, label in (('conclusions', '结论'), ('research_gaps', '研究空白'),
                         ('boundary_statement', '边界说明')):
            L.append('- %s：%s' % (label, _brief(syn.get(k))))
    else:
        L.append('（未完成）')
    L.append('')
    L.append('## 5. 不确定性 / 待人工确认 / 错误 / 降级')
    L.append('- uncertainty：%s' % _brief(result.get('uncertainty')))
    L.append('- pending_human：%d 项' % len(result.get('pending_human') or []))
    L.append('- errors：%d 项' % len(result.get('errors') or []))
    L.append('- degradations：%d 项' % len(result.get('degradations') or []))
    L.append('')
    L.append('## 6. 改动记录（change_log）')
    for e in result.get('change_log') or []:
        if isinstance(e, dict):
            L.append('- [%s] %s %s → %s' % (e.get('at', ''), e.get('actor', ''),
                                            e.get('action', ''), e.get('status', '')))
    return '\n'.join(L) + '\n'


def run_finalize(input_dir, output_dir, work_dir):
    input_dir, output_dir, work_dir = Path(input_dir), Path(output_dir), Path(work_dir)
    _ensure_dirs(output_dir, work_dir)
    ts = now_iso()

    result_path = output_dir / 'result.json'
    if not result_path.is_file():
        run_skeleton(input_dir, output_dir, work_dir)
    result, _err = _load_json(result_path)
    if result is None or not isinstance(result, dict):
        run_skeleton(input_dir, output_dir, work_dir)
        result, _err = _load_json(result_path)
        if result is None or not isinstance(result, dict):
            result = {'case_id': derive_case_id(None, input_dir), 'errors': []}

    errors = list(result.get('errors') or [])
    degradations = list(result.get('degradations') or [])
    pending = list(result.get('pending_human') or [])

    contract = result.get('output_contract')
    if not isinstance(contract, dict):
        contract = {}
    result['output_contract'] = contract

    # 合并 fragments
    frag_dir = work_dir / 'fragments'
    frag_touched = {}
    merged_count = 0
    if frag_dir.is_dir():
        for fp in sorted(frag_dir.glob('*.json')):
            frag, ferr = _load_json(fp)
            if frag is None or not isinstance(frag, dict):
                errors.append({'code': 'bad_fragment', 'file': fp.name,
                               'detail': (ferr or {}).get('detail', 'not a JSON object')})
                continue
            touched = _deep_merge(result, frag)
            merged_count += 1
            for k in touched:
                frag_touched.setdefault(k, []).append(fp.name)

    # NaN 检测并净化
    nan_fields = _find_nan(result)
    if nan_fields:
        errors.append({'code': 'nan_value', 'detail': ', '.join(nan_fields[:10])})
        degradations.append({'code': 'nan_value',
                             'detail': '%d field(s) contain NaN/Inf' % len(nan_fields)})
        result = _sanitize_nan(result)

    # 契约逐字段对齐（重算顶层值状态与来源）
    alignment = {}
    for key in sorted(contract.keys()):
        collides = key in RESERVED_KEYS
        top = result.get(key)
        src = 'reserved' if collides else ((frag_touched.get(key) or [None])[0])
        alignment[key] = {
            'contract_value': contract[key],
            'top_level': top,
            'filled': top is not None,
            'collides_with_reserved': collides,
            'source': src,
        }
    result['contract_alignment'] = alignment

    # 证据可核验性检查
    for i, ev in enumerate(result.get('evidence_matrix') or []):
        if not isinstance(ev, dict):
            continue
        if not ev.get('locator') or not ev.get('quote'):
            pending.append({'topic': 'evidence_trace', 'item': 'evidence_matrix[%d]' % i,
                            'detail': 'missing locator or verbatim quote'})

    result['errors'] = errors
    result['degradations'] = degradations
    result['pending_human'] = pending
    result['status'] = _recompute_status(result)

    runtime = result.get('runtime')
    if not isinstance(runtime, dict):
        runtime = {}
    runtime['phase'] = 'finalize'
    runtime['updated_at'] = ts
    result['runtime'] = runtime

    log = result.get('change_log')
    if not isinstance(log, list):
        log = []
        result['change_log'] = log
    filled_contract = sum(1 for a in alignment.values() if a.get('filled'))
    log.append({
        'at': ts, 'actor': 'finalize', 'action': 'merge_validate',
        'file': 'result.json', 'status': result['status'],
        'detail': 'merged %d fragment(s); %d/%d contract fields filled'
                  % (merged_count, filled_contract, len(alignment)),
    })

    _write_json(result, result_path)
    _write_text(_render_report(result), output_dir / 'report.md')
    return 0


def run_all(input_dir, output_dir, work_dir):
    """骨架 → 证据 → 收尾 一气呵成；用于最小可运行兜底与自检。"""
    run_skeleton(input_dir, output_dir, work_dir)
    run_evidence(input_dir, output_dir, work_dir)
    run_finalize(input_dir, output_dir, work_dir)
    return 0


if __name__ == '__main__':
    sys.exit(run_all('/input', '/app/output', '/app/work'))
