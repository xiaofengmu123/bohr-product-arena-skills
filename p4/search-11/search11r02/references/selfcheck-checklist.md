# 自检清单（对应评分维度）

提交前逐条勾选；每条与 `scripts/selftest.py` 的自动断言或题目评分维度对应。

## 结构完整性（对应"实际效果·完整性 / 可靠体验·调用与易用性"）

- [ ] SKILL.md 顶层 YAML frontmatter 含 `name`、`description`、`version`，且 `name` 与包目录一致
- [ ] 正文写清：角色与何时使用、输入契约、输出契约、执行流程、自检清单、降级策略、脚本
- [ ] 文件数 5–8 个、总体积 60–140KB、`main.py` < 30KB、脚本仅用 Python 标准库
- [ ] 脚本调用路径用相对路径（`scripts/xxx.py`），实际文件存在且可执行

## 输出契约（对应"实际效果·完整性 / 结果可核验性"）

- [ ] 产出 `/app/output/result.json`，含 `case_id`、`status`、主产物、`sources`、`uncertainty`、`pending_human`、`change_log`
- [ ] `case.json` 的 `output_contract` 全部字段**原样回填**（缺值 `null`/`[]`，不改名、不删未知键）
- [ ] `status` 取值合法且诚实：`ok | partial | insufficient_evidence | input_error`
- [ ] `evidence[]` 每条含 `locator` + 逐字 `quote`；冲突并列标 `source_conflict`
- [ ] `runtime{}` 含 `started_at/finished_at/elapsed_seconds`；`budget{}` 含阶段预算账本

## 时间预算与早落盘（对应"可靠体验·运行稳定性 / 使用增益·效率增益"）

- [ ] 写明总预算 900 秒 / 64 次模型请求，并拆成 P0–P4 阶段预算（含时间窗与请求上限）
- [ ] 规定**前 3 分钟（180 秒）内先落盘 partial result.json**，之后只做增量改进
- [ ] 阶段预算之和 ≤64，预留异常/冲突核验余量
- [ ] `budget` 子命令可打印剩余时间/请求与当前所处阶段

## 异常与降级（对应"可靠体验·异常处理"）

- [ ] 空输入目录：退出码 0，写 `insufficient_evidence` + `pending_human`
- [ ] 坏 JSON/坏编码：记入 `errors`/`degradations`，继续处理其余材料，退出码 0
- [ ] 缺输入目录：退出码 0，写 `insufficient_evidence`
- [ ] 检索无命中/限流/鉴权失败：写 `sources`/`limitations`，标 `partial`，不编造
- [ ] 法律状态冲突/过期：标 `source_conflict` + `staleness_risk` + `pending_human`，不推断"有效"
- [ ] 缺软件/接口不可用：标 `input_error` 或 `partial`，不伪造运行记录或分数

## 证据溯源与授权（对应"实际效果·结果正确性 / 创新增益·方法改进"）

- [ ] 号码规范化：公开号/申请号/优先权号分开，`raw` + `normalized` + `number_type` + `confidence`
- [ ] 同族归并：按共用优先权号/申请号/公开号 union-find，标 `observed` + `exhaustive:false`
- [ ] 法律状态来源优先官方登记页，标注查询日期与滞后风险
- [ ] 数据源许可分级绿/黄/红，逐项写可展示字段与留存期限

## 运行验证（提交前实际执行）

```bash
python3 -B scripts/main.py --help        # 退出码 0
python3 -B scripts/main.py scaffold      # 早落盘（3 分钟内）
python3 -B scripts/main.py finalize      # 校验 + 补 runtime + 生成 report.md
python3 -B scripts/main.py validate      # 必须打印 VALID
python3 -B scripts/selftest.py           # 27 项自检全 PASS，退出码 0
```
