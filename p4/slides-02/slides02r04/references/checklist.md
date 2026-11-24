# 检查清单与接口（checklist）

与 SKILL.md 第 5–8 节逐项对应；最终交付前逐条核对。以下为接口说明与 schema 示例（示意，非正式案例结果）。

## 1. 终检清单（按评分维度，逐条可勾选）

实际效果（40）
- [ ] 可编辑演示文件存在且可打开；页数/页序符合约束；每页有叙事角色；备注齐备
- [ ] PDF/PNG 预览真实生成（可播放/可查看）
- [ ] 页上数字、图注、引用编号与原文一致，无编造/按印象取整
- [ ] 覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素
- [ ] evidence_map、sources、版本快照、change_log、self_check 齐全，可复跑复核

使用增益（25）
- [ ] 同输入同模板可比；记录返工页数/重排次数/复查轮次
- [ ] 质量与效率改进有 diff/日志/对照支撑，不以文字自述代替

创新增益（15）
- [ ] 页序规划、模板槽位填充、图表数据绑定、局部编辑有明确方法与边界说明
- [ ] 防编造机制（claim_kind/confidence/quote 强制）有可核验实现

可靠体验（10）
- [ ] 稳定触发与调用；三类论文/不同模板/重复运行不塌版
- [ ] 缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有识别与处置

生态贡献（10）
- [ ] 说明、依赖、参数、页数限制、已知问题齐全；脚本/骨架/清单可复用

## 2. result.json schema（示意）

```json
{
  "case_id": "<来自 case.json，字符串>",
  "status": "partial | complete | failed",
  "presentation_file": "<相对 /app/output 的路径或 null>",
  "preview_files": [],
  "speaker_notes": "<路径或内联对象，按契约>",
  "page_plan": {},
  "evidence_map": [
    {
      "page": 4, "role": "result", "claim": "…",
      "claim_kind": "fact", "confidence": "confirmed",
      "refs": ["Fig 3"], "locator": "paper.pdf#p7",
      "quote": "n = 42", "reason": null
    }
  ],
  "version_records": [
    {"name": "v1", "note": "…", "t": "…", "files": [
      {"path": "…", "exists": true, "bytes": 0, "sha256": "…", "mtime": 0}
    ]}
  ],
  "sources": [
    {"ref": "Fig 3", "loc": "paper.pdf#p7", "kind": "figure",
     "quote": "…", "used_in": [4]}
  ],
  "uncertainty": {
    "unresolved": [], "limitations": [], "missing_artifacts": [],
    "degradations": [
      {"t": "…", "what": "…", "evidence": "…", "attempted": "…",
       "failure": "…", "fallback": "…"}
    ], "notes": ""
  },
  "pending_human": [],
  "change_log": [
    {"t": "…", "step": "scaffold", "action": "init_partial_skeleton",
     "status": "partial", "reason": "…"}
  ],
  "runtime": {
    "started_at": "…", "ended_at": null, "budget_seconds": 900,
    "budget_requests": 64, "tokens_per_response": 16384, "contract_keys": ["…"]
  },
  "self_check": "self_check.json"
}
```

注：`presentation_file` 等字段名以 case.json 的 `output_contract` 为准；示例仅说明结构与保留字段，不得当作任何正式案例的答案。

## 3. 脚本调用（相对 Skill 根目录，运行时为 /skills/weekly-skill/）

```bash
python3 scripts/main.py scaffold [--force]
python3 scripts/main.py record source --ref "Fig 3" --loc "paper.pdf#p7" --kind figure --quote "…" --used-in 4
python3 scripts/main.py record evidence --page 4 --role result --claim "…" --refs "Fig 3" --loc "paper.pdf#p7" --quote "…" --kind fact --confidence confirmed
python3 scripts/main.py record version --name v1 --note "…" --files /app/output/deck.pptx
python3 scripts/main.py record uncertainty --kind limitation --text "…"
python3 scripts/main.py record uncertainty --kind pending_human --text "需人工确认：…"
python3 scripts/main.py record uncertainty --kind degradation --text "…" --evidence "…" --attempted "…" --failure "…" --fallback "…"
python3 scripts/main.py record artifact --field presentation_file --value /app/output/deck.pptx
python3 scripts/main.py check
python3 scripts/main.py finalize --status complete|partial [--note "…"]
python3 scripts/main.py selftest        # 或 python3 scripts/selftest.py
```

脚本只用 Python 标准库；目录可用 `INPUT_DIR`/`WORK_DIR`/`OUTPUT_DIR` 覆盖（默认 /input、/app/work、/app/output）。`check` 退出码 1 表示有 error 级缺口；`finalize --status complete` 在 check 存在 error 时会自动降级为 partial。

## 4. 降级决策表

| 情形 | 处置 | 结果标注 |
|-|-|-|
| 论文 PDF 缺失 | 只用可确证材料 | limitation |
| 图缺失/位图无数据 | 引用原图+定位，不重绘不补值 | uncertainty |
| 模板槽位不足 | 映射最近似版式 | uncertainty |
| 字体缺失 | font-discovery 选替代字体 | uncertainty |
| 页面溢出 | 删要点/拆页，字号≥16pt | change_log |
| 导出失败 | 交付源文件+部分预览 | status=partial + 失败原因 |
| 缺 python-pptx/soffice | 回退 Markdown/HTML | status=partial，不伪造渲染 |
| 数字/事实不确定 | 不进页面主张 | uncertainty / pending_human |

## 5. 已知边界

- 本 Skill 不实现科研绘图算法与 PPT 软件本身；图表重绘仅在数据可复核时进行。
- 脚本只依赖 Python 标准库；PPTX 生成由运行时已装工具（python-pptx/office-renderer）完成，脚本负责骨架、证据记录、降级登记、终检与自检。
- 正式案例、白板与评分由平台提供；本包不含预写答案或隐藏测试。
