# 输出契约与数据规格（output contract & spec schema）

本文件是 `result.json` 与 `spec.json` 的唯一字段定义来源。校验脚本
`scripts/main.py check-result` 与 `scripts/main.py validate-spec` 按此契约执行，
字段名不得改名、不得删除，未知字段保留（不回填、不丢弃）。

## 1. spec.json —— 机制规格（模型编辑的单一事实源）

渲染器是 `spec.json` 的纯函数。模型只负责写科学内容，图例、布局坐标、
SVG、内容哈希全部由脚本确定性派生。

```json
{
  "schema_version": "1.0",
  "title": "机制图标题",
  "entities": [
    {
      "id": "e1",
      "label": "EGFR",
      "name_type": "protein",
      "species": "Homo sapiens",
      "aliases": ["HER1", "ERBB1"],
      "compartment": "membrane",
      "transmembrane": true,
      "data": {"expression": 2.3, "significance": "p<0.05"},
      "sources": [
        {"locator": "materials/paper.pdf#p3", "quote": "EGFR is a transmembrane receptor", "confidence": "high"}
      ]
    }
  ],
  "relations": [
    {
      "id": "r1",
      "from": "e1",
      "to": "e2",
      "type": "activation",
      "direction": "forward",
      "modifier": null,
      "evidence": {"strength": "reported", "label": null},
      "sources": [
        {"locator": "materials/paper.pdf#p4", "quote": "EGF activates the receptor", "confidence": "high"}
      ]
    }
  ],
  "layout": {"orientation": "TB", "order": ["e1", "e2", "e3"]},
  "data_mappings": [
    {"channel": "fill", "metric": "expression", "scale": {"min": 0, "max": 10}, "note": null}
  ],
  "uncertainty": ["歧义/无来源项的文字说明"],
  "notes": []
}
```

### 实体字段（entities[]）

| 字段 | 必填 | 取值 | 说明 |
|---|---|---|---|
| `id` | 是 | 非空字符串，唯一 | 稳定 ID，edges 用它引用 |
| `label` | 建议 | 字符串 | 图上显示名；缺省回退为 id |
| `name_type` | 建议 | `gene / protein / complex / family / cell_type / ligand / compartment / other` | 决定形状与字体（gene 斜体）；未知回退 `other` 并告警 |
| `species` | 否 | 字符串 | 物种前缀/来源物种，无来源写 null |
| `aliases` | 否 | 字符串数组 | 同义名/旧名归并 |
| `compartment` | 建议 | `extracellular / membrane / cytosol / nucleus / organelle / unspecified` | 决定区室带；未知回退 `unspecified` 并告警 |
| `transmembrane` | 否 | bool | 跨膜受体为 true；若 compartment 非 membrane 产生告警 |
| `data` | 否 | 对象 | 供 data_mappings 编码的数值/显著性/置信度 |
| `sources` | 否 | 数组 | 逐字证据，见下文 |

### 关系字段（relations[]）

| 字段 | 必填 | 取值 | 说明 |
|---|---|---|---|
| `id` | 建议 | 字符串 | 缺省自动 `rN` |
| `from` / `to` | 是 | 已存在实体 id | 引用不存在实体 = 阻断错误 |
| `type` | 是 | `activation / inhibition / binding / modification / transport / conversion / expression / recruitment` | 决定箭头/端点/线型/颜色 |
| `direction` | 是 | `forward / reverse / bidirectional / self` | 信号流向；`reverse` 箭头落在 `from`（反馈），`self` 画自环 |
| `modifier` | 否 | 字符串 | modification 的修饰标签（如 "P" 磷酸化、"Ub" 泛素化） |
| `evidence` | 否 | `{strength, label}` | 证据强度，不编造 |
| `sources` | 否 | 数组 | 逐字证据 |

### 证据单元（sources[]，实体与关系通用）

每一条必须可回溯：`locator`（材料位置）与 `quote`（逐字引用）至少填一个，
二者都有最好。无来源的数值/结论不得标注为确定，写入 `uncertainty`。

```json
{"locator": "materials/paper.md#p3", "quote": "逐字原文", "confidence": "high"}
```

## 2. result.json —— 每个案例的最终产物

| 字段 | 必填 | 说明 |
|---|---|---|
| `case_id` | 是 | 取自 `/input/case.json`（键 `case_id/id/caseId/challenge_id/armiId`），缺失时回退 `case-<task.md 哈希前12位>` |
| `status` | 是 | `success / partial / failed / insufficient_evidence / input_error` |
| `title` | 否 | 图题 |
| `editable_source_files` | 是 | 非空数组；每项 `{path, format, editable, layers[], sha256}`，path 相对 `/app/output` 且必须真实存在 |
| `export_artifacts` | 是 | 数组；每项 `{path, format, width_px, height_px, dpi, font_check, color_check, renderer}`，只记录真实生成的格式 |
| `entities` | 是 | 归一化后的实体数组（与 spec 一致，含 sources） |
| `relations` | 是 | 归一化后的关系数组 |
| `legend` | 是 | 由脚本派生的图例项（与图面同源，天然自洽） |
| `data_mappings` | 是 | 归一化后的编码映射 |
| `sources` | 是 | 聚合溯源表：`{element, kind, locator, quote, confidence}` |
| `uncertainty` | 是 | 歧义实体、方向不明、无来源数值 |
| `pending_human` | 否 | 需人工确认的项 |
| `change_log` | 是 | 增量记录 `{sec, phase, action}`；首条必须 `sec<=180` 且为骨架落盘 |
| `limitations` | partial/failed/insufficient_evidence 必填 | 缺口说明 |
| `degradations` | 否 | 降级记录（缺工具/缺字体等） |
| `errors` | 否 | 阻断错误清单 |
| `determinism` | 否 | `{schema_version, spec_sha256, svg_sha256}`，用于复核同输入同输出 |
| `runtime` | 否 | `{model_requests_used, wall_seconds}` |

## 3. 脚本退出码

| 退出码 | 含义 |
|---|---|
| 0 | 通过 |
| 2 | 仅警告（不阻断交付，但必须在回复中说明） |
| 1 | 阻断错误 / 参数错误 / 坏 JSON |

`check-result` 对 `export_artifacts` 为空只给警告（允许 SVG-only 交付），
但 `status != success` 而 `limitations` 为空给警告。`run` 是中间产物生产者，
其退出码只反映阻断错误；最终门禁用 `check-result`。

## 4. 确定性契约

- 同一 `spec.json` 字节 → 同一 `mechanism.svg` 字节 → 同一 `spec_sha256/svg_sha256`。
- 无墙钟时间戳、无随机数、不依赖字典迭代顺序（全部按 key 排序）。
- `change_log` 用相对秒 `sec` 而非墙钟时间，保证重跑一致。
- 交付前必须 `python scripts/selftest.py` 且 9 项全过。
