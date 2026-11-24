# -*- coding: utf-8 -*-
"""Deterministic semantic-invariant extraction and conservation checks.

This is the machine-checkable core of the Skill. It turns two agent-written
intermediate artifacts -- the invariant registry in ``plan.json`` and the
per-sentence edit ledger in ``ledger.json`` -- into a verifiable audit trail.
The question "did the rewrite break a fact?" is answered by code here, not by
re-reading alone.

Pure standard library; no network; no model calls; deterministic.
"""

import re
from collections import defaultdict

# ---------------------------------------------------------------------------
# Normalization (whitespace- and full-width-insensitive, lowercase)
# ---------------------------------------------------------------------------

_FW_TABLE = {
    ord('０'): '0', ord('１'): '1', ord('２'): '2', ord('３'): '3', ord('４'): '4',
    ord('５'): '5', ord('６'): '6', ord('７'): '7', ord('８'): '8', ord('９'): '9',
    ord('．'): '.', ord('，'): ',', ord('：'): ':', ord('（'): '(', ord('）'): ')',
    ord('［'): '[', ord('］'): ']', ord('％'): '%', ord('－'): '-', ord('—'): '-',
    ord('µ'): 'μ', ord('°'): '°', ord('×'): '×',
}


def norm(text):
    """Compact normalized form used for hard-token containment checks."""
    s = str(text or '')
    s = s.replace('\u00a0', ' ').replace('\u2009', ' ').replace('\u200b', '')
    s = s.translate(_FW_TABLE)
    s = re.sub(r'\s+', '', s)
    return s.lower()


def norm_space(text):
    """Whitespace-collapsed form for display and comparisons that keep words."""
    s = str(text or '')
    s = s.replace('\u00a0', ' ').replace('\u2009', ' ').replace('\u200b', '')
    s = s.translate(_FW_TABLE)
    return re.sub(r'\s+', ' ', s).strip()


# ---------------------------------------------------------------------------
# Hard-token invariant kinds and extraction heuristics
# ---------------------------------------------------------------------------

# Kinds whose tokens must survive verbatim (after normalization).
HARD_KINDS = {
    'number', 'unit', 'citation', 'figure_ref', 'table_ref',
    'proper_name', 'equation', 'stat', 'range',
}

# Kinds whose *strength / logic* must survive but wording may change.
LOGIC_KINDS = {
    'hedge', 'quantifier', 'negation', 'conditional', 'causal', 'terminology',
}

ALL_KINDS = HARD_KINDS | LOGIC_KINDS

# Hedges ranked by claim strength. Higher = stronger claim.
HEDGE_RANKS = {
    'absolute': 4,
    'strong': 3,
    'moderate': 2,
    'weak': 1,
    'agnostic': 0,
}

_HEDGE_MARKERS = {
    'absolute': ['prove', 'proves', 'proved', 'confirm', 'confirms', 'confirmed',
                 'demonstrate', 'demonstrates', 'established', 'conclusively',
                 '证实', '确证', '确定', '必然', '无疑', 'definitely', 'always'],
    'strong': ['show', 'shows', 'showed', 'shown', 'indicate', 'indicates',
               'indicated', 'found', 'find', 'reveal', 'reveals', 'revealed',
               '显著', '表明', '显示', '发现', '指出', '明确'],
    'moderate': ['suggest', 'suggests', 'suggested', 'imply', 'implies',
                 'indicate that', 'likely', 'probably', 'seems', 'appears',
                 '提示', '暗示', '可能', '倾向于', '一致于'],
    'weak': ['might', 'could', 'perhaps', 'preliminary', 'tentatively',
             '初步', '尚需', '有待', '可能（较低置信）', 'cannot rule out'],
    'agnostic': ['unknown', 'unclear', 'uncertain', 'not reported', 'not determined',
                 'inconclusive', '未知', '尚不明确', '未报道', '不能确定'],
}


def classify_hedge(sentence):
    """Heuristic strength class for guidance. The agent's recorded class in
    plan.json is the source of truth for checks; this only seeds the guess."""
    s = norm_space(sentence)
    best = 'agnostic'
    best_rank = -1
    for cls, markers in _HEDGE_MARKERS.items():
        for m in markers:
            if m in s.lower():
                if HEDGE_RANKS[cls] > best_rank:
                    best, best_rank = cls, HEDGE_RANKS[cls]
                break
    return best


# Common units in biomedical / physical / chemical writing.
_UNITS = [
    '%', 'mmHg', 'mm Hg', 'mg/dL', 'mg/ml', 'mg/mL', 'μg', 'ug', 'μL', 'uL',
    'ng', 'pg', 'fg', 'mL', 'ml', 'L', 'g', 'kg', 'm', 'mm', 'cm', 'nm', 'μm',
    'um', 'pm', 's', 'ms', 'min', 'h', 'hr', '°C', 'K', 'M', 'mM', 'μM', 'uM',
    'nM', 'pM', 'fM', 'U/mL', 'IU', 'kDa', 'Da', 'bp', 'kb', 'Mb', 'Hz', 'kHz',
    'MHz', 'V', 'mV', 'A', 'W', 'mol', 'mmol', 'Pa', 'dB', 'rpm', 'g/L',
    'cells/mL', 'CFU', 'log10', '×', '±', '℃',
]

_NUMBER_RE = r'(?<![\w.])[-+]?\d+(?:[.,]\d+)*(?:[eE][-+]?\d+)?(?![\w.])'
_CITATION_BRACKET_RE = r'\[(?:\d+(?:\s*[,，\-–—]\s*\d+)*|\d+\s*[-–—]\s*\d+)\]'
_CITATION_PAREN_RE = r'\([A-Z][A-Za-z&.\- ]{1,60}?,?\s*(?:et al\.?)?\s*,?\s*\d{4}[a-z]?\)'
_FIG_TABLE_RE = r'\b(?:Fig(?:ure)?\.?\s*\d+[A-Za-z]?|Table\s*\d+[A-Za-z]?|图\s*\d+[A-Za-z]?|表\s*\d+[A-Za-z]?)'
_EQUATION_RE = r'\$[^$]+\$|\\begin\{equation\}.*?\\end\{equation\}'


def extract_hard_candidates(text, base_locator=''):
    """Seed the invariant registry with regex candidates. The agent confirms,
    trims false positives, and adds strength/logic invariants by reading.
    Returns a list of {kind, quote, tokens, locator, strength_class}."""
    out = []
    if not text:
        return out
    seen = set()

    def add(kind, quote, tokens, strength_class='hard'):
        q = norm_space(quote)
        key = (kind, q)
        if not q or key in seen:
            return
        seen.add(key)
        out.append({
            'kind': kind,
            'quote': q,
            'tokens': [norm(t) for t in tokens],
            'locator': base_locator,
            'strength_class': strength_class,
        })

    for m in re.finditer(_NUMBER_RE, text):
        n = m.group(0)
        ctx_start = max(0, m.start() - 12)
        ctx_end = min(len(text), m.end() + 24)
        ctx = text[ctx_start:ctx_end]
        # Prefer number+unit as one invariant; bare number otherwise.
        unit = ''
        for u in sorted(_UNITS, key=len, reverse=True):
            if ctx[m.end() - m.start():].lstrip().lower().startswith(u.lower()):
                unit = u
                break
        add('number' if not unit else 'unit',
            (n + (' ' + unit if unit else '')), [n + ('' + unit if unit else '')])

    for m in re.finditer(_CITATION_BRACKET_RE, text):
        add('citation', m.group(0), [m.group(0)])
    for m in re.finditer(_CITATION_PAREN_RE, text):
        add('citation', m.group(0), [m.group(0)])
    for m in re.finditer(_FIG_TABLE_RE, text):
        add('figure_ref' if m.group(0).lower().startswith(('fig', '图')) else 'table_ref',
            m.group(0), [m.group(0)])
    for m in re.finditer(_EQUATION_RE, text):
        add('equation', m.group(0), [m.group(0)])
    return out


# ---------------------------------------------------------------------------
# Conservation verification (deterministic)
# ---------------------------------------------------------------------------

def _tokens_present(inv, revised_norm):
    """Return list of missing normalized tokens for a hard invariant."""
    missing = []
    for tok in inv.get('tokens', []):
        t = norm(tok)
        if t and t not in revised_norm:
            missing.append(tok)
    return missing


def verify_unit(unit, plan):
    """Verify one ledger unit against its touched invariants.

    Returns a dict {invariant_id: {verdict, detail}} where verdict is
    'preserved' | 'error' | 'warning'."""
    results = {}
    by_id = {i.get('id'): i for i in plan.get('invariants', [])}
    revised_norm = norm(unit.get('revised', ''))

    for inv_id in unit.get('touched_invariants', []):
        inv = by_id.get(inv_id)
        if inv is None:
            results[inv_id] = {'verdict': 'warning',
                               'detail': 'touched invariant missing from plan.json'}
            continue
        kind = inv.get('kind')
        if kind in HARD_KINDS:
            missing = _tokens_present(inv, revised_norm)
            if missing:
                results[inv_id] = {'verdict': 'error',
                                   'detail': 'hard tokens missing from revised text: %s'
                                             % ', '.join(missing)}
            else:
                results[inv_id] = {'verdict': 'preserved', 'detail': 'tokens present'}
        elif kind == 'hedge':
            before = inv.get('strength_class') or 'agnostic'
            after = (unit.get('conservation', {}).get(inv_id, {}).get('after_class')
                     or before)
            if before == after:
                results[inv_id] = {'verdict': 'preserved', 'detail': 'strength %s unchanged' % before}
            else:
                delta = HEDGE_RANKS.get(after, 0) - HEDGE_RANKS.get(before, 0)
                direction = 'strengthened' if delta > 0 else 'weakened'
                results[inv_id] = {
                    'verdict': 'warning',
                    'detail': 'hedge strength %s (%s -> %s); needs revert or explicit reason'
                              % (direction, before, after),
                }
        else:
            # logic kinds: agent supplies a conservation proof; accept but flag
            # if no explicit before/after record exists.
            proof = unit.get('conservation', {}).get(inv_id)
            if proof and proof.get('verdict') in ('preserved', 'reverted', 'explicit_reason'):
                results[inv_id] = {'verdict': proof['verdict'],
                                   'detail': proof.get('detail', '')}
            else:
                results[inv_id] = {'verdict': 'warning',
                                   'detail': 'logic invariant lacks a conservation proof'}
    return results


def verify_terminology(plan, all_revised_text):
    """Whole-corpus terminology sweep: banned variants must not reappear."""
    errors = []
    text_norm = norm(all_revised_text)
    for term in plan.get('terminology', []):
        canonical = norm(term.get('canonical', ''))
        for v in term.get('variants', []):
            vn = norm(v)
            if vn and vn != canonical and vn in text_norm:
                errors.append({
                    'term': term.get('concept', canonical),
                    'canonical': term.get('canonical'),
                    'offending_variant': v,
                })
    return errors


def build_fact_source_map(plan):
    """Turn invariant registry entries into the output_contract fact map."""
    items = []
    for inv in plan.get('invariants', []):
        fact = inv.get('quote') or inv.get('id')
        items.append({
            'fact': fact,
            'locator': inv.get('locator', ''),
            'quote': inv.get('quote', ''),
            'kind': inv.get('kind'),
        })
    return items


def build_version_boundary(plan, ledger):
    """Assemble diffs / human confirmations / gaps from intermediate artifacts."""
    items = []
    for u in ledger.get('units', []):
        if u.get('requires_human'):
            items.append({
                'type': 'human',
                'detail': u.get('human_note', 'needs human confirmation'),
                'status': 'pending',
                'unit_id': u.get('id'),
            })
        for inv_id, proof in (u.get('conservation', {}) or {}).items():
            if proof.get('verdict') == 'explicit_reason':
                items.append({
                    'type': 'human',
                    'detail': '%s: %s' % (inv_id, proof.get('detail', 'hedge change documented')),
                    'status': 'documented',
                    'unit_id': u.get('id'),
                })
    for g in plan.get('gaps', []):
        items.append({'type': 'gap', 'detail': g, 'status': 'not_reported'})
    for c in plan.get('conflicts', []):
        items.append({'type': 'source_conflict', 'detail': c, 'status': 'conflict'})
    return items


def summarize_report(unit_results, term_errors, plan, ledger):
    """Consolidate verification into errors/warnings + stats."""
    errors, warnings = [], []
    touched_ids = set()
    for u in ledger.get('units', []):
        for inv_id, r in (unit_results.get(u.get('id'), {}) or {}).items():
            touched_ids.add(inv_id)
            if r['verdict'] == 'error':
                errors.append({'unit_id': u.get('id'), 'invariant_id': inv_id,
                               'detail': r['detail']})
            elif r['verdict'] == 'warning':
                warnings.append({'unit_id': u.get('id'), 'invariant_id': inv_id,
                                 'detail': r['detail']})

    # Coverage: every invariant in the registry must be touched by some unit.
    for inv in plan.get('invariants', []):
        if inv.get('id') not in touched_ids:
            errors.append({'unit_id': None, 'invariant_id': inv.get('id'),
                           'detail': 'invariant not covered by any edit unit'})
        if not inv.get('locator') or not inv.get('quote'):
            errors.append({'unit_id': None, 'invariant_id': inv.get('id'),
                           'detail': 'invariant missing locator or quote'})

    for te in term_errors:
        errors.append({'unit_id': None, 'invariant_id': None,
                       'detail': 'terminology: banned variant "%s" used instead of "%s"'
                                 % (te['offending_variant'], te['canonical'])})

    return {
        'errors': errors,
        'warnings': warnings,
        'stats': {
            'invariants': len(plan.get('invariants', [])),
            'units': len(ledger.get('units', [])),
            'terms': len(plan.get('terminology', [])),
            'gaps': len(plan.get('gaps', [])),
            'conflicts': len(plan.get('conflicts', [])),
        },
    }
