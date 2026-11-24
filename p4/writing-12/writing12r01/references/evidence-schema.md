# evidence_map 规范与逐字校验规则

本 Skill 的核心设计取舍是「证据溯源强化」：稿件中的每个关键结论都必须能
逐条回溯到材料原文，且 `quote` 必须是材料中的逐字片段，而非转述。本文件
定义 evidence_map 的结构、locator 规则与校验语义。

## 1. 结构

```json
{
  "schema_version": "1.0",
  "claims": [
    {
      "id": "claim-001",
      "type": "number | term | citation | figure | claim | edit",
      "statement": "稿件中的断言（与 quote 对应）",
      "locator": "materials/paper.md#L12",
      "quote": "逐字原文片段",
      "status": "verified | pending_human | conflict | gap",
      "confidence": "high | medium | low",
      "note": "可选的补充说明"
    }
  ],
  "conflicts": [
    {"claims": ["claim-003", "claim-004"], "description": "...", "status": "source_conflict"}
  ]
}
```

## 2. locator 规则

- 形如 `materials/paper.md#L12`：相对 `/input` 的路径 + `#L<1 起始行号>`。
- 跨行 quote 用起止行 `#L12-L14`（verify 只做子串匹配，行号用于定位）。
- PDF 先用平台 `pdf-text-tool`（pdftotext）转文本，再引用转换后的文本文件
  与页码（例如 `materials/paper.pdf.txt#L12`），不得引用未解析的 PDF 二进制。

## 3. 逐字校验语义（evidence.py verify）

- 校验前把 `\r\n`/`\r` 归一为 `\n`，并对 quote 两端做 strip，避免抽取时的
  尾随换行造成误报。
- `verify` 判定 quote 是否为文件文本的**原文子串**；不是子串即 `no_verbatim_match`。
- `verify-map` 对整张 map 逐条校验：无法定位到源文件或非逐字匹配的 claim
  一律降级为 `gap`，并在 `note` 追加原因；返回非零退出码以便调用方修复。

## 4. status 语义

| status | 含义 | 触发 |
|---|---|---|
| verified | quote 已通过逐字校验 | verify-map 通过 |
| pending_human | 需人工确认（歧义/范围/取值口径） | 材料可读但需决定 |
| conflict | 多来源不一致 | 同指标两来源数值/表述不同 |
| gap | 材料缺失或无法逐字定位 | 无出处、quote 无法匹配 |

冲突不隐藏：两个不一致来源各自保留一条 claim，并在 `conflicts` 里成对登记，
`description` 说明冲突点。绝不删除其中一个来源来「消解」冲突。

## 5. 与 result.json 的关系

- `result.json.sources` 是来源级清单（locator + quote），`evidence_map.claims`
  是结论级清单；一条 source 可被多条 claim 引用，一条 claim 必须对应唯一
  source 片段。
- `result.json.uncertainty` 汇总缺口/冲突/待确认项；`pending_human` 列出须
  人工决定项；`change_log` 记录每步改动时间戳与说明。
- 数字、单位、术语、引用逐字复用材料；材料没有的数值补 `MATERIAL GAP`，
  status 标 `gap`，绝不补造。

## 6. 示例（示例，非正式案例）

材料 `materials/paper.md`：

```text
Line 3: The measured temperature was 300 K.
```

对应 claim：

```json
{
  "id": "claim-001",
  "type": "number",
  "statement": "测得温度为 300 K",
  "locator": "materials/paper.md#L3",
  "quote": "The measured temperature was 300 K.",
  "status": "verified",
  "confidence": "high"
}
```

若稿件写成 299 K，则 quote 应为 `299 K`（无法逐字匹配 → 降级 gap），而不得
声称出自材料。
