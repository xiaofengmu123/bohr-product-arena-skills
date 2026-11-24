# 自检清单（对应评分维度，提交前逐条勾选）

收尾前在 Skill 根目录执行：

```bash
python3 scripts/main.py --help        # 退出码 0
python3 scripts/selftest.py           # 必须打印 ALL CHECKS PASSED
python3 scripts/main.py validate      # 必须打印 VALID
```

## A. 输入契约与早落盘（可靠体验 · 运行稳定性）

- [ ] 扫描 `/input` 而非假设固定文件名：`case.json`、`task.md`、`materials/`、PDF 均能识别
- [ ] 文本按 `utf-8-sig` 容错读取；单文件 2MB 上限，超限跳过并记录
- [ ] 坏 JSON / 坏编码记录到 `errors`/`degradations`，继续处理其余材料，不崩溃
- [ ] **前 3 分钟内已落盘一版合法 partial `result.json`**（`scaffold` 即落盘），之后只做增量改进

## B. 输出契约（实际效果 · 结果完整性）

- [ ] `result.json` 含 `case_id` + `status`（`ok|partial|insufficient_evidence|input_error`）
- [ ] `case.json` 的 `output_contract` 全部字段**原样回填**（缺值 `null`/`[]`，不改名、不删未知键）
- [ ] 主产物齐全：`patent_records / families / legal_status / literature / authorization`
- [ ] 元信息齐全：`sources / evidence / uncertainty / pending_human / errors /
      degradations / limitations / change_log / runtime`
- [ ] 未伪造分数、文献、号码、状态或运行记录

## C. 中间表示与可复核（创新增益 · 结果可核验性）

- [ ] 检索行动前已写 `/app/work/plan.json`（研究问题、对象类型、数据源分级、五问、预算护栏）
- [ ] 号码规范化结果已写 `/app/work/records.json`（raw + normalized + number_type + verified）
- [ ] 结论-证据对齐已写 `/app/work/evidence_map.json`（locator + quote + 冲突清单）
- [ ] 中间产物已登记进 `result.json` 的 `intermediate_artifacts`（path + sha256 + size）
- [ ] 他人可按 `sources[]` 的 engine/query/url/retrieved_at 复跑检索

## D. 证据溯源（实际效果 · 结果可核验性）

- [ ] 每个关键结论都有 `locator` + 逐字 `quote`
- [ ] `quote` 与材料原文逐字一致；OCR 粘连处附 `ocr_uncertain`，不脑补干净文本
- [ ] 冲突信息并列记录并标 `source_conflict`，不静默择一

## E. 字段口径正确性（实际效果 · 结果正确性）

- [ ] 公开号 / 授权号 / 申请号 / 优先权号区分正确，无混用
- [ ] 同族口径为 `observed` 且 `exhaustive=false`；不把 observed 族当完整族
- [ ] 法律状态只采用官方登记来源；官方未知标 `unknown` + `staleness_risk`，不推断"有效"
- [ ] 权利要求逐字引用，不越界概括；同族成员权利要求不互相替用

## F. 授权边界（实际效果 · 结果完整性）

- [ ] `authorization[]` 逐源记录 `source / license_basis / tier / displayable_fields / retention`
- [ ] 绿/黄/红分级正确；red 级不登录、不抓取、不展示原文，只写降级方案

## G. 异常处理（可靠体验 · 异常处理）

- [ ] 空输入 → `insufficient_evidence`，`pending_human` 列缺口，退出码 0
- [ ] 缺输入目录 → 退出码 0，产物合法
- [ ] 检索无命中 / 限流 / 鉴权失败 → 如实写 `sources` 与 `limitations`，标 `partial`，不补造
- [ ] 写盘失败 → 降级为最小合法 result.json，进程不崩溃
- [ ] 同一输入两次运行结果一致（确定性，无随机数）
