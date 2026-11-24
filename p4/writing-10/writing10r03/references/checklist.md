# 自检清单（对应评分维度）

本清单由 `scripts/selftest.py` 机械化校验一部分（代码列注明 C1–C7），其余为 Agent 在写作时逐条勾选。

## 实际效果（40 分）

- [ ] 每条意见进入 `response_ledger`，一条意见内的多个子要求全部拆出且各有处置结论（C2 覆盖校验）。
- [ ] 每条 `accept|partial` 有具体 `action`，改后锚点能在修改稿中被检索到（C3）。
- [ ] 需补实验/数据/文献/重分析的意见有 `evidence`（出处+到位状态），无法补做时写明原因与替代说明（C4）。
- [ ] `fact_source_mapping` 覆盖所有数字/术语/引用/关键论断；`traced` 项有逐字 `quote`，不新增材料外事实（C5）。
- [ ] 回复信、修改稿、`version_boundary.changes` 引用同一版本与同一批定位，无孤儿 change（C6）。
- [ ] 语气四档（accept/clarify/rebut/appeal）使用恰当：无冒犯性表达、无过度承诺、无无原则顺从。

## 使用增益（25 分）

- [ ] 意见台账由 scaffold 预填，Agent 从"逐条核对"开始而非从零拆解，减少漏答与往返。
- [ ] 提供可复跑的对照方式：同输入下 `main.py` + `selftest.py` 两遍运行，`content_hash` 与 canonical 骨架一致（C7）。
- [ ] 每个 FAIL/WARN 给出具体 code 与定位，能直接指导修正，而非只报"通过/失败"。

## 创新增益（15 分）

- [ ] 意见拆解 + 逐条回应 + 修改定位 + 证据清单 + 语气 + 版本一致性，沉淀为可机器校验的 `response_ledger` 结构。
- [ ] 定位用「原文锚点→改后锚点」而非行号，改稿后仍可检索；这是行号定位不具备的稳定性。
- [ ] 确定性指纹（content_hash）与 canonical 比对，使"要素是否漂移"可客观判读。

## 可靠体验（10 分）

- [ ] 缺材料先写 `version_boundary.material_gaps` 与 `uncertainty`，再按显式假设推进。
- [ ] 意见冲突记录双方并标 `source_conflict`；意见本身有误时用 clarify/rebut 并给证据，不隐去、不盲从。
- [ ] 长稿件/多意见/多轮返修下 comment_id 与台账条目数稳定，重复运行不漂移。
- [ ] 坏 JSON/坏编码/空目录/写盘失败均降级为合法结果或明确错误，不崩溃、不编造。

## 生态贡献（10 分）

- [ ] SKILL.md 写明用途、适用边界、调用方式、输入输出、异常处理；版本与提交一致。
- [ ] 台账字段、意见分类法、语气四档、定位脚手架、自检表可直接复用（见 `elements.md`）。
- [ ] 脚本纯标准库、相对路径、`--demo` 可独立验证；不依赖个人文件或手工操作。

## 执行前必查

- [ ] 已读 `/input/case.json` 与 `/input/task.md` 原文，未按文件名臆测内容。
- [ ] 3 分钟内已落盘 result.json 骨架（`main.py`），`status=in_progress`。
- [ ] 每次修改追加 `change_log`、更新 `content_hash`，最终跑 `selftest.py` 并修正 FAIL。
