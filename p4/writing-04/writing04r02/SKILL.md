---
name: sci-citation-governance
description: 参考文献与引用治理 Skill（时间预算与早落盘版）：解析稿件与文献库，完成引用定位、编号连锁重排、可解释去重、格式转换与来源核验，并以检查点日志（checkpoint.jsonl）落实 900 秒 / 64 请求阶段预算与“前 3 分钟先落盘 partial 骨架、之后只做增量改进”的可审计交付。当需要整理、改写或校验学术稿件的引用与参考文献时使用。
version: 2.0.0
---

# 参考文献与引用治理 Skill

## 1. 角色与何时使用

本 Skill 面向“真实稿件 + 真实文献库 → 完成引用管理并保留来源”的学术写作任务，覆盖四个核心动作：挂得对（引用插到真正需要来源的论断句）、编得顺（增删调序后正文编号与文末列表、图表注、交叉引用同步）、收得净（中英文题名 / 预印本与正式版 / 不同数据库导出的重复条目合并并说明判据）、对得上（每条引用真实存在、元数据准确、论断与所引文献匹配，可逐句回溯）。

适用：插入引用、删除/调序后重排编号并同步列表、文献去重与条目归并、BibTeX/RIS/JSON/CSV/TSV 与 GB/T 7714—2015 / APA 之间转换、正文—文献一致性核验。

不适用：一次性脚本；与引用无关的纯文本润色；没有材料却要求生成论文内容（此时输出“材料不足”，不得示例填充）。

## 2. 输入契约（运行时）

- 原始输入固定为 `/input/case.json` 与 `/input/task.md`；材料可能在 `/input` 下任意子目录，**扫描发现，不假设文件名/键名**。`case.json` 与 `task.md` 本身不是案例材料，脚本会自动排除。
- 识别稿件 `.md/.txt/.tex/.docx/.pdf`、文献库 `.bib/.ris/.enw/.json/.csv/.tsv/.xlsx`。PDF 文本用运行时 `pdf-text-tool`，本包脚本不内置 PDF 解析。
- 单文件 2 MB 上限，超限跳过并记 `degradations`。文本按 `utf-8-sig` 容错读取（回退 `gb18030`/`latin-1`），坏编码/坏 JSON 降级不崩溃。
- `case.json` 的 `output_contract` 是输出字段的**唯一权威**；工作目录 `/app/work`，最终产物全部落到 `/app/output/`。

## 3. 输出契约（必须产出）

`/app/output/result.json` 必须含：

1. `case_id`（取自 case.json，容错回退 `id`/`challenge_id`/`armiId`）。
2. `output_contract` 的**全部字段原样回填**：能确定的填实际值（如产物路径），不能确定的填 `null`/`[]`；不改名、不删未知键。
3. 附加字段：`status`（`ok | partial | insufficient_evidence | input_error`）、`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`pending_human`、`artifacts`、`runtime`。
4. 主产物（可编辑、可复核）：
   - `manuscript.md`：带重排后引用标记的可编辑稿件
   - `references.md`：与正文编号同步的文献表
   - `evidence_map.json`：每条引用 → `locator + 逐字 quote` + 匹配依据
   - `consistency_report.json`：未解决引用、未引用条目、编号缺口
5. 每个关键结论/数值/引用必须有来源定位；冲突不隐藏（记 `source_conflict`）；缺失写 `not_reported`/`pending_human`，不编造。

## 4. 执行流程：900 秒 / 64 请求阶段预算与早落盘

总预算：900 秒、64 次模型请求、每次 ≤16384 tokens。**铁律：前 3 分钟（180 秒）内必须先落盘一版合法 `result.json` 骨架 + 占位产物 + 输入快照，之后只做增量改进。** 脚本把每次阶段切换写入 `/app/work/checkpoint.jsonl`（含 `phase/status/at/elapsed_seconds`），使预算纪律与“早落盘→增量”路径可被复核；`--budget` 可随时查看剩余预算。

| 阶段 | 时间窗 | 请求预算 | 输入 → 输出 |
|---|---|---|---|
| 0 发现 | 0–45s | ≤4 | 扫 `/input`，读 case.json/task.md，列材料清单与字节数 |
| 1 早落盘（强制） | 0–180s | ≤3 | **先跑** `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`，落盘合法骨架 + 空产物 + `/app/work` 快照 |
| 2 语义 | 180–600s | ≤32 | 识别论断句、插入/定位引用、逐句 claim↔文献匹配、去重复核，回写 `evidence_map.json` 与 `change_log` |
| 3 核验 | 600–840s | ≤16 | 编号/交叉引用/格式复核、DOI/元数据核验、一致性检查 |
| 4 终稿 | 840–900s | ≤9 | `python3 -B scripts/main.py --phase finalize` 同步 result.json，复核产物 |

阶段 0+1 必须合计在 180 秒内完成；若阶段 1 之后任意时刻中断，`/app/output` 仍留下合法骨架，不会出现空目录或不完整交付。每次增量都写回 `result.json`（幂等）。脚本只做确定性部分（解析、去重、编号重排、格式转换、一致性检查）；语义判断（论断定位、claim↔文献匹配、来源核验）由 Agent 按 `references/citation_rules.md` 完成并回写 `evidence_map.json` 与 `change_log`，脚本不臆造这些结论。

## 5. 证据溯源规范（科研场景硬要求）

- 每条引用/论断写入 `evidence_map.json`，条目形如 `{claim_id, claim_text, citation_id, locator, quote, match_basis}`；`locator` 指向稿件或文献库的段落/条目，`quote` 为逐字片段。
- 冲突信息不隐藏：同一指标多来源不一致时全部记录并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `not_reported`/`pending_human`。
- 去重合并依据写入 `merge_evidence`（DOI 相等 / 规范化题名+第一作者+年份相等）；近重复但判据不足的条目不自动合并，标 `pending_human`。

## 6. 脚本调用（相对路径，纯标准库）

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py                          # 缺省 /input -> /app/output
python3 -B scripts/main.py --style apa              # gbt7714 | apa
python3 -B scripts/main.py --self-test              # 内存自检，退出码 0
python3 -B scripts/main.py --phase finalize         # 同步已编辑产物到 result.json
python3 -B scripts/main.py --budget                 # 查看检查点日志与剩余时间
```

最小调用示例（示意，非正式案例）：

```bash
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
# 之后 Agent 编辑 /app/output/manuscript.md 与 evidence_map.json
python3 -B scripts/main.py --phase finalize --output /app/output --work /app/work
```

缺参时 `--input/--output/--work` 分别指向 `/input`、`/app/output`、`/app/work`。脚本幂等：同一输入重复运行结果一致。

## 7. 自检清单（逐条对应评分维度）

- [ ] **实际效果·完成度**：交付可编辑稿件 + 同步文献表 + 证据映射，覆盖任务要求动作与全部引用点/条目。
- [ ] **实际效果·正确性**：每条引用可定位到文献库真实条目；元数据来自材料；论断与文献匹配。
- [ ] **实际效果·完整性**：全部章节、引用点、条目都被处理，无漏项。
- [ ] **实际效果·可核验性**：evidence_map + change_log + checkpoint.jsonl 能回溯原稿、文献库快照、匹配依据与每步改动及耗时。
- [ ] **使用增益**：编号/去重/格式由脚本自动完成，人工只做语义确认；同输入重复运行一致（`--self-test` 对照）。
- [ ] **创新增益**：编号连锁更新、去重判据可解释、论断级匹配逐句记录、阶段预算与早落盘可审计。
- [ ] **可靠体验**：`--self-test` 与 `--help` 退出码 0；空目录/坏 JSON/坏编码/缺字段不崩溃且如实记录。
- [ ] **生态贡献**：说明与提交一致，脚本/规则/清单可被他人独立复用。
- [ ] 详见 `references/checklist.md`，逐条勾选后才可交付。

## 8. 降级与诚实原则

- 缺文献库/缺稿件 → `status = insufficient_evidence`，`pending_human` 列出缺口，产物用占位符并标明“非真实结果”。
- 坏 JSON/坏编码/解析失败 → 记 `errors`；跳过超限/不支持文件 → 记 `degradations`，继续处理其余材料。
- DOI 解析失败、条目歧义、同名不同文献 → 不自动合并/不声称已核验，标 `not_verified`/`pending_human`/`source_conflict`。
- 缺 Office/PDF 渲染软件 → 交付 Markdown 可编辑版并标注 `limitations`；`.docx/.pdf` 阅读与渲染依赖运行时 `office-renderer`/`pdf-text-tool`。
- **严禁编造**评测分数、DOI、元数据、检索结果或实验数据；`status` 如实，`ok` 只能在语义核验完成后设置。

## 9. 边界

一个 Skill 只解决“参考文献与引用整理”这一类任务；不代写不存在的实验/数据，不生成虚假参考文献，不做期刊排版模板适配。没有真实材料时输出“材料不足”，而不是示例填充。
