# 自检清单与降级规则

## 1. 收口自检清单（逐条勾选，对应评分维度）

- [ ] **六类要素全部覆盖**：步骤、实验设置、参数、数据集与划分、对照、复现与限制均无空节、无占位空话（结果完整性 5 分）。
- [ ] **数值逐字一致**：正文每个数值/参数/版本与材料逐字一致，无补数、无条件串台（结果正确性 15 分）。
- [ ] **证据可回溯**：每个事实性句子有 `[S#]`，`evidence_map.json` 每项 locator+quote 非空（结果可核验性 5 分）。
- [ ] **成稿可用**：`manuscript.md` 满足题面体裁、受众、格式；任务完成（任务完成度 15 分）。
- [ ] **输出契约完整**：`case_id`、`status`、`sources`、`uncertainty`、`change_log` 齐；`output_contract` 全部字段原样回填（输出契约）。
- [ ] **缺口显式标注**：`MATERIAL GAP` / `pending_human` / `not_reported`，无静默编造（说得住）。
- [ ] **冲突并列记录**：标 `source_conflict`；限制条件指向具体失效场景而非套话。
- [ ] **可编辑产物 + 预览**：主产物可编辑；Office/图产物同时有真实 PDF/PNG 预览。
- [ ] **`validate.py` 通过**：`python3 scripts/main.py validate` 输出 `failed: 0`。
- [ ] **`change_log` 完整**：骨架→初稿→改进→收口逐条追加，可回退。

## 2. 运行前自检（交付者视角）

```bash
cd /skills/weekly-skill
python3 scripts/selftest.py          # 结构 + 空输入/坏 JSON/缺输入/重复运行四类场景
python3 scripts/main.py --help       # 退出码 0
```

selftest.py 会在系统临时目录造最小用例，不写输入目录、不联网。

## 3. 降级策略（诚实优先）

| 场景 | 处置 | 结果要求 |
|-|-|-|
| 空输入目录 / 材料缺失 | `status=insufficient_evidence`，列出缺口，不生成示例答案 | result.json 仍合法 |
| 缺软件（无 pdftotext/Office/渲染器） | 调用题面 pdf-text-tool / office-renderer / ocr-tool；仍失败写 `degradations` | 不影响其它材料 |
| 坏 JSON / 坏编码 | 记录到 `errors`/`degradations`，继续处理其余材料 | 不崩溃 |
| 冲突 / 无来源参数 | 标 `source_conflict` 或 `pending_human`，不自行裁定 | 不补数 |
| 时间或请求耗尽 | 先保证 result.json 合法 + 可编辑初稿 + 证据映射，再增量完善 | 宁交 `partial`，不交编造的 `ok` |
| 写盘失败 | 降级到最小合法产物（至少 result.json + case_id + status） | 不让进程崩溃 |

## 4. 诚实性红线

1. 不编造材料外的数值、引用、DOI、软件状态或科学数据。
2. 不依据错误领域标签补造数据；以实际读取的文件为准。
3. 不把候选事实当核实事实：`fact_model.json` 的候选必须逐条回到原文确认后才进入成稿。
4. `status=ok` 仅在成稿齐全且证据映射齐备时使用；任何未确认项都降到 `partial` 或更低。

## 5. 增量改进的 change_log 规范

每轮改进追加一条，不改写历史：

```json
{
  "at": "2026-11-24T00:00:00Z",
  "phase": "improve",
  "action": "正文 learning_rate 与参数表对齐",
  "affected": ["S1", "参数表"],
  "revertable": true
}
```

写入 `/app/output/result.json` 的 `change_log`，并同步 `/app/work/state.json`。

## 6. 失败模式目录（分享推广价值）

沉淀常见失败模式与处置，供复现与迁移：

| 失败模式 | 信号 | 处置 |
|-|-|-|
| 条件串台 | 训练参数写进验证/测试小节，或 train/val 用同一配置却未确认 | 分开小节，逐条核 locator，无法确认标 `pending_human` |
| 补数 | 段落顺滑而补出材料没有的数值 | 该值改 `not_reported` 并加 `pending_human` |
| 版本含混 | 数据集/软件只写名称不写版本 | 写 `not_reported`，复现清单标注版本依赖 |
| 不可比对照 | 与基线比较却未说明预算/指标/统计方式 | 补同预算、指标、统计方式，否则降为描述性对比并说明 |
| 套话限制 | "可能受限于环境" | 改写为具体失效场景与适用范围 |
| 静默冲突 | 两个来源同指标不一致被忽略 | 两条都记录，标 `source_conflict` |

## 7. 维护与适用边界

- 依赖：仅 Python 标准库（3.8+），无需联网、无需第三方包；PDF 抽取依赖环境中的 pdftotext 或题面 pdf-text-tool。
- 适用：材料形态为代码/配置/日志/数据说明/笔记的 Methods/Experiments 章节写作。
- 不适用：无事实材料的纯生成任务、期刊排版、纯润色、需要跨学科常识推理的综述。
- 版本：见 SKILL.md frontmatter；修改脚本后必须重跑 `scripts/selftest.py`，保持自检与实现同步。

## 8. 脚本接口速查（资源可复用）

所有脚本均为标准库、确定性、`--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`：

| 命令 | 退出码 | 输入 | 输出 |
|-|-|-|-|
| `python3 scripts/main.py skeleton` | 0 | `/input/case.json`、`task.md` | `result.json` 骨架、`state.json`、`plan.json`、`fact_model.json`、空 `evidence_map.json`/`preserved_facts.json` |
| `python3 scripts/main.py inventory` | 0 | `/input/` 全部材料 | `materials_inventory.json`、`fact_model.json`（候选事实） |
| `python3 scripts/main.py validate` | 0 全过 / 1 有失败 | `result.json`、`state.json`、产物 | `validation_report.json` |
| `python3 scripts/preview.py` | 0 / 1 缺稿 | `manuscript.md` | `manuscript.html`、`manuscript.txt` |
| `python3 scripts/selftest.py --root <skill>` | 0 全过 / 1 有失败 | Skill 包目录 | 终端逐项 PASS/FAIL |

`main.py validate` 的 `validation_report.json` 字段：`checks`（name/ok/detail）、`passed`、`failed`、`generated_at`、`case_id`、`status`。收口标准为 `failed == 0`；若仅 `status=partial` 因待人工确认而保留，属诚实结果而非失败。
