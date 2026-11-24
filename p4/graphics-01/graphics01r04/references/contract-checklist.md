# 评分维度对照与降级矩阵

本文件把题目评分维度翻译成可逐条勾选的自检项，并给出降级与不确定性的显式化规则。执行时按 §7 清单逐条勾选，缺口写入 `uncertainty`/`pending_human`，不得冒充完成。

## 1. 评分维度 → 自检映射

| 评分维度 | 分值 | 对应自检动作 | 落盘证据 |
|-|-|-|-|
| 实际效果 | 40 | 走通「材料→图稿→导出」；实体/关系/方向/层级与来源一致；无脑补基因名/浓度/时间点/单位；图例与数据逐项一致；覆盖图面要素 | `provenance[]`（locator+quote）+ `sources[]` + 实际 SVG/PDF/PNG |
| 使用增益 | 25 | 改 spec 可重绘；SVG 可编辑；`change_log` 如实记录返工轮次 | `main.py --spec` 重跑 + `editable_source_files` |
| 创新增益 | 15 | 文字→实体关系抽取可说明；数据表驱动图例重绘；导出前合规检查真实运行 | `selftest.py` 输出 + `validation` |
| 可靠体验 | 10 | 按说明可独立出图；重复运行稳定；异常可识别可处置 | 幂等重跑 + `errors`/`degradations` |
| 生态贡献 | 10 | 文档、依赖、路径、边界齐全；模板/配色/图例规范可复用 | `references/` + `SKILL.md` |

## 2. 三态置信度（本版核心约定）

| confidence | 定义 | 图面处理 | 落盘 |
|-|-|-|-|
| `confirmed` | 有逐字 `quote` + 可定位 `source_ref` | 正常绘制 | `provenance[].confidence="confirmed"` |
| `inferred` | 可说明推导，但无逐字原文 | 绘制并标「推测」 | `confidence="inferred"` + `uncertainty` |
| `missing` | 材料缺失，无法判断 | 不画或画占位框 | `confidence="missing"` + `pending_human` |

原则：**只画能追溯到来源的元素**；无法判断方向/类型的边标 `inferred`，绝不把「抑制」画成「激活」。

## 3. 降级矩阵（缺什么、为什么、影响、补救）

| 场景 | 处置 | 字段 |
|-|-|-|
| case.json 缺失/不可读 | 写骨架 `status=failed`，退出码 0 | `errors` + `uncertainty` |
| task.md 缺失 | 继续出图，标注评分维度可能不全 | `degradations` + `uncertainty` |
| 材料文件缺失 | 该实体标 `missing` 不画 | `uncertainty` + `pending_human` |
| 两来源矛盾 | 两条都记录，标 `source_conflict` | `uncertainty` + `pending_human` |
| PDF 无文本层 | OCR/pdftoppm，标注「需人工复核」 | `sources[].locator` + `uncertainty` |
| 关系方向/类型存疑 | 标 `inferred`，图面写「推测」 | `provenance[].confidence` + `uncertainty` |
| 缺字体 | font-discovery 找替代；无法嵌入 `font_check="none"` | `export_files[].font_check` + `uncertainty` |
| 缺 PNG 转换器 | 纯标准库写矢量 PDF 兜底；PNG 记 failed/降级预览 | `export_files` + `degradations` |
| 导出失败 | 保留 SVG，不伪造 PNG | `export_files`（failed）+ `status=partial` |

## 4. 字段语义

- `errors`：机器级硬故障（文件不可读、渲染异常、写盘失败）。
- `degradations`：降级操作记录（用 OCR、字体替换、用兜底 PDF、跳过超限文件）。
- `uncertainty`：自动可管理的不确定性（缺证据、置信度不足、需 OCR 复核）。
- `pending_human`：**必须人工决策**的事项（数据缺失补录、来源矛盾裁决、推测确认）。
- 两者分离：能自动降级的进 `uncertainty`，需人拍板的进 `pending_human`。

## 5. status 判定

- `complete`：SVG 存在、导出件至少一组、`validation.issues` 为空。
- `partial`：有 SVG 但存在未解决的缺口/验证问题；材料不足也属此类，**不判 failed**。
- `failed`：case.json 不可读，或渲染/写盘硬错误导致无任何图稿。

## 6. 提交前最小自检

```bash
python scripts/main.py --help                       # 退出码 0
python scripts/selftest.py                          # 退出码 0，输出 {"ok": true, ...}
```

selftest 覆盖：frontmatter 完整、骨架落盘、spec 渲染、SVG 可解析、PDF 头合法、空目录降级、provenance 逐字 quote 非空。正式案例的输入与结果由平台提供，本 Skill 不预写任何答案或测试产物。
