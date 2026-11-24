---
name: sci-research-transfer-writing
description: 科研成果转化写作：把论文摘要/技术报告等源文改写为面向不同读者与体裁（科普文章、项目申报、技术报告、政策建议等）的可编辑稿件，同时锁定事实、数据、术语与论断强度并保留逐段可复核的来源与改动记录。当任务要求"按给定源文、目标读者、体裁与篇幅完成一次成果转化改写/校对/结构化，且保留来源"时使用。
version: 1.0.0
---

# 科研成果转化写作 Skill

## 1. 角色与何时使用

本 Skill 解决一个高频、输入输出明确的问题：**把一份科研源文（摘要、论文、技术报告、底稿）改写成指定受众与体裁的可提交稿，同时不漂移事实、不夸大论断、可逐段复核。**

适用：任务含"源文 + 目标读者 + 目标体裁 + 篇幅/格式约束"，要求产出可直接使用的改稿而非写作建议、提纲或片段润色。
不适用：仅做泛泛润色、不要求保留来源；或不需要产物、只给建议的闲聊。

四道质量门（贯穿全流程）：
- **读得懂**：目标读者不补背景也能懂；缩写首次展开、公式与行话有解释。
- **留得住**：关键结论、数据、单位、样本量、统计口径、比较基准、图表编号、引用、限定条件全保留；删的是冗余不是证据。
- **说得准**：论断强度与源文证据匹配；相关不写因果、初步不写成定论；同一概念全文只一个说法。
- **接得住**：每处删改有清单、理由与定位；他人能按 `source_id + locator + quote` 逐段复核。

## 2. 输入契约

运行时输入在 `/input`：
- `case.json`：含 `case_id`、`task_instruction`、`output_contract`（契约键名以它为准）。
- `task.md`：任务说明（读者、体裁、篇幅、格式、评分要求）。
- 材料：稿件/模板/图表/数据/审稿意见/参考列表等（文件名不固定，需扫描发现；可能含 PDF）。

工作目录 `/app/work`，最终产物写 `/app/output`。**只使用 Python 标准库；不联网。** 预算：64 次模型请求 / 900 秒 / 每回复 16384 tokens。
> 材料含 PDF 时：`show` 会先尝试 `pdftotext`；不可用则用平台 `pdf-text-tool`/`ocr-tool` 提取文本落盘到 `/app/work` 后再继续（本包对 `.md/.txt/.csv/.json/.tex` 直接读取）。

## 3. 输出契约（必须全部满足）

必须产出 `/app/output/result.json`，顶层含：
1. `case_id`（从 `/input/case.json` 原样回填）；
2. `/input/case.json` 的 `output_contract` **全部键**——原样回填、不改名、不删未知键，缺值给 `null`/`[]`；
3. 公共字段：`status`（`answered|partial|insufficient_evidence|input_error`）、`sources`、`uncertainty`、`change_log`，另加 `errors`/`degradations`/`pending_human`/`limitations`/`runtime`/`coverage`。

主交付为**可编辑**文本（`.md`/`.json`/`.csv`/`.tex`/`.docx`）；需要导出预览的（Office/图）另交真实 PDF/PNG。配套资产：`preserved_facts.json`、`evidence_map.json`、`change_log.json`、`glossary.csv`、`report.md`。

`status` 必须诚实：材料不足→`insufficient_evidence`，输入坏→`input_error`，有缺口→`partial`，不编造数据或结论。

## 4. 执行流程（总 900 秒 / 64 次请求）

> 所有命令先 `cd /skills/weekly-skill`（若路径不同，`find / -maxdepth 4 -name SKILL.md 2>/dev/null` 定位后替换）。脚本调用路径一律相对：`python scripts/main.py ...`。

**第 0 步（前 3 分钟内必须完成）——先落盘骨架：**

```bash
cd /skills/weekly-skill && python scripts/main.py init --input /input --output /app/output --work /app/work
```

`init` 秒级写入：`/app/work/source_index.json`、`/app/work/evidence_candidates.json`、`/app/work/draft.skeleton.json`、以及一份合法的 `/app/output/result.json`（`status=partial` 骨架）。**任何后续失败都不会留下空产物。**

**第 1 步——勘察环境与材料（~1 分钟）：**

```bash
python scripts/main.py survey
python scripts/main.py show          # 打印带 locator 的源文，事实/引文从这里逐字复制
python scripts/main.py extract       # 数值/单位/样本量/引用/图表/缩写候选
```

**第 2 步——读契约定结构（~2 分钟）：** 读 `/input/case.json` 的 `output_contract`，确认业务字段（如 `result` 内的对象/数组结构、`claims`、`uncertainties`），据此确定 `draft.result` 的形态。

**第 3 步——真实撰写/改写（主体时间）：** 把 `draft.skeleton.json` 复制为 `/app/work/draft.json` 并填满（结构见第 5 节）。正文由你基于材料真实写成；事实与引文逐字复制 `show` 输出，读不出/材料没报告就写 `uncertainties`/`pending_human`。

**第 4 步——机械校验与组装：**

```bash
python scripts/main.py check
python scripts/main.py build
```

`build` 组装主交付 + `result.json` + 证据/改动/术语资产；若有 FAIL 自动把 `status` 降为 `partial`。之后可反复 `build` 增量改进（每次 append `change_log`）。

## 5. 工作稿 `/app/work/draft.json` 结构

```json
{
  "deliverable_name": "rewrite.md",
  "audience": "公众/基金评审/技术同行/媒体/政策（按 task.md）",
  "genre": "科普文章/项目申报/技术报告/简报/政策建议（按 task.md）",
  "deliverable_text": "主交付正文（真实写成的改稿）",
  "result": { "按 output_contract 填的业务结构" },
  "preserved_facts": [{"fact_id":"F1","value":"87.4","unit":"%","source_id":"S1",
                       "locator":"paper.md#para.1","quote":"逐字原文","status":"preserved"}],
  "evidence_map":   [{"claim_id":"C1","claim":"正文论断","source_id":"S1",
                       "locator":"paper.md#para.1","quote":"逐字原文","kind":"direct","strength":"established"}],
  "change_log":     [{"id":"R1","action":"rewrite|compress|delete|translate",
                       "original_quote":"改前","revised_quote":"改后","reason":"为什么","locator":"paper.md#para.3"}],
  "glossary":       [{"term":"术语","canonical":"锁定写法","first_expansion":"首次展开","synonyms":"收敛掉的同义词","note":""}],
  "uncertainties":[], "pending_human":[], "limitations":[], "errors":[], "degradations":[], "extra_files":{}
}
```

- `preserved_facts`：数值/单位/样本量/统计口径/比较基准/图表编号/引用/限定条件，逐条可回溯。
- `evidence_map`：正文事实性论断→原文证据（目标覆盖 ≥90% 关键论断）。
- `change_log`：每次改写/压缩/删除/翻译都要有改前、改后、理由与定位。
- `glossary`：锁定术语，保证同一概念全文一个说法；跨文体替换只在"首次展开/括注"层面，不改科学含义。

## 6. 六条改写纪律

1. 数值/单位逐字复用：`87.4%` 不改写成"约九成"；`n=120`、`p<0.05`、比较基准、图表编号保留。
2. 论断强度只降不升：源文"相关/关联"不写成"因果/导致"；"初步/可能/有待验证"必须保留；`scripts/main.py check` 会机械拦截疑似升级。
3. 缩写首次展开、术语收敛：同一概念全文只一个说法；缩写第一次出现给全称（+括号缩写）。
4. 删改留痕：删掉冗余要写 `change_log` 的 delete + 理由；不得静默删除证据。
5. 材料没有的不补：缺数值/引用/方法参数写 `pending_human` 或 `uncertainties`，不造参考文献、DOI、分数、运行日志。
6. 冲突不隐藏：同一指标两个来源不一致，两条都记录并标 `source_conflict`，不自行取舍。

## 7. 自检清单（逐条勾选，对应评分维度）

**实际效果（40：完成度15/正确性15/完整性5/可核验5）**
- [ ] 按给定源文、读者、体裁与篇幅产出一份可直接使用的完整改稿，而非建议/提纲/片段。
- [ ] 改稿中的事实、数据、单位、图表编号、引用与论断强度与源文一致，无新增结论。
- [ ] 关键结论、数据、限定条件、方法要点、图表引用全保留；删的是冗余不是证据。
- [ ] 提供逐段对照（evidence_map）+ 改动清单（change_log）+ 删改理由 + 待确认问题。

**使用增益（25：质量15/效率5/可验证5）**
- [ ] 同源文同约束下，比基础 Agent 更少事实漂移/受众错配/术语不一致（脚本机械校验兜底）。
- [ ] 从拿到源文到可提交稿，核对成本下降（证据候选 + 强度拦截 + 术语表直接复用）。

**创新增益（15）**
- [ ] 形成"受众画像→取舍策略→边界标注→多文体映射"链条（见 `references/genre_rules.md`）。
- [ ] 论断强度分级、术语锁定、保留优先级、逐段对照方法可复用。

**可靠体验（10：易用3/稳定4/异常3）**
- [ ] Agent 能在"提供源文+读者+体裁"时正确触发；用户可按说明完成一次完整改写。
- [ ] 同一源文重复运行结果一致（确定性排序 + 稳定 hash，`selfcheck` 验证）。
- [ ] 源文缺引用、数据自相矛盾、体裁与篇幅冲突、材料涉密/未发表等被识别并标注。

**生态贡献（10：文档3/复用4/分享3）**
- [ ] SKILL.md、体裁规则、术语表、验收口径、失败处理、版本维护齐全。

## 8. 降级策略（诚实，不编造）

- 空输入/无材料 → `insufficient_evidence`，`uncertainty` 列出缺口，退出码 0。
- 坏 JSON/坏编码 → `input_error`，`errors` 记录，继续处理其余材料。
- 缺软件（pdftotext/office 渲染等）→ 纯文本降级，`degradations` 注明"未渲染预览"；不伪造软件状态。
- 数据缺失/冲突/需要人工确认 → 写 `pending_human`/`source_conflict`，**不得编造数值、引文、DOI、分数或运行记录**。
- 若脚本无法运行（罕见），仍手写合法 `/app/output/result.json`：逐键回填契约，`status` 写 `partial`/`input_error`，`errors` 写实际错误，`uncertainties` 说明手工兜底。**宁可交不完整但合法的 result.json，也不要零产物。**

## 9. 常见错误

- 只给改写建议/提纲，没有完整正文；
- 事实漂移：数字/单位/样本量被改、相关写成因果、初步写成定论；
- 缩写不展开、术语全文不一致；
- 删改无记录、证据无法回溯；
- 编造缺失的引用/数据/运行记录。

## 10. 参考文件与版本

- `references/genre_rules.md`：受众画像、体裁映射、论断强度分级、保留优先级、术语锁定规则。
- `references/checklist.md`：评分维度映射、字段级契约、失败处理与验收口径。
- `scripts/lib.py` / `scripts/main.py` / `scripts/validate.py`：实现（仅标准库）。

版本 1.0.0；维护：新增体裁映射须同步 `references/genre_rules.md` 与 `lib.py` 的 `GENRE_PROFILES`，并保持 `selfcheck` 通过。
