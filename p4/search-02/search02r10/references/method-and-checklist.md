# 方法与检查清单

本文件给出方法论细则、六要素拆解模板、停止判据与逐条检查清单，供 Agent 执行时遵循，也供他人在另一环境独立复现。

## 1. 六要素拆解模板

把一个科研问题拆成可检索子问题，每条子问题标注六要素（缺失要素写 `not_specified`，不编造）：

```json
{
  "id": "SQ1",
  "question": "对象 X 中机制 Y 的干预研究（2015–2024）",
  "object": "X", "mechanism": "Y", "method": "干预/随机对照",
  "time_range": "2015–2024", "comparison": "X vs 对照", "evidence_requirement": "原始研究，含效应量与样本量"
}
```

## 2. 查询规划与依赖

- 一条 `query_plan` 记录一次查询：`query`（真实检索式，非空泛）、`database`（snapshot/paper_search/web_search，快照优先）、`target_sub_questions`、`depends_on`（前置查询 id）、`parallel`（可与哪些查询并行）、`order`。
- 前置关系用于控制顺序：先拆解再检索、先核对快照再补充外部检索；可并行查询明确标注，减少无效轮次。
- 快照题型原则：**先穷尽本地 `records.json` 的筛选**，再决定是否要外部检索补元数据或核对署名；不把活库检索当作全局召回。

## 3. 术语扩展与发散控制

- 每个概念最多 6 个变体：同义词、缩写、跨语言、上下位、领域变体各取其一即可，避免组合爆炸。
- 在 `term_expansion` 记录歧义与限定词；严重歧义拆成两条查询分别记录，同分结果不合并。

## 4. 全量筛选与去重

- 对 `records.json` 的**每一条**记录都给出 decision：`include`（相关且证据充分）、`exclude`（有明确排除理由）、`uncertain`（暂无法判断）。
- `include` 必须带 `reason` + `evidence.locator` + `evidence.quote`；`exclude` 必须有理由，不能静默丢弃。
- 指纹去重逢重：重复导出标 `dedup.role=duplicate`，字段不一致写入 `dedup.conflicts`；题目声明的"重复导出/冲突意见"标 `input_fixture=true`，不作为真实冲突或答案。

## 5. 证据日志（evidence）与冲突

- 每轮查询、每次筛选决策、排序依据、停止判断都写一条 evidence：`kind ∈ {query, screen, rank, stop, conflict, gap}`。
- 冲突证据不隐藏：`kind=conflict`，两个来源都记录，`rationale` 写明差异与 `source_conflict`，不擅自取舍。
- 边界校验失败（坏 JSON/坏编码/超限/缺工具）写入 `errors`/`degradations`，继续处理其余材料。

## 6. 停止判据

在 `plan.stop_decision` 写明：

- `status=sufficient`：每个子问题 ≥1 条 included 证据，无覆盖缺口，边际收益低。
- `status=marginal`：继续检索新增相关记录低于阈值（如连续两轮无新增）。
- `status=handoff`：有子问题无法在快照内回答，转入 `pending_human` 并列出具体缺口。

三段阈值建议（可在 plan 中覆盖）：`min_evidence_per_sub_question=1`、`max_query_rounds=6`、`marginal_gain_window=2；"连续两轮无新增相关记录即停止"。

## 7. 供他人复现的最小步骤

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py scan --input /input --work /app/work
python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
# 写拆解/筛选/证据到 /app/work/plan_in.json、screen_in.json、evidence_in.json 后：
python3 -B scripts/main.py plan --work /app/work --file /app/work/plan_in.json
python3 -B scripts/main.py screen --work /app/work --file /app/work/screen_in.json
python3 -B scripts/main.py evidence --work /app/work --file /app/work/evidence_in.json
python3 -B scripts/main.py rank --work /app/work
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work --runtime-seconds 120 --runtime-requests 7
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
python3 -B scripts/selftest.py
```

以上命令从空输入到落盘中间表示再到最终结果，全程可由他人独立执行；示例输入/输出需自建，不使用任何预写答案。

## 8. 对应评分维度的逐条检查（交付前勾选）

- [ ] **实际效果/任务完成度**：子问题 ≥1 条，每条含对象/机制/方法/时间/比较/证据要求；查询计划有顺序/依赖/并行标注。
- [ ] **实际效果/结果正确性**：标题/作者/年份/期刊/DOI 真实且逐字保留；相关性依据为 locator+quote，非空泛评语。
- [ ] **实际效果/结果完整性**：每个子问题 ≥1 条 included；全部记录（含重复导出）进入 screening 账本；缺口列入 `coverage_gaps`。
- [ ] **实际效果/可核验性**：每轮查询、筛选、排序、停止理由都在 evidence；排除项有理由。
- [ ] **使用增益**：六要素拆解、依赖化规划、受控扩展、指纹去重、全量筛选减少人工返工；效率主张以本地快照轮数为依据，不虚报全局召回。
- [ ] **创新增益**：单一中间表示 + 三段边界校验 + 指纹去重 + 透明打分可复现；与"一轮关键词打天下"的差异在 documents 中写明。
- [ ] **可靠体验**：`--help` 退出码 0；空输入/坏 JSON/坏编码不崩溃；重复运行决策字段一致（change_log 时间戳取快照 mtime）；异常落在 errors/degradations。
- [ ] **生态贡献**：SKILL.md + scripts + references 齐全；他人可独立复现；限制（仅标准库、快照题型、不覆盖单篇精读/翻译/写作）写明。
- [ ] **诚实性**：不虚构文献/元数据/数值/评测分数；无材料写"材料不足"；`GITHUB_URL.txt`（若提交）指向真实仓库，无链接则不放占位符。

## 9. 边界与限制

- 只做标准库、只接受有限快照题型；不声称全局召回或活库覆盖。
- 不负责单篇精读、全文翻译、写作润色、开放式科研问答。
- PDF 抽取依赖 `pdftotext` 或平台 PDF 文本工具；缺失时该 PDF 标 `pdf_not_extracted` 并列入 `degradations`，不编造正文。
- 不做人工评审冒充：`stop_decision.status=handoff` 时明确转人工，而非伪造结论。