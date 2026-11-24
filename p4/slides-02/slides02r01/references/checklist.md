# 检查清单与接口（checklist）

与 SKILL.md 逐项对应；最终交付前逐条核对，并用 `python3 scripts/selftest.py check` 机械复核。以下 schema 示例为示意，不是任何正式案例的答案。

## 1. 终检清单（按评分维度）
实际效果（40）
- [ ] 可编辑演示文件存在且可打开；页数/页序符合约束；每页有叙事角色；备注齐备
- [ ] PDF/PNG 预览真实生成（可播放/可查看）
- [ ] 页上数字、图注、引用编号与原文一致，无编造/取整
- [ ] 每条关键结论都有 locator + 逐字 quote，且 quote 经核验或标 unverified
- [ ] 覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素
- [ ] evidence_map、sources、版本快照、change_log、self_check 齐全，可复跑复核
使用增益（25）
- [ ] 同输入同模板可比；记录返工页数/重排次数/复查轮次
- [ ] 质量与效率改进有 diff/日志/对照支撑
创新增益（15）
- [ ] 页序规划、模板槽位填充、图表数据绑定、局部编辑有方法与边界说明
- [ ] locator+quote 证据绑定可机械复核，构成区别于基础 Agent 的新增能力
可靠体验（10）
- [ ] 稳定触发与调用；三类论文/不同模板/重复运行不塌版
- [ ] 缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有处置
生态贡献（10）
- [ ] 说明、依赖、参数、页数限制、已知问题齐全；脚本/骨架/清单可复用

## 2. result.json schema（示意）
```json
{
  "case_id": "<来自 case.json，字符串>",
  "status": "partial | complete | failed | insufficient_evidence | input_error",
  "presentation_file": "<相对 /app/output 的路径或 null>",
  "preview_files": [],
  "speaker_notes": "<路径或内联对象，按契约>",
  "page_plan": {},
  "evidence_map": [{"page": "", "role": "", "claim": "", "refs": [], "locator": "", "quote": "", "quote_type": "", "quote_verified": false, "confidence": "", "checked": false}],
  "version_records": [{"name": "", "note": "", "files": [], "t": ""}],
  "sources": [{"ref": "", "loc": "", "kind": "", "quote": null, "used_in": []}],
  "uncertainty": {"unresolved": [], "missing_artifacts": [], "notes": ""},
  "pending_human": [],
  "change_log": [{"t": "", "step": "", "action": "", "status": "", "reason": ""}],
  "self_check": {"run": false, "gaps": [], "checked_at": null}
}
```
注：`presentation_file` 等字段名以 case.json 的 `output_contract` 为准；本示例仅说明结构与保留字段，不得当作任何正式案例的答案。

## 3. 脚本调用（相对本 Skill 根目录，运行时为 /skills/weekly-skill/）
```bash
python3 scripts/scaffold.py
python3 scripts/record.py change --step build --action "..." --reason "..."
python3 scripts/record.py source --ref "Fig 3" --loc "论文 p.7" --kind figure --quote "..."
python3 scripts/record.py evidence --page 4 --role result --claim "..." --refs "Fig 3" --loc "p.7" --quote "..."
python3 scripts/record.py version --name v1 --note "..." --files /app/output/deck.pptx
python3 scripts/selftest.py selftest
python3 scripts/selftest.py check
python3 scripts/selftest.py finalize --status complete|partial
```
脚本只依赖 Python 标准库；目录可用 `INPUT_DIR`/`WORK_DIR`/`OUTPUT_DIR` 环境变量覆盖（默认 /input、/app/work、/app/output）。

## 4. 降级决策表
| 情形 | 处置 | 结果标注 |
|-|-|-|
| 论文 PDF 缺失 | 只用可确证材料，其余页不补造段落 | limitation |
| 图缺失/位图无数据 | 引用原图+定位，不重绘不补值 | uncertainty |
| 模板槽位不足 | 映射最近似版式 | uncertainty |
| 字体缺失 | font-discovery 选替代字体 | uncertainty |
| 页面溢出 | 删要点/拆页，字号≥16pt | change_log |
| 导出失败 | 交付源文件+部分预览 | status=partial + 失败原因 |
| 缺 python-pptx/soffice | 回退 Markdown/HTML | status=partial，不伪造渲染 |
| quote 比对不到 | 标 quote_verified=false + 记 uncertainty | uncertainty |
| 数字/事实不确定 | 不进页面主张 | uncertainty / pending_human |

## 5. 已知边界
- 本 Skill 不实现科研绘图算法与 PPT 软件本身；图表重绘仅在数据可复核时进行。
- 脚本只依赖 Python 标准库；PPTX 生成由运行时已装工具（python-pptx/office-renderer）完成，脚本负责骨架、记录、证据溯源与终检。
- 正式案例、白板与评分由平台提供；本包不含预写答案或隐藏测试。
