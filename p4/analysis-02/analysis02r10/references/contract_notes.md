# 字段级契约、复现要求与降级规则（contract_notes）

## 1. result.json 字段级契约

- 顶层 = `case_id` + `output_contract` 全部字段（逐键回填，缺值 `null`/`[]`）+ 信封。
- `case_id` 从 `/input/case.json` 原样复制；缺失写 `"unknown-case"`。
- `output_contract` 可能是 dict（key→spec）或 list（[{name/key,type}]）；脚本按两种形态
  都能回填。类型推断：list/array→`[]`，object/map/dict→`{}`，其余→`null`。
- 信封键固定：`status`、`sources`、`uncertainty`、`change_log`、`errors`、
  `degradations`、`pending_human`、`runtime`。契约键若与信封键冲突，保留信封并在
  `_contract_collisions` 记录。
- `status` 取值：`ok` / `partial` / `insufficient_evidence` / `input_error`。
- `sources[]` 元素：`{source_id, path, kind, rel_path}`。
- `change_log[]` 元素：`{id, time, action, before, after, reason}`。

## 2. 复现要求

- 脚本只依赖标准库；调用前 `cd /skills/weekly-skill`。
- 相对路径调用：`python scripts/main.py <stage>`；缺省 `--input /input`、`--output
  /app/output`、`--work /app/work`。
- 确定性：全流程无随机数；同一输入两次运行得到相同数字（时间戳除外）。
  交付自检：`python scripts/selftest.py`（退出码 0）。
- 中间表示（IR）依次为 `index.json → schema.json → plan.json → analysis.json`，
  每步可单独重跑；换数据只改 `plan.json`，不重写代码。
- 证据溯源：关键数值在 `evidence_map` 中带 `locator`（文件|列|行范围）+ `quote`
  （表头与样本行逐字片段）。

## 3. 降级规则（诚实优先）

| 触发 | 状态 | 说明 |
|---|---|---|
| 无数据文件 / 表为空 | `insufficient_evidence` | uncertainty 列缺口，不给统计结论 |
| case.json 坏 JSON/坏编码 | `input_error` 或 `partial` | errors/degradations 记录，case_id 写 unknown-case |
| 单个表/字段坏 | `partial` | 处理其余材料 |
| 组内方差 0 / n<2 / 缺失过高 | 对应检验 `applicable:false` | 不给 p，写 reason |
| 单位不明 | `unit_unknown` | 不猜单位 |
| 脚本不可运行 | 手写骨架 result.json | 退出码恒 0，宁可 partial 不零产物 |
| 2MB 单文件上限 | 跳过并记录 | 不崩溃 |

## 4. 评分维度映射（对题目的 5 维 100 分）

| 维度 | 本 Skill 的对应实现 |
|---|---|
| 实际效果（40） | 七类情形全处理；t/F/χ² 用标准库特殊函数算精确 p；效应量+CI+解释；IR 与 cleaned CSV、evidence_map 支持复算 |
| 使用增益（25） | 声明式计划把“任务解读”与“计算”解耦；`all` 一键运行；确定性减少重算/核对 |
| 创新增益（15） | plan.json 中间表示 + 逐字证据模型 + 按分布自动选检验，非“换说法包装同一套计算” |
| 可靠体验（10） | 空输入/坏 JSON/缺软件均降级不崩溃；selftest.py 覆盖四类边界 |
| 生态贡献（10） | stats_checklist.md 可复用的检验选择表；contract_notes.md 字段级契约；stdlib 无依赖 |

## 5. 交付清单核对

- [ ] `outputs/SKILL.md`（frontmatter 含 name/description/version；正文 ≤180 行）
- [ ] `outputs/scripts/main.py`（薄入口，<30KB，转发 pipeline.py）
- [ ] `outputs/scripts/statlib.py`（标准库统计库，特殊函数+t/F/χ²+检验+效应量+CI）
- [ ] `outputs/scripts/pipeline.py`（probe/skeleton/infer/plan/compute/package/verify/all）
- [ ] `outputs/scripts/selftest.py`（自检，退出码 0）
- [ ] `outputs/references/stats_checklist.md`、`outputs/references/contract_notes.md`
- [ ] 总文件数 ≤14，总体积 ≤1.2MB；不预写正式测试产物、不伪造分数
