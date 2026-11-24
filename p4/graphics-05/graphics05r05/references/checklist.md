# 自检清单（逐条可勾选，对应评分维度）

交付前逐条核对；`python scripts/selftest.py` 会自动化验证其中带 `[自动]` 标记的条目。
未通过的条目：先修复；无法修复则写入 `result.json` 的 `limitations`/`uncertainty`，并把
`status` 降到 `partial` 或 `failed`，绝不静默通过。

## A. 输出契约完整性（结果完整性 / 可核验性）

- [ ] `result.json` 存在于 `/app/output/`，可被 `json.loads` 解析，无语法错误 `[自动]`。
- [ ] 顶层含 `case_id` 与 `status`，且 `status ∈ {completed, partial, failed}` `[自动]`。
- [ ] 案例 `output_contract` 的**每一个键**都出现在 `result.json` 中，未改名、未删除、
      未知键保留 `[自动]`。
- [ ] `sources`、`uncertainty`、`pending_human`、`change_log` 存在，且 `change_log`
     至少有一条、`version` 单调递增 `[自动]`。
- [ ] 每次实质改写都追加了 `change_log`（骨架 → 填事实 → 重绘 → 导出 → 定稿）。

## B. 科学内容正确（结果正确性）

- [ ] 每个实体都有 `id/label/kind`，能对应到材料中的研究对象，无张冠李戴。
- [ ] 每条关系的 `from/to/direction` 与来源句子的因果方向一致；方向不明就写
      `undirected` 并进 `uncertainty`。
- [ ] 每个数值标签都有 `value` + `unit`，且数值、单位与来源材料逐字一致。
- [ ] 图例的每个色块/长度/面积映射都有一条 `data_mapping` 或 `legend` 记录，且带
      `source.locator + quote`。

## C. 可编辑源文件与导出（可编辑源文件 / 导出件）

- [ ] 产出分层 SVG：存在命名的 `layer-*` 分组 `[自动]`。
- [ ] 文字保持为可编辑 `<text>`，不是 text-as-path `[自动]`。
- [ ] 关键图元带 `data-source` 属性，可回溯到材料 `[自动]`。
- [ ] PNG/PDF 为真实渲染文件；无渲染器时如实记录 `preview_not_rendered`，不伪造
      位图/PDF。

## D. 版式与视觉层级（版式与视觉层级）

- [ ] 无画布溢出：所有文本/形状都在 canvas 边界内 `[自动]`。
- [ ] 字号 ≥ 8 px；标题、面板题、正文有明显层级 `[自动]`。
- [ ] 配色来自色觉安全调色板，灰度下可区分（脚本内置 8 色 Okabe-Ito 系）。
- [ ] 阅读顺序为 问题 → 方法 → 结果 → 结论，图例/单位/条件标签齐全。

## E. 运行稳定性 / 可复核（运行稳定性）

- [ ] 相同输入重复运行得到结构一致的 SVG（确定性布局、固定调色板、无随机数）。
- [ ] 脚本只依赖 Python 标准库，路径使用相对路径 `scripts/*.py` `[自动]`。
- [ ] 空输入、坏 JSON、缺 `case.json` 三种情况各跑一次 `selftest.py`，退出码符合预期
      `[自动]`。

## F. 异常处理 / 诚实降级（异常处理）

- [ ] 缺失数值 → 字段 `null` + `uncertainty` 条目，不估算。
- [ ] 实体/方向歧义 → 拆 id 或 `undirected`，并注明歧义。
- [ ] 渲染器/字体缺失 → 保留 SVG，`exports.status` 记 `preview_not_rendered` 或 `failed`，
      写 `limitations`，不伪造文件。
- [ ] 无 `case.json` → 写 `status=failed` 骨架并退出 1，停止下游步骤。

## G. 生态贡献 / 复用性（生态贡献）

- [ ] `SKILL.md` 说明了用途、触发条件、输入输出、预算、降级与自检。
- [ ] 图稿模板、图层命名、图例样式、导出参数可直接被他人复用。
- [ ] `references/` 与 `scripts/` 文件真实存在，引用路径有效，无隐藏个人文件。
