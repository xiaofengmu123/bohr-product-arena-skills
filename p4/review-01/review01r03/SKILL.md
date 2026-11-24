---
name: paper-quality-review
description: 论文质量审查 Skill。把“这篇稿子哪里有问题”转成结构固定、可定位、可交办的问题清单：按事实核验、引用支持、方法设计、统计报告、论证链、学术规范六类分面逐条审查，每条给出原文定位与逐字引用、严重度、问题性质、判断依据、修改建议与证据映射；产出可复核的 result.json 与 review_report.md。强调确定性——同输入两次运行主产物字节一致（易变量只进 runtime），并内置 scripts/selftest.py 自检。适用于投稿前自审、审稿、结题/可发表性检查。
version: 1.0.0
---

# 论文质量审查 Skill

## 1. 角色与何时使用

本 Skill 负责对给定论文/科研材料做**逐条、可定位、可复核**的质量审查，交付问题清单而非整篇评语。适用：投稿前自审、审稿、结题前检查，以及事实/引用/方法/统计/逻辑/规范的一致性核验。

不适用：润色改写、语言翻译、查重比对、投稿排版等事务性工作；不覆盖本稿之外的领域判断。

## 2. 输入契约（评测环境事实）

- 原始输入固定为 `/input/case.json` + `/input/task.md`；科研材料（PDF、DOCX、TXT/MD、图片、表格、JSON 等）也在 `/input/` 下，**不要假设固定文件名**，脚本递归扫描发现。
- `case.json` 可能含 `case_id`、`task_instruction`、`output_contract`（dict 或 list）；键名不保证固定。
- 工作目录 `/app/work`，最终产物写 `/app/output/`（必须含 `result.json`）。
- 预算：**900 秒、64 次模型请求、单次回复 ≤16384 词元**；780 秒必须 finalize。
- 脚本只依赖 Python 标准库；`scripts/main.py` 用相对路径调用，不硬编码安装路径。平台另提供 pdf-text-tool / ocr-tool 等，按需调用。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层含：

1. `case_id`：原样保留 case.json 的 case_id。
2. **output_contract 全部字段原样回填**：不改名、不删除；未知键写 `null`/`[]`。
3. 信封字段：`status`、`sources`、`uncertainty`、`pending_human`、`errors`、`degradations`、`changes`、`change_log`、`runtime`。
4. 主结构 `result.findings[]` + `result.unchecked_items[]` + `result.verdict_summary`（含 counts 与 coverage）。

`status` 取值：`ok`（六类相关项已查并有结果）/ `partial`（部分完成，写明缺口）/ `insufficient_evidence`（材料不可用）/ `input_error`（坏 JSON 等）。单条 finding 形状、枚举与字段说明见 `references/output-schema.md`，判据见 `references/review-checklist.md`。

主产物 `review_report.md` 由 `build` 生成，逐条列 finding 与定位/引用，供他人回到原文复核。

## 4. 执行流程（分步骤、每步输入输出、时间预算）

**脚本不替你阅读理解**；必须先读材料、写工作稿，禁止只跑 build。命令序列：

| 步骤 | 命令 | 输入 | 输出 | 时间预算 |
|---|---|---|---|---|
| 0 落盘骨架 | `python3 scripts/main.py init` | `/input` 全部文件 | `/app/work/index.json` + **合法 partial `result.json`** | **≤3 分钟（前 3 分钟内必须落盘）** |
| 1 通读 | `show` + 读 case.json/task.md/材料原文 | 步骤 0 索引 + 原文 | 理解检查项、材料、六类 facet | 3–10 分钟 |
| 2 分面审查 | 亲自推理，写 `/app/work/draft.json` | 材料原文 + 六类判据 | `draft.json`（findings/unchecked/verified_facets/verdict） | 10–13 分钟 |
| 3 机械校验 | `python3 scripts/main.py check` | `draft.json` + 索引 | `check_report.json`（quote 命中/字段/枚举/覆盖） | 13–14 分钟 |
| 4 成稿 | `python3 scripts/main.py build` | `draft.json` + 索引 | `review_report.md` + 最终 `result.json` | 14–15 分钟 |
| 5 复检 | `python3 scripts/main.py selfcheck` | 最终产物 | `selfcheck.json`（仅复检，不重做） | 可选 |

要点：

- **步骤 0 用 init 先落盘一版合法 result.json 骨架**；此后任何失败都不会留空产物，之后只做增量改进。
- 步骤 1 必须读 case.json 的 `output_contract` 与 `task_instruction`，确认六类 facet 中哪些与本稿相关。
- 步骤 2 是主工作：逐 facet 排查，每条写一条 finding；`quote` 必须是原文逐字片段，先 `show` 抄录；“查过且无问题”的 facet 写进 `verified_facets`，未查的写 `unchecked_items`。
- 步骤 3 报 `quote not found verbatim` 就回原文重抄，直到无 FAIL。
- 接近 780 秒立即 `build`，未完成部分写进 `unchecked_items`/`pending_human` 并置 `status=partial`。

### 确定性要求（本版强化）

- 同一输入重复运行，剥离 `runtime` 后 `result.json` 必须**字节一致**；finding 顺序由 build 按 `(facet, finding_id)` 排序，不依赖手写顺序。
- draft 中**不得**写时间戳、随机数或依赖运行序的字段；易变量只进 `runtime`。
- 交付前运行 `python3 scripts/selftest.py`，其第 5 项会实际验证两次运行字节一致。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] 实际效果：`result.json` 是结构化问题清单，每条 finding 有 facet/severity/issue_type/location/quote/rationale/revision/confidence/evidence；不是整篇评语或复述。
- [ ] 结果正确性：每条 quote 已在 `check` 中逐字命中原文；severity/issue_type 与判据一致；无凭空结论、无误报。
- [ ] 结果完整性：六类 facet 都处理——有 finding、`verified_facets` 或 `unchecked_items`（并说明原因），`counts`/`coverage` 齐。
- [ ] 可核验性：case_id、来源、locator、逐字 quote、判定依据、sources 齐全，他人能回到原文逐条核对。
- [ ] 使用增益：同输入重复运行 `findings`/`counts`/`coverage` 一致（易变量只进 `runtime`）。
- [ ] 可靠体验：`--help` 退出码 0；空输入/坏 JSON/缺材料/坏 PDF 均退出码 0 且产出合法 `result.json`。
- [ ] 生态贡献：SKILL.md 引用的每个相对路径存在；脚本仅标准库；无 `__pycache__`/`.pyc`；无预制答案。
- [ ] 确定性：`python3 scripts/selftest.py` 全部 PASS（含两次运行字节一致）。

## 6. 降级策略（缺数据/缺软件必须如实标注，不编造）

| 场景 | 处置 | 落点 |
|---|---|---|
| PDF 无文本层/扫描件 | 优先调平台 pdf-text-tool 把逐页文本（页间 `\f`）存 `/app/work/text/<source_id>.txt` 后重跑 `init`；仍不可读则 `insufficient_evidence`，不猜内容 | `degradations` + `pending_human` |
| 图片/图表无 OCR | 调 ocr-tool；失败则列缺口，不猜数值 | `pending_human` |
| 参考文献缺失/引用无法核验 | 列 `citation_support` 为未检查，注明“无法核验”，不臆断被引文献主张 | `unchecked_items` + `uncertainty` |
| 原文歧义/两处数值冲突 | 两处都记录并标 `source_conflict`，不擅自取舍 | `uncertainty` |
| 材料超长（单文件 >2MB） | 跳过并记录；不截断后假装全查 | `degradations` |
| 写盘失败/异常 | 顶层捕获、退出码恒 0，保留最小合法 `result.json` | `errors` |
| 缺软件（无渲染/OCR 等） | 如实写 limitations，不伪造软件状态；不伪造实验数据与评测分数 | `degradations` |

原则：材料不足就写 `insufficient_evidence`/`partial` 并给缺口，**绝不编造数据、引用、DOI 或结论**；不确定项一律 `to_review` 而非 `confirmed`。

## 7. 参考文件

- `references/review-checklist.md`：六类 facet 检查锚点、严重度/问题性质判据、误报抑制规则、确定性规则。
- `references/output-schema.md`：`result.json` 完整字段规格与 finding 字段说明。
- `scripts/selftest.py`：离线自检，覆盖契约、健壮性与确定性。

## 8. 最小调用示例（示意，非正式案例结果）

```bash
# 1) 扫描并落盘骨架（前 3 分钟内）
python3 scripts/main.py init
# 2) 打印带 locator 的原文，供逐字抄录 quote
python3 scripts/main.py show --source D1
# 3) Agent 写 /app/work/draft.json，然后机械校验
python3 scripts/main.py check
# 4) 确定性成稿
python3 scripts/main.py build
# 5) 交付前自检
python3 scripts/selftest.py
```

示意 `result.json`（截断，仅示结构，**示例**）：

```json
{
  "case_id": "demo-001",
  "status": "partial",
  "result": {
    "findings": [{"finding_id": "F01", "facet": "method_design",
                  "severity": "major", "quote": "……逐字原文……"}],
    "unchecked_items": [{"facet": "citation_support", "reason": "无参考文献"}],
    "verdict_summary": {"overall_verdict": "to_review", "counts": {}, "coverage": {}}
  },
  "sources": [], "uncertainty": [], "change_log": "……", "runtime": {}
}
```
