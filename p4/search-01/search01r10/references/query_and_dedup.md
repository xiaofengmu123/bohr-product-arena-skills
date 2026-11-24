# 查询构造、去重与排序规则

本文件固化检索过程中可复现的确定性规则；相关性判断本身由调用方 Agent 依据
材料完成，这里只规范“怎么组织查询、怎么去重、怎么排序”。

## 1. 查询构造（多轮 + 关键词扩展）

按召回从窄到宽推进，每一轮查询都以 `evidence` 落账：

1. **单概念**：研究问题中最核心的术语/缩写/跨语言表达各查一次；
2. **AND 组合**：主题 × 方法 × 研究对象 两两/三元组合；
3. **引文/引用图**：由已命中文献的 DOI 或 references 追相关文献；
4. **核验清单**：每轮结束逐项记录——
   - 是否覆盖了同义词（如 `machine learning` / `ML` / 机器学习）；
   - 是否覆盖缩写全称双向（`NMR` ↔ `nuclear magnetic resonance`）；
   - 是否覆盖跨语言表达；
   - 是否控制了无关结果（新增限制词或时间窗）。

生成查询后用 `scripts/main.py plan --plan '{...}'` 写入，或用 `import`
直接导入命中记录。

## 2. 去重键

- 优先 **DOI**：小写、去 `https://doi.org/` 与 `doi:` 前缀；
- 无 DOI 用 **规范化标题**：NFKC 统一全/半角 -> 小写 -> 仅保留字母数字与
  CJK -> 折叠空白。

同键记录不重复占位：后到记录与既有记录**合并**，补全缺失元数据；若
同一字段出现不同取值，写入 `source_conflict` 并保留既有值，绝不擅自取舍。

## 3. 相关性评分与排序

```
score = 0.60 * relevance + 0.20 * metadata_completeness + 0.20 * source_reliability
```

排序键固定为 `(-score, -year, title 小写, cand_id)`，保证重复运行结果一致。
`relevance` 由调用方对每条记录给出 0–1 值并附 `relevance_reason`。

## 4. 检索后必答清单

- 空结果：说明检索词与范围，写入 `status_notes` 与 `coverage_gaps`；
- 元数据缺失：字段标 null 并进入 `uncertainty.missing_metadata`；
- 来源冲突：两来源值都保留，标注 `source_conflict`；
- 权限/限流：写入 `degradations`，改用本地材料并标注局限；
- 判断不了：写入 `pending_human`，不猜测。