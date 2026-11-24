---
name: graphical-abstract
description: >-
  Deterministic, traceable scientific graphical-abstract authoring for the official
  /input/case.json + /input/task.md harness. Phase-budgeted pipeline (900 s wall clock /
  64 model requests) that persists a valid partial result.json skeleton within the first
  3 minutes, extracts entities/relations/data with locator + verbatim-quote provenance,
  builds a layered editable SVG via scripts/core_impl.py, produces honest real PNG/PDF
  previews or records preview_not_rendered, validates schema + provenance + exports with
  scripts/main.py, and writes /app/output/result.json containing case_id plus every
  output_contract field plus status/sources/uncertainty/change_log. Use for graphical
  abstract, schematic, mechanism, flowchart and chart-edit tasks.
version: 1.1.0
---

# Graphical Abstract Skill

## 1. Role & when to use

You are a scientific figure author. For one fixed research case you produce a **single-page
graphical abstract** in the order *question → method → core results → conclusion*, plus an
**editable layered SVG source** and **real rendered PNG/PDF previews**, with every visual
element traceable to the source material. Use this Skill for any task whose `task.md` asks
for a graphical abstract, schematic, mechanism diagram, flowchart, or chart edit. Do not
write paper prose, statistics, or experimental design; this Skill owns only figure content,
layout, editability, and export quality.

## 2. Input contract

- Runtime input: `/input/case.json` (case facts, materials, optional `output_contract`) and
  `/input/task.md`. Material PDFs/CSVs/figures may also sit under `/input/`; **read the actual
  files, never trust labels**. PDF text comes from the PDF itself (no pre-extracted layer).
- Work files go under `/app/work/`; final deliverables under `/app/output/`.
- The Skill is installed at `/skills/weekly-skill/`. Run scripts from that directory so
  `scripts/*.py` resolves (`cd /skills/weekly-skill` or set that as cwd).
- Scripts use only the Python standard library; no pip install is required.

## 3. Output contract

Write `/app/output/result.json` containing `case_id` **plus every field in the case's
`output_contract`** (echoed verbatim; missing values become `null`/`[]`, never renamed or
dropped). If the case gives no contract, use the canonical schema in
`references/output_schema.md`. Always add `status`, `sources`, `uncertainty`, `change_log`,
plus `limitations`, `pending_human`, `errors`, `degradations`, `runtime`.

- `status` is exactly `completed | partial | failed` (never `ok`).
- The editable source is a layered SVG `fig_<case_id>.svg` with `layer-*` groups, editable
  `<text>`, and `data-source` attributes; text must never be converted to paths.
- `exports` records PNG/PDF with `renderer` + `status`. A preview is claimed only when a real
  file with correct magic bytes (`\x89PNG`, `%PDF-`) exists; otherwise record
  `preview_not_rendered` — never fabricate an image.
- Every entity/relation/data entry carries a `source` with a `locator` (file#section or page)
  and a **verbatim `quote`** from the material. Conflicts are recorded, not hidden.

## 4. Execution flow (900 s / 64 requests budget)

Budget is split into phases; `scripts/main.py` persists a stage ledger on each phase so the
remaining time and request spend stay visible. **Rule: a valid `result.json` skeleton with
`status=partial` must be on disk within the first 3 minutes (≤180 s).** After that, only
improve incrementally — never start over from nothing.

| # | Phase (time / requests) | Input → Output | Command |
|-|-|-|-|
| 1 | **skeleton** (≤60 s / ≤4) | `/input/case.json`+`task.md` → `result.json`(partial), `case_normalized.json`, `figure_spec.json`, `stage_ledger.json` | `python scripts/main.py --phase skeleton` |
| 2 | **facts** (≤90 s / ≤12) | normalized case → edit `figure_spec.json`: entities/relations/charts/legend/panels, each with `source.locator`+`quote` | agent edits JSON (no code) |
| 3 | **build** (≤60 s / ≤6) | `figure_spec.json` → layered SVG + `fig_manifest.json` + attempted PNG/PDF | `python scripts/main.py --phase build` |
| 4 | **validate** (≤60 s / ≤8) | result+SVG → validation report; fill `sources`/`data_mapping`; fix critical defects | `python scripts/main.py --phase validate` |
| 5 | **improve** (≤360 s / ≤20) | report → fix overflow/small font/low contrast/missing source; re-run phases 3–4; bump `change_log` each rewrite | re-run build/validate |
| 6 | **finalize** (≤60 s / ≤6) | final SVG/PNG/PDF + report → set `status`, fill `limitations`/`uncertainty`, write final `result.json` | `python scripts/main.py --phase finalize` |
| — | **reserve** (~120 s / ~6) | renderer fallback, error handling, re-checks | `python scripts/main.py --phase ledger` |

Check the ledger at any time with `python scripts/main.py --phase ledger`. Request cap is 64
model requests total; after **two** failed attempts with the same strategy, stop retrying,
record the limitation honestly, and continue with a `partial` result.

## 5. Self-check checklist (maps to scoring dimensions)

Tick each before finalizing; `scripts/selftest.py` automates several.

- [ ] `result.json` has `case_id` + all `output_contract` fields + `status`/`sources`/`uncertainty`/`change_log` (结果完整性 / 可核验性).
- [ ] Every box/arrow/bar/legend swatch maps to one entity/relation/data entry, each with a `source` locator + verbatim quote (实体关系与数据映射正确).
- [ ] Arrow directions match the source sentence; units, scale and condition labels present (结果正确性).
- [ ] SVG is layered (`layer-*`), text stays editable `<text>`, no text-as-path; PNG/PDF are real files or honestly `preview_not_rendered` (可编辑源文件 / 导出件).
- [ ] No canvas overflow, no font < 10 px, colorblind-safe/grayscale-separable palette (版式与视觉层级).
- [ ] Same input reproduces the same structure (deterministic grid + fixed seed); `change_log` increments each rewrite (运行稳定性 / 可复核).
- [ ] Missing/ambiguous items are in `uncertainty`/`limitations`/`pending_human`, never fabricated (异常处理).
- [ ] Scripts run from relative paths `scripts/*.py`, stdlib only, no hidden personal files; `python scripts/selftest.py` exits 0 (生态贡献 / 复用性).

## 6. Degradation & honesty

- **Missing data/value**: set the field `null`, add an `uncertainty` entry; do not estimate.
- **Ambiguous entity/direction**: split ids or use an undirected connector, and note the ambiguity.
- **Missing renderer**: keep the SVG source, record `preview_not_rendered`, try the platform
  renderers listed in `environment/resources.yaml` (pdf-render-tool, office-renderer,
  browser-automation, latex-compiler); never fabricate a PNG/PDF.
- **No case.json / bad JSON**: `skeleton` still writes a `status=failed` result with the
  limitation and exits 0; report the blocker and stop dependent steps.
- **Font substitution / export failure**: state it in `limitations`; the editable SVG still
  counts as the primary deliverable.

## 7. Minimal example

```bash
cd /skills/weekly-skill
python scripts/main.py --phase skeleton
# edit /app/work/figure_spec.json (fill entities/relations/data/legend + sources)
python scripts/main.py --phase build
python scripts/main.py --phase validate
python scripts/main.py --phase finalize
```

The `figure_spec.json` schema and field semantics are documented in
`references/output_schema.md`; the checkable rubric is in `references/checklist.md`.
