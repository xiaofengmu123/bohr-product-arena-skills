# 自检清单（交付前逐条勾选）

对应评分维度：实际效果（40）、使用增益（25）、创新增益（15）、可靠体验（10）、生态贡献（10）。
FAIL 级必须修复；WARN 级应写入 `uncertainty`、`pending_human` 或正文 `limitations` 说明。运行 `python scripts/main.py check` 与 `python scripts/selftest.py` 可自动核验其中机械部分。

## A. 实际效果（正确性 / 完整性 / 可核验）

- [ ] `result.json` 合法、可被 JSON 解析，含 `case_id` 与题目 `output_contract` 全部字段。
- [ ] 七要素齐全且非空：研究背景、问题定义、目标、技术路线、阶段计划、风险、预期成果各至少一条。
- [ ] 研究问题具体、可证伪；背景为「已知—未知—待验证」缺口，而非文献罗列（立得住）。
- [ ] 目标、技术路线、验证方式、预期成果与研究问题一一对应（对得上）。
- [ ] 阶段计划落在给定周期、经费、数据与人员条件内，里程碑与任务量匹配（排得开）。
- [ ] 风险按数据获取/方法可行性/进度/伦理与合规分类，每条有触发信号、影响范围、备选方案与决策时点（说得清）。
- [ ] 论断忠于材料原文：无凭空术语、无张冠李戴的数字/结论、无材料外新增事实。
- [ ] 每条 `fact`/`summary` 论断带 `locator` + 逐字 `quote`，可在 `sources`/`evidence_map` 中找到且对应。
- [ ] 预期成果考核方式写明指标、样本、对照、时间点，边界写明。
- [ ] `artifacts` 非空且指向真实存在的可编辑文件与预览。

## B. 使用增益（质量 / 效率 / 可验证）

- [ ] 已运行 `python scripts/main.py check`，FAIL=0（WARN 已在正文说明）。
- [ ] 已运行 `python scripts/main.py render`，生成 `proposal.md` 与 `evidence_map.json`。
- [ ] 前 3 分钟内已落盘第一版骨架（`init`），后续为增量改进而非最后一次性重写。
- [ ] `change_log` 记录了关键动作，能还原「骨架 → 成品」过程。
- [ ] 相比不加载 Skill，减少了大纲搭建、章节重写、往返修改与材料核对工作量（以可核验记录说明）。

## C. 创新增益（新增能力 / 方法改进）

- [ ] 每条论断有 `claim_id` + `kind` + `verdict`，而非自由文本堆砌。
- [ ] `evidence_map` 为细粒度扁平索引，`locator` + 逐字 `quote` 可逐条回溯，非口头声称。
- [ ] 已做「排得开」可行性校验：阶段总时长与给定周期比较，超期标 WARN 并给备选里程碑。
- [ ] 已做「对得上」对齐校验：目标数量与问题数量、指标与验证方式对应，未对齐项显式标注。
- [ ] 冲突信息不隐藏：同指标两来源不一致时两版本并列并标 `conflict`，写入 `pending_human`。

## D. 可靠体验（易用 / 稳定 / 异常处理）

- [ ] 脚本仅依赖 Python 标准库，路径为相对路径 `scripts/main.py`，可重复运行、幂等。
- [ ] 空输入目录、坏 JSON、坏编码、缺文件均不崩溃，捕获并记录到 `errors`/`degradations`。
- [ ] 材料缺失/周期过短/指标不可达/学科规范冲突/引用无法获取时，已识别并写入 `uncertainty` 或 `pending_human`。
- [ ] 权限不足或软件缺失时，`status` 如实降级为 `partial`/`failed`，并说明缺口。
- [ ] 模糊请求下先索要课题指南、周期、经费与数据条件，不臆断补齐。

## E. 生态贡献（文档 / 复用 / 分享）

- [ ] `SKILL.md` 写清角色、输入输出契约、执行流程、时间预算、自检与降级。
- [ ] `references/output-contract.md` 定义字段与词表，他人可据此复用。
- [ ] `references/checklist.md` 提供逐条可勾选清单，映射评分维度。
- [ ] 最小调用示例已在说明中给出，且标注为示意输出。
- [ ] 未编造评测分数、软件状态或科学数据；正式测试案例与白板由平台提供。

## F. 评分锚点（自查通过/不通过信号）

将题目四问转成可机械判定的信号，供交付前复核；通过信号不保证满分，未命中任一信号必须回改。

| 题目问法 | 通过信号 | 不通过信号 |
|-|-|-|
| 立得住 | 问题可证伪，背景结尾落在「待验证」缺口，而非综述收尾 | 背景为领域罗列；问题宽泛、无判定条件 |
| 对得上 | 目标数=问题数，每条目标有对应指标与验证方式 | 目标、路线、成果各写各的，无一一映射 |
| 排得开 | 阶段总时长 ≤ 给定周期，里程碑有人员/经费/数据支撑 | 总时长超期；里程碑与任务量不匹配 |
| 说得清 | 每条风险有触发信号+备选+决策时点；论断有 locator+quote | 风险无触发信号；论断无法定位到材料 |

其中「排得开」的机械校验由 `python scripts/main.py check` 自动执行（阶段 `months` 求和 vs case.json 周期）；「对得上」的对齐关系须在 `seven_elements` 的 `claim_id` 前缀上可核验（`pq-*` ↔ `obj-*` ↔ `route-*` ↔ `out-*` 一一对应）。

## G. 自检脚本用法

在 Skill 根目录执行：

```bash
python scripts/main.py init   --case /input/case.json --out /app/output/result.json --work /app/work
python scripts/main.py check  --result /app/output/result.json --case /input/case.json   # 期望 fail=0
python scripts/main.py render --result /app/output/result.json --out /app/output --work /app/work
python scripts/selftest.py    # 空输入/坏 JSON/正常案例三场景自检，期望 SELFTEST OK
```

`check` 退出码：FAIL=0 时返回 0，否则返回 1；WARN 不阻断退出码，但须在正文 `uncertainty`/`limitations` 说明。

