---
name: sci-citation-ir
description: 参考文献与引用管理的可复核 Skill，以结构化中间表示 citation_model.json 为单一事实源，完成引用定位、编号重排、文献去重、格式转换与来源核验，交付可编辑稿件、同步文献表与逐条证据映射。当需要整理、改写或校验学术稿件的引用与参考文献时使用。
version: 1.0.0
---

# 参考文献与引用管理 Skill（中间表示驱动）

## 1. 角色与何时使用

本 Skill 面向“从真实稿件 + 真实文献库出发，完成引用管理并保留来源”的学术写作任务。适用场景：给论断句插入引用（挂得对）、插入/删除/调序后重排编号并同步列表（编得顺）、合并中英文题名/预印本与正式版等重复条目（收得净）、核验条目真实性与正文—文献匹配（对得上）、BibTeX/RIS/EndNote/JSON/CSV 与 GB/T 7714—2015 / APA 之间转换。

**本版设计取舍**：以结构化中间表示（IR）`citation_model.json` 为单一事实源，`manuscript.md`、`references.md` 都是 IR 的确定性投影。脚本只做机械部分；语义判断（论断定位、claim↔文献匹配、来源核验）由 Agent 按 `references/citation_rules.md` 完成，写入 `agent_edits.json` 后由脚本合并重渲染。正文标记与文末列表因此不可能漂移。

不适用：一次性脚本；与引用无关的纯文本润色；没有材料却要求生成论文内容。

## 2. 输入契约（运行时）

- 原始输入固定为 `/input/case.json` 与 `/input/task.md`；材料可能在 `/input` 下任意子目录，**扫描发现，不假设文件名/键名**。
- 识别稿件 `.md/.markdown/.txt/.tex`（`.docx/.pdf` 先经运行时 `pdf-text-tool` 转文本）；文献库 `.bib/.ris/.enw/.json/.csv/.tsv`（`.xlsx` 需运行时表格工具，脚本会记录降级）。
- 单文件 2 MB 上限，超限跳过并记录。文本按 `utf-8-sig→utf-8→gb18030→latin-1` 容错读取；坏编码/坏 JSON 降级不崩溃。
- `case.json` 的 `output_contract` 是输出字段的**唯一权威**，全部键原样回填；工作目录 `/app/work`，最终产物全部落到 `/app/output/`。

## 3. 输出契约（必须产出）

`/app/output/result.json` 必须含：

1. `case_id`（取自 case.json，多键兜底）。
2. `output_contract` 的**全部字段原样回填**：能确定的填实际值（产物路径），不能确定的填 `null`/`[]`；不改名、不删未知键。
3. 附加字段：`status`（`ok | partial | insufficient_evidence | input_error`）、`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`pending_human`、`artifacts`、`runtime`。
4. 主产物（可编辑、可复核）：
   - `manuscript.md`：带重排后引用标记的可编辑稿件
   - `references.md`：与正文编号同步的文献表
   - `evidence_map.json`：每条引用 → `locator + 逐字 quote` + 匹配依据
   - `consistency_report.json`：未解决引用、未引用条目、编号缺口、待确认合并
   - `citation_model.json`：**结构化中间表示**（claims/references/citations/merge_proposals/numbering）
   - `plan.json`（工作目录）：发现清单、任务动作、预算分配、阶段检查点
5. 每个关键结论/数值/引用必须有来源定位；冲突不隐藏（记 `source_conflict`）；缺失写 `not_reported`/`pending_human`，不编造。

## 4. 执行流程（900 秒 / 64 次模型请求 / 每次 ≤16384 tokens）

| 阶段 | 时间 | 请求预算 | 输入 → 输出 |
|---|---|---|---|
| 0 落骨架 | 0–60s | ≤2 | **必须先跑** `python3 -B scripts/main.py --init`，落盘合法 `result.json` 骨架 + 空产物 + `plan.json` |
| 1 建 IR | 60–180s | ≤4 | 跑 `python3 -B scripts/main.py`，确定性解析、去重提案、claim 抽取、`citation_model.json` 落盘 |
| 2 语义 | 180–600s | ≤32 | 读 `citation_model.json`，逐句 claim↔文献匹配、来源核验，写 `agent_edits.json` |
| 3 合并重渲染 | 600–780s | ≤12 | 重跑 `python3 -B scripts/main.py`，合并决策、重排编号、渲染产物、一致性检查 |
| 4 终稿 | 780–900s | ≤8 | 复核 `result.json`（status/sources/uncertainty/change_log）、逐条勾选清单 |

**前 3 分钟铁律**：立即运行 `python3 -B scripts/main.py --init`，让 `result.json` 与全部产物先以合法骨架落盘；后续任何阶段中断都不会留下空目录或不完整交付。之后每次增量都把最新状态写回 `result.json`（幂等，同一输入重复运行结果一致）。

脚本只做确定性部分（解析、去重判据、编号重排、格式渲染、一致性检查）；语义判断由 Agent 完成并回写 `agent_edits.json` 与 `change_log`，脚本不臆造这些结论。

## 5. 脚本调用（相对路径，纯标准库）

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py --init                 # 快速落骨架（前 3 分钟）
python3 -B scripts/main.py                        # 缺省 /input -> /app/output
python3 -B scripts/main.py --style apa            # gbt7714 | apa
python3 -B scripts/main.py --self-test            # 内存自检，退出码 0
python3 -B scripts/selftest.py                    # 独立自检（引擎+CLI 鲁棒性）
```

缺参时 `--input/--output/--work` 分别指向 `/input`、`/app/output`、`/app/work`。

## 6. 自检清单（逐条对应评分维度）

- [ ] **实际效果·完成度**：从真实稿件 + 真实文献库出发，交付可编辑稿件 + 同步文献表 + 证据映射，覆盖任务要求的动作与全部引用点/条目。
- [ ] **实际效果·正确性**：每条引用落到真实、可定位的文献条目；元数据来自材料；论断与所引文献主题/结论匹配。
- [ ] **实际效果·完整性**：全部章节、引用点、条目都被处理，无漏项。
- [ ] **实际效果·可核验性**：`evidence_map` + `citation_model.json` + `change_log` 能回溯原稿、文献库快照、匹配依据与每步改动。
- [ ] **使用增益**：编号/去重/格式由脚本自动完成，人工只做语义确认；同输入重复运行一致，可用 `--self-test` 验证。
- [ ] **创新增益**：IR 驱动——正文标记与列表双向同步、去重判据可解释（DOI/arXiv 自动合并，题名+作者+年份仅作候选）、论断级匹配逐句记录。
- [ ] **可靠体验**：`--self-test`/`--help` 退出码 0；空目录/坏 JSON/坏编码/缺字段不崩溃且如实记录。
- [ ] **生态贡献**：说明与提交一致，脚本/规则/清单可被他人独立复用。
- [ ] 详见 `references/checklist.md`，逐条勾选后才可交付。

## 7. 降级与诚实原则

- 缺文献库/缺稿件 → `status = insufficient_evidence`，`pending_human` 列出缺口，产物用占位符并标明“非真实结果”。
- 坏 JSON/坏编码/解析失败 → 记 `errors`；跳过超限/不支持文件 → 记 `degradations`，继续处理其余材料。
- DOI 解析失败、条目歧义、同名不同文献 → 不自动合并、不声称已核验，标 `not_verified`/`pending_human`/`source_conflict`；题名+作者+年份匹配仅作 `candidate`，须 Agent 确认后才合并。
- 缺 Office/PDF 渲染软件 → 交付 Markdown 可编辑版并标注 `limitations`，Office 渲染需运行时 `office-renderer`；`.docx/.pdf` 稿件与 `.xlsx` 文献库需运行时工具预处理。
- **严禁编造**评测分数、DOI、元数据、检索结果或实验数据；`status` 如实，`ok` 只能在语义核验完成后设置。

## 8. 边界

一个 Skill 只解决“引用与参考文献整理”这一类任务；不代写不存在的实验/数据，不生成虚假参考文献，不做期刊排版模板适配。没有真实材料时输出“材料不足”，而不是示例填充。
