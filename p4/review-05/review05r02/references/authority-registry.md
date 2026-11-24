# 依据绑定与防编造规则（authority-registry）

每条 finding 的 `basis` 必须回答同一个问题：**这条判定凭什么成立？** 依据不可靠，问题就不可靠。评分明确惩罚"编造条款、期刊要求或文献元数据"，因此本 Skill 把依据绑定做成硬约束。

## 1. basis 结构

```json
"basis": {
  "kind": "material_quote",
  "status": "resolved",
  "source": "/input/paper.md",
  "version": "",
  "date": "",
  "text": "材料中关于目标期刊或授权范围的原文"
}
```

- `kind`：依据类型，只能取以下六种之一。
- `status`：`resolved`（权威依据已取得）、`general_principle`（通用审查惯例）、`unavailable`（无权威依据）。
- `source` / `version` / `date`：权威依据的出处、版本与日期；拿不到就留空字符串，不得虚构。
- `text`：依据的原文（条款、期刊要求、授权范围、许可声明、材料自述）。拿不到就写空并 `status=unavailable`。

## 2. 六种 kind 与取得方式

| kind | 含义 | 取得方式 |
|---|---|---|
| `material_quote` | 材料原文（材料自述的目标期刊、伦理批件号、数据授权、许可声明等） | 直接引用 `/input/` 下材料，带 locator |
| `target_journal_guide` | 目标期刊/会议的作者指南 | 材料或允许资料范围内提供的指南原文；没有就 `unavailable` |
| `dataset_license` | 数据集的许可条款 | 材料或允许资料范围内提供的许可文本；没有就 `unavailable` |
| `ethics_policy` | 伦理规范或批件条款 | 材料自述的批件编号/机构规范；没有就 `unavailable` |
| `general_principle` | 本 Skill 内置的通用审查惯例（见下） | 引用 `skill:audit-rules.md` 中的条款编号 |
| `unavailable` | 无法取得权威依据 | 仅陈述事实性观察，不升级为规范指控 |

## 3. 防编造硬规则

1. **不得**写出任何材料或允许资料范围之外的具体条款、期刊要求、批件号、许可条款、文献元数据。
2. 需要具体权威依据而拿不到时，`kind=unavailable`、`status=unavailable`，`severity` 强制降为 `minor`/`info`，并写入 `pending_human`。
3. 引用文献是否真实存在：只能核验材料内部自洽（正文引用与参考文献列表是否对得上）与可取得的数据库记录；无法核验时标 `not_reported` / `pending_human`，不得断言"该文献不存在"或"该文献存在"。
4. 通用审查惯例必须引用 `audit-rules.md` 的条款号，并在 `basis.text` 中复述该惯例的原文，不冒充期刊要求。

## 4. 内置通用审查惯例（可引用）

- `gp-citation-verify`：被引文献应在参考文献列表中存在，且正文论断与被引文献内容对应；这是可核验的学术惯例，非特定期刊条款。→ `audit-rules.md` §1 citation。
- `gp-privacy-minimize`：涉及人的研究材料应去除或最小化可识别个人信息，除非取得授权并说明依据；这是通用隐私合规原则。→ §1 privacy。
- `gp-ethics-consent`：涉及人的研究应说明知情同意与伦理审批依据；动物实验应说明规范遵循；这是通用伦理要求。→ §1 ethics。
- `gp-copyright-attribution`：第三方图片/表格/代码/数据应标注来源与许可类型，明确复用条件；这是通用版权合规原则。→ §1 copyright。
- `gp-method-reproducibility`：方法、参数、数据可得性应足以让同行复现；这是通用可复现性原则。→ §1 method。
- `gp-statistics-report`：统计结论应交代检验方法、样本量与效应量/置信区间，避免以相关性冒充因果。→ §1 statistics。

引用示例：`"kind": "general_principle", "source": "skill:audit-rules.md", "text": "gp-citation-verify: ..."`。

## 5. 校验

`validate.py` 会检查：

- `basis.kind` 在六种合法值内；
- `kind=unavailable` 且 `severity ∈ {critical, major}` 时报 FAIL，要求降级；
- `source` 指向的材料/条款在允许范围内可定位。

修复 FAIL 后再提交；预算不足时如实标 `partial` 并保留 `pending_human`，不强行补依据。
