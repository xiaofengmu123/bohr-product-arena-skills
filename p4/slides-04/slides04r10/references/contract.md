# 契约说明：result.json 与 Deck IR

## 1. result.json 根字段

以 `/input/case.json` 的 `output_contract` 为准**逐键原样回填**（不改名、不删未知键；缺值 `null`/`[]`）。`output_contract` 可能是 dict 或 `[{name,type,required}]` 列表，脚本两者都识别；缺失时回退到下列七个标准键。

| 字段 | 要求 |
|---|---|
| `case_id` | 从 case.json 原样复制 |
| `status` | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `editable_source` | `{path,format:"pptx",slides,mode,template:{path,masters,layouts_used},sha256}` |
| `export_artifact` | `{pdf:{path,pages,engine}, png:{path,count,engine}}`；PDF 页数 == 幻灯片数 |
| `content_map` | 逐页 `{n,title,claim,role,evidence[],assets,notes}` |
| `compliance_checks` | `[{id,required,measured,verdict}]`，verdict ∈ pass/warn/fail |
| `exceptions` | `[{item,reason,impact}]`，所有降级与缺口如实列出 |

## 2. 统一信封（附加字段）

| 字段 | 内容 |
|---|---|
| `sources` | 材料清单 `[{source_id,rel_path,kind,size}]` |
| `uncertainty` | 未确证/需人工核对的点 |
| `pending_human` | 需人工补料后才能完成的项 |
| `change_log` | `[{step,detail,at}]` 改动记录 |
| `errors` / `degradations` | 硬错误与降级路径 |
| `runtime` | `{skill_version,task,steps,contract_keys,contract_missing,elapsed_ms,generated_at}` |
| `coverage` | `{slides,content_map,compliance_checks,evidence_verbatim,evidence_total}` |

## 3. status 语义

- `ok`：lint 无 FAIL、compliance 无 fail、pptx 真继承且页数一致、证据引文逐字命中。
- `partial`：产物可用但有降级（缺模板、溢出、引文未逐字命中、某检查 warn/fail）。
- `insufficient_evidence`：无可引用文本材料（语料为空）。
- `input_error`：case.json 缺失/坏 JSON/未知异常（仍写合法 result.json，退出码 0）。

## 4. Deck IR（`/app/work/deck.json`）

单一事实源；pptx、content_map、report 均由此派生。字段：

```json
{
  "case_id": "…",
  "mode": "adapt",
  "plan": {"purpose": "", "audience": "", "duration_min": 10, "narrative": ""},
  "template": {"path": "template.pptx"},
  "pages": [
    {"n": 1, "role": "title", "title": "…", "claim": "一句话主张",
     "bullets": ["要点"], "notes": "讲稿",
     "evidence": [{"source_id": "S1", "locator": "para.1", "quote": "逐字原文"}],
     "assets": [{"id": "fig1", "file": "figures/fig1.png", "caption": "…", "fit": "contain"}]}
  ],
  "uncertainties": [], "pending_human": []
}
```

- `role` 枚举：`title/section/background/method/result/discussion/limitation/conclusion/other`；必备 `title/background/method/result/conclusion`。
- `evidence` 由 `normalize_ir` 自动补 `quote_status`：`verbatim`（逐字命中语料）/`paraphrase`（未逐字命中，需人工确认）/`missing`（无引文）。
- `assets` 只描述来源与适配方式（等比 `contain`/裁切 `cover`），实际图片由 office-renderer/agent 按容器等比缩放写入，脚本不伪造图片文件。

## 5. 证据溯源（硬要求）

- 每个关键结论/数值/图元必须带 `source_id + locator + quote`，quote 逐字命中材料（去空白/大小写后子串命中，保留标点避免把转述当直引）。
- 冲突信息不隐藏：同一指标两个来源不一致时都记录并标 `source_conflict` 入 `uncertainty`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。
