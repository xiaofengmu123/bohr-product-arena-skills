# 规则与检查清单（rules / checklist）

本文是 SKILL.md 第 5 节自检清单的展开，Agent 与复核人均按此核对。评分对应五类维度：
实际效果、使用增益、创新增益、可靠体验、生态贡献。

## 1. 关键参数识别与可行域

- 参数必须来自材料/设备/安全/历史记录中的**逐字事实**，禁止凭领域标签臆测。
- 每个参数给 `name / unit / suggested / min / max / step`，并附 `basis_locator` + `basis_quote`。
- `min/max` 只能来自：设备量程、材料耐温、安全阈值、历史记录上下限；否则写入 `unverified_values`。
- 建议值必须落在 `[min, max]` 内；越界是阻断错误，`check.py validate-plan` 会拦截。
- 互斥关系（如 A 升则 B 必须降）写入 `risks` 与 `exception_handling`。

## 2. 响应指标口径

- 每个指标必须含：口径 `definition`、单位 `unit`、测量 `method`、方向 `direction`、
  目标 `target`、可接受下限 `accept_lower`。
- 必须披露 `prediction_method`（历史拟合 / 代理模型 / 经验规则）与 `uncertainty`；
  不写"精确"二字。
- 指标与参数的关系判断要有依据；没有依据就写 `pending_human`，不得编造相关系数。

## 3. 搜索策略

- 必须写明：`method`（网格 / 序贯 / 贝叶斯 / 分阶段筛选）、`initial_points`、
  `per_round`、`stop_criteria`、`candidate_count`、`seed`。
- 候选点必须可由 `python scripts/main.py doe --seed 0` 复现；不手写无法复现的点表。
- 每轮补点如何利用上轮结果，写进 `per_round`；停止判据必须可判定（不是"差不多了"）。

## 4. 成本与时间预算

- `cost_budget.items` 每项给 `unit_cost / quantity / total / basis`（机时、耗材、样品数、通量）。
- `total` 与 `time_hours` 必须与各 item 自洽；口径要写清（如"按预约价 50/h"）。
- `tradeoff_note` 必须写明"牺牲多少可接受"，例如"允许转化率降 5 个百分点换一半机时"。

## 5. 风险与异常处置

必须逐项覆盖以下情形，不能只写"注意安全"：

| 情形 | 处置 |
|---|---|
| 无可行解 | 报告并转人工，不改约束硬凑 |
| 约束冲突（目标温度 vs 材料耐温） | 记录双方值，给出降级选项或转人工 |
| 数据缺失 | 写 `missing_data`，status 降为 partial/insufficient_evidence |
| 设备报错 | 写 `degradations`，跳过或降级采样 |
| 指标互相矛盾 | 两个都记录并标 `source_conflict`，不隐藏 |

## 6. 证据溯源

- 每个关键结论/数值配 `{locator, quote, claim}`；`quote` 为逐字引用，禁止改写。
- `locator` 用 `materials/<file>#<定位>` 格式；PDF 用平台 pdf-text-tool 读原文，不假设存在预提取答案层。
- 冲突信息两条都记录并标 `source_conflict`；缺失写 `not_reported` 或进 `missing_data`。

## 7. 确定性清单

- [ ] 产物不含墙钟时间；change_log 用固定 step 标识。
- [ ] 所有 JSON 由 `write_json` 写出（sort_keys + LF）。
- [ ] `doe` 固定 seed，参数按名称排序。
- [ ] `finalize` 为纯函数：同输入两次输出逐字节一致（`selftest.py` 验证）。
- [ ] `input_fingerprint` 已写入 result.json，两次运行指纹相同。
- [ ] 不把未经确认的数值写成可执行事实。

## 8. 自检脚本（提交前运行）

```bash
python -B scripts/selftest.py          # 退出码 0
python -B scripts/main.py --help       # 退出码 0
python -B scripts/check.py --help      # 退出码 0
```

selftest 覆盖：demo 全链路 + 确定性对比、空输入、坏 JSON、缺输入四种场景。
