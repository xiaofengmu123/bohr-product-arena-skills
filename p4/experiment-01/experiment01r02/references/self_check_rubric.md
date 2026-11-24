# self_check_rubric：评分维度 → 检查项 → 常见失败与修复

把题面 5 个评分维度映射成可勾选的检查项、对应脚本/人工检查与常见失败。本文件既是自检清单的细则，也是他人可复用的对照清单。

## 1. 实际效果（40：完成度 15 / 正确性 15 / 完整性 5 / 可核验 5）

| 检查项 | 怎么查 | 常见失败 → 修复 |
|-|-|-|
| 目标是可检验假设而非复述 | 读 `plan.hypothesis_and_variables.hypothesis`，须有方向与可证伪表述 | 复述目标 → 拆成“变量 + 预期方向 + 判读标准” |
| 变量有操作定义/单位/量程 | 逐条检查 `variables[]` 四要素齐全 | 只写名字 → 补测量方式/单位/range |
| 每个对照排除哪类混杂 | `controls[].confounders_excluded` 非空且与 type 匹配 | 只有空白组 → 按目标补阳性/剂量梯度 |
| 参数在量程内、来源真实 | `validate_result.py` 算术项全绿；`source` 非空 | 越界 → 裁剪参数或改 allowed 区间并给依据 |
| 六要素均非空 | `validate_result.py` completeness 检查 | 空段 → 回填或 status 降级 |
| 证据带 locator+quote | 检查 `evidence_map[]` 每项 | 无引用 → 回材料逐字摘录 |
| 版本/change_log 可复核 | `change_log[]` 非空且单调 | 无日志 → 补 skeleton_written + 每次改动一条 |

## 2. 使用增益（25：质量 15 / 效率 5 / 增益可验证 5）

- 质量增益：对 3 个候选方案输出**量程/预算/排期比较表**（plan.md 或 `candidate_comparison`），逐项给“采纳/否决/合并”结论与理由。
- 效率增益：早落盘 + 增量写回，避免返工；骨架即合法 partial，无需重写结构。
- 增益可验证：候选比较表每行带来源定位；`selftest.py` 三态通过 + `validate_result.py` 报告作为可复核证据。

常见失败：候选方案只并列不复判 → 逐项算术比较并写取舍；比较无据 → 每行带 locator。

## 3. 创新增益（15：新增能力 5 / 方法改进 5 / 创新证据 5）

至少落实一类，并在 plan.md 显式标注：
- 按研究目标反推对照设置（正/阴/剂量梯度与混杂一一对应）。
- 按设备量程与预算裁剪参数（越界即裁剪并给替代）。
- 生成风险预案与替代路径（失败模式→判据→止损点→替代路径成链）。
- 本版新增：**时间盒账本 + 截止线治理**，把 900s/64 请求拆成阶段预算，剩余 <120s 强制收尾。

常见失败：创新只写空话 → 落到可执行条目；无证据 → 引用材料片段或算法报告。

## 4. 可靠体验（10：易用 3 / 稳定 4 / 异常 3）

- 易用：`SKILL.md` 第 2/3/4 节可按步骤跑通；`selftest.py` 退出码 0。
- 稳定：空输入 → `insufficient_evidence` 不崩溃；坏 JSON → `input_error` 继续；重复运行一致。
- 异常：输入缺失/约束冲突/依赖不可用/内容不确定 → 写 `pending_human` 或 `uncertainty`，不静默。

常见失败：空输入崩 → 脚本捕获所有异常；坏 JSON 中断 → 记录 errors 后继续；缺货/排期满不识别 → 从合同读可用性并标 pending_human。

## 5. 生态贡献（10：文档 3 / 复用 4 / 分享 3）

- 文档：SKILL.md 含角色、输入/输出契约、执行流程、自检、降级、示例、限制。
- 复用：本文 + `plan_schema_and_evidence.md` + 脚本三件套可直接套用到其他“生成类”科研任务。
- 分享：标注 version、维护说明、不适用边界；不预写正式测试答案。

## 6. 提交前硬核对（对应运行时契约）

- [ ] frontmatter 有 name / description / version。
- [ ] result.json 含 case_id、status、主产物字段、sources、uncertainty、pending_human、change_log。
- [ ] 总预算 900s / 64 请求 / 16384 tokens 已写明，前 3 分钟早落盘已写明。
- [ ] 缺数据/缺软件如实标 `not_reported`/`degradations`，不编造。
- [ ] 每个关键结论有 locator + quote。
- [ ] 文件 5–8 个、总体积 60–140KB；脚本仅标准库。
