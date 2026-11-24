---
name: weekly-skill
description: >-
  生物机制图与通路图科研绘图 Skill：从科研材料提取实体、关系、数据与图面约束，
  生成分层可编辑 SVG 与真实 PDF/PNG 导出件，产出可复核 result.json（含 case_id、
  评分维度对齐、证据溯源、不确定性、修改记录），全程可降级、可复现。
version: 1.1.0
---

# 生物机制图与通路图绘图 Skill

## 0. 本版设计取舍（区别于常见绘图 Skill）

- 评分维度对齐是一等公民：result.json 内置 `rubric_alignment`，把题目五个评分维度逐条映射到流程步骤与自检项；每条实体/边/图例都带所属维度标签。
- 证据是 schema 级硬约束：断言类 entity/relation/data_mapping 必须带 `sources[{locator, quote}]`（逐字引用）；校验脚本对“已断言却无引用”判为阻断（exit 1）。
- 确定性优先：布局由 spec 顺序 + 区室分层唯一决定，同输入重复运行产物字节级一致（可靠体验计分）。
- 骨架即产品：`init` 在 3 分钟内落盘 partial，其后所有步骤只做增量改进。

## 1. 角色与何时使用

把科研材料（论文段落、图注、数据表、PDF）转成可逐条质疑、可编辑、可复核的生物机制图/通路图，用于机制示意图、信号通路、流程图。不做统计分析、实验设计、论文排版美化。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例元数据）+ `/input/task.md` + 按 topic 提供的科研材料/PDF。
- 必须实际读取材料内容，禁止按文件名臆测；PDF 用平台 `pdf-text-tool`/`pdf-render-tool` 取文本，不要假设存在预提取答案层。
- 工作目录 `/app/work`；最终产物写入 `/app/output/`。
- 预算：900 秒、64 次模型请求、单响应含推理 ≤16384 token；三个案例各独立运行。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段定义见 `references/output_contract.md`，至少包含：

- `case_id`、`status`（ok / partial / insufficient_evidence / input_error）
- `editable_source_files`（分层可编辑 SVG）、`export_artifacts`（PDF/PNG，含尺寸、分辨率、字体、色彩检查）
- `entities` / `relations` / `legend` / `data_mappings`（结构化机制规格，与 spec.json 一致，含 sources）
- `sources`（聚合 locator + 逐字 quote）、`uncertainty`、`pending_human`、`change_log`
- `rubric_alignment`（评分维度对齐）、`limitations` / `degradations` / `errors`、`runtime`

## 4. 执行流程（900 秒 / 64 请求）

### 阶段 A：0–3 分钟（≤8 请求）——先落盘骨架，不得跳过

1. 读 `/input/case.json`、`/input/task.md`，用 `pdf-text-tool` 读 PDF 文本。
2. 运行 `python scripts/main.py init`：扫描输入、按词法保守抽取候选实体/关系（全部 `inferred:true`），写 `/app/work/spec.json` 与 `/app/output/result.json` partial 骨架（change_log 首条“骨架落盘”）。

### 阶段 B：3–10 分钟（≤30 请求）——抽取与建模迭代

3. 逐条把候选实体改成断言：命名规范（基因斜体、蛋白正体、物种前缀、同义名归并、复合物/家族不混用），每条补 `sources[{locator, quote}]` 并把 `inferred` 置 false。
4. 逐条判定关系语义（促进→activation、抑制→inhibition、磷酸化→modification、转运→transport…），补 direction/modifier/evidence 与逐字 quote。
5. 定区室布局与跨膜标记；每条 data_mapping 补来源；每次修改后跑 `python scripts/main.py check-spec`（exit 0=通过、1=阻断、2=警告），阻断必须清零。

### 阶段 C：10–14 分钟（≤20 请求）——出图与自检

6. 运行 `python scripts/main.py build`：校验 spec，生成分层 SVG 与 stdlib 矢量 PDF（真实可打开），组装 result.json。
7. 用平台 `office-renderer`/`browser-automation` 把 SVG 渲染为高保真 PNG/PDF（可用时覆盖图件），写回 `export_artifacts` 的 renderer/font_check/color_check。
8. `python scripts/main.py check` 逐条修复阻断与警告。

### 阶段 D：14–15 分钟（≤6 请求）——收尾

9. 剩余缺口写入 spec 的 `limitations`/`pending_human`，重跑 build + check，更新 change_log，status 按实际取 ok/partial/insufficient_evidence。

脚本一律以 Skill 根目录 `/skills/weekly-skill` 为工作目录调用（`python scripts/main.py ...`）。

## 5. 评分维度对齐表（本版核心）

| 评分维度 | 流程步骤 | 自检项 | result.json 落点 |
|---|---|---|---|
| 实际效果 40 分 | B 实体/关系建模 | 实体不混用、关系可区分、方向正确、闭环完整 | entities / relations / legend |
| 结果可核验性 | B+C 证据补录 | 每条有 locator + 逐字 quote，追溯链独立可复核 | sources + SVG data-* 属性 |
| 使用增益 25 分 | A 骨架先行 + C 导出 | 减少改图条目、重画轮次、重导出次数 | change_log / runtime |
| 创新增益 15 分 | B 结构化映射 + C 一致性校验 | 机制文字→图元映射、箭头语义判定、图例自洽 | rubric_alignment / data_mappings |
| 可靠体验 10 分 | D 异常处置 | 缺方向、歧义、数据矛盾、导出失败可识别处置 | uncertainty / limitations / errors |
| 生态贡献 10 分 | 脚本与参考可复用 | 词表、配色、布局、校验脚本可被他人复用 | references/*.md |

## 6. 自检清单（逐条勾选）

- [ ] 已读 case.json + task.md + 实际材料（非仅文件名），PDF 已用解析工具取文本
- [ ] 实体命名规范：基因斜体、蛋白正体、物种前缀、同义名归并、复合物/家族不混用
- [ ] 关系语义 activation/inhibition/binding/modification/transport/conversion 正确且可区分
- [ ] 通路方向：受体→胞内→核内、跨膜位置、分支与反馈环无误
- [ ] 图例由 build 统一生成，与实际配色/线宽/箭头自洽
- [ ] 每条断言实体/边/data_mapping 有 `sources[{locator, quote}]`，无来源的已标 `inferred:true`
- [ ] editable_source_files 为分层 SVG；export_artifacts 含真实 PDF/PNG 且文件实际存在
- [ ] sources 覆盖关键实体/边；uncertainty 记录歧义与缺口
- [ ] change_log 含骨架落盘与后续增量；status/limitations 如实
- [ ] `check` 退出码 0 或仅警告（警告已写入 limitations/degradations）

## 7. 降级策略（禁止编造）

- 材料缺失：无法确定的实体/方向/数值写入 uncertainty，图面不画未证实关系，limitations 注明。
- 同名基因/蛋白歧义：按上下文证据判定；证据不足列 warning 并记 uncertainty。
- 导出工具缺失：仍交付 SVG；`build` 保证 stdlib 矢量 PDF；PNG 无法渲染时 export_artifacts 不写 PNG 记录并在 limitations 说明，绝不伪造。
- 字体缺失：SVG 用可替换 font-family 栈；导出记录 font_check 实际状态。
- 时间/请求不足：先保证骨架已落盘，再按 A→B→C→D 增量；超时即停止并保留当前 result.json。
- 严禁编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 脚本与参考

- `scripts/main.py`：`init` / `check-spec` / `build` / `check` / `run` 子命令，纯标准库，确定性。
- `scripts/selftest.py`：空输入、坏 JSON、缺输入、重复运行四类自检。
- `references/output_contract.md`：result.json 全字段定义。
- `references/entity_relation_rules.md`：实体/关系/布局/图例/数据编码规则。
