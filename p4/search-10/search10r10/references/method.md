# 方法规则（references/method.md）

## 1. 证据记录 schema（唯一事实来源）

证据以追加式 JSONL 写入 `/app/work/evidence.ndjson`，每行一条记录；`record` 按 `id` 覆盖已存在记录，因此重复执行同一步幂等。

```json
{
  "id": "e001",
  "kind": "term|query|result|research_question|dedup|conflict|missing|degradation|error|uncertainty",
  "body": { "...": "step-specific payload" },
  "locator": "materials/paper.pdf#p3 或 URL/DOI",
  "quote": "逐字引文",
  "source": "检索来源 URL/DOI（可选，与 body.source 二选一）",
  "confidence": "high|medium|low|undetermined",
  "severity": "blocking|informational",
  "pending_human": false,
  "pending_note": "待人工确认的事项",
  "uncertainty_note": "不确定性说明"
}
```

- `body` 为各 kind 的主载荷；`locator`+`quote` 是证据溯源的最小要求，缺失的命中必须 `pending_human: true`。
- `result` 记录的 `body` 必含：title、authors、year、journal/venue、doi/url、language、version、source、relevance（相关性依据）。language/version 取值见第 4 节。
- `render()` 把记录按 kind 归入 `terminology_table`/`queries`/`results`/`dedup`/`missing`/`conflicts`，把带 locator/quote 的记录并入 `evidence_map`，并汇总 `sources`、`uncertainty`、`pending_human`、`change_log`。result.json 从不被手工改动。

## 2. 术语对齐（terminology）

每条 term 必含：源词（src）、源语种、目标语种对应词、type（synonym/abbreviation/hyponym/hypernym/transliteration/local_name）、basis（术语取自哪个材料或词典）、verification（回译、平行语料或人工核对方式）。未验证的译法标 `unverified_translation`。术语表用于生成逐语种查询并控制发散，每条可独立复核。

## 3. 逐语种查询（queries）

- 源语言与目标语种各自构造查询，不得只用母语；每条 query 记录翻译服务与版本（如 `translator:xxx vN`）、原文-译文对照、目标语种、检索源（web_search/paper_search）。
- 查询词必须来自术语表，禁止把整句机翻结果当作已核对；译文与原文若非同义，视为错配风险并记录。

## 4. 语言与版本判定

- `language`：正文语言（title/abstract/journal 判定）；不确定标 `undetermined`。
- `version` 取值：`original`（原文）、`translation`（出版方正式译文）、`bilingual_journal`（双语刊）、`abstract_only`（仅英文摘要）、`undetermined`。
- 判定依据写清楚：正文语言、摘要语言、双语刊声明、出版方翻译声明。原文不可得或仅摘要时不得冒充全文。

## 5. 去重（dedup）

同一论文的多个语种/版本按规范身份归并：优先用 DOI；无 DOI 用规范化 title + 第一作者 + 年份（大小写、变音符、复合词拆分后比较）。`dedup` 记录含 group_id、成员 id 列表、primary（首选记录）、basis（判定依据）。不同语种是同一著作的不同视图，只计一次；不同 DOI 的独立论文不得误并。

## 6. 冲突与遗漏

同一指标来源不一致时，两个都记录并标 `source_conflict`，不隐藏。无结果、权限受限、接口异常、缺失元数据分别写入 `missing`/`degradations`/`errors`，逐条说明，不补造。

## 7. 确定性与幂等

除 `runtime`（墙钟时间）外，所有内容字段由 (state, evidence) 纯函数派生，无随机性、无时间依赖的排序差异。同一输入重复运行，内容一致。
