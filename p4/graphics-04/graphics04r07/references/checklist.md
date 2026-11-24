# 自检清单与失败处置（scix-figure-skill）

交付前逐条勾选；`python scripts/selftest.py` 覆盖健壮性五场景，`main.py validate` 覆盖 IR/SVG 结构校验。

## 1. 逐条自检（对应评分维度）

实际效果·完成度
- [ ] 已产出可编辑分层 SVG（五个命名图层）并复制到 `/app/output/figure.svg`。
- [ ] 已尝试真实 PNG/PDF 渲染；缺工具时 `exports[].status="skipped"` 并写 `limitations`，不伪造成功。

实际效果·正确性
- [ ] 部件名称/数量/接口与材料逐字一致；同类部件未合并。
- [ ] 每个 relation 的 `from`/`to` 指向存在的 entity，箭头方向与材料流向一致，回流用 dashed。
- [ ] 数值标注与来源一致，无凭空补齐的部件、管路或数值。

实际效果·完整性
- [ ] `result.json` 覆盖六类要素：`entities / relations / data_encoding / legend_annotations / editable_sources / exports`。
- [ ] `output_contract` 中题面声明的全部字段原样回填，未知键不改名、不删除。

实际效果·可核验
- [ ] 每个关键结论给 `source_ref`，`sources` 含 `locator` + 逐字 `quote`。
- [ ] `result.json` 含 `case_id / status / sources / uncertainty / pending_human / change_log / errors / degradations / runtime`。

使用/创新增益
- [ ] IR→布局→SVG→校验流水线可复跑，`<script id="ir-json">` 内嵌 IR 支持从 SVG 复原。
- [ ] 编号规则、图例映射、数据编码规则沉淀在 `references/ir_schema.md`。

可靠体验（本版重点：鲁棒性）
- [ ] 空输入目录 → `insufficient_evidence`，退出码 0，不抛异常。
- [ ] 坏 JSON / 坏编码 → 记录 `errors/degradations`，继续处理其余材料。
- [ ] 缺参数 / 未知参数 → `--input/--work/--out` 缺省指向 `/input`、`/app/work`、`/app/output`。
- [ ] 单文件 > 2MB → 跳过并记录，不整文件读入。
- [ ] 同一输入两次运行结果一致（确定性布局，无随机数）。

生态贡献
- [ ] `SKILL.md` 写清用途、输入输出契约、执行流程、自检、降级与限制。
- [ ] `scripts/selftest.py` 与 `references/` 可直接复用。

## 2. 常见失败样例（须规避）

1. 只给一段文字或单张栅格截图，无 SVG 源文件。
2. 部件名称与材料对不上；把两个同类部件合并成一个。
3. 管路/箭头凭空补齐、方向反向、断头或自环。
4. 步骤编号与正文顺序错位。
5. 图例颜色/线型与图形不一致。
6. 字体缺失导致导出后中文方框，却声称嵌入成功。
7. 数值标注无来源或与来源不一致。
8. 画布溢出被裁剪而不提示。
9. `result.json` 缺 `case_id/status/sources/uncertainty/change_log`。
10. 伪造渲染成功、缺失软件状态或评测分数。

## 3. 失败处置

- 同一策略失败两次即换策略；错误信息写入 `change_log` 与 `limitations/errors`。
- 材料缺失/无法判断 → 标 `insufficient_evidence`，把缺口写入 `pending_human`，禁止编造。
- 渲染工具缺失 → 只交付 SVG，`exports[].status="skipped"`。
- 字体缺失 → 用 `font-discovery` 查字体；查不到时 `limitations/fonts` 标注并降级英文标签。
