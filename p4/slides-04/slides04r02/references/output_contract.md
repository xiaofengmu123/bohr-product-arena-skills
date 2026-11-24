# result.json 输出契约（全字段说明）

## 1. 顶层结构

`/app/output/result.json` 是唯一强制结果文件。骨架阶段（前 180s）即写入全部字段并以 null/[] 占位，此后只做增量填充。顶层键：

| 键 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `case_id` | string | 是 | 来自 `/input/case.json` 的 `case_id`/`caseId`/`id`；缺失时为 `unknown-<hash>` 并标 `input_error` |
| `status` | string | 是 | 见下方状态枚举 |
| `output_contract` | object\|null | 否 | 原样保留题面 `output_contract`（若提供） |
| `presentation` | object | 是 | 可编辑演示文件 + 页计划 |
| `evidence_map` | array | 是 | 页级证据映射 |
| `render_record` | object | 是 | 渲染与版本记录 |
| `sources` | array | 是 | 输入材料清单（含 locator） |
| `uncertainty` | array | 是 | 未满足项/缺口/偏差 |
| `pending_human` | array | 是 | 需人工判定的事项 |
| `errors` | array | 是 | 可捕获错误 |
| `degradations` | array | 是 | 降级处理记录 |
| `change_log` | array | 是 | 带时间戳的变更记录 |
| `runtime` | object | 是 | 预算与台账摘要 |

若题面 `output_contract` 带有额外键，一律原样回填到顶层（未知键不删除、不改名）；其键与缺省三键冲突时以题面 schema 为准。

## 2. status 枚举（诚实原则）

| 值 | 含义 |
|---|---|
| `ok` | 全部可交付项生成且校验通过 |
| `partial` | 有产出但存在缺口（缺某页版式/某图源/某导出件） |
| `insufficient_evidence` | 输入材料不足以支撑结论 |
| `input_error` | 输入缺失/无法解析/缺 case_id |
| `failed` | 关键步骤失败且无可用主产物 |

运行中途允许 `in_progress`，收口阶段必须替换为上述五值之一。**不编造数据、不编造软件状态、不编造评测分数。**

## 3. presentation（缺省契约）

```json
{
  "pptx_path": "/app/output/deck.pptx",
  "pdf_path": "/app/output/deck.pdf",
  "png_paths": ["/app/output/slide-01.png"],
  "slide_count": 12,
  "page_plan": [
    {"index": 1, "title": "...", "claim": "...", "layout": "...",
     "notes": "讲稿备注", "refs": ["materials/paper.md#L10-L15"]}
  ]
}
```

- `pptx_path` 必须可打开、可继续编辑；`pdf_path`/`png_paths` 是同一内容真实渲染的预览（非占位）。
- `page_plan` 覆盖 封面→正文→结论 的主要页类型；每页 `claim` 是一句可验证的主张，`refs` 指向来源定位。

## 4. evidence_map（逐字溯源）

每页一条（或每主张一条）：

```json
{
  "page": 2,
  "claim": "方法使用 XYZ 模型",
  "refs": ["materials/paper.md#L120-L134"],
  "evidence_status": "verified",
  "evidence": [
    {"locator": "materials/paper.md#L120-L134",
     "quote": "We adopt the XYZ model as ..."}
  ]
}
```

- `locator` 形式：`<文件>#L起-L止`（文本）、`<文件>.pdf#page=N`（PDF 页）、`figures/<名>`（图）、`tables/<名>`（表）。
- `quote` 为**逐字引用**，不得改写；关键数值/图号/结论必须可被 quote 直接支撑。
- `evidence_status` 取值：`verified`（已定位核对）/`inferred`（推断，需在 uncertainty 说明）/`source_conflict`（多源冲突，两处都记）/`not_reported`（材料未报告）。
- 冲突信息不隐藏：同一指标两处来源不一致时，两项都记录并标 `source_conflict`。

## 5. render_record（渲染与版本）

```json
{
  "source_files": [{"relpath": "materials/paper.pdf", "sha1": "...", "size_bytes": 123456}],
  "template_file": "materials/template.pptx",
  "layouts_used": [
    {"slide_index": 1, "layout_name": "Title Slide", "master": "master1",
     "inherited": true, "note": ""},
    {"slide_index": 5, "layout_name": "Blank (fallback)", "master": "master1",
     "inherited": false, "note": "模板缺 Results 版式，用 Blank 替代"}
  ],
  "fonts": [
    {"role": "title", "spec": "Noto Sans CJK SC", "resolved": "WenQuanYi Zen Hei",
     "fallback": true, "note": "缺 Noto，font-discovery 回退"}
  ],
  "exports": [
    {"format": "pdf", "path": "/app/output/deck.pdf", "ok": true, "note": ""}
  ],
  "integrity": {"pptx_zip_ok": true, "slide_count": 12, "preview_matches": true,
                "overflow_pages": [], "figure_number_mismatch": []},
  "media": [{"relpath": "materials/fig2.png", "used_on_page": 4, "scaled": "contain",
             "crop": null, "note": ""}]
}
```

- `layouts_used[].inherited` 必须是**查过 pptx XML** 后的事实（母版/版式/占位符继承关系可查）；替代版式显式标注，不得谎称"已继承"。
- `fonts[].fallback=true` 表示字体缺失后回退，禁止假装字体存在。
- `exports` 记录导出格式/路径/成败；失败时 `path` 为 null 并写 note。

## 6. uncertainty / pending_human / change_log 条目格式

- `uncertainty[]`：`{"code": "text_overflow|missing_layout|font_missing|figure_broken|export_failed|...", "page": 4|null, "detail": "...", "impact": "..."}`。
- `pending_human[]`：`{"item": "...", "why": "...", "decision_needed": "..."}`。
- `change_log[]`：`{"ts": "ISO8601", "event": "...", "detail": "..."}`，按时间追加。

## 7. runtime / 台账

```json
{
  "started_at": "ISO8601", "skeleton_written_at": "ISO8601",
  "started_epoch": 1700000000.0, "skeleton_written_epoch": 1700000010.0,
  "budget_s": 900, "request_budget": 64, "requests_used": 0
}
```

阶段级台账见 `/app/work/phase_ledger.json`（由 `scripts/init_result.py` 创建、`scripts/phase_gate.py` 维护）。

## 8. 脚本退出码语义

| 脚本 | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| `init_result.py` | 骨架已写、输入正常 | — | 骨架已写但输入缺失/坏 JSON/缺 case_id | 输出目录不可写 |
| `phase_gate.py` | 命令成功 / 门禁通过 | 有告警（阶段超时或超请求） | 门禁违规（骨架晚于 180s / 总超 900s / 超 64 请求） | 台账不可写 |
| `selftest.py` | 全部自检通过 | 存在失败项 | — | — |

## 9. 完整示意（降级场景，非正式案例）

下面是**示意值**，仅说明字段形态与诚实降级写法，不代表任何正式案例结果：

```json
{
  "case_id": "demo-partial",
  "status": "partial",
  "presentation": {
    "pptx_path": "/app/output/deck.pptx",
    "pdf_path": "/app/output/deck.pdf",
    "png_paths": ["/app/output/slide-01.png"],
    "slide_count": 10,
    "page_plan": [
      {"index": 1, "title": "研究问题", "claim": "本文研究 XYZ 问题",
       "layout": "Title Slide", "notes": "开场：问题与意义", "refs": ["materials/paper.md#L10-L15"]},
      {"index": 6, "title": "结果", "claim": "方法 A 比基线高 3.2%",
       "layout": "Blank (fallback)", "notes": "模板缺 Results 版式",
       "refs": ["materials/paper.md#L210-L220", "materials/figures/fig3.png"]}
    ]
  },
  "evidence_map": [
    {"page": 6, "claim": "方法 A 比基线高 3.2%",
     "refs": ["materials/paper.md#L210-L220"],
     "evidence_status": "verified",
     "evidence": [{"locator": "materials/paper.md#L213",
                   "quote": "Method A improves over the baseline by 3.2%."}]},
    {"page": 6, "claim": "图 3 为结果对比",
     "refs": ["materials/figures/fig3.png"],
     "evidence_status": "not_reported",
     "evidence": []}
  ],
  "render_record": {
    "source_files": [{"relpath": "materials/paper.pdf", "sha1": "…", "size_bytes": 123456}],
    "template_file": "materials/template.pptx",
    "layouts_used": [
      {"slide_index": 6, "layout_name": "Blank (fallback)", "master": "master1",
       "inherited": false, "note": "模板缺 Results 版式，用 Blank 替代"}
    ],
    "fonts": [{"role": "title", "spec": "Noto Sans CJK SC",
               "resolved": "WenQuanYi Zen Hei", "fallback": true, "note": "缺 Noto 回退"}],
    "exports": [{"format": "pdf", "path": "/app/output/deck.pdf", "ok": true, "note": ""}],
    "integrity": {"pptx_zip_ok": true, "slide_count": 10, "preview_matches": true,
                  "overflow_pages": [7], "figure_number_mismatch": []},
    "media": [{"relpath": "materials/fig3.png", "used_on_page": 6,
               "scaled": "contain", "crop": null, "note": ""}]
  },
  "sources": [{"relpath": "materials/paper.pdf", "size_bytes": 123456}],
  "uncertainty": [
    {"code": "missing_layout", "page": 6, "detail": "Results 版式缺失，用 Blank 替代",
     "impact": "版式继承不完整"},
    {"code": "font_missing", "page": null, "detail": "Noto Sans CJK SC 缺失，回退 WenQuanYi",
     "impact": "字体观感与规范有差异"},
    {"code": "text_overflow", "page": 7, "detail": "第 7 页文本溢出，未压字号", "impact": "需人工确认"}
  ],
  "pending_human": [{"item": "第 7 页溢出是否拆分", "why": "自动换行会破坏要点结构",
                     "decision_needed": "拆成两页或精简要点"}],
  "errors": [],
  "degradations": [],
  "change_log": [{"ts": "2026-01-01T00:00:00Z", "event": "skeleton_init", "detail": "早落盘骨架"},
                  {"ts": "2026-01-01T00:12:00Z", "event": "layout_fallback", "detail": "Results→Blank"}],
  "runtime": {"started_at": "2026-01-01T00:00:00Z", "skeleton_written_at": "2026-01-01T00:00:05Z",
              "budget_s": 900, "request_budget": 64, "requests_used": 18}
}
```

要点：`status=partial` 时，每个缺口都在 `uncertainty` 有 `code/page/detail/impact`；`layouts_used.inherited=false` 与 `fonts.fallback=true` 显式标注替代关系，不假装"已继承/字体存在"；失败导出件路径留 null 并写 note。
