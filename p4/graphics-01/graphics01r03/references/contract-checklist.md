# 输出契约字段定义与自检清单

本文件是 `SKILL.md` 的字段级参考，定义 `result.json` 的完整契约、评分维度对照与确定性校验策略。`scripts/selftest.py` 实现可自动执行的部分，人工核对部分由 Agent 在交付前逐条勾选。

## 1. result.json 完整契约

```json
{
  "case_id": "从 case.json 提取，缺失填 'unknown'",
  "status": "complete | partial | failed | input_error | insufficient_evidence",
  "editable_source_files": [
    {"path": "figure01.svg", "kind": "svg", "layers": true,
     "editable_text": true, "font_replaceable": true, "note": ""}
  ],
  "export_files": [
    {"path": "figure01.png", "format": "png", "width_px": 2400,
     "height_px": 1400, "dpi": 300, "color_space": "RGB",
     "font_check": "embedded|none|replaced", "note": ""}
  ],
  "provenance": [
    {"element": "A", "source_ref": "materials/paper.pdf", "locator": "第3页 结果节",
     "quote": "逐字片段……", "mapping": "图例 L1 色块", "version": "1.0",
     "confirmed": true}
  ],
  "sources": [
    {"ref": "材料引用名", "path": "/input/...", "locator": "页码/节",
     "used_for": "抽取实体 A、关系 r1"}
  ],
  "uncertainty": [
    {"item": "", "reason": "", "impact": "", "mitigation": ""}
  ],
  "pending_human": [
    {"item": "", "why": "", "action": ""}
  ],
  "change_log": [
    {"step": "init", "time": "ISO8601", "action": "", "detail": ""}
  ],
  "errors": [],
  "degradations": [],
  "runtime": {"wall_clock_seconds": 0, "model_requests": 0}
}
```

- `case_id`、`status`、主产物、`sources`、`uncertainty`、`change_log` 必填；`pending_human` 在有人工待决项时必填。
- 若 `case.json` 中存在题目 `output_contract` 定义的其它键，必须**原样回填全部键**（缺值给 `null`/`[]`），不改名、不删未知键。
- `status` 语义：
  - `complete`：全部交付且自检通过；
  - `partial`：部分交付，须在 `uncertainty`/`change_log`/`degradations` 标明 limits；
  - `failed`：未完成且说明原因；
  - `insufficient_evidence`：材料不足以支撑任何图元，不编造；
  - `input_error`：输入不可解析（坏 JSON/坏编码），记录 `errors`。

## 2. 评分维度对照（每题 100 分）

| 维度 | 分值 | 本 Skill 的落实点 |
|-|-|-|
| 实际效果 | 40 | 材料→实体/关系/图例抽取→确定性渲染→导出；provenance 每条带 locator+逐字 quote |
| 使用增益 | 25 | 改 spec 重跑即重绘；可编辑 SVG 改标签不重画；change_log 如实记录返工轮次 |
| 创新增益 | 15 | 文字→实体关系清单方法；图例与 entities 同源；导出前 selftest.py 合规检查真实运行 |
| 可靠体验 | 10 | 按 SKILL.md 可独立出图；重复运行产物一致；缺数据/缺字体/导出失败如实标注 |
| 生态贡献 | 10 | 说明、依赖、路径、边界齐全；调色板/图例规范/导出预设/语法规范可复用 |

## 3. 自动自检项（scripts/selftest.py 可执行）

- [ ] `result.json` 存在、可解析、含 `case_id` 与非空 `status`
- [ ] `editable_source_files`/`export_files` 列出的每个文件真实存在
- [ ] SVG 是良构 XML（`xml.etree.ElementTree` 可解析），含 `<g>` 图层与可编辑 `<text>`
- [ ] 每个 provenance 条目含非空 `locator` 与 `quote`；`element` 能回指规范中的 id
- [ ] 图例颜色数 == 数据表分类数（若 `data.mapping` 存在则逐项回指）
- [ ] 关系 `from/to` 引用的实体均存在；无重复实体 id
- [ ] `status != complete` 时 `uncertainty` 或 `degradations` 或 `errors` 非空
- [ ] 确定性：重跑 `spec_to_svg.py` 两次 SVG 字节一致；同输入重跑主流程「科学内容核心」哈希一致

## 4. 人工自检清单（Agent 交付前逐条勾选）

### 实际效果（40）
- [ ] 走通「材料→图稿→导出」，不是套模板插画
- [ ] 每个实体、关系、方向、层级与来源一致（抑制≠激活、上下游不倒置、包含≠并列）
- [ ] 图中不出现材料中不存在的基因名/浓度/时间点/单位
- [ ] 图例项、配色、标记与数据表分类和数值逐项一致
- [ ] 覆盖实体、关系、图例、标注、单位、子图编号等必要图面元素
- [ ] provenance 每条都能定位到来源，可独立复核

### 使用增益（25）
- [ ] 数据改动后能重绘（改 spec 重跑 spec_to_svg.py）
- [ ] 输出可编辑 SVG，改标签不整张重画
- [ ] change_log 如实记录人工修改量与返工轮次

### 创新增益（15）
- [ ] 文字描述→实体关系清单的抽取方法可说明
- [ ] 图例与 entities 同源（legend/data 由同一规范驱动）
- [ ] selftest.py 导出前合规检查真实运行且退出码为 0

### 可靠体验（10）
- [ ] 按 SKILL.md 可独立完成一次出图
- [ ] 重复运行稳定产出，异常可识别可处置
- [ ] 缺数据/缺字体/导出失败时如实标注，不编造

### 生态贡献（10）
- [ ] 说明、依赖、路径、边界与维护信息齐全
- [ ] 调色板/图例规范/导出预设可复用
- [ ] 沉淀可分享的案例与经验（不预写正式答案）

## 5. 确定性校验策略（本版重点）

「同输入两次产物一致」保证的是**科学内容与产物字节**的一致；墙钟时间、模型请求数等运行元信息天然每次不同，属 `runtime` 字段，不计入哈希。

- `spec_to_svg.py`：无随机、无时间戳 → 同规范字节 → 同 SVG 字节。
- `selftest.py`：对 `result.json` 的确定性核心（`case_id`、`status`、`provenance`、`sources`、`uncertainty`、`editable_source_files`、`export_files` 的路径/尺寸/格式字段）取 SHA-256，写入 `/app/work/.determinism.json`。下一次运行若输入未变而核心哈希不同，报告 `determinism_violation` 并退出码 1。
- 判定「输入未变」的方法：对 `/input/` 下所有文本材料取哈希存入同一清单。材料字节未变 + 核心哈希变化 = 违反确定性；材料变化 + 核心哈希变化 = 正常重绘。

## 6. 降级与诚实标注规则

| 场景 | 处置 |
|-|-|
| PDF 无文本层 | 用 pdftotext/pdftoppm/OCR，`sources` 标「OCR 识别，需人工复核」 |
| 缺字体 | font-discovery 找替代并记录替换；无法嵌入 `font_check="none"` 并写入 uncertainty |
| 渲染/导出失败 | 保留 SVG，`export_files` 记 failed 与原因，`status=partial`，不伪造 PNG |
| 材料缺失/矛盾 | 记入 `uncertainty`；不确定实体不画或标「推测」，绝不编造基因名/浓度/时间点/单位 |
| 坏 JSON/坏编码 | 捕获写入 `errors`/`degradations`，继续处理其余材料，不崩溃 |

所有 limitations 写入 `uncertainty` 与 `change_log`，不得在 `result.json` 冒充完成；不伪造评测分数与运行结果。
