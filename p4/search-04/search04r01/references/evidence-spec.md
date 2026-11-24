# 证据溯源规范（evidence-spec）

本规范是 SKILL.md §6 的展开，规定 `evidence_map` 的字段语义、写入规则与最小示例。
目标：任何第三方只凭 `result.json`，无需原始材料也能逐条复核每个结论。

## 1. 定位：一句话

每个**关键结论**（论文身份、引用边、关联边、排序依据、剪枝决定、路径跳）都必须有唯一 `claim_id`；每条 `claim` 至少对应一条 `evidence`，evidence 必须给出 `locator` 与逐字 `quote`。结论可以有多条证据，证据也可以冲突。

## 2. evidence_map 顶层结构

```json
{
  "evidence_map": {
    "version": "1.1",
    "items": [ { ...evidence... } ]
  }
}
```

`version` 固定为 `1.1`。`items` 顺序即登记顺序，逐条递增 `evidence_id`（`ev-0001`、`ev-0002`…）。

## 3. evidence 字段语义

| 字段 | 必需 | 取值/说明 |
|---|---|---|
| `evidence_id` | 是 | `ev-NNNN`，全局唯一 |
| `claim_id` | 是 | 对应结论的 `c-NNNN` |
| `claim` | 是 | 一句可判真假的中文结论 |
| `locator` | 是 | 可回指定位，如 `materials/seed.pdf#p3`、`crossref/seed.json#reference[2]` |
| `quote` | 是 | 逐字来自材料的原文片段，不得改写、不得概括 |
| `quote_sha256` | 是 | `sha256(quote 的 UTF-8 字节)`，用 `tool quote-hash` 计算 |
| `source_id` | 否 | 对应 `sources[]` 条目 id |
| `source_role` | 是 | `primary`（原论文/原始记录）/`secondary`（引文库返回）/`metadata`（题录） |
| `evidence_type` | 是 | `identity`/`edge`/`association`/`ranking`/`pruning`/`path_hop`/`conflict` |
| `provenance` | 是 | `parsed_pdf`/`crossref`/`europepmc`/`author_material`/`inferred` |
| `confidence` | 是 | `high`/`medium`/`low` |
| `status` | 是 | `verified`/`conflicted`/`unverified`/`not_reported` |
| `conflicts` | 否 | 与之冲突的 `evidence_id[]` |

## 4. 逐字 quote 的硬规则

1. `quote` 必须是从材料复制的连续片段；若材料是扫描/图片，用 OCR 结果并标 `provenance=ocr`、`confidence` 降级。
2. 禁止把多段拼接成一句话再当作“原文”；需要多段时拆成多条 evidence。
3. 截断允许，但用省略号 `…` 标注且不改变原意；`quote` 首尾不能是自己补充的词。
4. 字段值与 `quote` 不一致时：以 `quote` 为事实，字段标 `source_conflict` 并另立一条 conflict 证据。

## 5. status 与 conflict 处置

- `verified`：单一来源、可回指、与其它证据一致。
- `conflicted`：两来源对同一指标给不同值。**两个值都登记**，各自的 `status=conflicted`，`conflicts` 互指对方 `evidence_id`，并在 `uncertainty` 里写清差异。
- `unverified`：来源只给结论、没给可定位原文。
- `not_reported`：材料中没有该字段，明确缺失而不是猜测。

## 6. sources 条目

每条来源（原 PDF、Crossref 响应、EuropePMC 记录、作者材料）在 `sources[]` 登记：

```json
{
  "source_id": "src-0001",
  "locator": "materials/seed.pdf",
  "quote": "（来源本身的可识别片段，如标题页/DOI 行）",
  "type": "pdf|crossref|europepmc|author_material",
  "retrieved_at": "ISO 时间或 not_reported"
}
```

## 7. 最小示例（示意，非正式案例结果）

```json
{"evidence_id":"ev-0001","claim_id":"c-0001",
 "claim":"种子论文的 DOI 为 10.1109/CVPR.2018.123",
 "locator":"materials/seed.pdf#p1",
 "quote":"DOI: 10.1109/CVPR.2018.123",
 "quote_sha256":"…由脚本计算…","source_id":"src-0001",
 "source_role":"primary","evidence_type":"identity",
 "provenance":"parsed_pdf","confidence":"high","status":"verified","conflicts":[]}
```

## 8. 与脚本的接口

- `python3 scripts/main.py tool quote-hash --text "逐字原文"` 返回 `quote_sha256`。
- `finalize` 会把 `evidence_map.items` 逐条渲染进 `report.md` 的证据表，便于人工复核。
- 脚本不做真伪判断：证据真伪由 Agent 依据材料判定；脚本只保证结构合法、哈希可复算、产物可打开。
