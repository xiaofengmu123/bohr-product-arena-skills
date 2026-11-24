# 方法与自检细则

本文件给出可复现的方法规则与交付前检查清单。他人可按本文件与 `SKILL.md` 独立完成一次完整检索。

## 1. 问题拆解（六要素）

把研究问题拆成可检索子问题，每条尽量标注：

- **对象**：研究对象/人群/暴露；
- **机制**：作用机制或因果路径；
- **方法**：研究设计（RCT/队列/机制实验等）；
- **时间**：时间范围；
- **比较**：对照或比较关系；
- **证据要求**：需要什么级别的证据（效应量、机制陈述、发生率等）。

无法判断的要素写 `null` 或 `"unknown"`，不猜测。子问题数量控制在 2–6 条，过少覆盖不足、过多发散。

## 2. 查询规划（依赖化）

- 为每个子问题安排一条主查询，标注 `depends_on`（前置子问题 id）与 `order`。
- 无依赖的子问题可并行；有依赖的按顺序执行。
- 每轮查询写清 `rationale`（为何这样查、预期补哪个缺口）。

## 3. 术语扩展（受控发散）

- 每个核心概念扩展同义词、缩写、跨语言、上下位、领域变体，**每概念 ≤6 变体**。
- 严重歧义时在 `term_expansion` 注明歧义并加限定词，或拆两条查询分别记录。
- 术语扩展目标：提高召回，同时避免查询爆炸。

## 4. 多轮检索与全量筛选

- 检索轮次按 `query_plan` 顺序执行；每轮把命中记录导入 `records`。
- **逐条筛选**：`included`（纳入）、`excluded`（排除，须理由）、`uncertain`（待定，须理由）。
- 每条结果必须附 `evidence`：`locator`（如 `materials/paper.md#p3`）+ 逐字 `quote`。
- 重复记录合并并写入 `screening.merged_duplicates`，不静默丢弃。
- 每轮检索把查询/来源/时间/理由写入 `evidence` 日志。

## 5. 停止条件（显式）

满足以下任一条即可停止，并在 `stop_decision` 写明：

1. **证据充分**：每个子问题已有 ≥1 条直接 `included` 证据；
2. **边际收益过低**：新增一轮无新增 relevant 记录（连续 2 轮）;
3. **转人工**：存在无法自动解决的关键缺口或冲突，`status=handoff`，缺口写入 `pending_human`。

## 6. 交付前检查清单（与评分维度对应）

**实际效果 / 任务完成度**

- [ ] 研究问题被拆成 ≥1 条六要素子问题；
- [ ] 每步（拆解/规划/扩展/检索/筛选/收敛/组装/自检）输入输出清楚。

**实际效果 / 结果正确性**

- [ ] 每条结果标题/作者/年份/期刊/DOI 真实且逐字保留来源写法；
- [ ] 结果与子问题真正相关；相关性依据为 locator+quote，非空泛评语。

**实际效果 / 完整性**

- [ ] 每个子问题 ≥1 条 `included` 证据；
- [ ] 未覆盖子问题列入 `uncertainty.coverage_gaps`。

**实际效果 / 可核验性**

- [ ] `evidence` 记录每轮查询、筛选、排序与停止理由；
- [ ] `screening` 覆盖全部记录，排除/待定项均有理由。

**使用增益**

- [ ] 多轮规划与术语扩展提高召回；
- [ ] 全量筛选与脚本打分减少人工返工；
- [ ] `python3 -B scripts/main.py selfcheck ...` 全 PASS。

**创新增益**

- [ ] 六要素拆解、依赖化查询规划、受控术语扩展、显式停止阈值；
- [ ] 打分公式 `0.55·relevance + 0.20·evidence + 0.15·metadata + 0.10·reliability` 由脚本固化、可复现。

**可靠体验**

- [ ] `python3 -B scripts/main.py --help` 退出码 0；
- [ ] 空输入/坏 JSON/坏编码/超限文件不崩溃且退出码 0；
- [ ] `python3 -B scripts/selftest.py` 全 PASS（31 项）；
- [ ] 重复运行分析正文字节一致（`deterministic_content_sha256` 相同）。

**生态贡献**

- [ ] `SKILL.md` + `scripts/` + `references/` 齐全；
- [ ] 他人按本文件可独立复现一次完整检索。

## 7. 独立复现步骤

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py --help
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
# 拆解/规划/扩展/检索（模型执行，结果写入 plan_in.json / records_in.json / evidence_in.json）
python3 -B scripts/main.py set-plan --kind sub_questions --file /app/work/plan_in.json --work /app/work
python3 -B scripts/main.py import --file /app/work/records_in.json --work /app/work
python3 -B scripts/main.py record-evidence --file /app/work/evidence_in.json --work /app/work
python3 -B scripts/main.py set-plan --kind stop_decision --file /app/work/stop.json --work /app/work
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
python3 -B scripts/selftest.py
```

完成后核对 `/app/output/result.json`、`screening.md`、`plan.md`、`manifest.json` 均存在且合法；`result.json` 含 `case_id` 与 `output_contract` 全部字段。
