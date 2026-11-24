# 失败类型与降级处置对照表

本表规定各类异常在 `result.json` 中的如实记录方式。原则：任何异常都不编造数据或结果，只降级、标注、并写明缺口。

## 1. 输入侧异常

| 场景 | 处置 | 记录位置 |
|-|-|-|
| 输入目录为空 | 退出码 0，写合法结果，`status=insufficient` 语义 → 用 `partial`，`case_id` 若可得仍填写 | `uncertainty.material_gaps` |
| `case.json` 坏 JSON/坏编码 | 捕获并记录错误，继续处理其余材料；无法解析 `case_id` 时 `case_id=""` | `change_log`、`uncertainty.limitations` |
| 材料文件超 2MB | 跳过并记录 | `sources` 或 `change_log` |
| 材料只有摘要/全文不可得 | 只写“摘要表明”，不补正文细节 | `uncertainty.limitations` |
| 文件按领域标签与实际内容不符 | 以实际文件内容为准，不按标签脑补 | `change_log` |

## 2. 模板与规格侧异常

| 场景 | 处置 | 记录位置 |
|-|-|-|
| 模板缺失/版本不明 | 标 `not_specified`，写 `pending_human`，不假设默认值 | `version_boundary.human_confirmation` |
| 多个模板版本冲突 | 全部列入，并列不隐藏，正文标“存在冲突” | `uncertainty.conflicting_evidence` |
| 字体缺失 | 标 `not_checkable`，记录所需字体名 | `version_boundary.anomalies` |
| 自动抽取规格（`confidence=low`） | 保持 `pending`，人工复核后才能标 `compliance` | `template profile` |

## 3. 转换与导出侧异常

| 场景 | 处置 | 记录位置 |
|-|-|-|
| 转换失败/内容无法保真 | 保留源文件与差异记录，`status=partial/failed` | `version_boundary.material_gaps` |
| 预览未生成（依赖不可用） | 标注“预览未生成/依赖不可用”，不伪造渲染文件 | `deliverable.preview_path` 留空 + `limitations` |
| 文件打不开/页码目录不可用 | 标 `fail`，写 `pending_human` | `template profile`、`version_boundary` |
| 内容单元被删除/图片化 | `preserved=false`，`transform=dropped` | `version_boundary.material_gaps` |

## 4. 证据侧异常

| 场景 | 处置 | 记录位置 |
|-|-|-|
| 摘录无法在材料全文定位 | 标 `limitations`，`verify` 报 warning（退出码 1） | `uncertainty.limitations` |
| 引用无法验证真实性 | `sources.verification=unverified`，记录核验路径 | `sources`、`change_log` |
| 原文与模板冲突 | 两边都记录，标 `source_conflict` | `uncertainty.conflicting_evidence` |
| 数字/术语/图表/引用无来源 | 不新增材料外事实，标 `claims_requiring_confirmation` | `uncertainty` |

## 5. 硬性失败

以下情况 `scripts/main.py verify` 退出码 2，必须先修复再继续：

- `case_id` 缺失或与 `/input/case.json` 不一致。
- `result.json` 缺少 `output_contract` 的任何字段。
- `result.json` 缺少 `sources / uncertainty / change_log / pending_human` 任一保留字段。
- `result.json` 不是合法 JSON 或顶层不是对象。
- `status` 取值不在 `partial / complete / failed` 中。

退出码 1 = 有 warning（如摘录未定位、占位符、产物路径不存在），可交付但需说明；退出码 0 = 全部通过。
