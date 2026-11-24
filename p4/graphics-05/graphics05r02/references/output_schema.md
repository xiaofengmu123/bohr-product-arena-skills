# Canonical output schema (fallback when `output_contract` is absent)

`result.json` must always contain `case_id` and `status`, plus every key of the
case's `output_contract` echoed verbatim. When the case provides no contract, the
canonical fields below are used; they are also the recommended structure even when a
contract exists.

## Top-level fields

| Field | Type | Required | Meaning |
|-|-|-|-|
| `case_id` | string | yes | stable identifier echoed from the case |
| `status` | enum | yes | `completed` \| `partial` \| `failed` |
| `graphical_abstract` | object | yes | `{question, method, results, conclusion}` one-line summaries |
| `editable_source` | object | yes | `{path, format:"svg", layers:[], text_editable:true, renderer}` |
| `exports` | array | yes | PNG/PDF records with `{format, path, renderer, status, width, height, dpi}` |
| `entities` | array | yes | `{id, label, kind, panel, source}` |
| `relations` | array | yes | `{id, from, to, type, direction, label, source}` |
| `data_mapping` | array | yes | `{visual_channel, entity_or_relation, data_value, unit, source}` |
| `legend` | array | yes | `{label, color, meaning, source}` |
| `sources` | array | yes | `{id, path, locator, quote, role}` |
| `uncertainty` | array | yes | `{item, issue, resolution}` |
| `limitations` | array | yes | honest list of unresolved limits |
| `pending_human` | array | yes | items needing human confirmation |
| `errors` / `degradations` | array | yes | runtime problems and fallbacks |
| `change_log` | array | yes | `{version, time, changes, reason}` |
| `runtime` | object | yes | `{started_at, finished_at}` |

## Source provenance (hard requirement)

Every entity, relation, data point and legend swatch carries a `source` object:

```json
{"locator": "materials/paper.pdf#page3", "quote": "peak current increased with depolarization"}
```

`locator` is a file + page/section position; `quote` is the **verbatim** text from the
material. Never invent a quote or DOI. Missing provenance is recorded in `uncertainty` or
`pending_human`, and the field stays `null`.

## Export honesty

- `status: "rendered"` may only be set when the file exists and starts with the right
  magic bytes: PNG `\x89PNG\r\n\x1a\n`, PDF `%PDF-`.
- Otherwise set `status: "preview_not_rendered"` and keep the SVG as the primary source.
- `scripts/main.py --phase build` probes renderers in this order (stdlib `shutil.which`):
  rsvg-convert → inkscape → chromium/google-chrome → wkhtmltoimage/wkhtmltopdf →
  ImageMagick, with a 45 s timeout each. Probe results are written to `fig_manifest.json`.

## `figure_spec.json` (the single agent-editable file)

The agent edits only this file between `skeleton` and `build`. Its schema:

```json
{
  "case_id": "...", "title": "...", "version": 1,
  "canvas": {"width":1600,"height":1000,"unit":"px","min_font_size":10,"margin":40},
  "layout": {"mode":"q_m_r_c","reading_order":["question","method","results","conclusion"]},
  "panels": {"question":{"title":"Question","summary":"","source":null},
             "method": {...}, "results": {...}, "conclusion": {...}},
  "entities": [{"id":"E1","label":"...","kind":"...","panel":"method","source":{...}}],
  "relations": [{"id":"R1","from":"E2","to":"E1","type":"activates|inhibits|leads_to|part_of",
                 "direction":"forward|undirected","label":"","source":{...}}],
  "charts": [{"id":"D1","kind":"bar","panel":"results",
              "values":[{"label":"","value":0.0,"unit":""}],"source":{...}}],
  "legend": [{"label":"","color":"#117733","source":{...}}],
  "notes": {"units":"","scale":"","conditions":"","uncertainty":""}
}
```

`kind` and `type` map to the Okabe-Ito colorblind-safe palette so that category identity is
preserved in grayscale printing. Layout is deterministic: the same spec always yields the
same SVG.
