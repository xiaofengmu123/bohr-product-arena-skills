#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""figure_model.py -- core intermediate-representation layer.

Converts the agent-authored extraction plan (``plan.json``) into a canonical,
reviewable figure model (``figure_model.json``), then derives a layered editable
SVG and a minimal valid PDF from that model.

Design contract
---------------
* Pure Python standard library only (no third-party dependencies).
* Deterministic: the same plan yields the same model, SVG and PDF bytes.
  No random seeds, no wall-clock-dependent geometry, no iteration order leaks.
* This module never invents biology. It only normalizes, deduplicates,
  positions and styles what the extraction plan already asserts. Unknowns stay
  unknown and are surfaced in ``uncertainty`` / validation warnings.
* Every SVG glyph carries ``data-*`` traceability attributes that point back to
  the figure-model id and the source locator(s) of the element.

Public API used by ``scripts/main.py``::

    build_model(plan) -> model dict
    validate_model(model) -> (errors, warnings)
    render_svg(model) -> str
    render_pdf(model) -> bytes
    publicize(model) -> model dict with internals removed
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Domain constants (drawing rules kept in one place so the agent and the
# renderer cannot disagree).
# ---------------------------------------------------------------------------

ENTITY_TYPE_META = {
    "gene": {
        "font_style": "italic",
        "shape": "rounded_rect",
        "fill": "#deebf7",
        "stroke": "#3182bd",
        "description": "Gene (italic label)",
    },
    "rna": {
        "font_style": "italic",
        "shape": "rounded_rect",
        "fill": "#d0e4f5",
        "stroke": "#2171b5",
        "description": "RNA transcript (italic label)",
    },
    "protein": {
        "font_style": "normal",
        "shape": "rounded_rect",
        "fill": "#e5f5e0",
        "stroke": "#31a354",
        "description": "Protein (upright label)",
    },
    "complex": {
        "font_style": "normal",
        "shape": "ellipse",
        "fill": "#fde0dd",
        "stroke": "#de2d26",
        "description": "Complex / multi-subunit assembly",
    },
    "family": {
        "font_style": "normal",
        "shape": "ellipse",
        "fill": "#fff7bc",
        "stroke": "#fec44f",
        "description": "Family / family member group",
    },
    "cell_type": {
        "font_style": "normal",
        "shape": "ellipse",
        "fill": "#ece7f2",
        "stroke": "#756bb1",
        "description": "Cell type",
    },
    "small_molecule": {
        "font_style": "normal",
        "shape": "circle",
        "fill": "#f6e8c3",
        "stroke": "#b8860b",
        "description": "Small molecule / ligand / metabolite",
    },
    "process": {
        "font_style": "normal",
        "shape": "diamond",
        "fill": "#fee0d2",
        "stroke": "#e6550d",
        "description": "Biological process / phenotype",
    },
    "unknown": {
        "font_style": "normal",
        "shape": "rounded_rect",
        "fill": "#f0f0f0",
        "stroke": "#999999",
        "description": "Unspecified entity type",
    },
}

COMPARTMENT_ORDER = [
    "extracellular",
    "membrane",
    "cytosol",
    "endosome",
    "golgi",
    "er",
    "mitochondria",
    "nucleus",
]

COMPARTMENT_LABELS = {
    "extracellular": "Extracellular",
    "membrane": "Membrane",
    "cytosol": "Cytosol",
    "endosome": "Endosome",
    "golgi": "Golgi",
    "er": "ER",
    "mitochondria": "Mitochondria",
    "nucleus": "Nucleus",
    "unknown": "Unspecified",
}

COMPARTMENT_FILL = {
    "extracellular": "#fbfdff",
    "membrane": "#f5f0e8",
    "cytosol": "#f6fbf5",
    "endosome": "#f7f5fa",
    "golgi": "#faf6ee",
    "er": "#f8f6f0",
    "mitochondria": "#faf3f3",
    "nucleus": "#f0f4fb",
}

RELATION_META = {
    "activation": {
        "arrow": "filled",
        "line": "solid",
        "color": "#1b9e1b",
        "width": 1.8,
        "description": "Activation / promotion",
    },
    "inhibition": {
        "arrow": "tbar",
        "line": "solid",
        "color": "#d7191c",
        "width": 1.8,
        "description": "Inhibition / repression",
    },
    "binding": {
        "arrow": "none",
        "line": "solid",
        "color": "#636363",
        "width": 1.4,
        "description": "Binding / physical interaction",
    },
    "modification": {
        "arrow": "filled",
        "line": "solid",
        "color": "#2b83ba",
        "width": 1.6,
        "description": "Post-translational modification",
    },
    "transport": {
        "arrow": "open",
        "line": "dashed",
        "color": "#762a83",
        "width": 1.5,
        "description": "Transport / translocation",
    },
    "conversion": {
        "arrow": "double",
        "line": "solid",
        "color": "#e6a817",
        "width": 1.6,
        "description": "Conversion / catalysis to product",
    },
    "expression": {
        "arrow": "filled",
        "line": "dashed",
        "color": "#525252",
        "width": 1.4,
        "description": "Expression (gene -> RNA/protein)",
    },
    "association": {
        "arrow": "none",
        "line": "solid",
        "color": "#969696",
        "width": 1.2,
        "description": "Association (unspecified direction)",
    },
    "unknown": {
        "arrow": "open",
        "line": "dotted",
        "color": "#999999",
        "width": 1.2,
        "description": "Unspecified relationship",
    },
}

# Predicate lexicon: applied only when the agent has not supplied an explicit
# ``semantic_hint``. Order matters (more specific phrases first). The chosen
# rule is recorded on the relation for reviewability.
PREDICATE_LEXICON = [
    (r"phosphoryl|phospho|kinase", "modification"),
    (r"ubiquitin|sumoylat|neddyl", "modification"),
    (r"methylat|acetylat|glycosylat|palmitoyl|myristoyl", "modification"),
    (r"inhibit|suppress|repress|block|antagon|down.?regulat|decreas|negat.*regulat",
     "inhibition"),
    (r"activ|promot|induc|up.?regulat|enhanc|stimulat|increase|positiv.*regulat",
     "activation"),
    (r"transport|translocat|import|export|shuttle|traffic|internaliz|transcyt",
     "transport"),
    (r"convert|cleav|hydrolyz|produc|generat|catalyz|synthesiz", "conversion"),
    (r"bind|interact|associat|recruit|dimer|complex|anchors?|attach", "binding"),
    (r"express|transcrib|translat|encod", "expression"),
]

FONT_STACK = "Helvetica, Arial, 'Noto Sans SC', 'WenQuanYi Micro Hei', sans-serif"

# Geometry defaults (SVG user units == points in the PDF fallback).
GEOM = {
    "margin": 32,
    "band_label_h": 22,
    "band_pad": 14,
    "node_h": 40,
    "node_min_w": 76,
    "node_max_w": 220,
    "gap_x": 26,
    "gap_y": 26,
    "max_cols": 5,
    "font_size": 12,
    "edge_label_size": 10,
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _norm_id(value: Any) -> str:
    """Normalize an identifier for deduplication/matching (lower alnum)."""
    return re.sub(r"[^0-9a-z]", "", str(value or "").lower())


def _first(mapping: Dict[str, Any], keys: Tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        val = mapping.get(key)
        if val not in (None, "", [], {}):
            return val
    return default


def _conf(item: Dict[str, Any]) -> str:
    raw = str(_first(item, ("confidence", "evidence", "evidence_strength"), "medium")).lower()
    if raw in ("high", "strong", "confirmed", "直接证据", "高"):
        return "high"
    if raw in ("low", "weak", "间接证据", "低"):
        return "low"
    return "medium"


def _sources_of(item: Dict[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for s in item.get("sources", []) or []:
        if isinstance(s, str):
            out.append({"locator": s, "quote": ""})
        elif isinstance(s, dict):
            loc = str(_first(s, ("locator", "location", "source", "ref"), ""))
            quote = str(s.get("quote") or s.get("text") or "")
            if loc:
                out.append({"locator": loc, "quote": quote})
    loc = str(_first(item, ("locator", "location"), ""))
    quote = str(item.get("quote") or "")
    if loc or quote:
        out.append({"locator": loc, "quote": quote})
    return out


def _dedupe(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    out = []
    for it in items:
        key = (it.get("locator", ""), it.get("quote", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _valid_entity_type(value: Any) -> str:
    v = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "protein_kinase": "protein",
        "enzyme": "protein",
        "receptor": "protein",
        "transcription_factor": "protein",
        "tf": "protein",
        "ligand": "small_molecule",
        "kinase": "protein",
        "transporter": "protein",
        "mrna": "rna",
        "mirna": "rna",
        "lncrna": "rna",
        "cell": "cell_type",
        "phenotype": "process",
        "pathway": "process",
    }
    v = aliases.get(v, v)
    return v if v in ENTITY_TYPE_META else "unknown"


def _valid_compartment(value: Any) -> Optional[str]:
    v = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "extracellular_space": "extracellular",
        "plasma_membrane": "membrane",
        "cell_membrane": "membrane",
        "cytoplasm": "cytosol",
        "cytoplasmic": "cytosol",
        "nuclear": "nucleus",
        "endoplasmic_reticulum": "er",
        "golgi_apparatus": "golgi",
        "mitochondrial": "mitochondria",
    }
    v = aliases.get(v, v)
    return v if v in COMPARTMENT_ORDER else (None if v in ("", "none", "unspecified") else "unknown")


def _valid_relation_type(value: Any) -> Optional[str]:
    v = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "promotion": "activation",
        "activation_phosphorylation": "activation",
        "repression": "inhibition",
        "phosphorylation": "modification",
        "ubiquitination": "modification",
        "methylation": "modification",
        "acetylation": "modification",
        "interaction": "binding",
        "translocation": "transport",
        "transcription": "expression",
        "translation": "expression",
        "upregulates": "activation",
        "downregulates": "inhibition",
    }
    v = aliases.get(v, v)
    return v if v in RELATION_META else None


def _infer_relation(predicate_text: str) -> str:
    text = str(predicate_text or "").lower()
    for pattern, rtype in PREDICATE_LEXICON:
        if re.search(pattern, text):
            return rtype
    return "unknown"


def _norm_direction(direction: Any, subj_raw: str, obj_raw: str,
                    s_ent: Dict[str, Any], o_ent: Dict[str, Any]) -> str:
    d = str(direction or "").strip()
    if d in ("object->subject", "reverse", "leftward", "upstream", "obj->subj"):
        return f"{o_ent['id']}->{s_ent['id']}"
    return f"{s_ent['id']}->{o_ent['id']}"


def _match_entity(entities: List[Dict[str, Any]], raw: str) -> Optional[Dict[str, Any]]:
    key = _norm_id(raw)
    for ent in entities:
        if _norm_id(ent["label"]) == key:
            return ent
        for alias in ent.get("aliases", []):
            if _norm_id(alias) == key:
                return ent
    return None


def _escape_xml(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _escape_pdf_text(text: Any) -> str:
    return (
        str(text)
        .replace("\\", r"\\")
        .replace("(", r"\(")
        .replace(")", r"\)")
    )


def _latin1_or_question(text: Any) -> str:
    """PDF built-in fonts cover WinAnsi only; degrade CJK honestly."""
    out = []
    for ch in str(text):
        out.append(ch if ord(ch) < 256 else "?")
    return "".join(out)


def _wrap_label(label: str, width: int, font_size: int) -> List[str]:
    if width >= len(label) * font_size * 0.62:
        return [label]
    lines: List[str] = []
    current = ""
    for ch in label:
        if len(current) * font_size * 0.62 + font_size * 0.62 > width and current:
            lines.append(current)
            current = ch
        else:
            current += ch
    if current:
        lines.append(current)
    return lines[:2] or [label]


# ---------------------------------------------------------------------------
# Model building
# ---------------------------------------------------------------------------

def build_model(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Transform the agent-authored plan into a canonical figure model."""
    plan = plan or {}
    case_id = str(_first(plan, ("case_id", "caseId", "id", "challenge_id"), "unknown"))

    # --- entities ----------------------------------------------------------
    entities: List[Dict[str, Any]] = []
    index: Dict[str, Dict[str, Any]] = {}
    internal: Dict[str, Any] = {
        "compartment_defaulted": [],
        "unresolved_relations": [],
        "naming_checks": [],
    }

    for m in plan.get("entity_mentions", []) or []:
        if not isinstance(m, dict):
            continue
        mention = str(m.get("mention") or m.get("label") or "").strip()
        if not mention:
            continue
        resolved = str(m.get("resolved_id") or mention).strip()
        key = _norm_id(resolved) or _norm_id(mention)
        type_hint = _valid_entity_type(_first(m, ("type_hint", "type"), "unknown"))
        label = str(m.get("label") or mention).strip()

        if key not in index:
            ent = {
                "id": "E%d" % (len(entities) + 1),
                "label": label,
                "type": type_hint,
                "aliases": [],
                "compartment": None,
                "transmembrane": bool(m.get("transmembrane")),
                "sources": [],
                "confidence": _conf(m),
            }
            index[key] = ent
            entities.append(ent)

        ent = index[key]
        for alias in (mention, resolved, label):
            if alias and alias not in ent["aliases"]:
                ent["aliases"].append(alias)
        ent["sources"].extend(_sources_of(m))
        if ent["type"] == "unknown" and type_hint != "unknown":
            ent["type"] = type_hint
        if m.get("transmembrane"):
            ent["transmembrane"] = True
        ent["confidence"] = _conf(m)
        if ent["type"] == "gene":
            internal["naming_checks"].append({
                "entity": ent["id"], "label": label,
                "rule": "gene label rendered italic in SVG; PDF uses oblique substitute",
            })

    for ent in entities:
        ent["sources"] = _dedupe(ent["sources"])

    # --- compartments ------------------------------------------------------
    hint_map: Dict[str, Dict[str, Any]] = {}
    for h in plan.get("layout_hints", []) or []:
        if not isinstance(h, dict):
            continue
        hid = _norm_id(_first(h, ("resolved_id", "entity", "mention", "id"), ""))
        if hid:
            hint_map[hid] = h

    for ent in entities:
        hint = None
        for alias in ent["aliases"]:
            if _norm_id(alias) in hint_map:
                hint = hint_map[_norm_id(alias)]
                break
        if hint is None:
            hint = hint_map.get(_norm_id(ent["label"]))
        comp = _valid_compartment((hint or {}).get("compartment") or (hint or {}).get("location"))
        if (hint or {}).get("transmembrane"):
            ent["transmembrane"] = True
        if ent["transmembrane"] and comp in (None, "cytosol"):
            comp = "membrane"
        if comp is None:
            comp = "cytosol"
            internal["compartment_defaulted"].append(ent["id"])
        ent["compartment"] = comp
        if hint and (hint.get("basis") or hint.get("note")):
            ent["layout_basis"] = str(hint.get("basis") or hint.get("note"))

    # --- relations ---------------------------------------------------------
    relations: List[Dict[str, Any]] = []
    for rm in plan.get("relation_mentions", []) or []:
        if not isinstance(rm, dict):
            continue
        subj = str(_first(rm, ("subject", "source", "from"), "")).strip()
        obj = str(_first(rm, ("object", "target", "to"), "")).strip()
        if not subj or not obj:
            continue
        s_ent = _match_entity(entities, subj)
        o_ent = _match_entity(entities, obj)
        if s_ent is None or o_ent is None:
            internal["unresolved_relations"].append({
                "subject_raw": subj, "object_raw": obj,
                "sources": _dedupe(_sources_of(rm)),
            })
            continue
        sem = _valid_relation_type(_first(rm, ("semantic_hint", "type", "relation_type"), None))
        if sem is None:
            sem = _infer_relation(str(_first(rm, ("predicate_text", "predicate"), "")))
            semantics_source = "lexicon"
        else:
            semantics_source = "agent"
        rel = {
            "id": "R%d" % (len(relations) + 1),
            "subject": s_ent["id"],
            "object": o_ent["id"],
            "type": sem,
            "direction": _norm_direction(
                _first(rm, ("direction_hint", "direction"), "subject->object"),
                subj, obj, s_ent, o_ent),
            "predicate_text": str(_first(rm, ("predicate_text", "predicate"), "")),
            "modifier": rm.get("modifier"),
            "evidence": _conf(rm),
            "sources": _dedupe(_sources_of(rm)),
            "semantics_source": semantics_source,
        }
        if rel["type"] == "modification" and not rel["modifier"]:
            ptext = rel["predicate_text"].lower()
            if "phosphoryl" in ptext:
                rel["modifier"] = "P"
            elif "ubiquitin" in ptext:
                rel["modifier"] = "Ub"
            elif "methylat" in ptext:
                rel["modifier"] = "Me"
            elif "acetylat" in ptext:
                rel["modifier"] = "Ac"
        relations.append(rel)

    # --- data mappings -----------------------------------------------------
    data_mappings: List[Dict[str, Any]] = []
    for d in plan.get("data_items", []) or []:
        if not isinstance(d, dict):
            continue
        subj = str(_first(d, ("subject", "entity"), "")).strip()
        ent = _match_entity(entities, subj)
        dm = {
            "entity": ent["id"] if ent else None,
            "subject_raw": subj,
            "metric": str(_first(d, ("metric", "name"), "value")),
            "value": d.get("value"),
            "unit": d.get("unit"),
            "encoding": _first(d, ("encoding", "visual"), None),
            "sources": _dedupe(_sources_of(d)),
        }
        if ent is not None:
            ent["data"] = dm
        data_mappings.append(dm)

    # --- layout and legend -------------------------------------------------
    layout = _compute_layout(entities, relations)
    legend = _build_legend(entities, relations, data_mappings, plan.get("legend_hints", []) or [])

    model = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "entities": entities,
        "relations": relations,
        "layout": layout,
        "legend": legend,
        "data_mappings": data_mappings,
        "uncertainty": list(plan.get("ambiguities", []) or []),
        "pending_human": list(plan.get("pending_human", []) or []),
        "task_notes": str(plan.get("task_notes", "")),
        "_internal": internal,
    }
    return model


def _compute_layout(entities: List[Dict[str, Any]],
                    relations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministic vertical-band layout: extracellular -> membrane -> nucleus."""
    used = sorted({e["compartment"] for e in entities},
                  key=lambda c: COMPARTMENT_ORDER.index(c) if c in COMPARTMENT_ORDER else 99)

    # Group entities by compartment, preserving mention order.
    by_comp: Dict[str, List[Dict[str, Any]]] = {c: [] for c in used}
    for ent in entities:
        by_comp[ent["compartment"]].append(ent)

    # Node sizes (uniform width within a compartment for a tidy grid).
    for comp in used:
        comp_entities = by_comp[comp]
        max_label = max((len(e["label"]) for e in comp_entities), default=4)
        width = min(GEOM["node_max_w"], max(GEOM["node_min_w"], int(max_label * 7.2 + 30)))
        for ent in comp_entities:
            ent["_w"] = width
            ent["_h"] = GEOM["node_h"]

    bands: List[Dict[str, Any]] = []
    positions: Dict[str, Dict[str, Any]] = {}
    y_cursor = GEOM["margin"]
    content_left = GEOM["margin"]

    for comp in used:
        comp_entities = by_comp[comp]
        if not comp_entities:
            continue
        n = len(comp_entities)
        cols = min(GEOM["max_cols"], n)
        rows = int(math.ceil(n / cols))
        node_h = GEOM["node_h"]
        band_h = GEOM["band_pad"] + GEOM["band_label_h"] + rows * node_h + \
            (rows - 1) * GEOM["gap_y"] + GEOM["band_pad"]
        band = {
            "compartment": comp,
            "label": COMPARTMENT_LABELS.get(comp, comp),
            "y": y_cursor,
            "height": band_h,
            "top": y_cursor,
            "bottom": y_cursor + band_h,
        }
        bands.append(band)

        content_y = y_cursor + GEOM["band_pad"] + GEOM["band_label_h"]
        for i, ent in enumerate(comp_entities):
            col = i % cols
            row = i // cols
            w = ent["_w"]
            h = ent["_h"]
            if ent.get("transmembrane"):
                h = max(h * 2.0, band_h - 2 * GEOM["band_pad"] - GEOM["band_label_h"])
            x = content_left + col * (w + GEOM["gap_x"])
            y = content_y + row * (node_h + GEOM["gap_y"])
            if ent.get("transmembrane"):
                y = y_cursor + GEOM["band_pad"] + GEOM["band_label_h"]
            positions[ent["id"]] = {
                "x": x, "y": y, "w": w, "h": h,
                "cx": x + w / 2.0, "cy": y + h / 2.0,
                "compartment": comp,
            }
        y_cursor += band_h

    canvas_width = max(680, int(content_left + GEOM["max_cols"] * (GEOM["node_min_w"] + GEOM["gap_x"]) + GEOM["margin"]))
    canvas_height = int(y_cursor + GEOM["margin"])

    # Feedback loops: relation whose target sits above its source.
    feedback: List[List[str]] = []
    for rel in relations:
        s = positions.get(rel["subject"])
        o = positions.get(rel["object"])
        if s and o and o["cy"] < s["cy"]:
            feedback.append([rel["subject"], rel["object"]])

    return {
        "compartments": [b["compartment"] for b in bands],
        "bands": bands,
        "positions": positions,
        "canvas": {"width": canvas_width, "height": canvas_height},
        "feedback_loops": feedback,
        "orientation": "vertical",
        "order": "extracellular -> membrane -> cytosol -> nucleus",
    }


def _build_legend(entities: List[Dict[str, Any]], relations: List[Dict[str, Any]],
                  data_mappings: List[Dict[str, Any]],
                  legend_hints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    legend: List[Dict[str, Any]] = []
    used_types = sorted({e["type"] for e in entities})
    for t in used_types:
        meta = ENTITY_TYPE_META[t]
        legend.append({
            "kind": "entity",
            "key": t,
            "description": meta["description"],
            "glyph": f"shape={meta['shape']}; fill={meta['fill']}; stroke={meta['stroke']}",
            "font_style": meta["font_style"],
        })
    used_rels = sorted({r["type"] for r in relations})
    for t in used_rels:
        meta = RELATION_META[t]
        legend.append({
            "kind": "relation",
            "key": t,
            "description": meta["description"],
            "glyph": f"line={meta['line']}; arrow={meta['arrow']}; color={meta['color']}",
        })
    encodings = {}
    for dm in data_mappings:
        if dm.get("encoding") and dm.get("entity"):
            encodings[dm["encoding"]] = dm
    for enc, dm in encodings.items():
        legend.append({
            "kind": "data",
            "key": str(enc),
            "description": f"data encoding '{enc}' (metric={dm['metric']}, unit={dm.get('unit') or '-'})",
            "glyph": "text badge next to node",
            "source": (dm.get("sources") or [{}])[0] if dm.get("sources") else None,
        })
    for h in legend_hints:
        if isinstance(h, dict) and (h.get("encoding") or h.get("mapping")):
            legend.append({
                "kind": "data",
                "key": str(h.get("encoding") or h.get("mapping")),
                "description": str(h.get("mapping") or h.get("encoding") or ""),
                "glyph": str(h.get("glyph") or "as specified"),
                "source": h.get("source") or h.get("locator") or None,
            })
    return legend


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_model(model: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    internal = model.get("_internal", {})
    entities = model.get("entities", []) or []
    relations = model.get("relations", []) or []

    if not model.get("case_id"):
        errors.append("missing case_id")

    ids = {}
    for ent in entities:
        if not ent.get("id"):
            errors.append("entity without id")
            continue
        if ent["id"] in ids:
            errors.append(f"duplicate entity id {ent['id']}")
        ids[ent["id"]] = ent
        if ent.get("type") not in ENTITY_TYPE_META:
            errors.append(f"entity {ent['id']} has unsupported type {ent.get('type')}")
        if not ent.get("sources"):
            warnings.append(f"entity {ent['id']} ({ent.get('label')}) has no source locator")
        if ent.get("confidence") == "low":
            warnings.append(f"entity {ent['id']} ({ent.get('label')}) is low confidence")

    for rid in internal.get("compartment_defaulted", []) or []:
        warnings.append(f"entity {rid} compartment defaulted to cytosol (no explicit layout hint)")

    for rel in relations:
        if rel.get("subject") not in ids:
            errors.append(f"relation {rel.get('id')} references unknown subject {rel.get('subject')}")
        if rel.get("object") not in ids:
            errors.append(f"relation {rel.get('id')} references unknown object {rel.get('object')}")
        if rel.get("type") not in RELATION_META:
            errors.append(f"relation {rel.get('id')} has unsupported type {rel.get('type')}")
        if not rel.get("sources"):
            warnings.append(f"relation {rel.get('id')} has no source locator")
        if rel.get("evidence") == "low":
            warnings.append(f"relation {rel.get('id')} is low evidence")

    for ur in internal.get("unresolved_relations", []) or []:
        warnings.append(
            "unresolved relation mention "
            f"'{ur.get('subject_raw')} -> {ur.get('object_raw')}' (no matching entity)")

    if not entities and not relations:
        warnings.append("figure model contains no entities or relations")

    if model.get("uncertainty"):
        warnings.append(f"{len(model['uncertainty'])} ambiguity/uncertainty entries remain")
    if model.get("pending_human"):
        warnings.append(f"{len(model['pending_human'])} pending-human items remain")

    return errors, warnings


def publicize(model: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy without the '_internal' bookkeeping and geometry scratch."""
    out = {k: v for k, v in model.items() if k != "_internal"}
    entities = []
    for ent in out.get("entities", []):
        entities.append({k: v for k, v in ent.items() if not k.startswith("_")})
    out["entities"] = entities
    return out


# ---------------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------------

def render_svg(model: Dict[str, Any]) -> str:
    layout = model["layout"]
    W, H = layout["canvas"]["width"], layout["canvas"]["height"]
    positions = layout["positions"]
    lines: List[str] = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-label="biological mechanism diagram">')
    lines.append("  <title>Biological mechanism / pathway diagram</title>")
    lines.append("  <desc>Generated from figure_model.json; every glyph carries data-entity-id / "
                 "data-relation-id and data-source-locator for auditability.</desc>")

    # Defs: arrowhead markers per (color, style) actually used.
    marker_defs = _svg_markers(model)
    lines.append(f"  <defs>{marker_defs}</defs>")

    # Layer: compartments
    lines.append(f'  <g id="layer-compartments" data-layer="compartments">')
    for band in layout["bands"]:
        comp = band["compartment"]
        fill = COMPARTMENT_FILL.get(comp, "#f7f7f7")
        lines.append(
            f'    <rect x="{GEOM["margin"]-6}" y="{band["top"]}" '
            f'width="{W - 2*GEOM["margin"] + 12}" height="{band["height"]}" '
            f'rx="6" fill="{fill}" stroke="#c8c8c8" stroke-width="1" '
            f'data-compartment="{_escape_xml(comp)}"/>')
        lines.append(
            f'    <text x="{GEOM["margin"]+4}" y="{band["top"] + GEOM["band_pad"] + 14}" '
            f'font-size="13" font-weight="bold" font-family="{FONT_STACK}" '
            f'fill="#444444" data-compartment-label="{_escape_xml(comp)}">'
            f'{_escape_xml(band["label"])}</text>')
    lines.append("  </g>")

    # Layer: relations (drawn under entities)
    lines.append(f'  <g id="layer-relations" data-layer="relations">')
    for rel in model["relations"]:
        lines.append("    " + _svg_relation(rel, positions))
    lines.append("  </g>")

    # Layer: entities
    lines.append(f'  <g id="layer-entities" data-layer="entities">')
    for ent in model["entities"]:
        lines.append("    " + _svg_entity(ent, positions))
    lines.append("  </g>")

    # Layer: data annotations
    lines.append(f'  <g id="layer-data" data-layer="data">')
    for ent in model["entities"]:
        if ent.get("data") and ent["data"].get("value") not in (None, ""):
            pos = positions[ent["id"]]
            lines.append("    " + _svg_data_badge(ent, pos))
    lines.append("  </g>")

    # Layer: legend
    lines.append(f'  <g id="layer-legend" data-layer="legend">')
    lines.append("    " + _svg_legend(model, W, H))
    lines.append("  </g>")

    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def _svg_markers(model: Dict[str, Any]) -> str:
    used: Dict[str, str] = {}
    for rel in model["relations"]:
        meta = RELATION_META[rel["type"]]
        key = f"{meta['color'].lstrip('#')}-{meta['arrow']}"
        used[key] = f"{meta['color']}|{meta['arrow']}"
    parts = []
    for key, spec in used.items():
        color, arrow = spec.split("|")
        safe = re.sub(r"[^0-9a-zA-Z]", "", key)
        if arrow == "filled":
            parts.append(
                f'<marker id="arr-{safe}" viewBox="0 0 10 10" refX="9" refY="5" '
                f'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
                f'<path d="M0,0 L10,5 L0,10 Z" fill="{color}"/></marker>')
        elif arrow == "open":
            parts.append(
                f'<marker id="arr-{safe}" viewBox="0 0 10 10" refX="8" refY="5" '
                f'markerWidth="9" markerHeight="9" orient="auto-start-reverse">'
                f'<path d="M0,0 L10,5 L0,10" fill="none" stroke="{color}" '
                f'stroke-width="1.6"/></marker>')
        elif arrow == "tbar":
            parts.append(
                f'<marker id="arr-{safe}" viewBox="0 0 10 10" refX="9" refY="5" '
                f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                f'<path d="M1,0 L1,10" stroke="{color}" stroke-width="3"/></marker>')
        elif arrow == "double":
            parts.append(
                f'<marker id="arr-{safe}" viewBox="0 0 10 10" refX="9" refY="5" '
                f'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
                f'<path d="M0,0 L10,5 L0,10 Z" fill="{color}"/></marker>')
    return "".join(parts)


def _marker_ref(rel: Dict[str, Any]) -> str:
    meta = RELATION_META[rel["type"]]
    key = f"{meta['color'].lstrip('#')}-{meta['arrow']}"
    safe = re.sub(r"[^0-9a-zA-Z]", "", key)
    return f"url(#arr-{safe})"


def _svg_relation(rel: Dict[str, Any], positions: Dict[str, Any]) -> str:
    meta = RELATION_META[rel["type"]]
    s = positions.get(rel["subject"])
    o = positions.get(rel["object"])
    if s is None or o is None:
        return ""
    dash = ' stroke-dasharray="6,4"' if meta["line"] == "dashed" else \
           (' stroke-dasharray="2,3"' if meta["line"] == "dotted" else "")
    color = meta["color"]
    width = meta["width"]

    # Port selection.
    dy = o["cy"] - s["cy"]
    dx = o["cx"] - s["cx"]
    if abs(dy) >= abs(dx):
        if dy >= 0:
            x1, y1 = s["cx"], s["cy"] + s["h"] / 2
            x2, y2 = o["cx"], o["cy"] - o["h"] / 2
        else:
            x1, y1 = s["cx"], s["cy"] - s["h"] / 2
            x2, y2 = o["cx"], o["cy"] + o["h"] / 2
    else:
        if dx >= 0:
            x1, y1 = s["cx"] + s["w"] / 2, s["cy"]
            x2, y2 = o["cx"] - o["w"] / 2, o["cy"]
        else:
            x1, y1 = s["cx"] - s["w"] / 2, s["cy"]
            x2, y2 = o["cx"] + o["w"] / 2, o["cy"]

    marker_start = f' marker-start="{_marker_ref(rel)}"' if meta["arrow"] == "double" else ""
    marker_end = ""
    if meta["arrow"] in ("filled", "open", "tbar", "double"):
        marker_end = f' marker-end="{_marker_ref(rel)}"'

    loc = " | ".join(sr.get("locator", "") for sr in rel.get("sources", []))
    attrs = (
        f'data-relation-id="{_escape_xml(rel["id"])}" '
        f'data-relation-type="{_escape_xml(rel["type"])}" '
        f'data-source-locator="{_escape_xml(loc)}" '
        f'data-evidence="{_escape_xml(rel["evidence"])}"'
    )

    if dy < 0 and abs(dy) >= abs(dx):
        # Upward (feedback) edge: arc on the right side.
        bulge = 60
        path = (f'M {x1:.1f},{y1:.1f} C {x1:.1f},{y1 - bulge:.1f} '
                f'{x2:.1f},{y2 + bulge:.1f} {x2:.1f},{y2:.1f}')
        seg = (f'<path d="{path}" fill="none" stroke="{color}" '
               f'stroke-width="{width}"{dash}{marker_end} {attrs}/>')
    else:
        seg = (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
               f'stroke="{color}" stroke-width="{width}"{dash}{marker_start}{marker_end} {attrs}/>')

    label = ""
    if rel.get("modifier"):
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        label = (f'<circle cx="{mx + 9:.1f}" cy="{my - 4:.1f}" r="9" fill="white" '
                 f'stroke="{color}" stroke-width="1"/>'
                 f'<text x="{mx + 9:.1f}" y="{my - 1:.1f}" font-size="9" '
                 f'font-family="{FONT_STACK}" text-anchor="middle" fill="{color}">'
                 f'{_escape_xml(rel["modifier"])}</text>')
    elif rel.get("predicate_text"):
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        label = (f'<text x="{mx:.1f}" y="{my - 6:.1f}" font-size="{GEOM["edge_label_size"]}" '
                 f'font-family="{FONT_STACK}" text-anchor="middle" fill="#444444">'
                 f'{_escape_xml(rel["predicate_text"][:24])}</text>')
    return seg + label


def _svg_entity(ent: Dict[str, Any], positions: Dict[str, Any]) -> str:
    pos = positions.get(ent["id"])
    if pos is None:
        return ""
    meta = ENTITY_TYPE_META[ent["type"]]
    x, y, w, h = pos["x"], pos["y"], pos["w"], pos["h"]
    rx = 8 if meta["shape"] == "rounded_rect" else (h / 2 if meta["shape"] in ("circle", "ellipse") else 3)
    loc = " | ".join(sr.get("locator", "") for sr in ent.get("sources", []))
    attrs = (
        f'data-entity-id="{_escape_xml(ent["id"])}" '
        f'data-entity-type="{_escape_xml(ent["type"])}" '
        f'data-source-locator="{_escape_xml(loc)}" '
        f'data-confidence="{_escape_xml(ent["confidence"])}"'
    )
    border_dash = ' stroke-dasharray="4,3"' if ent["confidence"] == "low" else ""
    fill = meta["fill"]
    stroke = meta["stroke"]
    if meta["shape"] == "circle":
        shape = (f'<ellipse cx="{x + w/2:.1f}" cy="{y + h/2:.1f}" rx="{w/2:.1f}" '
                 f'ry="{h/2:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"{border_dash} {attrs}/>')
    elif meta["shape"] == "ellipse":
        shape = (f'<ellipse cx="{x + w/2:.1f}" cy="{y + h/2:.1f}" rx="{w/2:.1f}" '
                 f'ry="{h/2:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"{border_dash} {attrs}/>')
    elif meta["shape"] == "diamond":
        pts = f'{x + w/2:.1f},{y:.1f} {x + w:.1f},{y + h/2:.1f} {x + w/2:.1f},{y + h:.1f} {x:.1f},{y + h/2:.1f}'
        shape = (f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"{border_dash} {attrs}/>')
    else:
        shape = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"{border_dash} {attrs}/>')

    font_style = f' font-style="{meta["font_style"]}"' if meta["font_style"] != "normal" else ""
    lines_wrapped = _wrap_label(ent["label"], w, GEOM["font_size"])
    line_h = GEOM["font_size"] + 3
    total_h = len(lines_wrapped) * line_h
    ty = y + h / 2 - total_h / 2 + GEOM["font_size"] - 1
    texts = []
    for i, line in enumerate(lines_wrapped):
        texts.append(
            f'<text x="{x + w/2:.1f}" y="{ty + i * line_h:.1f}" font-size="{GEOM["font_size"]}"'
            f'{font_style} font-family="{FONT_STACK}" text-anchor="middle" fill="#1a1a1a" '
            f'data-entity-label="{_escape_xml(ent["id"])}">{_escape_xml(line)}</text>')
    return shape + "".join(texts)


def _svg_data_badge(ent: Dict[str, Any], pos: Dict[str, Any]) -> str:
    dm = ent["data"]
    value = f'{dm["value"]}{dm.get("unit") or ""}'
    x = pos["cx"]
    y = pos["y"] + pos["h"] + 13
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="10" font-family="{FONT_STACK}" '
            f'text-anchor="middle" fill="#555555" data-metric="{_escape_xml(dm.get("metric",""))}">'
            f'{_escape_xml(value)}</text>')


def _svg_legend(model: Dict[str, Any], W: int, H: int) -> str:
    items = model["legend"]
    box_w = 300
    box_h = 24 + len(items) * 20 + 8
    x = W - box_w - 16
    y = H - box_h - 16
    parts = [f'<rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" rx="6" '
             f'fill="white" stroke="#888888" stroke-width="1"/>']
    parts.append(f'<text x="{x + 12}" y="{y + 18}" font-size="12" font-weight="bold" '
                 f'font-family="{FONT_STACK}" fill="#222222">Legend (auto-generated)</text>')
    for i, item in enumerate(items):
        iy = y + 40 + i * 20
        swatch = "#cccccc"
        if item["kind"] == "entity":
            meta = ENTITY_TYPE_META.get(item["key"], {})
            swatch = meta.get("fill", "#ccc")
        elif item["kind"] == "relation":
            meta = RELATION_META.get(item["key"], {})
            swatch = meta.get("color", "#ccc")
        parts.append(f'<rect x="{x + 12}" y="{iy - 10}" width="12" height="12" rx="2" '
                     f'fill="{swatch}" stroke="#999" stroke-width="0.5"/>')
        desc = item.get("description", item.get("key", ""))
        parts.append(f'<text x="{x + 32}" y="{iy}" font-size="10.5" '
                     f'font-family="{FONT_STACK}" fill="#333333">{_escape_xml(desc)}</text>')
    return "".join(parts)


# ---------------------------------------------------------------------------
# Minimal PDF rendering (pure standard library, vector, WinAnsi fonts)
# ---------------------------------------------------------------------------

def render_pdf(model: Dict[str, Any]) -> bytes:
    layout = model["layout"]
    W = float(layout["canvas"]["width"])
    H = float(layout["canvas"]["height"])
    positions = layout["positions"]

    content = bytearray()
    em = b""

    def op(s: bytes):
        nonlocal em
        em += s + b"\n"

    # --- compartments -----------------------------------------------------
    for band in layout["bands"]:
        comp = band["compartment"]
        fill = COMPARTMENT_FILL.get(comp, "#f7f7f7")
        r, g, b = _hex_rgb(fill)
        top = H - band["bottom"]
        bh = band["height"]
        op(b"q %.3f %.3f %.3f rg" % (r, g, b))
        op(b"%.1f %.1f %.1f %.1f re f Q" % (GEOM["margin"] - 6, top, W - 2 * GEOM["margin"] + 12, bh))
        op(b"BT /F2 11 Tf 0 0 0 rg %.1f %.1f Td (%s) Tj ET" % (
            GEOM["margin"] + 4, top + GEOM["band_pad"] + 8,
            _escape_pdf_text(_latin1_or_question(band["label"])).encode("latin1", "replace")))

    # --- relations --------------------------------------------------------
    for rel in model["relations"]:
        meta = RELATION_META[rel["type"]]
        s = positions.get(rel["subject"])
        o = positions.get(rel["object"])
        if s is None or o is None:
            continue
        dash = b" [3 2] 0 d" if meta["line"] == "dashed" else \
               (b" [1 1.5] 0 d" if meta["line"] == "dotted" else b" [] 0 d")
        r, g, b = _hex_rgb(meta["color"])
        dy = o["cy"] - s["cy"]
        dx = o["cx"] - s["cx"]
        if abs(dy) >= abs(dx):
            if dy >= 0:
                x1, y1 = s["cx"], s["cy"] + s["h"] / 2
                x2, y2 = o["cx"], o["cy"] - o["h"] / 2
            else:
                x1, y1 = s["cx"], s["cy"] - s["h"] / 2
                x2, y2 = o["cx"], o["cy"] + o["h"] / 2
        else:
            if dx >= 0:
                x1, y1 = s["cx"] + s["w"] / 2, s["cy"]
                x2, y2 = o["cx"] - o["w"] / 2, o["cy"]
            else:
                x1, y1 = s["cx"] - s["w"] / 2, s["cy"]
                x2, y2 = o["cx"] + o["w"] / 2, o["cy"]
        y1, y2 = H - y1, H - y2
        op(b"q %.3f %.3f %.3f RG %.3f w %s" % (r, g, b, meta["width"], dash))
        op(b"%.1f %.1f m %.1f %.1f l S Q" % (x1, y1, x2, y2))
        angle = math.atan2(y2 - y1, x2 - x1)
        _pdf_arrowhead(op, x2, y2, angle, meta["arrow"], (r, g, b))
        if meta["arrow"] == "double":
            _pdf_arrowhead(op, x1, y1, angle + math.pi, meta["arrow"], (r, g, b))

    # --- entities ---------------------------------------------------------
    for ent in model["entities"]:
        pos = positions.get(ent["id"])
        if pos is None:
            continue
        meta = ENTITY_TYPE_META[ent["type"]]
        x, y, w, h = pos["x"], pos["y"], pos["w"], pos["h"]
        fill = meta["fill"]
        stroke = meta["stroke"]
        fr, fg, fb = _hex_rgb(fill)
        sr_, sg_, sb_ = _hex_rgb(stroke)
        py = H - y - h
        shape = meta["shape"]
        if shape in ("circle", "ellipse"):
            _pdf_ellipse(op, x + w / 2, py + h / 2, w / 2, h / 2)
            op(b"%.3f %.3f %.3f rg f" % (fr, fg, fb))
            op(b"q %.3f %.3f %.3f RG 1.2 w" % (sr_, sg_, sb_))
            _pdf_ellipse_path(op, x + w / 2, py + h / 2, w / 2, h / 2)
            op(b"S Q")
        else:
            op(b"q %.3f %.3f %.3f rg" % (fr, fg, fb))
            op(b"%.1f %.1f %.1f %.1f re f" % (x, py, w, h))
            op(b"Q %.3f %.3f %.3f RG 1.2 w" % (sr_, sg_, sb_))
            op(b"%.1f %.1f %.1f %.1f re S" % (x, py, w, h))
        font = b"/F3" if meta["font_style"] == "italic" else b"/F1"
        lines_wrapped = _wrap_label(ent["label"], int(w), GEOM["font_size"])
        total_h = len(lines_wrapped) * (GEOM["font_size"] + 3)
        ty = py + h / 2 - total_h / 2 + GEOM["font_size"] - 2
        for i, line in enumerate(lines_wrapped):
            op(b"BT %s %.1f Tf 0.1 0.1 0.1 rg %.1f %.1f Td (%s) Tj ET" % (
                font, GEOM["font_size"], x + w / 2 - 3, ty + i * (GEOM["font_size"] + 3),
                _escape_pdf_text(_latin1_or_question(line)).encode("latin1", "replace")))

    content.extend(em)
    return _assemble_pdf(content, int(math.ceil(W)), int(math.ceil(H)))


def _hex_rgb(hex_color: str) -> Tuple[float, float, float]:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return (0.6, 0.6, 0.6)
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


def _pdf_arrowhead(op, x: float, y: float, angle: float, kind: str,
                   rgb: Tuple[float, float, float]) -> None:
    r, g, b = rgb
    size = 6.0
    tip = (x, y)
    back = (x - size * math.cos(angle), y - size * math.sin(angle))
    perp = math.atan2(math.sin(angle + math.pi / 2), math.cos(angle + math.pi / 2))
    p1 = (back[0] + size * 0.5 * math.cos(perp), back[1] + size * 0.5 * math.sin(perp))
    p2 = (back[0] - size * 0.5 * math.cos(perp), back[1] - size * 0.5 * math.sin(perp))
    if kind == "tbar":
        op(b"q %.3f %.3f %.3f RG 3 w" % (r, g, b))
        op(b"%.1f %.1f m %.1f %.1f l S Q" % (p1[0], p1[1], p2[0], p2[1]))
        return
    op(b"%.3f %.3f %.3f rg" % (r, g, b))
    if kind in ("filled", "double"):
        op(b"%.1f %.1f m %.1f %.1f l %.1f %.1f l h f" % (
            tip[0], tip[1], p1[0], p1[1], p2[0], p2[1]))
    elif kind == "open":
        op(b"q %.3f %.3f %.3f RG 1.3 w" % (r, g, b))
        op(b"%.1f %.1f m %.1f %.1f l S" % (p1[0], p1[1], tip[0], tip[1]))
        op(b"%.1f %.1f m %.1f %.1f l S Q" % (p2[0], p2[1], tip[0], tip[1]))


def _pdf_ellipse_path(op, cx: float, cy: float, rx: float, ry: float) -> None:
    k = 0.5522847498
    op(b"%.1f %.1f m" % (cx + rx, cy))
    op(b"%.1f %.1f %.1f %.1f %.1f %.1f c" % (
        cx + rx, cy + k * ry, cx + k * rx, cy + ry, cx, cy + ry))
    op(b"%.1f %.1f %.1f %.1f %.1f %.1f c" % (
        cx - k * rx, cy + ry, cx - rx, cy + k * ry, cx - rx, cy))
    op(b"%.1f %.1f %.1f %.1f %.1f %.1f c" % (
        cx - rx, cy - k * ry, cx - k * rx, cy - ry, cx, cy - ry))
    op(b"%.1f %.1f %.1f %.1f %.1f %.1f c" % (
        cx + k * rx, cy - ry, cx + rx, cy - k * ry, cx + rx, cy))


def _pdf_ellipse(op, cx: float, cy: float, rx: float, ry: float) -> None:
    _pdf_ellipse_path(op, cx, cy, rx, ry)


def _assemble_pdf(content: bytearray, width: int, height: int) -> bytes:
    objects: List[bytes] = []
    objs: Dict[int, int] = {}

    def add(data: bytes) -> int:
        objects.append(data)
        return len(objects)

    catalog = add(b"<< /Type /Catalog /Pages 2 0 R >>")
    pages = add(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    page = add(
        ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents 4 0 R "
         "/Resources << /Font << /F1 5 0 R /F2 6 0 R /F3 7 0 R >> >> >>" % (width, height)
         ).encode("ascii"))
    stream = add(b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" +
                 bytes(content) + b"\nendstream")
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique /Encoding /WinAnsiEncoding >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0] * (len(objects) + 1)
    for i, data in enumerate(objects, start=1):
        offsets[i] = len(out)
        out += ("%d 0 obj\n" % i).encode("ascii")
        out += data
        out += b"\nendobj\n"
    xref_pos = len(out)
    out += ("xref\n0 %d\n" % (len(objects) + 1)).encode("ascii")
    out += b"0000000000 65535 f \n"
    for i in range(1, len(objects) + 1):
        out += ("%010d 00000 n \n" % offsets[i]).encode("ascii")
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" %
            (len(objects) + 1, xref_pos)).encode("ascii")
    return bytes(out)


if __name__ == "__main__":  # pragma: no cover - tiny CLI for manual probing
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--validate":
        m = json.load(open(sys.argv[2], encoding="utf-8-sig"))
        errs, warns = validate_model(m)
        print("errors=%d warnings=%d" % (len(errs), len(warns)))
        for e in errs:
            print("ERROR:", e)
        for w in warns:
            print("WARN:", w)
        sys.exit(1 if errs else (2 if warns else 0))
