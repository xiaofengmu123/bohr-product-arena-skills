# 输出契约对齐规范（本变体核心）

本规范定义 `result.json` 与题目 `output_contract` 逐字段对齐的语义，
供 Agent 与 `scripts/contract.py` 共同遵守。目标是让"契约是否完整"可机读、
可验证、可复核，而不是凭感觉。

## 1. 契约发现

- 契约藏在 `case.json` 中，键名不固定：递归匹配
  `output_contract / outputContract / contract / result_contract /
  result_schema / expected_output / expectedOutput`（大小写与下划线/连字符不敏感）。
- 多个候选时优先"浅层 + 叶节点多"：输出契约通常在顶层，浅层优先可避免误取
  嵌套的无关 schema。
- 契约本身必须是 dict；若值是 list 或标量则不算契约，继续搜索。
- 未发现契约时 `_contract_meta.found=false`，使用最小字段集兜底并记
  `uncertainty.pending_human`。

## 2. 叶节点类型与占位

`contract.flatten` 把契约递归展平为叶节点，三类：

| 类型 | 判定 | 占位值 |
|-|-|-|
| `scalar` | 值为非容器（str/number/bool/null） | `null` |
| `array` | 值为 list | `[]` |
| `object` | 值为空 dict `{}` | `{}` |

- 数组内部元素结构未知，不逐元素展开（避免编造元素数量）。
- 空 dict 视为一个 object 叶；非空 dict 递归展开其键。

## 3. 回填语义（backfill）

- `init` 阶段把契约全部叶节点写进 `result.json`，值用上述占位。
- 回填**只补缺失叶与类型不符叶**，绝不覆盖 Agent 已写入的非空值——这是"增量改进"
  的关键保证：Agent 每填一个字段，后续 update 不会把它重置回 null。
- 类型不符的处理：array 叶写成标量 → 重置为 `[]`；object 叶写成标量 → 重置为
  `{}`；scalar 叶写成容器 → 不重置（视为 Agent 有意提供结构化内容），只记录 issue。

## 4. 校验（validate）

- `check --require-contract` 逐叶校验：缺失（missing）、数组叶非 list
  （expected_array）、对象叶非 dict（expected_object）、顶层多出的键（extra_key，
  保留键除外）。
- 校验关心**结构存在性**，不要求值非空——`null` 是"未填但已声明"的合法状态；
  填充进度由 `fill_report` 单独统计。

## 5. 填充进度（fill_report）

- `{total, filled, missing}`：非空判断为值非 `null` 且非空 list/dict。
- `contract-report` 子命令打印该进度并附 `complete = (total == filled)`。
- 阶段 C 的目标是 `complete == true`；若某叶确无法取值，保留 `null` 并在
  `uncertainty` 说明，不允许用编造值凑满。

## 6. change_log 与 contract_delta

- 每次 `update --log` 追加一条 `{ts, seq, step, action, files, contract_delta}`。
- `seq` 从 1 递增、唯一；`contract_delta` 自动记录该步后的 `fill_report` 快照，
  使"哪一步推进了哪些契约字段"可审计。
- 保留键与本规范定义的键同名冲突时，以本规范语义为准并在 `uncertainty` 注明。

## 7. 契约完整性自查

```bash
cd /skills/weekly-skill
python3 scripts/main.py contract-report --input /input --output /app/output
# 期望：{"total": N, "filled": N, "missing": [], "complete": true}
python3 scripts/main.py check --input /input --output /app/output --require-contract
python3 scripts/selftest.py
```

`selftest.py` 内置契约对齐电池：空目录、坏 JSON、契约回填全叶、update seq 递增、
check --fail 退出码、确定性重跑、无契约兜底，交付前必须 SUMMARY ALL PASS。
