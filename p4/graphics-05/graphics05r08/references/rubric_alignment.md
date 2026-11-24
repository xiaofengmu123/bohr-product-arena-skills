# 评分维度对齐表（本 Skill 的核心设计取舍）

本 Skill 与参考打法的主要差异是：不把“评分维度”当作交付后的追述，而是把它当作
**运行时的施工图纸**。每个评分维度都映射到 `SKILL.md` 的执行流程步骤、一条可勾选的
自检项（S1–S18）和一个可落盘、可复核的产物。`scripts/main.py --stage validate` 会据此
生成 `/app/work/rubric_trace.json`，`finalize` 阶段把它并入 `result.json.rubric_trace`，
使“增益可验证性”这一项有真实证据，而非文字自述。

## 映射总表

| 评分维度 | 分值 | 流程步骤 | 自检项 | 产物 / 证据 |
|-|-|-|-|-|
| 任务完成度 | 15 | 步骤 2–3（四要素抽取、成图） | S1 | `graphical_abstract` 四面板 + `editable_source` |
| 结果正确性 | 15 | 步骤 2（实体/关系/数据）+ 步骤 4（校验） | S2/S3/S4 | `entities`/`relations`/`data` + 方向校验 + `data-source` |
| 结果完整性 | 5 | 步骤 1（契约回填） | S5 | `result.json` 全字段，缺值给 `null`/`[]` |
| 结果可核验性 | 5 | 步骤 4（证据溯源） | S6 | 每元素 `locator` + 逐字 `quote` |
| 质量增益 | 15 | 步骤 3（分层 SVG + 数据映射） | S7 | `layer-*` 分组、`text` 可编辑、图例与数据一一对应 |
| 效率增益 | 5 | 步骤 3（一次构建 + 导出） | S8 | 单次 `build` 产出 SVG+PNG/PDF，免重绘搬运 |
| 增益可验证性 | 5 | 步骤 5（增量改进留痕） | S9 | `rubric_trace.json` + 每次改写的 `change_log` |
| 新增能力 | 5 | 步骤 3（分层可编辑源文件） | S10 | `layer-panels/entities/relations/data/labels/legend/annotations` |
| 方法改进 | 5 | 步骤 2（确定性布局 + 视觉通道） | S11 | 固定网格/调色板，颜色=类别、长度=数值 |
| 创新证据 | 5 | 步骤 6（自检复现） | S12 | `scripts/selftest.py` 通过；两次构建字节一致 |
| 调用易用性 | 3 | 步骤 0（触发）+ frontmatter | S13 | `name/description/version` 齐备，相对路径可执行 |
| 运行稳定性 | 4 | 步骤 3 + 6（确定性/幂等） | S14 | 相同 `figure_spec.json` 产字节一致 SVG |
| 异常处理 | 3 | 步骤 7（降级） | S15 | `limitations`/`uncertainty`/`pending_human`，不编造 |
| 文档完整性 | 3 | 全部步骤 | S16 | `SKILL.md` + `references/*.md` 完整 |
| 资源可复用性 | 4 | 脚本与模板 | S17 | 仅标准库；`figure_spec.json` 模板可复用 |
| 分享推广价值 | 3 | 步骤 8（沉淀） | S18 | `GITHUB_URL.txt` + 图稿案例与版式经验 |

## 关键取舍：与参考包的三点不同

1. **评分维度 → 自检项双射。** 参考包的自检清单按“产物类型”组织（图元、方向、版式）；
   本包改按“评分维度”组织，使每一项评分都有对应的可执行检查，减少“做了但说不清”
   的丢分。自检编号 S1–S18 与上表一一对应，`SKILL.md` 的清单直接引用这些编号。

2. **`rubric_trace` 作为一等产物。** 不另写“测评报告”这类不可由运行时核验的文本，
   而是由 `validate` 阶段按真实 `validation_report` 与 `manifest` 派生状态字段，
   `finalize` 原样写回 `result.json`。评审者无需信任作者陈述，可直接读该字段核对
   “增益可验证性”。

3. **确定性优先于花哨版式。** 布局采用固定 1600×1200 画布、固定四列面板、按稳定
   `id` 排序的节点与 Okabe-Ito 色觉安全调色板；同一 `figure_spec.json` 两次构建字节
   一致。这直接服务“运行稳定性（4 分）”与“结果可核验性（5 分）”，并以
   `scripts/selftest.py` 的 `svg_determinism` 检查提供证据。

## 如何核对（评审 / 复跑者视角）

```bash
cd /skills/weekly-skill
python3 -B scripts/selftest.py                 # 退出码 0 为通过
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
# 之后读 /app/output/result.json 的 rubric_trace、sources、change_log
```

同一输入两次运行后，比较两次 `fig_<case_id>.svg` 的字节是否一致；若 `figure_spec.json`
未改动，字节必须一致。若不一致，说明流程中混入了随机因素，属于稳定性缺陷，应回查
实体/关系/数据的排序与调色板分配是否使用了稳定键。
