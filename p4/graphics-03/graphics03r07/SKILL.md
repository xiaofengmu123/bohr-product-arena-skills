---
name: weekly-skill
description: >-
  生物机制图与通路图科研绘图 Skill：从科研材料提取实体、关系、数据与图面约束，
  生成可编辑分层 SVG 源文件并导出 PNG/PDF，产出可复核的 result.json（含 case_id、
  源文件、导出件、来源、不确定性、pending_human、修改记录）。设计侧重鲁棒性：
  空输入、坏 JSON、缺参数、超大文件均优雅降级不崩溃，全程可回溯、可增量改进。
version: 2.0.0
---

# 生物机制图与通路图绘图 Skill

## 1. 角色与何时使用

本 Skill 面向"生物机制图与通路图"科研绘图任务：把科研材料（论文段落、图注、数据表、PDF）转成一张可被逐条质疑、可编辑、可复核的机制图/通路图。不做统计分析、实验设计、论文版式美化。

核心目标（对应评分维度）：① 实体画对（基因/蛋白/复合物/家族/细胞类型/区室不混用）；② 关系画准（激活/抑制/结合/修饰/转运/转化可区分）；③ 通路方向与数据编码正确；④ 交付分层可编辑源文件 + 真实 PNG/PDF 导出件；⑤ 每个关键元素可回溯到材料逐字片段；⑥ 缺数据/缺软件时如实降级，不编造。

**本版设计取舍（鲁棒性变体）**：与"先求全再防错"的常规做法不同，本包把"永不崩溃、始终留下合法 partial 产物"作为最高优先级——空输入目录、坏 JSON/坏编码、缺参数、单文件超 2MB、写盘失败、重复运行都要优雅降级并给出可解释的错误记录，而完整性通过"骨架先行 + 增量改进"保证。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例元数据）+ `/input/task.md`（任务说明）+ 按 topic 提供的科研材料/PDF。
- **必须实际读取材料文件**，不得依据文件名/标签臆测内容；PDF 用平台 `pdf-text-tool` / `pdf-render-tool` 读取，不要假设有预提取答案层。
- 工作目录 `/app/work`（spec.json 等中间态）；最终产物写入 `/app/output/`。
- 预算：900 秒、64 次模型请求、单次响应含推理 ≤16384 token；三个案例各独立运行。
- 参数缺省：`--input /input`、`--output /app/output`、`--work /app/work`；脚本可被 `python scripts/main.py --help` 调用。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段定义见 `references/output_contract.md`，至少包含：

- `case_id`（取自 `/input/case.json`，找不到写 `null` 并在 errors 说明）
- `task_type`、`status`（success/partial/insufficient_evidence/input_error/failed）
- `editable_source_files`：分层可编辑 SVG（含命名图层）
- `export_artifacts`：真实 PNG/PDF 导出件（尺寸、分辨率、字体、色彩检查）
- `entities` / `relations` / `legend` / `data_mappings`：结构化机制规格（与 spec.json 一致，含 sources）
- `sources`：聚合来源定位（元素 → locator + 逐字 quote）
- `uncertainty`：歧义实体、方向不明、无来源数值
- `pending_human`：需人工确认项
- `change_log`：增量修改记录（首条必须为 3 分钟内骨架落盘）
- `limitations`：status≠success 时必填；`errors`/`degradations`：已处理的错误与降级
- `runtime`：起止时间、耗时、SVG 字节数

源文件、导出件、result.json 三者路径互相对应，且都实际写入 `/app/output/`。缺材料写 `not_reported`，不编造引用/DOI/数值。

## 4. 执行流程（总 900 秒 / 64 请求）

### 阶段 A：0–3 分钟（≤180 秒，≤8 请求）——必须先落盘骨架

1. 读 `/input/case.json`、`/input/task.md`，定位并实际读取材料文件（PDF 用解析工具）。
2. 快速抽取实体/关系第一版，写入 `/app/work/spec.json`（LLM 写 JSON，schema 见 `references/entity_relation_rules.md`）。
3. 立即运行 `python scripts/main.py init --input /input --output /app/output --work /app/work`：读 spec.json → 生成 SVG → 写 result.json 骨架（`status="partial"` 或 `insufficient_evidence`，case_id 已填，字段齐全，change_log 首条"骨架落盘"）。
4. **此步不得跳过**：即使 spec.json 为空也会生成占位 SVG 与合法 result.json，保证超时/中断仍有可交付部分结果。

### 阶段 B：3–10 分钟（≤30 请求）——抽取与建模迭代

5. 逐条核对实体命名（基因斜体/蛋白正体、物种前缀、同义名归并），修正 `entities`。
6. 逐条判定关系语义（促进→activation、抑制→inhibition、磷酸化→modification、转运→transport…），修正 `relations` 的 type/direction/evidence/modifier。
7. 确定区室布局（extracellular→membrane→cytosol→nucleus，跨膜受体 `transmembrane:true`）。
8. 每次修改 spec.json 后重跑 `python scripts/main.py init`（覆盖 SVG + 增量更新 result.json change_log），阻断项必须清零。

### 阶段 C：10–14 分钟（≤20 请求）——导出与自检

9. 用平台导出工具把 SVG 渲染为 PNG/PDF（browser-automation / office-renderer / latex-compiler，视可用工具），核对尺寸、分辨率、字体、色彩，写回 `export_artifacts`。
10. 补全 `sources`（每个实体/边 → locator + 逐字 quote）与 `uncertainty`/`pending_human`。
11. 运行 `python scripts/main.py check --output /app/output`，逐条修复。

### 阶段 D：14–15 分钟（≤6 请求）——收尾

12. 运行 `python scripts/main.py finalize`：重建 SVG、校验契约、按实际取 status、盖章 runtime、追加 finalize 记录。
13. 无法解决的缺口写入 `limitations`；重跑 `python scripts/selftest.py` 确认；更新 change_log。

脚本调用一律用相对路径（相对 Skill 根目录 `/skills/weekly-skill`）；模型请求预算只作上限，不必用完。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] 已读 `/input/case.json` + `task.md` + 实际材料文件（非仅文件名）
- [ ] 实体命名规范：基因斜体、蛋白正体、物种前缀、同义名归并、复合物/家族不混用
- [ ] 关系语义：activation/inhibition/binding/modification/transport/conversion 均正确且可区分
- [ ] 通路方向：受体→胞内→核内、跨膜位置、分支与反馈环无误
- [ ] 图例与实际配色/线宽/形状/箭头自洽（由 `build_svg.py` 统一生成）
- [ ] 数据编码（表达量/显著性/置信度）均有 `source`，无来源不标注
- [ ] `editable_source_files` 为分层可编辑 SVG；`export_artifacts` 含真实 PNG/PDF
- [ ] `sources` 覆盖关键实体/边（locator + 逐字 quote）；`uncertainty` 记录歧义与缺口
- [ ] `change_log` 含骨架落盘与后续增量；`pending_human` 记录待确认项；status 与 limitations 如实
- [ ] `python scripts/selftest.py` 全部 PASS；`main.py check` 退出码 0 或仅警告

## 6. 降级策略（禁止编造）

- **材料缺失**：无法确定的实体/方向/数值写入 `uncertainty`，图上不画未证实关系，`limitations` 注明，status 取 `insufficient_evidence`。
- **同名基因与蛋白歧义**：按上下文证据判定；证据不足列为警告并在 `uncertainty` 说明。
- **导出工具缺失**：仍交付 SVG 源文件，`export_artifacts` 仅保留能真实生成的格式，缺失格式在 `limitations` 说明，不得伪造 PNG/PDF 记录。
- **字体缺失**：SVG 用可替换 font-family 栈；导出时字体不可用则如实记录 font_check。
- **空输入/坏 JSON/坏编码/缺参数/超大文件**：按 `references/entity_relation_rules.md` 第 6 节健壮性清单处置，均退出码 0 并留合法 partial 结果。
- **时间/请求不足**：先保证骨架已落盘，再按 A→B→C→D 顺序增量改进；超时即停止并保留当前 result.json。
- 任何情况下不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 脚本与参考

- `scripts/main.py`：入口（init/finalize/check 三子命令），输入发现 + 鲁棒 I/O + 结果组装与契约校验；<30KB。
- `scripts/build_svg.py`：由 spec.json 生成分层 SVG（命名图层 + 语义箭头 + 区室布局 + 自洽图例 + data-* 溯源属性），确定性、可独立运行。
- `scripts/selftest.py`：自检脚本（空输入、坏 JSON、坏编码、缺参数、超大文件、确定性、契约、悬空关系 12 项），退出码 0=通过。
- `references/entity_relation_rules.md`：实体/关系/布局/图例/证据规则与健壮性清单。
- `references/output_contract.md`：result.json 全部字段定义与退出码约定。
