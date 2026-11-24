---
name: sci-citation-management-v2
description: 参考文献与引用管理的可复核 Skill：解析稿件与文献库，完成引用定位、编号连锁更新、文献去重、GB/T 7714—2015 / APA 格式转换、来源核验与正文—文献一致性检查，交付可编辑稿件、同步文献表、逐句证据映射与可解释合并依据，并把官方五条评分维度逐条映射到流程与自检项（result.json 含 coverage 与 scoring_alignment）。当需要整理、改写或校验学术稿件的引用与参考文献时使用。
version: 1.1.0
---

# 参考文献与引用管理 Skill（评分维度对齐版）

## 1. 角色与何时使用

本 Skill 面向"从真实稿件 + 真实文献库出发，完成引用管理并保留来源"的学术写作任务。适用：给需要来源支撑的论断句在正确位置插入引用（挂得对）；插入/删除/调序后重排编号并同步列表与证据映射（编得顺）；合并中英文题名、预印本与正式版、不同数据库导出的重复条目（收得净）；核验条目真实性与正文—文献匹配（对得上）；在 BibTeX/RIS/EndNote/CNKI 导出与 GB/T 7714—2015 / APA 之间转换并保留 DOI、卷期页、文献类型标识。

不适用：一次性脚本；与引用无关的纯文本润色；没有材料却要求生成论文内容。

## 2. 输入契约（运行时）

- 原始输入固定为 `/input/case.json` 与 `/input/task.md`；科研材料可能在 `/input` 下任意子目录，**扫描发现，不假设文件名/键名**。
- 识别稿件 `.md/.txt/.tex/.docx/.pdf`、文献库 `.bib/.ris/.enw/.json/.csv/.tsv/.xlsx`。DOCX 由包内脚本用标准库 `zipfile` 提取；PDF 文本需运行时 `pdf-text-tool`（本包不内置 PDF 解析）；`.xlsx` 需运行时表格工具。
- `case.json` 与根目录 `task.md` 是任务说明，**不当作材料解析**。单文件 2 MB 上限，超限跳过并记录。
- 文本按 `utf-8-sig` 容错读取（回退 `utf-8/gb18030/latin-1`），坏编码/坏 JSON 降级不崩溃。`case.json` 的 `output_contract` 是输出字段的**唯一权威**。
- 工作目录 `/app/work`；最终产物全部落到 `/app/output/`。

## 3. 输出契约（必须产出）

`/app/output/result.json` 必须含：

1. `case_id`（取自 case.json）。
2. `output_contract` 的**全部字段原样回填**：能确定的填实际值（如产物路径），不能确定的填 `null`/`[]`；不改名、不删未知键。
3. 附加字段：`status`（`ok | partial | insufficient_evidence | input_error`）、`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`pending_human`、`artifacts`、`coverage`、`scoring_alignment`、`runtime`。
4. `coverage`：题目六类动作（引用定位、编号更新、去重归并、格式转换、来源核验、一致性检查）各一项，`status ∈ done_deterministic | done_semantic | pending_semantic | not_applicable` + 证据指针。
5. `scoring_alignment`：五条评分维度 → 证据指针（见第 8 节），复核人可直接定位。
6. 主产物（可编辑、可复核）：
   - `manuscript.md`：带重排后引用标记的可编辑稿件
   - `references.md`：与正文编号同步的文献表
   - `evidence_map.json`：每条引用 → `locator + 逐字 quote + 匹配依据`
   - `consistency_report.json`：未解决引用、未引用条目、编号问题
   - `library_snapshot.json`：解析/去重后的文献库快照与 `merge_evidence`
7. 每个关键结论/数值/引用必须有来源定位；冲突不隐藏（记 `source_conflict`）；缺失写 `not_reported`/`pending_human`，不编造。

## 4. 执行流程（900 秒 / 64 次模型请求 / 每次 ≤16384 tokens）

| 阶段 | 时间 | 请求 | 输入 → 输出 |
|---|---|---|---|
| 0 落骨架 | 0–180s | ≤4 | 扫 `/input`，读 case.json/task.md，**立即运行** `python3 -B scripts/main.py`，落盘合法 `result.json` 骨架 + 空产物；之后只做增量改进 |
| 1 解析去重 | 60–180s | ≤4 | 解析文献库、去重（脚本确定性完成），生成 `library_snapshot.json` |
| 2 语义 | 180–600s | ≤30 | 识别论断句，插入占位符 `[#cN]`，写 `/app/work/citation_plan.json`（claim↔条目匹配 + locator + 逐字 quote + match_reason），来源核验 |
| 3 核验 | 600–840s | ≤16 | 重跑 `main.py` 应用编号重排与格式转换；复核编号/交叉引用/DOI/一致性；修正 plan 后重跑（幂等） |
| 4 终稿 | 840–900s | ≤6 | 更新 `result.json`（status/sources/uncertainty/change_log），逐条自检 |

**前 3 分钟铁律**：立即运行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`，让 `result.json` 与产物先以合法骨架落盘；后续任何阶段中断都不会留下空目录或不完整交付。之后每次增量都把最新状态写回 `result.json`（幂等）。

脚本只做确定性部分（解析、去重、编号重排、格式转换、一致性检查）；语义判断（论断定位、claim↔文献匹配、来源核验）由 Agent 按 `references/rules.md` 完成并回写 `citation_plan.json`、`evidence_map.json` 与 `change_log`，脚本不臆造这些结论。

### citation_plan.json 契约（Agent 写，脚本读）

```json
{"citations": [{"id": "c1", "entry_id": "smith2020",
  "locator": "manuscript.md#L12", "quote": "逐字论断句",
  "match_reason": "该文献主题/结论如何支撑此论断", "verified": false}]}
```

稿件中用 `[#c1]`、`[#c2,c3]` 占位；`main.py` 按首次出现顺序重排为 `[1]`、`[2-3]`，同一文献多条论断共享一个编号，并同步 `references.md` 与 `evidence_map.json`。

## 5. 脚本调用（相对路径，纯标准库）

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py                          # 缺省 /input -> /app/output
python3 -B scripts/main.py --style apa              # gbt7714 | apa
python3 -B scripts/main.py --self-test              # 内存自检，退出码 0
python3 -B scripts/selftest.py                      # 同上，独立入口
```

缺参时 `--input/--output/--work` 分别指向 `/input`、`/app/output`、`/app/work`。脚本幂等：同一输入重复运行内容一致（固定编号顺序、确定性布局；仅 `runtime` 时间戳为运行元数据）。`core_impl.py` 承载确定性引擎，`main.py` 只做编排与 CLI。

## 6. 自检清单（逐条对应评分维度，交付前勾选）

- [ ] **实际效果·任务完成度(15)**：可编辑 `manuscript.md` + 同步 `references.md` + 证据映射，覆盖六类动作。
- [ ] **实际效果·结果正确性(15)**：每条引用绑定真实 `entry_id`；元数据来自材料；`match_reason` 说明论断与文献匹配。
- [ ] **实际效果·结果完整性(5)**：全部章节、引用点、条目被处理，`consistency_report.json` 无意外漏项。
- [ ] **实际效果·可核验性(5)**：`evidence_map.json` 每项含 `locator + 逐字 quote + 匹配依据`；`change_log` 记录每步改动。
- [ ] **使用增益·质量(15)**：编号连续、条目完整、引用位置在论断句处，优于整段末尾挂引用的基线。
- [ ] **使用增益·效率(5)**：编号/去重/格式由脚本自动完成，人工只做语义确认。
- [ ] **使用增益·可验证(5)**：同输入重复运行一致；`--self-test` 退出码 0。
- [ ] **创新·新增能力(5)**：编号连锁更新（正文 ↔ 文末列表 ↔ 证据映射三者同步）。
- [ ] **创新·方法改进(5)**：去重判据可解释（DOI / 规范化题名+年份+第一作者姓氏，union-find），合并依据写入 `merge_evidence`。
- [ ] **创新·创新证据(5)**：论断级匹配逐句记录 + `coverage`/`scoring_alignment` 说明相对基础 Agent 的价值边界。
- [ ] **可靠·调用易用(3)**：`--help` 与 `--self-test` 退出码 0；默认参数指向正确路径。
- [ ] **可靠·运行稳定(4)**：空目录/坏 JSON/坏编码/缺字段/重复运行均不崩溃且产出合法 `result.json`。
- [ ] **可靠·异常处理(3)**：字段缺失、DOI 冲突、条目歧义、编号冲突、格式不支持、文件损坏均写入对应字段。
- [ ] **生态·文档完整(3)**：用途、适用条件、输入输出、异常、限制、维护信息齐全。
- [ ] **生态·资源复用(4)**：脚本纯标准库；规则与清单位于 `references/`；去重规则、格式映射可独立复用。
- [ ] **生态·分享价值(3)**：沉淀失败模式与实践经验，不依赖作者个人文件或手工操作。
- [ ] 逐条勾选依据见 `references/checklist.md`，规则与判据见 `references/rules.md`。

## 7. 降级策略与诚实原则

- 缺文献库/缺稿件 → `status = insufficient_evidence`，`pending_human` 列出缺口，产物用占位符并标明"非真实结果"。
- 坏 JSON/坏编码/解析失败 → 记 `errors`；跳过超限/不支持文件 → 记 `degradations`，继续处理其余材料。
- DOI 解析失败、条目歧义、同名不同文献 → 不自动合并/不声称已核验，标 `not_verified`/`pending_human`/`source_conflict`。
- 缺 Office/PDF 渲染软件 → 交付 Markdown 可编辑版并标注 `limitations`，Office 渲染需运行时 `office-renderer`。
- **严禁编造**评测分数、DOI、元数据、检索结果或实验数据；`status` 如实，脚本永不自设 `ok`，`ok` 只能在语义核验完成后由 Agent 设置。

## 8. 评分维度对齐（本版核心：逐条映射到流程与自检项）

| 评分维度(分值) | 流程落点 | 自检项 | 可核验证据 |
|---|---|---|---|
| 实际效果(40) | 阶段 2–3 语义核验 | §6 前 4 条 | artifacts.manuscript/references/evidence_map/consistency_report + coverage |
| 使用增益(25) | 阶段 1/3 脚本自动化 | §6 第 5–7 条 | change_log + 确定性流水线 + `--self-test` |
| 创新增益(15) | 阶段 1 去重、阶段 2 论断级匹配 | §6 第 8–10 条 | merge_evidence + evidence_map(quote+locator) + coverage |
| 可靠体验(10) | 阶段 0/1 容错与幂等 | §6 第 11–13 条 | errors/degradations/pending_human/runtime + 退出码 |
| 生态贡献(10) | SKILL.md/references 文档 | §6 第 14–16 条 | SKILL.md + references/checklist.md + references/rules.md |

设计取舍：与"只贴规则"的通用做法不同，本包把每一条评分维度落到**可命名的产物文件与自检项**，并把六类动作完成状态写入 `result.json.coverage`、五条维度证据指针写入 `result.json.scoring_alignment`，使"文字自述"能被逐条指认或证伪。

## 9. 边界

一个 Skill 只解决"引用与参考文献整理"这一类任务；不代写不存在的实验/数据，不生成虚假参考文献，不做期刊排版模板适配。没有真实材料时输出"材料不足"，而不是示例填充。
