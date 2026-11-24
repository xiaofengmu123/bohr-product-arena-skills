# 六类要素覆盖表、参数表与证据映射模板

本参考文件是执行阶段的写作模板，不是预写答案。所有 `[]` 占位符必须由
`/input` 材料中的真实内容回填；材料没有的项写 `not_reported` 或
`MATERIAL GAP`，禁止补数。

## 1. 六类要素覆盖表（结果完整性评分点）

正文中每覆盖一个要素，就在 `result.json` 的 `sources` 中登记一条对应
`evidence_map.json` 的 `[S#]`，并同步把 `[S#]` 挂在 manuscript.md 的事实句后。

| 要素 | 必写字段 | 缺失时的写法 |
|---|---|---|
| 方法步骤 | 每步：输入→操作→输出→判断条件 | `MATERIAL GAP`：步骤不完整 |
| 实验设置 | train/val/test 分开；硬件、软件版本、环境、随机种子、重复次数 | `not_reported`：未提供的项 |
| 参数与来源 | 名称、取值、单位、作用对象、来源（config/log/author） | 来源列写 `pending_human` |
| 数据集与划分 | 名称、版本、规模、预处理、划分比例、随机方式、子集用途 | 版本不明写 `not_reported` |
| 对照实验 | 基线名称与版本、同预算与否、评价指标、统计方式 | 无可比性时写 `source_conflict` |
| 可复现与限制 | 代码版本、环境、命令、数据获取方式；失效场景与适用范围 | 具体失效场景，禁止套话 |

## 2. 参数表模板（参数与来源要素）

正文数值必须与参数表逐条一致，来源列必须可回溯到材料 locator。

```markdown
| 参数 | 取值 | 单位 | 作用对象 | 来源 |
|---|---|---|---|---|
| [名称] | [逐字数值] | [单位] | [作用于哪一步/哪个模块] | [materials/xx#Lyy] |
```

规则：
- 数值逐字复用，不改单位、有效数字；`mean±sd`、`n`、`p` 值原样保留。
- 同一参数两个来源不一致时，两行都写并在来源列标 `source_conflict`。

## 3. 证据映射模板（evidence_map.json）

```json
{
  "evidence": {
    "S1": {
      "claim": "正文中的事实性论断（原句）",
      "locator": "materials/config.yaml#L12",
      "quote": "材料中的逐字原文片段",
      "kind": "parameter"
    }
  }
}
```

- `locator` 形如 `materials/<file>#L<line>` 或 `materials/<pdf>#p<page>`。
- `quote` 必须是从材料原文中逐字复制的片段，不能改写。
- `kind` 取 `parameter | step | dataset | baseline | environment | limitation`。

## 4. preserved_facts.json 模板（数值/单位/统计量逐条保全）

```json
{
  "facts": [
    {
      "value": "0.001",
      "unit": "learning rate",
      "statistic": null,
      "locator": "materials/config.yaml#L12",
      "quote": "lr: 0.001"
    }
  ]
}
```

## 5. change_log 条目模板（追加式，不改写历史）

```json
{
  "seq": 3,
  "ts": "2026-01-01T00:00:00Z",
  "phase": "improve",
  "action": "aligned parameter table with config",
  "detail": "fixed lr from 0.01 to 0.001; added locator",
  "evidence_count": 12
}
```

`seq` 必须严格递增；每次改进追加一条，禁止回改历史条目。
