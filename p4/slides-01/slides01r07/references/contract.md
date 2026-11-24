# 输出契约与证据溯源规范

## 1. result.json 契约
- 权威字段名以 `/input/case.json` 的 `output_contract` 为准；`scripts/main.py` 会自动提取并**原样回填全部键**（缺值给 `null`/`[]`，不改名、不删未知键）。
- 若 `output_contract` 缺失，回退到七字段：`case_id, status, editable_source, export_artifact, content_map, compliance_checks, exceptions`。
- 统一信封字段（始终合并）：`sources, uncertainty, pending_human, change_log, errors, degradations, runtime`。
- `status` 取值：`ok | partial | insufficient_evidence | input_error`，诚实填写。

## 2. 主产物与渲染
- `editable_source`：`{"path":"presentation.pptx","format":"pptx","slides":N}`，指向真实可打开、可编辑文件。
- `export_artifact`：`{"pdf":{"path":"presentation.pdf","pages":M}}`，`M` 必须与 pptx 页数 `N` 一致；无 PDF 能力时给 SVG/PNG 预览并在 `exceptions` 标注。
- 无 pptx 能力时：`editable_source.format="svg"`，`exceptions` 记录原因。

## 3. content_map（页级证据映射）
每页一项，字段：
- `page`（从 1 起的页码）
- `claim`（一页一个主张）
- `role`（`title|background|method|result|discussion|limitation|conclusion|other`）
- `evidence`（`locator`：如 `paper.pdf#p3` 或 `Fig. 2`；`quote`：逐字命中原文；`figure_ref`/`table_ref`/`section_ref` 可选）
- `notes`（讲解备注，与页面文字不重复）

## 4. 证据溯源硬规则
1. 每个关键结论/数值/图元都带 `locator + 逐字 quote`；quote 逐字命中材料，禁止意译改写事实。
2. 冲突信息不隐藏：同一指标两处不一致时都记录，标 `source_conflict`。
3. 不虚构引用、DOI、数值；缺失写 `not_reported` 或 `pending_human`。
4. 讲解备注写过渡、强调点、被问补充证据，不复制 bullet 文字。
5. 区分主结论与支撑细节；示意图不当实验数据；他人结果不当本文结果。

## 5. draft.json（Agent 撰写的工作稿）
```json
{"case_id":"...","audience":"...","duration_minutes":0,"slide_cap":0,"title":"...",
 "slides":[{"id":1,"claim":"...","role":"method",
   "bullet_points":["..."],"evidence":{"locator":"...","quote":"...",
   "figure_ref":"Fig. 2","table_ref":null,"section_ref":"3. Results"},
   "speaker_notes":"..."}]}
```
脚本不替你组织内容；读不出就写缺口，绝不编造。
