# 评分维度 → 落点映射与常见失败模式

## 1. 评分维度映射

| 评分维度（100 分） | 本包落点 |
|---|---|
| 实际效果 40（完成度15/正确性15/完整性5/可核验5） | 主交付真实完整、格式规格与内容同时正确、检查项与内容单元全覆盖、逐条 `source_id + locator + quote` 可复现 |
| 使用增益 25（质量15/效率5/可验证5） | 规格抽取→核对→成稿→契约回填的机械化流程，减少逐项核对、重复排版与试错导出；`verification_report.json` 提供对照口径 |
| 创新增益 15 | 模板规格反推检查项清单、内容单元完整性核对、契约逐键回填 |
| 可靠体验 10 | 兜底交付、异常降级、重复运行一致、`status` 诚实 |
| 生态贡献 10 | SKILL/参考文档/脚本可复用，检查项清单可分享 |

## 2. 变体重点：输出契约完整

本版的核心设计取舍是**契约完整性优先**：

1. `contract_keys()` 解析 `output_contract` 的 dict/list 两种形态，逐键回填、不改名、不删键。
2. 无契约时用三段式兜底 `deliverable / fact_source_map / version_boundary`，并在 `runtime.contract_fallback=true` 如实声明。
3. `build` 产出 `verification_report.json`：逐键存在性 + 逐条 quote 命中，机械可复核。
4. `change_log` 每次落盘追加（`init`/`scaffold`/`build`），绝不整体重建后丢弃历史。

## 3. 常见失败模式

- 先改格式后补证据（证据与论断对不上）。
- 只改字号加粗，未改样式绑定，导致层级与目录错位。
- 交叉引用编号与图注/表注不一致。
- 参考文献字段顺序、作者截断、标点与中英文混排未逐条核对。
- 转换时公式/脚注/批注/域代码被图片化或丢失。
- 把相关性写成因果、把弱证据写成共识、冲突只留一方。
- `result.json` 缺 `output_contract` 字段、`case_id` 不一致、无 `change_log`。

## 4. 诚实边界

- 不伪造文献、数据、引文、模板继承、运行记录或评审分数。
- 材料没给的信息不补造；只做题目要求范围内的写作/修改。
- 主交付必须可编辑（md/tex/docx/SVG），导出件必须真实可打开。
- 示例仅标注为示例，不冒充正式案例结果。

## 5. 自检矩阵（逐条对应评分点，提交前跑 `scripts/selftest.py` 与 `main.py selfcheck`）

| # | 检查项 | 判定 | 对应评分点 |
|---|---|---|---|
| 1 | `case_id` 与 `/input/case.json` 一致 | `PASS`/`WARN` | 任务完成度 |
| 2 | `result.json` 含 `output_contract` 全部键 + `status/sources/uncertainty/change_log` | `PASS`/`FAIL` | 结果完整性 |
| 3 | 每条实质论断/数字/引用有 `source_id + locator + quote` | 逐条 join | 找得到/对得上 |
| 4 | 每条 `quote` 在材料全文命中，未命中写入 `limitations` | `check`/`verification_report.json` | 结果可核验性 |
| 5 | 模板规格逐项来自原文，未给出者标 `pending_human` | 逐项核对 | 认得准 |
| 6 | 标题层级/编号/图注/交叉引用/参考文献字段顺序逐项核对 | `format_checks` | 排得对 |
| 7 | 正文/图表/公式/脚注/批注/域代码/目录内容单元完整 | `version_boundary.material_gaps` | 转得全 |
| 8 | 导出文件可打开、字体嵌入、页码目录可用、可编辑源保留 | 渲染预览 | 交得出 |
| 9 | 冲突并列、相关≠因果、弱证据≠共识 | `uncertainty.conflicting_evidence` | 说得准 |
| 10 | 无 TODO/占位符，`status` 诚实 | `selfcheck` | 异常处理 |

## 6. 证据链协议（从输入到成品的可复现记录）

每个事实性论断按以下五元组记录，保证"论断编号 → 摘录 → 定位 → 强度理由"可独立回查：

```json
{
  "claim_id": "C1",
  "claim": "正文论断（不改写原文数值与限定）",
  "source_id": "S1",
  "locator": "para.2",
  "quote": "逐字原文摘录",
  "kind": "direct | inferred",
  "strength": "strong | moderate | weak"
}
```

- `quote` 必须是原文逐字片段；`locator` 与 `show` 输出一致。
- 改写/移动/合并的每次操作写入 `change_log`：改前、改后、理由、定位。
- 冲突证据双方都保留，`support` 标 `contradicts`，不隐藏单边。
- 模板继承关系（word master → 样式）与域代码/嵌入对象是否保留，写进 `version_boundary.exceptions`。

## 7. 对照口径（增益验证，非实测分数）

本包的增益以**可复核记录**为证，文字自述不替代实际运行证据：

- 质量：`check` 的 PASS/FAIL 与 `verification_report.json` 的 quote 命中统计。
- 效率：机械化 `survey→show→check→build` 流程减少逐项核对、重复排版与试错导出。
- 稳定性：同一输入两次运行产物字段一致；空输入/坏 JSON/缺模板各自降级并记录。

实际分数以平台出分为准；本文件不声称任何具体分数。

