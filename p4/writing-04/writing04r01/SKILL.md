---
name: sci-citation-provenance
description: 参考文献与引用管理的可复核 Skill，重点强化证据溯源：解析稿件与文献库，完成引用定位、编号重排、文献去重、格式转换与逐句来源核验；每条结论与每条引用均带 locator + 逐字 quote 的证据映射，交付可编辑稿件、同步文献表与可回溯 provenance 记录。当需要整理、改写或校验学术稿件的引用与参考文献时使用。
version: 2.0.0
---

# 参考文献与引用管理 Skill（证据溯源强化版）

## 1. 角色与何时使用

本 Skill 面向“从真实稿件 + 真实文献库出发，完成引用管理并保留来源”的学术写作任务。适用场景：给论断句插入引用（挂得对）、插入/删除/调序后重排编号并同步列表（编得顺）、合并中英文题名/预印本与正式版等重复条目（收得净）、核验条目真实性与正文—文献匹配（对得上）、BibTeX/RIS/JSON 与 GB/T 7714—2015 / APA 之间转换。

本版与基线包的核心差异在**证据溯源强化**：每个关键结论、每条引用、每条元数据片段都必须落到 `evidence_map.json` 里的 `locator`（文件 + 行/列/字符区间 + 字段）+ `quote`（逐字原文片段），并且 `evidence_map` 采用“论断级—证据级”两级结构。Agent 只做语义判断，机械定位由脚本确定性地完成。

不适用：一次性脚本；与引用无关的纯文本润色；没有材料却要求生成论文内容。

## 2. 输入契约（运行时）

- 原始输入固定为 `/input/case.json` 与 `/input/task.md`；材料可能在 `/input` 下任意子目录，**扫描发现，不假设文件名/键名**。
- 识别稿件 `.md/.txt/.tex/.docx/.pdf` 与文献库 `.bib/.ris/.enw/.json/.csv/.tsv/.xlsx`。
- PDF 文本用运行时 `pdf-text-tool` 提取，DOCX 用运行时 `office-renderer`；本包脚本不内置 PDF/DOCX 解析，遇到时记 `degradations` 并交由 Agent 处理。
- 单文件 2 MB 上限，超限跳过并记录；文本按 `utf-8-sig` 容错读取，坏编码/坏 JSON 降级不崩溃。
- `case.json` 的 `output_contract` 是输出字段的**唯一权威**；工作目录 `/app/work`，最终产物全部落到 `/app/output/`。

## 3. 输出契约（必须产出）

`/app/output/result.json` 必须含：

1. `case_id`（取自 case.json，按 `case_id/id/challenge_id/armiId` 顺序回退）。
2. `output_contract` 的**全部字段原样回填**：能确定的填实际值（如产物路径），不能确定的填 `null`；不改名、不删未知键。
3. 附加字段：`status`（`ok | partial | insufficient_evidence | input_error`）、`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`pending_human`、`evidence_map_path`、`runtime`。
4. 主产物（可编辑、可复核）：
   - `manuscript.md`：带重排后引用标记的可编辑稿件
   - `references.md`：与正文编号同步的文献表
   - `evidence_map.json`：论断级—证据级映射，见第 6 节
   - `consistency_report.json`：未解决引用、未引用条目、编号缺口
   - `merge_evidence.json`：去重归并的字段级依据
   - `change_log.json`：每步改动记录（before/after + locator）
5. 每个关键结论/数值/引用必须有来源定位；冲突不隐藏（记 `source_conflict`）；缺失写 `not_reported`/`pending_human`，不编造。

## 4. 执行流程（900 秒 / 64 次模型请求 / 每次 ≤16384 tokens）

| 阶段 | 时间 | 请求预算 | 输入 → 输出 |
|---|---|---|---|
| 0 发现 | 0–60s | ≤4 | 扫 `/input`，读 case.json/task.md，列材料清单 |
| 1 落骨架 | 60–180s | ≤4 | **必须先跑** `python3 -B scripts/main.py`，落盘合法 `result.json` 骨架 + 空产物；之后只做增量改进 |
| 2 语义 | 180–600s | ≤30 | 逐句 claim↔文献匹配、逐条证据回填 quote/basis/confidence/rationale、去重复核 |
| 3 核验 | 600–840s | ≤16 | 编号/交叉引用/格式复核、DOI/元数据核验、一致性检查、冲突回写 |
| 4 终稿 | 840–900s | ≤6 | 更新 result.json（status/sources/uncertainty/change_log），复核产物 |

**前 3 分钟铁律**：立即运行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`，让 `result.json` 与全部产物先以合法骨架落盘；后续任何阶段中断都不会留下空目录或不完整交付。之后每次增量都把最新状态写回 `result.json`（幂等）。

脚本只做确定性部分（解析、去重、编号重排、格式转换、一致性检查、证据定位与逐字 quote 抽取）；语义判断（论断定位、claim↔文献匹配、来源核验、conflict 判定）由 Agent 按 `references/citation_rules.md` 完成并回写 `evidence_map.json` 与 `change_log.json`，脚本不臆造这些结论。

## 5. 脚本调用（相对路径，纯标准库）

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py                          # 缺省 /input -> /app/output
python3 -B scripts/main.py --style apa              # gbt7714 | apa
python3 -B scripts/main.py --self-test              # 内存自检，退出码 0
python3 -B scripts/selftest.py                      # 等价的自检入口
```

缺参时 `--input/--output/--work` 分别指向 `/input`、`/app/output`、`/app/work`。脚本幂等：同一输入重复运行结果一致（固定编号顺序、确定性布局）。脚本文件：`scripts/main.py`（入口）、`scripts/citation_engine.py`（确定性引擎）、`scripts/selftest.py`（自检）。

## 6. 证据溯源规范（本版核心差异）

`evidence_map.json` 采用两级结构，字段固定：

```json
{
  "schema_version": "2.0",
  "provenance_model": "claim-level-locator-plus-verbatim-quote",
  "claims": [
    {
      "claim_id": "C001",
      "claim_text": "论断句原文",
      "claim_quote": "论断句逐字片段",
      "claim_locator": {"file": "manuscript.md", "line": 12, "col": 3, "char_start": 210, "char_end": 268},
      "citation_markers": ["[1]"],
      "cited_references": [
        {
          "citation_number": 1,
          "reference_locator": {"citekey": "smith2023", "doi": "10.1000/x", "year": "2023"},
          "evidence": [
            {"field": "title", "locator": "smith2023#title", "quote": "逐字题名"},
            {"field": "authors", "locator": "smith2023#authors", "quote": "逐字作者串"},
            {"field": "doi", "locator": "smith2023#doi", "quote": "10.1000/x"}
          ],
          "match_basis": "", "confidence": "not_verified", "rationale": ""
        }
      ],
      "match_decision": "pending",
      "basis": "", "confidence": "not_verified", "rationale": "", "notes": ""
    }
  ],
  "candidate_claims": [],
  "unresolved": [],
  "conflicts": []
}
```

规则：`claim_quote` 必须等于 `claim_locator` 指向的稿件逐字片段；每条 `evidence.quote` 必须等于文献库中该字段的逐字值；脚本预填所有可机械推导的 locator 与 quote，Agent 只补语义字段（`match_basis/confidence/rationale/match_decision`）。不得把 paraphrase 当 quote。冲突条目进 `conflicts` 并标 `source_conflict`。

## 7. 自检清单（逐条对应评分维度）

- [ ] **实际效果·完成度**：交付可编辑稿件 + 同步文献表 + 证据映射 + 一致性报告，覆盖任务要求动作与全部引用点/条目。
- [ ] **实际效果·正确性**：每条引用可定位到文献库真实条目；元数据来自材料；论断与文献匹配。
- [ ] **实际效果·完整性**：全部章节、引用点、条目都被处理，无漏项。
- [ ] **实际效果·可核验性**：`evidence_map`（locator + 逐字 quote）+ `change_log` 能回溯原稿、文献库快照、匹配依据与每步改动。
- [ ] **使用增益**：编号/去重/格式/证据定位由脚本自动完成，人工只做语义确认；同输入重复运行一致。
- [ ] **创新增益**：论断级—证据级两级 evidence_map、编号连锁更新、去重判据可解释、逐句 quote 可复核。
- [ ] **可靠体验**：`--self-test` 与 `--help` 退出码 0；空目录/坏 JSON/坏编码/缺字段不崩溃且如实记录。
- [ ] **生态贡献**：说明与提交一致，脚本/规则/清单可被他人独立复用。
- [ ] 详见 `references/checklist.md`，逐条勾选后才可交付。

## 8. 降级与诚实原则

- 缺文献库/缺稿件 → `status = insufficient_evidence`，`pending_human` 列出缺口，产物用占位符并标明“非真实结果”。
- 坏 JSON/坏编码/解析失败 → 记 `errors`；跳过超限/不支持文件 → 记 `degradations`，继续处理其余材料。
- DOI 解析失败、条目歧义、同名不同文献 → 不自动合并/不声称已核验，标 `not_verified`/`pending_human`/`source_conflict`。
- PDF/DOCX 无运行时渲染/解析工具 → 交付 Markdown 可编辑版并标注 `limitations`；所需工具为运行时 `pdf-text-tool`/`office-renderer`。
- **严禁编造**评测分数、DOI、元数据、检索结果或实验数据；`status` 如实，`ok` 只能在语义核验完成后设置。

## 9. 边界

一个 Skill 只解决“参考文献与引用整理”这一类任务；不代写不存在的实验/数据，不生成虚假参考文献，不做期刊排版模板适配。没有真实材料时输出“材料不足”，而不是示例填充。
