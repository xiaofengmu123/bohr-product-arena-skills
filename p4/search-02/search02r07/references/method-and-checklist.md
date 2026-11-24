# 方法与检查清单

本文件把"科研问题 → 拆解 → 规划 → 扩展 → 多轮检索 → 全量筛选 → 停止判断"的方法细则固化为可复核流程。脚本只做确定性持久化与组装，**推理与判断由 Agent 用模型请求完成**。

## 1. 六要素拆解（对应"实际效果/完成度"）

把一个复杂科研问题拆为 ≥1 条可检索子问题 SQ1..SQn，每条标注：

- **对象**（object）：研究什么；
- **机制**（mechanism）：作用机制/通路；
- **方法**（method）：实验或临床方法；
- **时间范围**（time_range）：年份窗口；
- **比较**（comparison）：与何对照；
- **证据要求**（evidence_requirement）：需要什么级别证据（随机对照、队列、原始测量等）。

子问题须互相可独立检索；把"前提/背景"子问题标为前置依赖。

## 2. 查询规划（对应"实际效果/完成度"+"使用增益/效率"）

为每个子问题安排查询顺序与依赖：

```json
{"sq_id": "SQ1", "queries": ["...", "..."], "dependency": "none | SQ0", "parallel": true}
```

前置依赖先查；互不依赖的子问题并行查，减少无效轮次。每轮查询记录进 `evidence`。

## 3. 术语扩展（对应"使用增益/质量"）

每个核心概念扩展同义词/缩写/跨语言/上下位/领域变体，**每概念 ≤6 变体**以控制发散：

```json
{"concept": "镰状细胞病", "variants": ["SCD", "sickle cell disease", "drepanocytosis"]}
```

严重歧义术语在 `term_expansion` 中注明歧义并加限定词，或拆成两条查询分别记录。

## 4. 多轮检索与全量筛选（对应"实际效果/完整性"+"可核验性"）

- 检索到**全部**候选记录逐条给 screening 决定：`included`/`excluded`/`uncertain`/`merged_duplicate`。
- 排除项不静默丢弃：每条带 `screening_reason`，写入 `screening.md` 与 `result.json.screening`。
- 每条 included 结果必须有 `evidence.locator + quote`（逐字原文），非空泛评语。
- 重复导出/冲突意见等输入夹具显式标 `merged_duplicate`/`source_conflict`，不当预期答案。

## 5. 停止判断（对应"实际效果/完整性"）

`stop_decision.status` 三态：

- `continue`：某子问题证据不足，边际收益仍高，继续检索；
- `stop`：每个子问题均有 ≥1 条 included 证据且新增查询边际收益低；
- `handoff`：无法完成全部子问题，`pending_human` 列出具体缺口。

收敛判据写进 `stop_decision.reason`（如"三个数据库均无新增，SQs 全覆盖，边际收益≈0"）。

## 6. 打分（透明、可复现）

`score = 0.6*relevance + 0.2*metadata_completeness + 0.2*source_reliability`，由脚本确定性计算；`relevance` 由 Agent 判断，其余由脚本按 `core.py` 映射得出。

## 检查清单（提交前逐条勾选，对应评分维度）

### 实际效果（40 分）
- [ ] 研究问题拆成 ≥1 条可检索子问题，每条含对象/机制/方法/时间/比较/证据要求标注
- [ ] 每条结果标题/作者/年份/期刊/DOI 真实且逐字保留来源写法；相关性依据为 locator+quote
- [ ] 每个子问题均有 ≥1 条 included 证据，未覆盖的列入 `uncertainty.coverage_gaps`
- [ ] `evidence` 记录每轮查询/筛选/排序/停止理由；`screening` 覆盖全部记录，排除项有理由

### 使用增益（25 分）
- [ ] 依赖化查询规划 + 受控术语扩展提高召回；全量筛选减少人工返工
- [ ] `python3 -B scripts/main.py selfcheck` 全 PASS

### 创新增益（15 分）
- [ ] 六要素拆解、依赖化规划、受控扩展、停止阈值、透明打分由脚本固化，可复现

### 可靠体验（10 分，本版重点：鲁棒性）
- [ ] `--help` 退出码 0
- [ ] 空输入、坏 JSON、坏编码、缺参数、超大文件（>2MB）均优雅降级、退出码 0
- [ ] 重复运行结果一致（幂等）；异常写入 `errors`/`degradations`
- [ ] 输出目录不可写时回退到 work/ 并记录

### 生态贡献（10 分）
- [ ] SKILL.md + scripts + references 齐全；他人可按本文档独立复现一次完整检索
- [ ] 无硬编码个人路径；脚本只依赖标准库；路径用相对路径

## 复现说明

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
# ... 分析案例后用 set-plan / import / evidence 增量更新 ...
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
```

在另一个环境（不同机器/Python 3.8+）重跑 `python3 scripts/selftest.py`，应得到 20/20 通过。
