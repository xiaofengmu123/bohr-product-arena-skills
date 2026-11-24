# 交付自检清单与评分维度对照

本清单用于收尾复核与 `selftest.py` 的逐条自检，对应题目 5 个评分维度。**逐条勾选，未勾选的项目必须在 `result.json` 的 `uncertainty` 或 `change_log` 说明原因，不得静默跳过。**

## A. 实际效果（40 分）

- [ ] 走通「材料 → figure_model.json → SVG → PDF/PNG」，不是套模板插画（`sources` 有真实材料文件与 locator）
- [ ] 每个实体、关系、方向、层级与来源一致：抑制画 T 端、激活画箭头、上下游不倒置、包含用 group 不用并列
- [ ] 图中不出现材料中不存在的基因名/浓度/时间点/单位（逐条比对 `entities[].label` 与来源 quote）
- [ ] 图例项、配色、标记与数据表分类和数值逐项一致（分类数 = 颜色数 = 标记数）
- [ ] 覆盖实体、关系、图例、标注、单位、子图编号等必要图面元素
- [ ] `provenance` 每条都能定位到来源（`source_ref` + `locator` + `quote`），可独立复核

## B. 使用增益（25 分）

- [ ] 数据改动后能重绘：改 `figure_model.json` 重跑 `render.py` 即得到新图（不改绘图代码）
- [ ] 输出可编辑 SVG：改标签不整张重画，文字为 `<text>`、图层为 `<g>`
- [ ] `change_log` 如实记录人工修改量与返工轮次的减少；`sources`/`pending_human` 区分已确认与待确认

## C. 创新增益（15 分）

- [ ] 文字描述 → 实体关系清单的抽取方法可说明（模型字段 `type/kind/group` 对应抽取步骤）
- [ ] 数据表驱动图例重绘：legend 与 entities/relations 同源（`maps_to` + `data_mapping`）
- [ ] 导出前合规检查真实运行：`python scripts/selftest.py --result … --model … --output …` 有真实输出

## D. 可靠体验（10 分）

- [ ] 按 SKILL.md 步骤可独立完成一次出图（`python scripts/main.py selftest` 退出码 0）
- [ ] 重复运行稳定产出：同一 `figure_model.json` 两次渲染字节一致（确定性布局，无随机量）
- [ ] 缺数据/缺字体/导出失败时如实标注，不编造（见 SKILL.md 降级策略）

## E. 生态贡献（10 分）

- [ ] 说明、依赖、路径、边界与维护信息齐全（SKILL.md + references/）
- [ ] 模板/配色/图例规范/导出预设可复用（`figure-model.md` 即规范；脚本仅标准库）
- [ ] 沉淀可分享案例与经验，不预写正式答案（selftest 用合成示例且显式标注）

## 常见硬伤速查

| 失败形态 | 判定 | 处置 |
|---|---|---|
| 抑制画成激活 | `kind` 与箭头端不一致 | 抑制用 `inhibition` + T 端，自检核对 |
| 上下游倒置 | `from→to` 反向 | 按来源语序定方向，provenance 留 quote |
| 包含画成并列 | 用了 relation 而非 group | 包含一律进 `groups[].contains` |
| 图例与数据不符 | 分类/颜色/标记数不一致 | selftest 报错，重写 `data_mapping` |
| 出现来源外基因/浓度/单位 | label 无法溯源 | 删除该实体或标 `confirmed:false` |
| 只有位图 | 缺可编辑源文件 | 补 SVG 并写入 `editable_source_files` |
| 导出参数不合规 | dpi/色彩空间/字体未查 | `export_files` 逐项回填并记录失败原因 |
