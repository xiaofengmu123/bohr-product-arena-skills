# 自检清单与失败模式

提交/交付前逐条勾选；每一条对应 `task.md` 的一个评分维度。本清单与
`scripts/main.py validate` 的自动校验互补：脚本校验契约与路径，本清单校验
内容正确性与可核验性。

## 自检清单（逐条勾选）

### 结果完整性（实际效果·完整性 5 分）
- [ ] 六类要素全部覆盖：步骤 / 实验设置 / 参数 / 数据集 / 对照 / 复现与限制。
- [ ] 无空节、无占位空话；每节都有材料支撑或显式 `MATERIAL GAP`。

### 结果正确性（实际效果·正确性 15 分）
- [ ] 正文每个数值、参数、版本、种子与材料逐字一致，无补数。
- [ ] 参数表与正文数值对齐；train/val/test 条件无串台。
- [ ] 限定词强度（"显著/可能"等）不升不降。

### 结果可核验性（实际效果·可核验性 5 分）
- [ ] 每个事实性句子挂 `[S#]`；`evidence_map.json` 齐且 locator+quote 非空。
- [ ] `preserved_facts.json` 记录数值/单位/统计量，逐条可回溯。

### 输出契约（运行后输出要求）
- [ ] `result.json` 含 `case_id` 与 `output_contract` 全部原始字段，键名数量一致。
- [ ] `status / sources / uncertainty / change_log / pending_human` 齐，未知值给 `null/[]/{}`。

### 诚实标注（异常处理 3 分）
- [ ] 缺口显式标注 `MATERIAL GAP / pending_human / not_reported`，无静默编造。
- [ ] 冲突并列记录并标 `source_conflict`；限制条件指向具体失效场景。

### 产物可编辑（使用与方法说明）
- [ ] 可编辑主产物 + 渲染 PDF/PNG 预览均存在；`validate` 通过。
- [ ] `change_log` 完整记录骨架→初稿→改进→收口，可回退。

## 失败模式与处置

| 场景 | 处置 | 结果状态 |
|---|---|---|
| 空输入 / 缺材料 | 列缺口，不生成示例答案 | `insufficient_evidence` |
| 坏 JSON / 坏编码 | 记录 `errors/degradations`，继续其余材料 | 如实 |
| 无 pdftotext / Office | 用 pdf-text-tool / office-renderer / ocr-tool；失败记录 | 不影响其余 |
| 参数无来源 / 版本不明 | 标 `pending_human` / `not_reported` | `partial` |
| 冲突信息 | 双方并列 + `source_conflict`，不自行裁定 | 如实 |
| 时间/请求耗尽 | 先保 result.json 合法 + 初稿 + 证据映射 | `partial` |

底线：宁交诚实的 `partial`，不交编造的 `ok`。
