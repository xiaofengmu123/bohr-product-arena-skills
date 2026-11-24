# 证据溯源规范（证据溯源强化版）

本 Skill 的差异化取舍是**每条结论都可回源、每条引用都可逐字校验**。以下规则与
`scripts/core_impl.py` 的 `build_evidence_map` / `verify_quote` 实现一一对应。

## 1. locator 最小字段

`evidence_matrix` 每行 `locator` 至少含：

```json
{"unit_type": "page|section|table|para|figure|appendix", "unit_id": "p3 §2.2"}
```

- `unit_type` 声明定位粒度；`unit_id` 是该粒度下的稳定定位符（页码、节号、表号、段落号）。
- 来源写在证据行顶层 `source`（相对 `/input` 的路径，如 `materials/a.pdf`），不塞进 `locator`。
- 定位必须真实取自材料；不确定页码时写 `unit_id: "unlocated"`，并在 `reason` 说明，而不是编造页码。

## 2. 逐字 quote 纪律

- `quote` 必须**逐字复制**材料原文，不做改写、不补字、不断章改变含义；跨页长引文可截断并在末尾加 `…`，但截断处不能改变原义。
- 校验算法：对 `quote` 与来源文本做空白归一化（连续空白折叠为单个空格、两端去空白）后做子串匹配；匹配成功记 `verified=true`。
- `quote_hash` = 归一化 quote 的 sha256 前 16 位，用于同一 quote 的跨产物比对与去重，防止结论正文与证据表之间悄悄改写。
- 回源失败时：`evidence_map.json` 该条 `verified=false`，`quote_integrity.failed` 追加记录；对应结论强度降为 `inferred` 或 `conjectural`，并在 `limitations` 说明。

## 3. evidence_map.json 的细粒度结构

`finalize` 生成 `evidence_map.json`，比 `evidence_matrix` 多三个索引与一道回源校验：

```json
{
  "schema_version": "2.0-evidence",
  "quote_integrity": {"total": 0, "verified": 0, "failed": []},
  "conclusion_index": [
    {"conclusion_id": "C1", "text": "…", "strength": "…",
     "evidence": [{"evidence_id": "E1", "locator": {}, "quote": "…",
                   "quote_hash": "…", "verified": true}]}
  ],
  "evidence_by_subquestion": {"SQ1": ["E1", "E2"]},
  "sources_index": [{"source": "materials/a.pdf", "evidence_ids": ["E1"]}]
}
```

- `conclusion_index`：从结论反查证据，复核时按结论 id 一次定位全部出处。
- `evidence_by_subquestion`：验证"子问题是否被证据覆盖"，暴露无证据的空子问题。
- `sources_index`：从来源反查证据，暴露只引用不核对、或整篇来源未被引用的情形。
- `quote_integrity`：运行期逐字回源结果，是"可核验"维度的机器可查证据。

## 4. 冲突与空白的最小字段

- 冲突逐条写 `source_a / source_b / issue / resolution`，两个来源都要有 locator + quote，禁止写"尚存争议"了事。
- 空白逐条写"缺什么证据 + 该证据为何必要"，例如"缺少 ≥12 个月的随访数据，无法评估长期效应"，而不是"有待进一步研究"。

## 5. 强度词约定

| strength | 含义 | 触发条件 |
|---|---|---|
| `established` | 材料内逐字支持 | quote 回源通过、判断为 supports |
| `inferred` | 合理推断 | 多证据组合或回源部分通过 |
| `conjectural` | 猜测/假设 | 单点弱证据或回源失败 |

严禁把 `inferred/conjectural` 写成事实；`established` 只用于有逐字引用的结论。
