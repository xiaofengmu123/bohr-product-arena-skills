# 查询构造、术语扩展、去重与打分规则

## 1. 多轮查询规划

复杂问题拆成至少 3 轮（见 `plan.json` 脚手架）：

1. **单概念召回**：核心术语单独检索，锁定高频同义表达与关键综述。
2. **概念组合**：2–3 个概念 AND 组合，提高精度。
3. **引文回溯**：从关键综述/种子论文回溯其参考文献与被引，补漏。

每轮落盘 `keywords/expansions/status` 作为证据；`evidence --inline` 记录真实查询串、来源与数据库。

## 2. 关键词/术语扩展清单（逐项核对）

- [ ] 专业术语与同义词
- [ ] 缩写 ↔ 全称（如 AAV ↔ adeno-associated virus）
- [ ] 跨语言表达（中/英，必要时其他语言）
- [ ] 上位/下位/近义概念
- [ ] 方法名异写（连字符、空格、大小写）
- [ ] 研究对象别名/俗名
- [ ] 排除词（明确列出，控制无关结果）

扩展后的每个词也要有来源依据：记录“这个词来自哪份材料哪一行”到 evidence。

## 3. 去重规则

- 键优先级：`doi:`（规范化 DOI，去 `doi.org/`、`doi:`、大小写） > `title:`（规范化标题：小写、去标点/URL、合并空白、保留 CJK）。
- 同键 upsert：新记录非空字段覆盖旧值，`evidence` 列表合并去重（按规范化 JSON 判等）。
- 去重决策写入 `evidence_map`（kind=`dedup`），写明“这两条为何判定为同一篇”。

## 4. 打分与排序

```
score = 0.6·relevance + 0.2·metadata_completeness + 0.2·source_reliability
```

- `relevance`（0–1）：Agent 依据研究问题打分，必须在 `relevance_reason` 给出理由与证据。
- `metadata_completeness`：`title/authors/year/journal/doi` 五字段中非空占比。
- `source_reliability`：crossref=1.0、Europe PMC/PubMed=0.95、OpenAlex/出版商=0.9、SemanticScholar/arXiv=0.85、其余/未知=0.7。
- 排序：score 降序；同分按规范化标题字典序，保证可复现。

## 5. 证据溯源铁律

- 每条候选的 `evidence`、每条 `evidence_map` 条目都必须含 `locator + quote`。
- `locator` 形式：`materials/<file>#page3`、`case.json#research_question`、`crossref#10.xxxx/yyyy`、`plan.json#round2`。
- `quote` 必须是从材料/返回结果中**逐字**抄录的片段，不得转述；抄录处无法定位就写 `null` 并列入 `missing_metadata`。
- 冲突不隐藏：同一指标多来源不一致，全部保留并标 `source_conflict`；无法判断写 `pending_human`。
