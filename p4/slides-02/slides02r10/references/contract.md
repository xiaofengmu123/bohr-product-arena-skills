# 输入/输出契约与中间表示（contract）

本包采用“规格先行”架构，运行时三条数据链各自独立、可分别复核：

- `deck_spec.json`（`/app/work/`）＝ 内容事实源：页级主张、叙事角色、图/表引用、locator+quote、讲稿备注、字数预算。
- `evidence_map.json`（`/app/work/`）＝ 证据映射：由 `bind` 从 spec 归并，逐页保留 `locator` 与逐字 `quote`。
- `result.json`（`/app/output/`）＝ 交付契约：只放题目要求的字段与保留字段，不混入过程文件路径。

## 1. result.json 顶层（示意，字段名以 case.json 的 output_contract 为准）

```json
{
  "case_id": "<来自 case.json，字符串>",
  "status": "partial | complete | failed",
  "presentation_file": "<相对 /app/output 的路径或 null>",
  "preview_files": [],
  "speaker_notes": "<路径或内联对象，按契约>",
  "page_plan": {},
  "evidence_map": "<路径或数组，按契约>",
  "version_records": {},
  "sources": [{"ref": "", "loc": "", "kind": "", "quote": "", "used_in": []}],
  "uncertainty": {"unresolved": [], "missing_artifacts": [], "notes": ""},
  "pending_human": [],
  "change_log": [{"time": "", "step": "", "action": "", "status": "", "reason": ""}]
}
```

- `output_contract` 声明的字段必须**原样回填**：字段名不改、未知键不删；未完成值用 `null`/`[]`，绝不编造。
- `status=complete` 仅当契约字段齐、主产物存在可打开、预览件真实、证据映射逐页齐备时使用；否则 `partial` 并写清局限。
- `sources` 每项尽量给 `loc`（图号/页码/章节）与 `quote`（逐字片段）；冲突来源两项都记并标 `source_conflict`。

## 2. deck_spec.json 最小结构

```json
{
  "title": "<页面主题或材料标题>",
  "audience": "<导师|评审|同行|通用>",
  "template": "<母版文件 rel 路径或 null>",
  "slide_count": 12,
  "pages": [
    {
      "role": "problem|method|result|comparison|limitation|conclusion|...",
      "claim": "<本页单一可证伪主张>",
      "bullets": ["<要点，≤4 条，每条 ≤ 28 字>"],
      "figure": ["Fig 3"], "table": [],
      "locator": "paper.md#p7 或 PDF 页码/章节",
      "quote": "<原文逐字片段，支撑 claim 的数字/结论>",
      "notes": "<讲稿备注>",
      "char_budget": 320
    }
  ]
}
```

- `role` 必须属于既有白名单（脚本 `KNOWN_ROLES`），未知角色记入缺口。
- `locator` + `quote` 缺一即视为证据不完整；数值/结论无原文支撑不得进入 `claim`。
- 每页只承担一个叙事角色；数字必须逐字来自 `quote`，不得按印象取整。

## 3. 脚本路径与参数

- 全部脚本相对 Skill 根目录调用：`python3 scripts/main.py ...`、`python3 -B scripts/selftest.py`。
- 全局参数 `--input-dir/--work-dir/--output-dir` 默认 `/input`、`/app/work`、`/app/output`，可用环境变量 `INPUT_DIR`/`WORK_DIR`/`OUTPUT_DIR` 覆盖。
- 脚本只依赖 Python 标准库；PPTX 读写使用 `zipfile` + `xml.etree`，不依赖 python-pptx/soffice。渲染 PDF/PNG 由运行时工具 office-renderer/soffice 完成，脚本只做读回与规则校验。

## 4. 最小调用示例（示意，非正式案例结果）

```bash
# 1) 前 3 分钟落骨架（坏 JSON/空目录也退出 0，并记录 degradation）
python3 scripts/main.py scaffold

# 2) 盘点材料
python3 scripts/main.py inventory

# 3) Agent 写 /app/work/deck_spec.json 后校验结构
python3 scripts/main.py spec

# 4) 绑定证据、读回渲染件、终检
python3 scripts/main.py bind
python3 scripts/main.py pptx-info /app/output/deck.pptx
python3 scripts/main.py check          # 退出码 1=有缺口，仍写 self_check.json
python3 scripts/main.py finalize --status complete

# 5) 离线自检（不依赖正式案例）
python3 -B scripts/selftest.py         # 退出码 0=通过
```

示意结果骨架（来自 `scaffold`，字段以实际 `output_contract` 为准）：

```json
{
  "case_id": "example-case",
  "status": "partial",
  "presentation_file": null,
  "preview_files": [],
  "speaker_notes": null,
  "page_plan": null,
  "evidence_map": null,
  "version_records": null,
  "sources": [],
  "uncertainty": {"unresolved": [], "missing_artifacts": [], "notes": "骨架阶段"},
  "pending_human": [],
  "change_log": [{"time": "", "step": "scaffold", "action": "write result skeleton", "status": "partial", "reason": ""}]
}
```
