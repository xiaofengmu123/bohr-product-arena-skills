---
name: sci-result-transfer-writing
description: 把科研源文（论文/技术报告/数据）转化为面向指定读者与体裁的可编辑稿件（论文、报告、申报材料、科普文章等），同时保留事实、数据、引用、图表编号与论断强度，并产出可逐条复核的证据映射与改动记录。当用户提供“源文 + 目标读者 + 体裁/篇幅”并要求改写、编译、校对、双语或结构化写作时使用。
version: 1.0.0
---

# 科研成果转化写作 Skill

## 1. 角色与何时使用

本 Skill 让 Agent 完成“成果转化写作”：读源文 → 确定读者与体裁 → 改写/编译/校对 → 交付可编辑稿件，同时保住证据、标注边界。核心目标四件事：

- **读得懂**：目标读者无需补背景即可读懂；缩写首次展开、公式行话解释或替换。
- **留得住**：关键结论、数据、单位、样本量、指标口径、图表编号、引用完整保留，删的是冗余不是证据。
- **说得准**：论断强度与源文一致，不把相关写成因果、不把初步结果写成定论，同概念全稿一个说法。
- **接得住**：每处删改都有清单和理由，他人能按记录逐段复核，稿子可继续编辑。

**何时使用**：用户给定了事实底稿/源文并说明目标读者、文体、篇幅或格式，要求产出可直接使用的稿件。**不使用**于：无源材料的凭空写作、只给写作建议不做成稿、一次性问答。

## 2. 输入契约

运行时输入目录为 `/input`，工作目录 `/app/work`，最终产物目录 `/app/output`。

- 必须读取 `/input/case.json` 与 `/input/task.md`；不假设固定键名，先扫描再判断。
- 材料可能是 `.md/.txt/.json/.csv` 或 `.pdf`（PDF 读原文件，不要相信标签里的领域结论）。扫描 `/input` 全部文件；单文件 >2MB 跳过并记入 `degradations`。
- 文本按 `utf-8-sig`→`utf-8`→`latin-1` 容错读取；坏 JSON/坏编码记录后继续处理其余材料。
- `case.json` 可能含 `output_contract`；若含，**原样回填全部键**，不改名、不删未知键。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：

1. `case_id`：来自 case.json（`case_id`/`id`/`challenge_id`）或 task.md；找不到写 `"unknown"`。
2. `output_contract` 的全部字段：逐项给出实际值（主稿件路径、证据映射、改动记录等），缺值给 `null`/`[]` 并在 `uncertainty` 说明。
3. `status`：`ok | partial | insufficient_evidence | input_error`，必须诚实，材料不足不编造。
4. `sources`：逐条材料来源（路径/类型/字节数，可含解析方式）。
5. `uncertainty`：材料缺口、冲突、未确认项、约定与假设。
6. `change_log`：每步操作的时间戳、动作、状态（含骨架→终稿的增量改动）。

主产物要“可打开、可编辑、可复现”：文本稿给 `.md`；表格给 `.csv`；办公文档给 `.docx/.pptx/.xlsx`；图形给可编辑 `SVG` + 真实 `PDF`。Office/幻灯片/图表类产物**必须另渲染真实 PDF/PNG 预览**供审阅。证据映射文件 `evidence_map.json` 每条含 `{fact, locator, quote, status}`，关键数值/引用/图表逐条回溯。

## 4. 执行流程（900 秒 / 64 次模型请求预算）

> 硬规则：**前 3 分钟内必须先落盘一版合法 result.json 骨架**，此后只做增量改进，任何异常都不留残缺产物。

| 阶段 | 时间 | 模型请求 | 动作 | 输入 → 输出 |
|-|-|-|-|-|
| P0 骨架 | 0–2 min | ≤2 | 运行 `python3 -B scripts/scaffold.py --input /input --output /app/output --work /app/work` | 输入目录 → `result.json` 骨架 + `manifest.json` |
| P1 读源与画像 | 2–10 min | ≤6 | 运行 `python3 -B scripts/facts.py`；读 facts/源文，产出受众画像、保留清单、术语表 | facts.json/terms.json → `/app/work/plan.md` |
| P2 成稿 | 10–40 min | ≤30 | 写主稿件 `.md` + `evidence_map.json` + 改动记录；边写边更新 result.json | 源文+plan → 稿件+证据映射 |
| P3 自检 | 40–55 min | ≤8 | 运行 `python3 -B scripts/check.py`；修复 FAIL/WARN | 产物 → check_report.json |
| P4 定稿 | 55–65 min | ≤6 | 终稿、渲染 PDF/PNG 预览、写 status/sources/uncertainty/change_log | 终稿 → 最终 result.json |
| P5 缓冲 | 65–90 min | ≤12 | 重试、长文分段、异常兜底 | — |

模型请求分配建议：发现/规划 ≤8、成稿 ≤30、校验与返工 ≤18、缓冲 ≤12。每次响应含推理 ≤16384 tokens，长文按章节分段成稿再合并。

## 5. 自检清单（对应评分维度）

- [ ] **任务完成度**：产出了完整可编辑稿件，不是建议/提纲/片段。
- [ ] **结果正确性**：`check.py` 无 FAIL；数值/单位/图表编号/引用均能回溯到源文 quote。
- [ ] **结果完整性**：关键结论、数据、限定条件、方法要点、图表引用全部保留。
- [ ] **可核验性**：`evidence_map.json` 每条含 locator + 逐字 quote；改动清单逐段可复核。
- [ ] **术语一致**：同一概念全稿一个说法，缩写首次展开并锁定。
- [ ] **论断强度**：无“相关→因果”“初步→定论”升级；强度词与源文匹配。
- [ ] **稳定性**：同一输入重复运行结果一致（无随机，确定性布局）。
- [ ] **异常处理**：空目录/坏 JSON/缺引用/自相矛盾/涉密未发表均已识别并在 uncertainty 标注。
- [ ] **契约完整**：result.json 含 case_id + output_contract 全部字段 + status/sources/uncertainty/change_log。

## 6. 降级与失败处理

- 材料缺失 → `status=insufficient_evidence`，在 `uncertainty`/`pending_human` 列明缺口，**不编造**数据、引用、DOI 或结论。
- 数值冲突 → 两个来源都记录并标 `source_conflict`；无法判断标 `pending_human`。
- 缺 PDF 文本/缺渲染工具 → 记入 `degradations`/`errors`，尽量用文本降级，标注局限；**不谎称软件状态**。
- 体裁与篇幅冲突、涉密/未发表材料 → 暂停生成，写入 `uncertainty` 并请求人工确认。
- 写盘失败 → 降到最小合法产物（仅 result.json + 说明），不让进程崩溃。

## 7. 最小调用示例（示意，非正式结果）

```bash
python3 -B scripts/scaffold.py --input /input --output /app/output --work /app/work
python3 -B scripts/facts.py --input /input --work /app/work
# Agent 按 plan.md 成稿并写入 /app/output/deliverable.md、evidence_map.json
python3 -B scripts/check.py --input /input --output /app/output --work /app/work
```

示意 result.json（片段，标注为示例）：

```json
{"case_id": "example-000", "deliverable": "deliverable.md",
 "evidence_map": [{"fact": "示例数值", "locator": "materials/source.md#L12",
   "quote": "逐字片段", "status": "verified"}],
 "status": "ok", "sources": [], "uncertainty": ["示例"], "change_log": []}
```

脚本仅依赖 Python 标准库，路径一律相对（`scripts/check.py`）；详细规则见 `references/rewrite-rules.md` 与 `references/verification.md`。
