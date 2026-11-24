# 方法与检查清单（method-and-checklist）

## 1. 六要素拆解（对应“实际效果/完成度”）

把复杂科研问题拆成 ≥1 条可检索子问题，逐条标注六要素：

- **对象（object）**：研究的实体/样本/人群/基因/化合物；
- **机制（mechanism）**：作用通路、因果关系、物理化学机制；
- **方法（method）**：实验方法、模型、测量、统计、综述 vs 原始研究；
- **时间范围（time_window）**：文献发表窗口、数据采集期、机制发生期；
- **比较关系（comparison）**：对照组、剂量、A vs B、跨物种/跨条件；
- **证据要求（evidence_requirement）**：数值、P 值、样本量、逐字引文、可复现性。

拆解规则：六要素都缺失或无法判断时写 `"not_reported"`，不要猜测；一个子问题只对应一组可执行的检索意图。

## 2. 查询规划（对应“效率增益”）

- 先排**前置**查询（定义术语、确认对象身份），再排**依赖**查询（用前置结果收敛范围），无依赖的标**并行**。
- 每个查询写明 `query`（实际检索式）、`rationale`（为何这么查）、对应的 `sub_question_ids`。
- 判断标准：无依赖的查询可以并行；有依赖的必须按顺序，避免无效检索。

## 3. 受控术语扩展（对应“召回 + 控制发散”）

对每个核心概念扩展：同义词、缩写、跨语言（中/英/其他）、上下位概念、领域变体；**每概念 ≤6 变体**。歧义术语写入 `term_expansion.ambiguities`，用限定词或拆成两条查询化解。

## 4. 多轮检索与停止判断（对应“收敛”）

- 每轮检索记录到 `evidence`（kind=`query`，含 query/source/database/timestamp/rationale）。
- 根据上一轮结果调整查询：补缺口、验证冲突、换术语变体；每次调整写 rationale。
- 停止判据（任一满足即可在 `set-stop` 声明）：
  1. **证据充分**：每个子问题均有 ≥1 条 included 证据且冲突已记录 → `status=converged`；
  2. **边际收益过低**：连续两轮无新增 included 记录 → 说明边际收益后 `converged`；
  3. **需转人工**：权限不足/关键证据缺失/严重歧义 → `status=handoff` + `pending_human`。

## 5. 证据溯源强化（本版创新，对应“结果可核验性”）

每条**结论（claim）**都要有 `claim_id`，并绑定一条或多条证据条目，每条证据必须：

1. `locator`：定位到材料段落/页码（如 `materials/paper.md#p3` 或 `paper_search`/`web` 的来源定位）；
2. `quote`：从来源**逐字复制**的原文（不转述、不改写、不截断改义）；
3. `quote_hash`：脚本对规范化 quote 计算的 sha1；
4. `local_verified`：脚本对本地文本类材料做规范化（空白折叠）子串匹配的核验结果。

脚本维护 `evidence_map`：

- `by_claim`/`by_result`/`by_sub_question` 三个索引，保证每条结论可沿证据链回溯；
- `orphan_claims`：被引用但无证据条目的结论 ID（正则提取 `CLM-*` 交叉校验）；
- `unverified_quotes`：指向本地材料但规范化后未命中的引文；
- `verification`：核验统计（total_claims / verified_local / unverified_local / external_unverifiable / orphan）。

Agent 职责：从工具返回原文**逐字复制** quote 与 locator；本地材料的 quote 若脚本判 `local_verified=false`，需复核原文是否被空格/连字符/转义破坏，修正后重新 `import`。

## 6. 透明打分（对应“结果可核验性/创新证据”）

脚本固定公式：

```text
score = 0.45 * relevance
      + 0.30 * evidence_strength
      + 0.15 * metadata_completeness
      + 0.10 * source_reliability
```

- `relevance`：Agent 依据逐字证据给出（0–1），必须对应 `relevance_reason` 中的 `CLM-*`；
- `evidence_strength`：证据条目强度均值（quote+locator+本地命中=1.0；quote+locator 外部=0.6；只有其一=0.3；均无=0.0）；
- `metadata_completeness`：title/authors/year/journal/doi 五字段命中率；
- `source_reliability`：本地材料 1.0 > paper_search/Crossref/OpenAlex 0.9 > arXiv/PubMed 0.8 > 出版商域名 0.7 > 一般 web 0.5。

排序仅在 `screening=included` 的记录内按 score 降序赋 rank；excluded/uncertain 的 rank 置 null，但**完整保留在 screening 账本**。

## 7. 交付前检查清单（逐条可勾选）

- [ ] `case_id` 与 `status` 存在且 status 合法；
- [ ] 每个子问题含六要素 facets；
- [ ] 每条 included 结果有 `relevance_reason` 且引用 `CLM-*`；
- [ ] 每条结果证据条目均有 locator + 逐字 quote；
- [ ] `evidence_map.verification` 已生成，orphan/unverified_local 为 0 或已列入 `pending_human`；
- [ ] `screening` 覆盖全部候选（含 excluded 及其理由）；
- [ ] 未覆盖子问题出现在 `uncertainty.coverage_gaps`；
- [ ] `change_log` 记录每步增量（time/action/reason/status）；
- [ ] `output_contract` 全部字段已原样回填；
- [ ] `python3 -B scripts/main.py selfcheck` 全 PASS；
- [ ] `python3 -B scripts/selftest.py` 退出码 0。

## 8. 失败处理与边界

- 空输入/坏 JSON/坏编码：脚本降级为合法 result.json 且退出码 0；status 分别为 `insufficient_evidence`/`input_error`。
- 缺软件/接口异常：记录 `errors`/`degradations`，改用本地材料；无法继续如实 `partial`。
- 不编造：无材料不示例填充；元数据缺失写 `null` + `uncertainty.missing_metadata`。
- 本 Skill 只解决“科研问题驱动检索”一类任务，不承担精读/翻译/写作；范围边界见 SKILL.md 第 1 节。
