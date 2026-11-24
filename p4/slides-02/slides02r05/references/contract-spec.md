# 输出契约规范与接口（contract-spec）

与 SKILL.md 第 3、7 节对应。所有示例均为示意，不冒充任何正式案例结果。

## 1. 契约发现规则
`scripts/contract.py` 从 case.json 中按以下优先级发现 output_contract：
1. `output_contract`（字段名数组，或 {字段: 类型提示} 对象，或含 `fields` 键的对象）；
2. `outputContract`；3. `contract`；
4. 均缺失时用内置默认契约（presentation_file/preview_files/speaker_notes/page_plan/evidence_map/version_records）。

类型提示只用于决定缺省空值：`array|list → []`，`object|dict|map → {}`，其余（string/number/bool/any）→ `null`。

## 2. result.json 骨架（示意）
```json
{
  "case_id": "<来自 case.json，字符串>",
  "status": "partial | complete | failed | insufficient_evidence | input_error",
  "presentation_file": null,
  "preview_files": [],
  "speaker_notes": {},
  "page_plan": {},
  "evidence_map": [],
  "version_records": [],
  "sources": [{"ref": "", "loc": "", "kind": "", "used_in": []}],
  "uncertainty": {"unresolved": [], "missing_artifacts": [], "notes": ""},
  "pending_human": [],
  "change_log": [{"t": "", "seq": 1, "step": "", "action": "", "status": "", "reason": ""}],
  "errors": [],
  "degradations": [],
  "runtime": {"started_at": null, "finished_at": null, "budget_seconds": 900,
               "budget_requests": 64, "max_tokens_per_response": 16384, "contract_source": ""}
}
```
`presentation_file` 等字段名以 case.json 的 output_contract 为准；本文件只说明结构与保留字段。

## 3. 契约字段对齐不变量
- 契约声明字段**必须全部出现**在 result.json 顶层；缺值显式给 null/[]/{}，不删除、不改名。
- case.json 中出现的未知键在增量合并时**保留不丢**。
- 保留字段（case_id/status/sources/uncertainty/pending_human/change_log/errors/degradations/runtime）恒存在。
- `change_log` 恒为数组且按 `seq` 递增；脚手架、成稿、渲染、终检四阶段至少各一条。
- 重复运行 scaffold 只补齐缺字段，不覆盖已有值（幂等增量）。

## 4. 校验与退出码
`main.py check` 写 `/app/output/self_check.json`，字段逐条给出 present/empty/ok；`--strict` 时契约字段为空也计缺口。
`main.py finalize --status complete` 会先按严格校验；产物缺失或契约字段为空时**自动降级为 partial** 并写 `degradations`，禁止伪造 complete。

| 情形 | 处置 | 结果标注 |
|-|-|-|
| 论文 PDF 缺失 | 只用可确证材料 | limitation |
| 图缺失/位图无数据 | 引用原图+定位，不重绘不补值 | uncertainty |
| 模板槽位不足 | 映射最近似版式 | uncertainty |
| 字体缺失 | font-discovery 选替代字体 | uncertainty |
| 页面溢出 | 删要点/拆页，字号≥16pt | change_log |
| 导出失败 | 交付源文件+部分预览 | status=partial + 失败原因 |
| 缺 python-pptx/soffice | 回退 Markdown/HTML | status=partial，不伪造渲染 |
| 数字/事实不确定 | 不进页面主张 | uncertainty |

## 5. 已知边界
- 本 Skill 不实现科研绘图算法与 PPT 软件本身；图表重绘仅在数据可复核时进行。
- 脚本只依赖 Python 标准库；PPTX 生成由运行时已装工具完成。
- 正式案例、白板与评分由平台提供；本包不含预写答案或隐藏测试。
