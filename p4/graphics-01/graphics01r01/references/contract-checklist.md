# 契约与评分维度对照（contract-checklist）

本文件把 result.json 字段、自检动作与题目评分维度逐条对应，供执行
`scripts/main.py validate` 前人工勾选，也供 scripts/selftest.py 复验。

## 1. result.json 字段（output_contract 全量 + 增补）

| 字段 | 必需 | 说明 |
|---|---|---|
| `case_id` | 是 | 从 case.json 提取，缺失填 `unknown` |
| `status` | 是 | `complete` / `partial` / `failed`，须与产物实况一致 |
| `primary_outputs` | 增补 | 主产物快捷索引 |
| `editable_source_files` | 是 | 分层 SVG（`<g>`、可编辑 `<text>`、可替换字体）；Office/PPT 类须附真实 PDF/PNG 预览 |
| `export_files` | 是 | PNG/PDF 及尺寸、dpi、色彩空间、字体检查；失败须记 failed+reason |
| `provenance` | 是 | 每个图面元素 → evidence_id/source_ref/mapping/version/confirmed |
| `evidence_map` | 增补 | 结论级 `locator` + 逐字 `quote` + status，本包溯源强化核心 |
| `sources` | 是 | 材料路径 + locator + used_for |
| `uncertainty` | 是 | 缺失/矛盾/OCR/降级，逐条 reason/impact/mitigation |
| `pending_human` | 增补 | 需人工确认的结论清单 |
| `errors` / `degradations` | 增补 | 异常与降级（坏 JSON、缺字体、渲染失败） |
| `change_log` | 是 | ISO8601 时间 + action + detail |
| `runtime` | 增补 | 起止时间、case_keys、材料清单 |

空数组不得静默省略：为空时在对应项或 `uncertainty` 中说明原因。

## 2. 评分维度对照

**实际效果（40）**
- [ ] 走通「材料 → 实体/关系/数据抽取 → figure_spec → SVG → 导出」全链路
- [ ] 实体、关系、方向、层级与来源一致（抑制≠激活、上下游不倒置、包含≠并列）
- [ ] 图中不出现材料中不存在的基因名/浓度/时间点/单位（`not_reported` 负向核验）
- [ ] 图例项、配色、标记与数据表分类和数值逐项一致
- [ ] 覆盖实体、关系、图例、标注、单位、子图编号等必要图面元素
- [ ] provenance/evidence_map 每条都能独立定位到材料（locator + quote）

**使用增益（25）**
- [ ] 数据改动后重跑 `render` 可重绘（spec 与 legend 同源）
- [ ] 输出可编辑 SVG，改标签不整张重画
- [ ] change_log 如实记录人工修改量与返工轮次

**创新增益（15）**
- [ ] 文字描述 → 实体关系清单的抽取方法可说明（SKILL.md §5）
- [ ] 数据表驱动图例重绘（legend.items / chart.series 同源）
- [ ] 导出前合规检查真实运行（scripts/main.py validate）

**可靠体验（10）**
- [ ] 按 SKILL.md 可独立完成一次出图
- [ ] 重复运行结果一致（确定性布局、固定字段顺序）
- [ ] 空输入/坏 JSON/缺字体/导出失败时如实标注，不编造

**生态贡献（10）**
- [ ] 说明、依赖、路径、边界与维护信息齐全
- [ ] figure_spec 模板、证据映射 schema、导出预设可复用
- [ ] 沉淀可分享的案例与经验（不预写正式答案）

## 3. 自检命令

```bash
python -B scripts/selftest.py          # 全链路自检，退出码 0 表示通过
python -B scripts/main.py --help       # 接口可解析
python -B scripts/main.py scaffold --input /input --out /app/output --work /app/work
python -B scripts/main.py extract --input /input --out /app/output --work /app/work
python -B scripts/main.py render --spec /app/work/figure_spec.json --svg /app/output/figure.svg
python -B scripts/main.py export --svg /app/output/figure.svg --out /app/output --work /app/work
python -B scripts/main.py merge --result /app/output/result.json --evidence /app/work/evidence_map.json --spec /app/work/figure_spec.json --work /app/work
python -B scripts/main.py validate --result /app/output/result.json --output-dir /app/output
```

## 4. 降级与诚实标注

| 场景 | 处置 |
|---|---|
| 缺 PDF 文本层 | 用 pdftoppm/OCR，`basis="ocr"`，sources 标「OCR，需人工复核」 |
| 缺字体 | font-discovery 找替代并记录替换；无法嵌入时 font_check="none" 并写 uncertainty |
| 渲染/导出失败 | 保留 SVG，export_files 记 failed+reason，status=partial，不伪造 PNG/PDF |
| 材料缺失或矛盾 | 写 uncertainty/evidence_map(conflict)；不确定实体不画或标「推测」 |
| 空输入目录 | 退出码 0，写 partial 骨架并说明缺口 |

严禁编造评测分数、运行结果、软件状态或科学数据；所有 limitations 写入
uncertainty 与 change_log，不得在 result.json 冒充 complete。
