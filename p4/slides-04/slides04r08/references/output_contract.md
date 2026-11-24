# result.json 输出契约

本文件说明 Skill 运行时写入 `/app/output/result.json` 的字段。`init_result.py` 在第一步落骨架，后续脚本增量填充；未知键不回删、不更名。若 `/input/case.json` 自带 `output_contract`，其全部键会被原样回填（缺值给 `null`/`[]`），并与下表字段合并。

## 顶层字段

| 字段 | 类型 | 含义 | 填谁 |
|---|---|---|---|
| `case_id` | string | 输入案例标识（来自 case.json 的 case_id/caseId/id/challenge_id/armiId） | init_result |
| `status` | string | 运行状态枚举 | 全程 |
| `presentation` | object | 可编辑演示文件契约 | 制作+校验 |
| `evidence_map` | array | 页级证据映射 | build_evidence_map |
| `render_record` | object | 渲染与版本记录 | validate_deck |
| `sources` | array | 输入与材料来源清单 `{path,role}` | init_result |
| `uncertainty` | array | 未满足/未定位/降级项 `{code,page?,detail}` | 全程 |
| `pending_human` | array | 需要人工核对的项 | 收口 |
| `change_log` | array | 带时间戳的修改记录 `{ts,event,detail}` | 各脚本 |
| `runtime` | object | 实际耗时/请求数记录 | 收口 |

## status 枚举

- `ok`：全部完成，主产物+预览+证据映射齐备且校验通过。
- `partial`：产物部分可用（缺预览/少几页/部分证据未定位）。
- `insufficient_evidence`：材料不足，无法可靠成册。
- `input_error`：输入不可解析（坏 JSON、缺 case.json 等）。
- `failed`：主产物无法生成。
- `in_progress`：骨架阶段的中间态，最终交付前须改写为上述之一。

## presentation

```json
{
  "pptx_path": "/app/output/slides.pptx",
  "pdf_path": "/app/output/slides.pdf",
  "png_paths": ["/app/output/slide-01.png"],
  "slide_count": 12,
  "page_plan": [
    {"index": 1, "title": "...", "claim": "...", "layout": "slideLayout3.xml", "notes": "...", "refs": []}
  ]
}
```

## evidence_map 条目

每页一条；每个 ref 必须带 `locator`（源文件+定位，如 `paper.txt#L120` 或 `paper.txt` 段落标识）与逐字 `quote`（源文本原句，未改写字句）。`evidence_status` 取值：`located / partial / not_verified / no_refs`。`build_evidence_map.py` 用"空白归一后子串匹配"做确定性核验；匹配不到即 `not_verified` 并进 `uncertainty`，不得自行改写 quote 使其"看起来命中"。

## render_record

- `source_files[]`：输入来源清单。
- `template_file`：实际使用的模板 pptx。
- `layouts_used[]`：各页实际引用的版式文件名；替换版式须显式标注替代关系。
- `fonts[]`：pptx 中出现的字体集合；字体回退记录在此。
- `exports[]`：`{kind,path,valid,...}` 导出件清单。
- `integrity`：`validate_deck.py` 的校验汇总，含 `auto_verifiable[]`（可自动测）与 `human_required[]`（只能人工核）两个边界清单。
- `media[]`：`{name,bytes}` 内嵌媒体清单。

## 可信性规则

- 每个数值、图号、主张都指回 `evidence_map` 的 locator+quote；缺失就写 `pending_human` 或 `not_reported`，不虚构引用/DOI/数值。
- 冲突信息不隐藏：同一指标两个来源不一致时两条都记录并标 `source_conflict`。
- `runtime` 记录 `started_at/finished_at/elapsed_seconds/model_requests`，作为"使用增益"的可核验证据；不编造请求数或耗时。

## 六类要素 → 字段映射

| 题面要素 | 结果落点 |
|---|---|
| 论文主线 | `presentation.page_plan[].claim` + `evidence_map[].claim` |
| 页结构 | `presentation.page_plan[]` + `presentation.slide_count` |
| 图表引用 | `page_plan[].refs` + `evidence_map[].refs`（locator+quote） |
| 讲解逻辑 | `page_plan[].notes`（即讲稿备注） |
| 模板约束 | `render_record.template_file / layouts_used / fonts` |
| 导出渲染 | `render_record.exports / integrity` + PDF/PNG 预览路径 |

## 降级代码表（uncertainty[].code）

| code | 含义 | 建议 status |
|---|---|---|
| `missing_input` | 无 case_id 且无材料文件 | insufficient_evidence |
| `evidence_not_located` | 逐字 quote 在源文本未命中 | partial |
| `missing_layout` | 模板缺所需版式，已用最近版式替代 | partial |
| `font_fallback` | 主题字体缺失，已回退并记录 | partial |
| `image_source_invalid` | 图表源文件失效 | partial |
| `export_failed` | PDF/PNG 导出失败 | partial |
| `overflow_detected` | 文本框/形状越界（几何启发式） | partial |
| `low_readability` | 最小字号低于 8pt | partial |
| `source_conflict` | 同一指标多来源不一致 | partial |

## 脚本 exit code 语义

| 脚本 | 0 | 1 | 2 |
|---|---|---|---|
| init_result.py | 成功（含空输入也落合法骨架） | 写盘失败 | 参数错误 |
| build_evidence_map.py | 成功 | — | 参数错误 |
| validate_deck.py | 校验通过 | 有告警/失败项（仍写 render_record） | 参数错误或 pptx 不存在/非法 |
| selftest.py | 全部通过 | 存在失败项 | — |

## 幂等与增量规则

- `init_result.py` 合并而非覆盖：已存在且非空的字段保留，只补齐缺失键。
- 其余脚本只追加 `change_log`、merge `render_record`/`evidence_map`，不回删未知键。
- 同一输入重复运行，骨架与校验结论一致；时间戳与日志为唯一可预期差异。

