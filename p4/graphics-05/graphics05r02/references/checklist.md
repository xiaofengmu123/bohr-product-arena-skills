# Checkable rubric (maps to the five scoring dimensions)

Use this as the final human- or agent-runnable checklist. Each item is phrased so a "yes"
is directly verifiable from files on disk; subjective items cite their evidence location.
`scripts/selftest.py` automates the mechanical items and must exit 0 before finalizing.

## A. 实际效果 (actual effect)

- [ ] A1 One-page figure in `question → method → results → conclusion` order; the four
  `graphical_abstract` fields are non-empty or explicitly `null` with an uncertainty entry.
- [ ] A2 Editable source `fig_<case_id>.svg` exists, parses as XML, and has `layer-*` groups.
- [ ] A3 All `<text>` is real editable text; no label is converted to `<path>`.
- [ ] A4 Every entity, relation, data bar and legend swatch in the SVG has a `data-source`
  attribute pointing to a `sources[]` id whose `locator`+`quote` are verbatim from material.
- [ ] A5 Units, scale and condition labels are present where data are plotted; missing ones
  are in `uncertainty` or `limitations`, not silently omitted.

## B. 使用增益 (gain over baseline)

- [ ] B1 `fig_manifest.json` + `stage_ledger.json` prove the SVG was rebuilt from a spec
  (editable) rather than hand-drawn once.
- [ ] B2 Re-running `build` on an unchanged `figure_spec.json` reproduces byte-identical
  structure (deterministic grid; same layer ids, same node positions).
- [ ] B3 Each improve-loop rewrite appends a `change_log` entry with `version` + `reason`.

## C. 创新增益 (novel capability)

- [ ] C1 Entity/relation/data are typed (`kind`/`type`) and rendered via the
  colorblind-safe Okabe-Ito palette, giving grayscale-separable categories.
- [ ] C2 Layout is a declarative 4-zone grid (`q_m_r_c`), so panel position and reading
  order are data, not hard-coded drawing commands.
- [ ] C3 The stage ledger makes the 900 s / 64 request budget observable, not just prose.

## D. 可靠体验 (reliability)

- [ ] D1 `skeleton` writes a valid `result.json` (status `partial`/`failed`) even when
  `case.json` is missing, empty, or malformed; exit code is 0 and the cause is in `errors`.
- [ ] D2 Font size ≥ 10 px, no node extends past the canvas (validator reports overflow).
- [ ] D3 Exports are honest: `rendered` implies a real file with correct magic bytes;
  otherwise `preview_not_rendered` with the SVG retained.
- [ ] D4 Ambiguous entities use split ids or an undirected connector and are noted in
  `uncertainty`.

## E. 生态贡献 (ecosystem)

- [ ] E1 `SKILL.md` frontmatter has `name`/`description`/`version`; scripts are stdlib-only
  and invoked by relative path (`scripts/*.py`).
- [ ] E2 `references/output_schema.md` documents the schema; `references/checklist.md`
  documents this rubric.
- [ ] E3 `scripts/selftest.py` passes on an isolated demo (example input only, never a
  scored case) and on empty-input / bad-JSON degradation paths.
- [ ] E4 No prepared answers, no fabricated benchmark scores, no personal files.

## Finalize gate

Finalize is blocked while: A1–A4 are unmet, `result.json` lacks `case_id`, or `status`
is not one of `completed | partial | failed`. If blocked, keep `status=partial` and list
the blockers in `limitations`.
