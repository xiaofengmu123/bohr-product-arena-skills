# 校验顺序与降级判定（checklist.md）

本文件说明 `core_impl.py` 的**校验顺序**、`result.json` 字段定义，以及缺数据/缺软件时的
诚实降级规则。与 `SKILL.md` §5 的评分维度清单互补：§5 是交付前逐条自检，本文是脚本
内置判定与字段约定。

## 1. 校验顺序（先校验，后渲染；先图内一致，后导出合规）

1. **schema 校验**：JSON 可解析；`entities` 非空；实体 id 唯一；`relation.from/to` 必须
   指向已存在实体；`legend.maps_to` 字段路径有效。→ 硬错误记入 `errors`。
2. **证据完整性**：每个实体/关系/图例/标注的 `evidence` 是否含 `locator` 与逐字 `quote`；
   `confidence` 是否受控。→ 缺失记入 `warnings`，渲染继续，但写入 `uncertainty`。
3. **图例-数据一致性**：图例 `maps_value` 必须落在数据字段实际取值中；每个实际取值都应有
   图例项；颜色不重复用于不同含义。→ 不符记入 `warnings`/`errors`。
4. **确定性渲染**：拓扑分层布局（无随机、无时钟依赖），输出七层 SVG
   `groups/edges/nodes/labels/annotations/legend/title`。
5. **导出合规检查**：探测 `rsvg-convert → inkscape → cairosvg → chromium`；校验 PNG
   (`\x89PNG\r\n\x1a\n`) 与 PDF (`%PDF-`) 文件头；记录 width/height/dpi/color_space/font_check。

## 2. status 判定

| 条件 | status |
|-|-|
| 未发现 `/input/case.json` | `failed` |
| 发现 case.json 但 `work/figure_ir.json` 缺失或 entities 为空 | `partial` |
| 存在硬错误（实体引用悬空、图例映射无效等） | `partial` |
| 无已验证的 PNG/PDF 导出件 | `partial` |
| 无硬错误、至少有 1 个可编辑源文件 + 1 个已验证导出件 | `complete` |

`warnings`（证据缺失、低置信推断等）不改变 status，但必须全部进入 `uncertainty`；
任何 `partial` 都必须在 `uncertainty` 或 `change_log` 说明 limits。

## 3. result.json 字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | str | 从 case.json 提取，缺失填 `unknown` |
| `status` | str | `complete|partial|failed` |
| `editable_source_files` | list | 分层 SVG（`layers:true`、`editable_text:true`） |
| `export_files` | list | PNG/PDF，含 width_px/height_px/dpi/color_space/font_check/ok/note |
| `provenance` | list | 每条含 element/source_ref/locator/quote/mapping/version/confirmed |
| `sources` | list | 材料引用聚合，含 ref/path/locator/used_for |
| `uncertainty` | list | item/reason/impact/mitigation |
| `pending_human` | list | item/why/suggested_action |
| `change_log` | list | time/action/detail |
| `runtime` | object | 工具版本/起止时间/输入路径/材料清单；不伪造评测分数 |

各数组为空时必须写原因，不得静默省略。

## 4. 降级与诚实标注

| 场景 | 处置 |
|-|-|
| PDF 无文本层 | pdftotext/pdftoppm/OCR；`sources.locator` 标「OCR 识别，需人工复核」 |
| 缺字体 | font-discovery 找替代；SVG 不嵌入字体则 `font_check="none"` 并写入 uncertainty |
| 无导出渲染器 | 保留 SVG，`export_files` 记 `ok:false` 与原因，`status=partial`，不伪造 PNG/PDF |
| 材料缺失/矛盾 | 记入 `uncertainty`；不确定实体不画或标「推测」，绝不编造基因名/浓度/时间点/单位 |
| 无原文引用 | `quote=""`、`confirmed=false`、`confidence=agent_inferred`，同步进 `pending_human` |
| 空输入目录 | 退出码 0，写合法 `failed` 结果，不抛异常 |
| 坏 JSON/坏编码 | UTF-8-sig→UTF-8→GB18030→latin-1 容错；超 2MB 材料跳过并记录 |

## 5. 可复核性约定

- SVG 每个节点带 `id="node-<eid>"` 与 `data-entity`，每条边带 `data-relation` 语义
  （label 或 type），图例样本形状由 `marker` 决定；审稿人可定位任一图元回 `provenance`。
- `provenance.quote` 是逐字引用，不含模型转述；转述必须另加 `locator` 并降 `confidence`。
- 重复运行结果一致：布局确定性、排序固定、无随机数。
