# result.json 输出契约（output_contract）

本文件定义本 Skill 在 `/app/output/result.json` 中必须写入的全部字段。字段由
`scripts/main.py` 自动聚合生成；`scripts/main.py --check` 按本契约逐项校验。
题目未提供显式 `output_contract` 键时，本契约即运行时交付标准。

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | 是 | 固定 `1.0.0` |
| `case_id` | string | 是 | 取自 `/input/case.json`，不得为空 |
| `status` | string | 是 | `success` / `partial` / `insufficient_evidence` / `input_error` / `failed`，必须诚实 |
| `task_summary` | string | 是 | 任务/材料摘要（截断至 400 字符） |
| `editable_source_files` | array | 是 | 可编辑源文件清单，见下 |
| `export_artifacts` | array | 是 | 导出件清单（SVG/PDF/PNG），见下 |
| `entities` | array | 是 | 实体清单（figure_model 投影） |
| `relations` | array | 是 | 关系清单（figure_model 投影） |
| `legend` | array | 是 | 图例项（与实际配色/线型/形状自洽） |
| `data_mappings` | array | 是 | 数据→图面编码映射 |
| `figure_model` | object | 是 | 规范化后的完整中间模型（可复核核心） |
| `intermediate_artifacts` | array | 是 | 中间产物索引（plan.json、figure_model.json） |
| `sources` | array | 是 | 聚合来源：`{locator, quote, element_refs}` |
| `uncertainty` | array | 是 | 歧义、方向不明、无来源数值 |
| `pending_human` | array | 是 | 需人工确认项 |
| `change_log` | array | 是 | 增量修改记录，首条必须是骨架落盘 |
| `errors` | array | 是 | 阻断性错误 |
| `degradations` | array | 是 | 降级与工具缺失记录 |
| `limitations` | array | 是 | partial/failed/insufficient 时必填 |
| `runtime` | object | 是 | 起止时间与耗时 |

## editable_source_files[] 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `path` | string | 相对 `/app/output/` 的路径，文件必须真实存在 |
| `format` | string | `svg` 等 |
| `editable` | bool | 必须 `true` |
| `layers` | array | SVG 命名图层：`compartments/relations/entities/data/legend` |
| `description` | string | 说明 |

## export_artifacts[] 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `path` | string | 相对 `/app/output/` 的路径，文件必须真实存在 |
| `format` | string | `svg` / `pdf` / `png` |
| `generated_by` | string | 生成工具（脚本名或平台工具名） |
| `width_px` / `height_px` | int | 像素尺寸（位图） |
| `width_pt` / `height_pt` | int | 点尺寸（矢量） |
| `dpi` | int | 分辨率（位图） |
| `font_check` | string | 字体检查结果（缺失须如实说明） |
| `color_check` | string | 色彩检查结果（须与图例一致） |

PNG 由平台 `browser-automation` 光栅化 SVG 产生后，用
`python scripts/main.py --register-export ...` 注册；若工具不可用，PNG 项
不得伪造，`limitations` 说明。

## figure_model（中间表示，可复核核心）

规范化后的模型：`entities[]`（id/label/type/aliases/compartment/transmembrane/
sources/confidence/data）、`relations[]`（id/subject/object/type/direction/
predicate_text/modifier/evidence/sources/semantics_source）、`layout`（bands/
positions/canvas/feedback_loops）、`legend[]`、`data_mappings[]`、`uncertainty`、
`pending_human`。它由 `scripts/figure_model.py: build_model(plan)` 从
`plan.json` 确定性生成，是 SVG/PDF 的唯一数据源。

## sources[] 字段（证据溯源硬要求）

| 字段 | 类型 | 说明 |
|---|---|---|
| `locator` | string | 材料位置，如 `materials/paper.md#p3` |
| `quote` | string | 逐字引用原文片段 |
| `element_refs` | array | 引用该来源的实体/关系 id |

每个关键结论/数值/图元都须能经 `sources` 回溯到材料的逐字片段；无来源元素
写入 `uncertainty` 或 `pending_human`，不得补造。
