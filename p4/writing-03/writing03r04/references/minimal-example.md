# 最小调用示例

本文内容全部为**示意示例**，用于说明接口与完整使用流程，不冒充正式案例结果。正式案例、白板与评测结果由平台提供。

## 1. 输入结构

```text
/input/
├── case.json        # 必含 case_id；可选 output_contract
├── task.md          # 题面与运行时约定
└── materials/
    ├── manuscript.docx
    └── template.docx
```

`case.json` 示意（字段名以平台实际 `output_contract` 为准，本 Skill 原样回填全部键）：

```json
{
  "case_id": "case-demo-000",
  "task": "按模板适配稿件并保留来源",
  "output_contract": {
    "deliverable": {"content": "", "artifact_path": [], "description": "可编辑稿件 + 渲染预览"},
    "fact_source_map": [],
    "version_boundary": {"changes": [], "pending_human": [], "material_gaps": [], "exceptions": []}
  }
}
```

## 2. 调用方式（在 /skills/weekly-skill 根目录，相对路径）

```bash
# 第一步：早期落盘 partial 骨架（前 3 分钟内）
python scripts/main.py --case /input/case.json --task /input/task.md \
  --out /app/output --work /app/work --material-root /input --material-root /app/work

# 第二步：执行格式适配与产物生成后，契约 + 摘录可核验性自检
python scripts/verify_contract.py --result /app/output/result.json \
  --case /input/case.json --report /app/output/verification_report.json \
  --material-root /input --material-root /app/work

# 第三步：交付前本地自检（可选，构造临时用例，不改动正式结果）
python scripts/selftest.py
```

## 3. 示意输出（result.json 骨架，非正式结果）

```json
{
  "case_id": "case-demo-000",
  "status": "partial",
  "deliverable": {"content": "", "artifact_path": [], "description": ""},
  "fact_source_map": [],
  "version_boundary": {"changes": [], "pending_human": [], "material_gaps": [], "exceptions": []},
  "sources": [],
  "uncertainty": {
    "claims_requiring_confirmation": [],
    "conflicting_specs": [],
    "missing_assets": [],
    "material_gaps": [],
    "not_verifiable": [],
    "degradations": [],
    "limitations": []
  },
  "pending_human": [],
  "change_log": [{"ts": "2026-11-24T00:00:00+00:00", "action": "scaffold", "detail": "初始骨架落盘；status=partial"}]
}
```

`verify_contract.py` 的示意退出码含义：`0`=通过；`1`=软警告（如摘录未在材料全文定位）；`2`=硬失败（缺 `case_id`/缺契约字段/JSON 无效/存在占位符）。

## 4. 预期行为

- 空输入目录、坏 JSON：`main.py` 仍退出码 0，写出合法结果且 `status=input_error`，不抛异常。
- 重复运行：`main.py` 幂等，`change_log` 只保留一条 `scaffold`，不整体重建已有字段。
- 材料缺字体/缺全文：如实写 `uncertainty`/`pending_human`，不补造数值或渲染文件。
