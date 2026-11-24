---
name: sci-citation-management
description: 参考文献与引用管理的可复核 Skill：解析稿件与文献库，完成引用定位、编号重排、文献去重、格式转换与来源核验，交付可编辑稿件、同步文献表与证据映射，并把所有不确定项与限制显式化。当需要整理、改写或校验学术稿件的引用与参考文献时使用。
version: 2.0.0
---

# 参考文献与引用管理 Skill

## 1. 角色与何时使用

本 Skill 面向“从真实稿件 + 真实文献库出发，完成引用管理并保留来源”的
学术写作任务。适用场景：给论断句插入引用（挂得对）、插入/删除/调序后重排
编号并同步列表（编得顺）、合并中英文题名/预印本与正式版等重复条目（收得净）、
核验条目真实性与正文—文献匹配（对得上）、BibTeX/RIS/JSON 与
GB/T 7714—2015 / APA 之间转换。

不适用：一次性脚本；与引用无关的纯文本润色；没有材料却要求生成论文内容。

## 2. 输入契约（运行时）

- 原始输入固定为 `/input/case.json` 与 `/input/task.md`；材料可能在 `/input`
  下任意子目录，**扫描发现，不假设文件名/键名**。
- 识别稿件 `.md/.txt/.tex/.docx/.pdf`、文献库 `.bib/.ris/.enw/.json/.csv/.tsv/.xlsx`。
  PDF 文本用运行时 `pdf-text-tool`；本包脚本不内置 PDF 解析。
- 单文件 2 MB 上限，超限跳过并记入 `degradations`。文本按 `utf-8-sig` 容错
  读取，坏编码/坏 JSON 降级不崩溃。
- `case.json` 的 `output_contract` 是输出字段的**唯一权威**；工作目录
  `/app/work`，最终产物全部落到 `/app/output/`。

## 3. 输出契约（必须产出）

`/app/output/result.json` 必须含：

1. `case_id`（取自 case.json）。
2. `output_contract` 的**全部字段原样回填**：能确定的填实际值（如产物路径），
   不能确定的填 `null`/`[]`；不改名、不删未知键。
3. 附加字段：`status`（`ok | partial | insufficient_evidence | input_error`）、
   `sources`、`uncertainty`、`limitations`、`change_log`、`errors`、
   `degradations`、`pending_human`、`artifacts`、`runtime`。
4. 主产物（可编辑、可复核）：
   - `manuscript.md`：带重排后引用标记的可编辑稿件
   - `references.md`：与正文编号同步的文献表
   - `evidence_map.json`：每条引用 → `locator + 逐字 quote` + `match_evidence`
   - `consistency_report.json`：未解决引用、未引用条目、编号缺口
5. 每个关键结论/数值/引用必须有来源定位（`locator + 逐字 quote`）；冲突不
   隐藏（记 `source_conflict`）；缺失写 `not_reported`/`pending_human`，不编造。

## 4. 执行流程（900 秒 / 64 次模型请求 / 每次 ≤16384 tokens）

| 阶段 | 时间 | 请求预算 | 输入 → 输出 |
|---|---|---|---|
| 0 发现 | 0–60s | ≤4 | 扫 `/input`，读 case.json/task.md，列材料清单 |
| 1 落骨架 | 60–180s | ≤4 | **必须先跑** `python3 -B scripts/main.py`，落盘合法 `result.json` 骨架 + 空产物；之后只做增量改进 |
| 2 语义 | 180–600s | ≤30 | 识别论断句、插入/定位引用、逐句 claim↔文献匹配、去重复核 |
| 3 核验 | 600–840s | ≤16 | 编号/交叉引用/格式复核、DOI/元数据核验、一致性检查 |
| 4 终稿 | 840–900s | ≤6 | 更新 result.json（status/sources/uncertainty/change_log），复核产物 |

**前 3 分钟铁律**：立即运行
`python3 -B scripts/main.py --input /input --output /app/output --work /app/work`，
让 `result.json` 与四个产物先以合法骨架落盘；后续任何阶段中断都不会留下空目录
或不完整交付。之后每次增量都把最新状态写回 `result.json`（幂等）。

脚本只做确定性部分（解析、去重、编号重排、格式转换、一致性检查）；语义判断
（论断定位、claim↔文献匹配、来源核验）由 Agent 按
`references/citation_rules.md` 完成，回写 `evidence_map.json` 与 `change_log`，
脚本不臆造这些结论。

## 5. 脚本调用（相对路径，纯标准库）

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py                          # 缺省 /input -> /app/output
python3 -B scripts/main.py --style apa              # gbt7714 | apa
python3 -B scripts/main.py --self-test              # 内存自检，退出码 0
python3 -B scripts/selftest.py                      # 合成用例全量自检，退出码 0
```

缺参时 `--input/--output/--work` 分别指向 `/input`、`/app/output`、`/app/work`。
脚本幂等：同一输入重复运行结果一致（固定编号顺序、确定性布局、无随机数）。

Agent 增量回写约定：把语义结论写入 `/app/work/agent_evidence_map.json` 与
`/app/work/agent_change_log.json`，再重跑 `main.py`，脚本会合并二者而不覆盖
确定性部分。

## 6. 自检清单（逐条对应评分维度）

- [ ] **实际效果·完成度**：交付可编辑稿件 + 同步文献表 + 证据映射，覆盖任务要求动作与全部引用点/条目。
- [ ] **实际效果·正确性**：每条引用可定位到文献库真实条目；元数据来自材料；论断与文献匹配。
- [ ] **实际效果·完整性**：全部章节、引用点、条目都被处理，无漏项。
- [ ] **实际效果·可核验性**：evidence_map + change_log 能回溯原稿、文献库快照、匹配依据与每步改动。
- [ ] **使用增益**：编号/去重/格式由脚本自动完成，人工只做语义确认；同输入重复运行一致。
- [ ] **创新增益**：编号连锁更新、去重判据可解释、论断级匹配逐句记录。
- [ ] **可靠体验**：`--self-test` 与 `--help` 退出码 0；空目录/坏 JSON/坏编码/缺字段不崩溃且如实记录。
- [ ] **生态贡献**：说明与提交一致，脚本/规则/清单可被他人独立复用。
- [ ] 详见 `references/checklist.md`，逐条勾选后才可交付。

## 7. 降级与不确定性（本包重点：显式化，不编造）

- 缺文献库/缺稿件 → `status = insufficient_evidence`，`pending_human` 列出缺口，
  产物用占位符并标明“非真实结果”。
- 坏 JSON/坏编码/解析失败 → 记 `errors`；跳过超限/不支持文件 → 记 `degradations`，
  继续处理其余材料。
- DOI 解析失败、条目歧义、同名不同文献 → 不自动合并/不声称已核验，标
  `not_verified`/`pending_human`/`source_conflict`。
- 缺 Office/PDF 渲染软件 → 交付 Markdown 可编辑版并标注 `limitations`，
  Office 渲染需运行时 `office-renderer`。
- **每条不确定项**写入 `uncertainty`：`{id, kind, locator, claim, detail,
  severity, resolution}`；系统性边界写入 `limitations`；`severity=high` 同步进
  `pending_human`，未决时 `status ≠ ok`。
- **严禁编造**评测分数、DOI、元数据、检索结果或实验数据；`status` 如实，
  `ok` 只能在语义核验全部完成且无未决 high/medium 项后设置。

## 8. 边界

一个 Skill 只解决“引用与参考文献整理”这一类任务；不代写不存在的实验/数据，
不生成虚假参考文献，不做期刊排版模板适配。没有真实材料时输出“材料不足”，
而不是示例填充。
