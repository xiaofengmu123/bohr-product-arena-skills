# 输出契约与证据映射（references/contract.md）

本文是 slides-01 的字段语义与证据规则速查。运行时以 `/input/case.json` 的
`output_contract` 为准；本文只解释通用语义，不替代任何案例的字段名。

## 1. result.json 必须包含的键

- `case_id`：从 `/input/case.json` 原样复制；读不到输入时为 `"unknown-case"`。
- `status`：`ok | partial | insufficient_evidence | input_error`，须诚实。
- 契约字段：`output_contract` 声明的全部字段，缺值给 `null`/`[]`/`{}`，不改名、不删未知键。
- 信封字段（无论契约是否声明都要有）：
  - `sources`：材料清单与定位（数组）。
  - `uncertainty`：读不出、歧义、冲突但不足以阻断交付的点（数组）。
  - `pending_human`：必须人工确认才能定的点（数组）。
  - `change_log`：每次重写 result.json 追加一条（数组）。
  - `errors` / `degradations`：运行错误与降级（数组）。
  - `runtime`：时间戳、耗时、所用脚本等（对象）。

## 2. 关键字段语义

| 字段 | 语义 | 完成标准 |
|---|---|---|
| `editable_source` | 可编辑演示源文件 | 真实 .pptx，`path`/`format`/`slides` 填实际值 |
| `export_artifact` | 导出渲染件 | 真实 .pdf（或 SVG+PNG 降级），`pages` 填实际值 |
| `content_map` | 页级证据映射（数组） | 每页有 claim/role/evidence，quote 逐字命中 |
| `compliance_checks` | 自检结果（数组） | 逐条可勾选，对应评分维度 |
| `exceptions` | 异常与处置（数组） | 图表/公式/加密/字体/页数超限等逐条说明 |

## 3. 页级证据映射（content_map 每项）

```json
{
  "page": 1,
  "title": "问题：…",
  "claim": "一句话主张",
  "role": "background|method|result|discussion|limitation|conclusion|title|other",
  "evidence": {
    "locator": "materials/paper.pdf#p3 或 paper.md#§2.1",
    "quote": "材料中逐字片段（不可改写）"
  },
  "notes_gap": "读不出时写缺口，转 pending_human/uncertainty"
}
```

硬规则：
1. `quote` 必须逐字命中材料；改写会触发"未改写事实"扣分。
2. 图表编号/单位/方法名/结论与论文一致，示意图不当实验数据。
3. 同一指标两个来源冲突 → 两个都记录并标 `source_conflict`，不隐藏。
4. 读不出 → 写 `uncertainty`/`pending_human`，**绝不编造**。

## 4. change_log 条目

```json
{"time": "2026-11-23T10:00:00+08:00", "scope": "content_map 第 3 页补证据", "why": "补 P1 漏读的表 2 定位"}
```

要求：每次原子重写 result.json 时追加一条；最终 change_log 非空且能还原每次改动。

## 5. draft.json（工作区页级计划）

P1 阶段产出，存 `/app/work/draft.json`：七个槽位（title/background/method/
result/discussion/limitation/conclusion），每槽 `{claim, role, evidence, speaker_note}`。
这是"先出页级计划，再成稿"的中间态，不是答案。

## 6. 降级与 status 判定

- 全部完成且产物可核验 → `ok`。
- 主产物存在但有缺口/降级 → `partial`（列 limits）。
- 材料不足以形成主线 → `insufficient_evidence`（列缺口）。
- 输入缺失/坏 JSON 无法解析 → `input_error`（`case_id="unknown-case"`）。
