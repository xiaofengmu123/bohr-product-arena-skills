# 检查清单与接口（checklist）

与 SKILL.md 逐项对应；最终交付前逐条核对。以下 schema 为示意，不冒充任何正式案例结果。

## 1. 终检清单（按评分维度）
实际效果（40）
- [ ] 可编辑演示文件存在且可打开；页数/页序符合约束；每页有叙事角色；备注齐备
- [ ] PDF/PNG 预览真实生成（可查看/可播放）
- [ ] 页上数字、图注、引用编号与原文一致，无编造、无按印象取整
- [ ] 覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素
- [ ] evidence_map、sources、版本快照、change_log、self_check 齐全，可复跑复核

使用增益（25）
- [ ] 同输入同模板可比；记录返工页数、重排次数、导出复查轮次
- [ ] 质量与效率改进有 diff/日志/公平对照支撑

创新增益（15）
- [ ] 页序规划、模板槽位填充、图表数据绑定、局部编辑有明确方法与边界

可靠体验（10）
- [ ] 稳定触发与调用；综述/实验/理论三类与不同模板、重复运行不塌版
- [ ] 缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有处置

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
  "evidence_map": "<路径或数组>",
  "version_records": {},
  "sources": [{"ref": "", "loc": "", "kind": "", "used_in": []}],
  "uncertainty": {"unresolved": [], "missing_artifacts": [], "notes": ""},
  "pending_human": [],
  "change_log": [{"t": "", "step": "", "action": "", "status": "", "reason": ""}]
}
```
注：`presentation_file` 等字段名以 case.json 的 `output_contract` 为准；保留字段 `case_id/status/sources/uncertainty/pending_human/change_log` 由脚本统一维护，与契约字段同名时语义相同。

## 3. 事实冻结与绑定（核心方法）
- 每个关键数值/图注/引用编号先冻结为 `facts.json` 一条：`{"id","fact_kind","quote","loc","used_in"}`，`quote` 必须是材料逐字原文，`loc` 是定位（如 `paper.pdf p.3` 或 `table2.csv row 5`）。
- 页面主张只引用 `fact_ids`，不重新打数字；`check` 会校验事实有 quote+locator、evidence 引用的事实都存在。
- 同一指标两个来源不一致时，两条都冻结并标 `source_conflict`，页面采用哪一条记入 uncertainty。

## 4. 脚本调用（相对 Skill 根目录）
```bash
python3 scripts/main.py scaffold                          # 前 3 分钟落骨架
python3 scripts/main.py record --kind change --step build --action "..." --reason "..."
python3 scripts/main.py record --kind source --ref "Fig 3" --loc "论文 p.7" --type figure --used-in 1,3
python3 scripts/main.py record --kind fact --id F1 --fact-kind number --quote "12.4%" --loc "论文 p.5"
python3 scripts/main.py record --kind evidence --page 4 --role result --claim "..." --fact-ids F1 --refs "Fig 3" --loc "p.7"
python3 scripts/main.py record --kind version --name v1 --note "..." --files /app/output/deck.pptx
python3 scripts/main.py check                            # 写 self_check.json，退出码 1=有缺口
python3 scripts/main.py finalize --status complete|partial
```
脚本不依赖当前工作目录；目录可用 `INPUT_DIR`/`WORK_DIR`/`OUTPUT_DIR` 环境变量覆盖（默认 /input、/app/work、/app/output）。

## 5. 降级决策表
| 情形 | 处置 | 结果标注 |
|-|-|-|
| 论文 PDF 缺失 | 只用可确证材料 | limitation |
| 图缺失/位图无数据 | 引用原图+定位，不重绘不补值 | uncertainty |
| 模板槽位不足 | 映射最近似版式 | uncertainty |
| 字体缺失 | font-discovery 选替代字体 | uncertainty |
| 页面溢出 | 删要点/拆页，字号≥16pt | change_log |
| 导出失败 | 交付源文件+部分预览 | status=partial + 失败原因 |
| 缺 python-pptx/soffice | 回退 Markdown/HTML | status=partial，不伪造渲染 |
| 数字/事实不确定 | 不进页面主张 | uncertainty 或 pending_human |

## 6. 已知边界
- 本 Skill 不实现科研绘图算法与 PPT 软件本身；图表重绘仅在数据可复核时进行。
- 脚本只依赖 Python 标准库；PPTX 生成由运行时已装工具（python-pptx/office-renderer）完成，脚本负责骨架、记录与终检。
- 正式案例、白板与评分由平台提供；本包不含预写答案或隐藏测试。
