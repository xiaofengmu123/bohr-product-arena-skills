# 方法规则（references/method.md）

本文件规定跨语言文献检索的方法规则，重点覆盖本题固定边界：葡萄牙语（pt）↔英语（en）双向的已知项（known-item）检索。规则可被第三方独立复核；与 `SKILL.md` 第 3、4 节一致。

## 1. 总流程
1. **读题定范围**：从 case.json/task.md 澄清研究问题，明确 `known_item`（是否要找某篇已知论文）、源语言、目标语种、检索范围（年份/期刊/学科）与可用的材料（PDF 原文/译文）。
2. **术语对齐**：为每个源词建立术语表（pt/en 双向）。
3. **逐语种查询**：为 pt、en 各构造至少一组查询，记录翻译服务与版本、原文-译文对照。
4. **取回与筛选**：用 web_search / paper_search 检索，逐条保留来源定位与逐字证据。
5. **语言/版本判定**：判定每条命中的 `body_language` 与 `version`。
6. **去重**：同一论文的多语版本合并计数。
7. **误译复核**：对高风险命中做回译或人工抽检，识别“词面对应、语义不同”的假阳性。
8. **收尾**：validate、change_log、诚实 status。

## 2. 术语对齐（pt↔en 重点）
术语表每条记录至少含：`term`（源词）、`language`、`variants`（同义词/缩写/上下位/音译/本地译名）、`type`、`basis`（依据：材料、词典、主题词表、已核译文）、`verification`（如何验证）。

葡萄牙语↔英语常见要点：
- **重音与词形变化**：葡语带重音（如 `ação`、`análise`、`relação`、`português`），检索时同时提供去重音/词干变体；英语用词干与屈折（-ing/-ed/-s）。
- **复合词与连词**：葡语复合词（`revisão por pares`、`ensino superior`）与英语（peer review、higher education）不对等，须保留整短语，勿逐词直译。
- **学科用词而非字面同义词**：如葡语 `célula`→英语 `cell`（生物学），`ensaio clínico`→`clinical trial`，`revisão sistemática`→`systematic review`；不得译成字面词（如 `trial` 译“审判”）。
- **缩写歧义**：同一缩写在不同语种可能指向不同概念（如 `AI`、`ECG`），缩写必须写全称与语种，跨语种含义不同时单独成条并标 `source_conflict`。
- **音译与本地译名**：人名/机构名保留原文拼写，必要时给出本地译名变体（如 `Organização Mundial da Saúde` ↔ `World Health Organization`）。
- **验证方式**：每条术语标注“依据材料第 X 段”“依据主题词表 MeSH/DeCS”“未验证”三级；DeCS（Descritores em Ciências da Saúde）是葡语医学检索的常用受控词表，可作依据并注明来源。

## 3. 查询构造与翻译记录
- 每种语言单独一组查询；不得把问题整句机翻后只搜一种语言。
- 查询须记录 `translation_service`（如未用外部服务则写 `manual`）与 `translation_version`、`original_text` 与 `translated_text` 的逐字对照。
- 未记录来源的译文一律标 `unverified_translation`，不得当作已核对。
- 查询词优先级：学科用词 > 同义词扩展 > 音译/本地译名；控制发散，避免无关概念混入。

## 4. 跨语言匹配
处理译文用词与论文原文用词不一致的问题：
- **词形变化**：对 pt 去重音、变格/变位；对 en 取词干、复数/过去式。
- **复合词**：保留完整短语做精确匹配，再做分词匹配。
- **语序**：pt/en 形容词位置等语序差异，用短语级而非词级匹配。
- **中英/葡英混排标题**：识别标题内夹杂的另一种语言词（如英文术语嵌入葡语标题），记录混排情况。

## 5. 语言识别与版本判定（决策树）
先判定 **body_language（正文语言）**：优先读全文/材料正文，其次摘要语言，再其次期刊声明；依据写入 `evidence_map`。仅凭文件名或标签判定视为未判定。

再判定 **version**，按下列优先级：
1. **duplicate**：与已有记录的 `title` 归一化 + DOI（或首作者+年份）完全一致，且元数据重复 → 标 `duplicate`，并入去重表，不重复计数。
2. **abstract_only**：该语种仅有摘要，正文为另一语种（典型：葡语原文 + 仅英语摘要）。判定依据：仅有 Abstract 段落、无正文、DOI 相同但语言字段仅为摘要语言。**不得当全译。**
3. **publisher_translation**：存在出版商发布的完整译文（如官方英译版），与原文同源但为独立译文版本。依据：出版方翻译声明、译文本 DOI/版本页、题目旁译文标注。
4. **original**：正文语言即该论文的原始语种，且无“翻译声明”证据。
5. **undetermined**：证据不足。

信号优先级：出版方声明 > 正文语言实测 > 摘要语言 > 标题/URL 猜测。证据不足一律 `undetermined`，写入 `uncertainty`。

## 6. 误译复核
对高风险命中（术语歧义、缩写同形、命中标题与问题词面相似但语义可疑）做回译或人工抽检：
- 记录 `claim`（待复核结论）、`back_translation`、`verdict`（`confirmed`/`false_positive`/`uncertain`）、`basis`。
- “词面对应、语义不同”的假阳性必须单独记录在 `conflicts`，不静默丢弃。
- 同一指标来源不一致时两个都记录并标 `source_conflict`。

## 7. 去重
- `work_key = normalize(title) + "|" + (DOI 小写 || 首作者 + 年份)`；`normalize` 去标点、去重音、小写、去冠词（pt：o/a/os/as/um/uma；en：a/an/the）。
- 同 `work_key` 的多语版本合并为一个“论文”条目，`versions` 数组列出各语种与版本，去重后只计一次。
- 去重判定写入 `dedup`：`{work_key, canonical_title, versions, decision, basis}`。
- `duplicate` 记录用 `duplicate_of` 指向主记录索引。

## 8. 证据溯源
- 每个关键结论/数值/命中必须有 `locator`（URL/DOI/材料文件与段落定位，如 `materials/xxx.pdf#p3`）与逐字 `quote`。
- 逐字引用用 UTF-8 原样摘录，不转写、不改写；无法定位时标 `pending_human` 或 `not_reported`。
- 不虚构引用、DOI、数值；缺失就写缺失，不补造。

## 9. 最小调用示例（示意，非正式案例）
以下输入与输出均为合成示意，仅用于说明接口与完整流程，不冒充正式案例结果。

**输入**（`/input/case.json`，示意）：
```json
{
  "case_id": "demo-pt-en-001",
  "task": "已知项检索：找回某论文的葡语原文与英语版本",
  "source_language": "pt",
  "target_language": "en",
  "output_contract": {
    "results": {"type": "array"},
    "evidence_map": {"type": "array"},
    "dedup": {"type": "array"},
    "missing": {"type": "array"}
  }
}
```

**调用序列**：
```bash
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
# 分析中逐条 patch（示意）
python3 -B scripts/main.py patch --output /app/output --work /app/work --patch step3.patch.json
python3 -B scripts/main.py validate --input /input --output /app/output
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
```

**示意输出**（`result.json` 节选，标为示意）：
```json
{
  "case_id": "demo-pt-en-001",
  "status": "partial",
  "results": [
    {"title": "Schematic work title", "body_language": "pt", "version": "original",
     "locator": "materials/schematic.pdf#p1", "quote": "原文逐字片段（示意）"}
  ],
  "dedup": [],
  "missing": ["示意：英文全译版本未在材料中提供，version=abstract_only"]
}
```

## 10. 复用模板（可被他人直接套用）
以下为结果记录的字段模板（JSON 对象结构），提交产物须符合；`*` 为必需字段。

**terminology_table 行**：
```json
{"term": "*", "language": "*", "variants": [], "type": "*",
 "basis": "*", "verification": "*"}
```

**queries 行**：
```json
{"language": "*", "query": "*", "translation_service": "*",
 "translation_version": "*", "original_text": "*", "translated_text": "*",
 "rationale": ""}
```

**results 行**：
```json
{"title": "*", "authors": [], "year": null, "venue": "",
 "doi": null, "url": null, "body_language": "*", "version": "*",
 "source": "*", "locator": "*", "quote": "*", "relevance": "",
 "work_key": "*", "duplicate_of": null}
```

**dedup 行**：
```json
{"work_key": "*", "canonical_title": "*", "versions": [],
 "decision": "*", "basis": "*"}
```

**evidence_map 行**：
```json
{"claim": "*", "locator": "*", "quote": "*", "result_index": null}
```

示例种子词表（示意，非正式答案，来源需按实际材料复核）：
| term(pt) | term(en) | type | 备注 |
|---|---|---|---|
| revisão sistemática | systematic review | 学科用词 | 勿译成 systematic revision |
| ensaio clínico | clinical trial | 学科用词 | 勿译成 clinical test |
| célula | cell | 学科用词 | 生物学语境 |
| doença | disease | 学科用词 | 医疗语境 |

未复核来源的术语一律标 `unverified_translation`。


