# 降级与不确定性口径（本版重点）

本 Skill 的核心设计取舍：**完整性可以分阶段补齐，但边界必须即时显式化**。任何“现在还不知道”的信息都要落入 `uncertainty` 或 `pending_human`，绝不通过脑补让结果看起来完整。

## 1. uncertainty 字段语义

| 字段 | 含义 | 触发示例 |
|-|-|-|
| `claims_requiring_confirmation` | 已写但需人工确认的论断 | 从摘要推断正文结论 |
| `conflicting_specs` | 模板版本差异或相互冲突的规定 | 院系规定与模板字号不一致 |
| `missing_assets` | 缺失的字体/样式/模板资源 | 未提供 Liberation Serif |
| `material_gaps` | 材料本身的缺口 | 只有摘要、全文不可得 |
| `not_verifiable` | 无法实测核验的项 | 栏数/行距无工具可测 |
| `degradations` | 实际发生的降级 | 预览未生成、依赖不可用 |
| `limitations` | 整体限制声明 | 时间/请求预算导致的范围收缩 |

## 2. 判定顺序（同一事实可能同时属于多类）

1. 先判断“缺的是什么”：缺材料→`material_gaps`；缺字体/样式资源→`missing_assets`；两者都有→都记。
2. 再判断“能否核验”：无法实测→`not_verifiable`，对应记录 `verification=unverified`。
3. 再判断“是否冲突”：多来源不一致→`conflicting_specs`，不偏选。
4. 最后判断“是否需人确认”：任何上述未决项若影响交付结论，同步进 `pending_human`。

## 3. 缺数据的表达

- 缺失数值一律写 `not_reported`，不填估计值；估计值须标 `estimated` 并给出依据。
- 缺材料只写“据现有材料/摘要表明”，不补写正文细节。
- 缺软件（pdf-text-tool/ocr/latex/office-renderer/font-discovery 不可用）时，标准库脚本仍可读纯文本；标 `degradations` 并注明“预览未生成/依赖不可用”，不伪造渲染文件。
- 无法校验来源真实性时，`sources` 标 `verification="unverified"`，`change_log` 记录核验路径。

## 4. 硬性失败与状态

- 缺 `case_id`、缺契约字段、JSON 无效、存在 TODO/占位符：`verify_contract.py` 退出码 2，先修复再继续。
- 摘录无法在材料全文定位：退出码 1（软警告），须将对应项写入 `limitations` 或标 `pending_human`。
- 材料不足以完成任务：`status=insufficient_evidence`；运行异常无法继续：`status=failed`；部分完成：`status=partial`。

## 5. 反编造红线

- 禁止编造评测分数、运行结果、文献元数据（DOI/卷/页）、字体嵌入状态或科学数据。
- 禁止为了“看起来完整”而补写模板规格、渲染文件、来源定位或核验结果。
- 示例一律标明“示例”，不得冒充正式案例结果。

## 6. 示例（仅示意，非正式案例）

```json
{
  "uncertainty": {
    "conflicting_specs": [
      {"spec_id": "SPEC-003", "detail": "模板要求正文五号，院系补充规定要求小四，二者冲突", "resolution": "按模板执行，补充规定列入 pending_human"}
    ],
    "missing_assets": [
      {"asset": "LiberationSerif-Regular.ttf", "effect": "无法实测字体嵌入", "fallback": "渲染时标 degraded"}
    ],
    "not_verifiable": [
      {"item_id": "SPEC-007", "reason": "行距 20 磅无工具可直接实测", "action": "标 unverified，待人工确认"}
    ]
  },
  "pending_human": [
    {"id": "PH-001", "item": "SPEC-003 与院系规定冲突的最终取舍", "why": "影响交付正确性"}
  ]
}
```
