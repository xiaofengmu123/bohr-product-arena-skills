---
name: weekly-skill
description: >-
  生物机制图与通路图科研绘图 Skill：从科研材料提取实体、关系、数据与图面约束，
  生成可编辑分层 SVG 源文件并导出 PNG/PDF，产出可复核的 result.json（case_id、
  源文件、导出件、逐字来源、不确定性、修改记录），全程可回溯、可降级。
version: 1.1.0
---

# 生物机制图与通路图绘图 Skill

## 1. 角色与何时使用

本 Skill 面向"生物机制图与通路图"科研绘图。当要把科研材料（论文段落、图注、数据表、PDF）转成一张可被逐条质疑、可编辑、可复核的机制图/通路图时使用；不做统计分析、实验设计、论文版式美化。

核心目标（对应评分维度）：①实体画对（基因/蛋白/复合物/家族/细胞类型/区室不混用）；②关系画准（激活/抑制/结合/修饰/转运/转化可区分）；③通路方向与数据编码正确；④交付分层可编辑 SVG + 真实 PNG/PDF；⑤每个关键元素可回溯到原始材料逐字片段；⑥缺数据/缺软件时如实降级，不编造。

本版设计取舍：**精简与可读**——单脚本核心（`scripts/core_impl.py`）+ 薄入口（`scripts/main.py`）+ 自检（`scripts/selftest.py`），规则与契约各一页，删冗余模板与重复说明。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例元数据与材料引用）+ `/input/task.md`（任务说明）+ 按 topic 提供的科研材料/PDF。
- 必须实际读取材料文件，不得依据文件名/标签臆测内容；PDF 用平台 `pdf-text-tool` / `pdf-render-tool` 读取，不假设有预提取答案层。
- 工作目录 `/app/work`；最终产物写 `/app/output/`。
- 预算：900 秒、64 次模型请求、单次响应含推理 ≤16384 token；三案例各独立运行。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段定义见 `references/output_contract.md`，包含题目 `output_contract` 全部字段，并加：`case_id`、`status`、主产物、`sources`、`uncertainty`、`pending_human`、`change_log`、`limitations`、`errors`、`degradations`、`runtime`。

- `editable_source_files`：分层可编辑 SVG（`main.py build` 输出，含命名图层 + 语义箭头 + data-* 溯源属性）
- `export_artifacts`：PNG/PDF 导出件（含尺寸、分辨率、字体、色彩检查；缺工具时只记真实生成的格式）
- `entities` / `relations` / `legend` / `data_mappings`：结构化机制规格（与 spec.json 一致，含 sources）
- `sources`：聚合来源定位（元素 → 材料位置 + 逐字 quote）
- `uncertainty`：歧义实体、方向不明、无来源数值；`change_log`：增量记录，首条为 3 分钟内骨架落盘
- `status`：success / partial / failed / insufficient_evidence / input_error；非 success 时 `limitations` 必填

源文件、导出件、result.json 三者路径互相对应，且都实际写入 `/app/output/`。

## 4. 执行流程（总 900 秒 / 64 请求）

脚本调用一律用相对路径（相对 Skill 根目录 `/skills/weekly-skill`）；模型请求预算只作上限。

### 阶段 A：0–3 分钟（≤180 秒，≤8 请求）——必须先落盘骨架
1. 读 `/input/case.json`、`/input/task.md`，定位并读取材料文件（PDF 用解析工具）。
2. 快速抽取实体/关系第一版，写入 `/app/work/spec.json`。
3. 立即 `python3 scripts/main.py validate /app/work/spec.json /app/work/spec.report.json`。
4. 立即 `python3 scripts/main.py build /app/work/spec.json /app/output/mechanism.svg`。
5. 立即 `python3 scripts/main.py skeleton /app/output/result.json --case-id <id>`，再手工补齐 case_id 等字段。**此步不得跳过**：保证超时/中断时仍有可交付 partial 结果。

### 阶段 B：3–10 分钟（≤30 请求）——抽取与建模迭代
6. 逐条核对实体命名（基因斜体/蛋白正体、物种前缀、同义名归并），修正 `entities`。
7. 逐条判定关系语义（促进→activation、抑制→inhibition、磷酸化→modification、转运→transport…），修正 `relations` 的 type/direction/evidence/modifier。
8. 确定区室布局（extracellular→membrane→cytosol→nucleus，跨膜受体 `transmembrane:true`）。
9. 每次修改后重跑 `validate`（阻断项清零）+ `build` 覆盖 SVG，并在 change_log 记录。

### 阶段 C：10–14 分钟（≤20 请求）——导出与自检
10. 用平台工具把 SVG 渲染为 PNG/PDF（office-renderer / browser-automation，视可用工具），核对尺寸/分辨率/字体/色彩，写回 `export_artifacts`。
11. 补全 `sources`（每个实体/边 → 材料位置 + quote）与 `uncertainty`。
12. `python3 scripts/main.py check /app/output/result.json --output-dir /app/output`，逐条修复。

### 阶段 D：14–15 分钟（≤6 请求）——收尾
13. 无法解决的缺口写入 `limitations`，status 按实际取；重跑 `check` 确认；更新 change_log。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] 已实际读取 `/input/case.json` + `task.md` + 材料文件（非仅文件名）
- [ ] 实体命名规范：基因斜体、蛋白正体、物种前缀、同义名归并、复合物/家族不混用
- [ ] 关系语义：activation/inhibition/binding/modification/transport/conversion 正确且可区分
- [ ] 通路方向：受体→胞内→核内、跨膜位置、分支与反馈环无误
- [ ] 图例与实际配色/线宽/形状/箭头自洽（由 `build_svg.py` 统一生成）
- [ ] 数据编码均有 `source`，无来源不标注
- [ ] `editable_source_files` 为分层可编辑 SVG；`export_artifacts` 含真实 PNG/PDF
- [ ] `sources` 覆盖关键实体/边（locator + 逐字 quote）；`uncertainty` 记录歧义与缺口
- [ ] `change_log` 含骨架落盘与后续增量；status 与 limitations 如实
- [ ] `python3 scripts/selftest.py` 全部 PASS；`check` 退出码 0 或仅警告（警告已说明）

## 6. 降级策略（禁止编造）

- 材料缺失：无法确定的实体/方向/数值写入 `uncertainty`，图上不画未证实关系，`limitations` 注明。
- 同名基因与蛋白歧义：按上下文证据判定；证据不足时列为警告并在 uncertainty 说明。
- 导出工具缺失（无 office-renderer/browser-automation）：仍交付 SVG 源文件，`export_artifacts` 只保留能真实生成的格式，缺失格式写入 limitations，不得伪造 PNG/PDF 记录。
- 字体缺失：SVG 用可替换 font-family 栈；导出时如实记录 font_check。
- 时间/请求不足：先保证骨架已落盘，再按 A→B→C→D 顺序增量改进；超时即停止并保留当前 result.json。
- 任何情况不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 脚本与参考

- `scripts/main.py`：入口，子命令 `validate` / `build` / `check` / `skeleton`（仅标准库，退出码 0=通过、1=阻断、2=警告）。
- `scripts/core_impl.py`：核心实现（规格归一/校验、分层 SVG 构建、result 契约校验、骨架落盘）。
- `scripts/selftest.py`：自检脚本（合成示例，非正式案例）。
- `references/rules.md`：实体/关系/布局/图例规则与自检清单。
- `references/output_contract.md`：result.json 全部字段定义。
