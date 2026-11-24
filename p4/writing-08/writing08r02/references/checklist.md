# 自检清单、追问清单与阶段预算

本文件把 SKILL.md 第 4/7 节展开成可复用表格，供写作与收口时逐项核对。

## 1. 阶段预算表（900 秒 / 64 次请求，早落盘优先）

| 阶段 | 时间 | 请求 | 动作 | 落盘产物 | 失败时 |
|---|---|---|---|---|---|
| 0 骨架 | 0–20s | 0（脚本） | `main.py skeleton` | `result.json`(partial) + `state.json` | 仍留合法 JSON |
| 1 发现 | 0–60s | 1–2 | `main.py inventory` + 读题 | `materials_inventory.json` + `extracted_facts.json` | 记录 degradation |
| 2 证据池 | 60–300s | 6–15 | 逐条核实候选 → 六要素归类 | `checkpoint --phase evidence` | 未核实候选弃用 |
| 3 初稿 | 300–600s | 15–30 | 写 `manuscript.md` + 证据映射 | `checkpoint --phase draft` | 缺口标 MATERIAL GAP |
| 4 改进 | 600–840s | 15–30 | 数值对齐、条件核对、补追问 | 每轮 `checkpoint --phase improve` | 保 partial 不编造 |
| 5 收口 | 840–900s | 0（脚本） | `main.py validate` 循环修复 | `validation_report.json` | 如实 status |

原则：前 3 分钟必须完成第 0 步；第 2/3/4 步每轮只做增量修改，`change_log` 只追加不重写。

## 2. 自检逐条勾选（对应评分维度）

| # | 检查项 | 评分维度 | 通过标准 |
|---|---|---|---|
| 1 | 六类要素全覆盖、无空节 | 结果完整性 | 六要素各至少一条有 locator 的事实 |
| 2 | 数值/参数/版本逐字一致 | 结果正确性 | 正文每个数值都能指回 quote |
| 3 | 每句挂 `[S#]`，证据映射齐 | 结果可核验性 | `evidence_map.json` 每条 locator+quote 非空 |
| 4 | 输出契约字段原样回填 | 输出契约 | `validate` 的 `output_contract_fields_present` 通过 |
| 5 | 缺口显式标注 | 说得住 | 无静默补数；`MATERIAL GAP`/`pending_human` 齐 |
| 6 | 冲突并列、限制具体 | 说得住 | `source_conflict` 记录；限制指向失效场景 |
| 7 | 主产物 + 预览存在 | 任务完成度 | `artifacts` 内文件均存在；Office 有 PDF/PNG |
| 8 | `change_log` 完整可回退 | 可靠性 | 骨架→证据池→初稿→改进→收口全记录 |

## 3. 六要素追问清单（缺口标注模板，写入 `pending_human`）

| 要素 | 应追问的具体问题（材料缺失时） |
|---|---|
| 方法步骤 | 该步的判断条件是什么？中间输出写到哪个路径？ |
| 实验设置 | 训练/验证/测试分别用什么种子与重复次数？软硬件版本？ |
| 参数与来源 | 该参数出处（配置文件/日志/作者确认）？单位与有效数字？ |
| 数据集与划分 | 数据集版本与规模？划分比例与随机方式？各子集用途？ |
| 对照实验 | 基线版本？是否同预算/同数据？统计检验方法与 p 值？ |
| 可复现与限制 | 代码版本、环境、命令、数据获取方式？限制的具体失效场景？ |

每条 `pending_human` 写 `{"question": "...", "reason": "材料缺 X", "locator": "..."}`；作者未答复前正文该处写 `MATERIAL GAP` 或 `not_reported`，不自行补数。

## 4. 失败模式 → 处置（可靠体验 / 异常处理）

| 失败模式 | 识别信号 | 处置 |
|---|---|---|
| 空输入/缺材料 | `inventory` 材料数 0 | `status=insufficient_evidence`，列缺口，不生成示例 |
| 坏 JSON/坏编码 | `errors` 非空 | 记录后继续处理其余材料 |
| 缺软件（pdf/Office） | `degradations` 记录 | 改用 pdf-text-tool/office-renderer/ocr-tool |
| 配置冲突/无来源参数 | 两个来源值不同 | 并列 + `source_conflict`，或 `pending_human` |
| 时间/请求耗尽 | 接近 840s 或 60 请求 | 先保合法 result.json + 初稿 + 证据映射，交 `partial` |
| 重复运行不一致 | 结果随机器/顺序变化 | 脚本固定顺序（sorted、确定性 regex），不引入随机数 |

## 5. 最小调用示例（示意，非正式案例）

```bash
# 前 3 分钟：落骨架
python3 scripts/main.py skeleton --input /input --output /app/output --work /app/work
# 发现材料
python3 scripts/main.py inventory --input /input --output /app/output --work /app/work
# 阶段性落盘（第 2/4 步用）
python3 scripts/main.py checkpoint --phase evidence --status partial --note "已核实 N 条候选" --output /app/output --work /app/work
# 收口自检
python3 scripts/main.py validate --input /input --output /app/output --work /app/work
```

（以上为示意流程；正式案例输入由平台提供，不预写答案。）
