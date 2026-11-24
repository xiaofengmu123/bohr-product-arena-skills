# 检查清单与接口（checklist）

与 SKILL.md 逐项对应；最终交付前逐条核对。以下为接口说明与 schema 示例（示意，非正式案例结果）。

## 1. 终检清单（按评分维度）
实际效果（40）
- [ ] 可编辑演示文件存在且可打开；页数/页序符合约束；每页有叙事角色；备注齐备
- [ ] PDF/PNG 预览真实生成（可播放/可查看）
- [ ] 页上数字、图注、引用编号与原文一致，无编造/取整
- [ ] 覆盖主线、页结构、图引用、讲解逻辑、模板约束、导出渲染六要素
- [ ] evidence_map、sources、版本快照、change_log、self_check.json 齐全，可复跑复核
使用增益（25）
- [ ] 同输入同模板可比；记录返工页数/重排次数/复查轮次
- [ ] 质量与效率改进有 diff/日志/对照支撑
创新增益（15）
- [ ] 页序规划、模板槽位填充、图表数据绑定、局部编辑有方法与边界说明
可靠体验（10）
- [ ] 稳定触发与调用；三类论文/不同模板/重复运行不塌版
- [ ] 缺图、位图无数据、槽位不足、字体缺失、超页、导出失败均有处置
生态贡献（10）
- [ ] 说明、依赖、参数、页数限制、已知问题齐全；脚本/清单可复用
确定性（本版附加）
- [ ] `python3 scripts/selftest.py` 退出码 0
- [ ] 同一输入两次运行 result.json 字节一致（input_digest 相同）

## 2. result.json schema（示意）
```json
{
  "case_id": "<来自 case.json，字符串>",
  "status": "partial | complete | failed",
  "presentation_file": "<相对 /app/output 的路径或 null>",
  "preview_files": [],
  "speaker_notes": "<路径或内联对象，按契约>",
  "page_plan": [],
  "evidence_map": [],
  "version_records": [],
  "sources": [{"ref": "", "loc": "", "kind": "", "quote": "", "used_in": []}],
  "uncertainty": {"unresolved": [], "missing_artifacts": [], "notes": ""},
  "pending_human": [],
  "change_log": [{"seq": 1, "step": "", "budget": "", "action": "", "reason": ""}],
  "determinism": {"guarantee": "", "seed": 0, "canonical_serialization": true, "input_digest": ""},
  "errors": [],
  "degradations": []
}
```
注：`presentation_file` 等字段名以 case.json 的 `output_contract` 为准；示例仅说明结构与保留字段，不得当作任何正式案例的答案。

## 3. 脚本调用（相对 Skill 根目录）
```bash
python3 scripts/main.py scaffold
python3 scripts/main.py record change --step build --action "..." --reason "..."
python3 scripts/main.py record source --ref "Fig 3" --loc "论文 p.7" --kind figure --quote "..."
python3 scripts/main.py record evidence --page 4 --role result --claim "..." --refs "Fig 3" --loc "p.7" --quote "..."
python3 scripts/main.py record plan --page 4 --role result --claim "..." --refs "Fig 3" --loc "p.7"
python3 scripts/main.py record version --name v1 --note "..." --files deck.pptx
python3 scripts/main.py record artifact --presentation deck.pptx --preview preview/4.png
python3 scripts/main.py record field --key slide_count --value 6
python3 scripts/main.py check                # 写 self_check.json，退出码 1=有缺口
python3 scripts/main.py finalize --status complete|partial|failed
python3 scripts/selftest.py
```
脚本不依赖当前工作目录；目录可用 `INPUT_DIR`/`WORK_DIR`/`OUTPUT_DIR` 环境变量覆盖（默认 /input、/app/work、/app/output）。

## 4. 证据溯源规则（硬要求）
- 每个关键结论/数值/图元指回材料的逐字片段：`loc`（如 `paper.md#p3` 或 `论文 p.7`）+ `quote`（原文逐字）。
- `evidence_map` 的 `quote` 必填；缺失的 `check` 会判为缺口。
- 同一指标两个来源不一致时，两个都记录并标 `source_conflict`，不隐藏冲突。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## 5. 降级决策表
| 情形 | 处置 | 结果标注 |
|-|-|-|
| 论文 PDF 缺失 | 只用可确证材料 | limitation / pending_human |
| 图缺失/位图无数据 | 引用原图+定位，不重绘不补值 | uncertainty |
| 模板槽位不足 | 映射最近似版式 | uncertainty |
| 字体缺失 | font-discovery 选替代字体 | uncertainty |
| 页面溢出 | 删要点/拆页，字号≥16pt | change_log |
| 导出失败 | 交付源文件+部分预览 | status=partial + 失败原因 |
| 缺 python-pptx/soffice | 回退 Markdown/HTML | status=partial，不伪造渲染 |
| 数字/事实不确定 | 不进页面主张 | uncertainty/pending_human |
| 空输入/坏 JSON | 落合法结果，不崩溃 | degradations/errors |

## 6. 确定性规则
- result.json 禁止墙钟时间；运行元数据只写 `work/run_meta.json`。
- 列表稳定排序、JSON 规范序列化、原子写盘；`determinism.input_digest` 记录输入清单 SHA-256。
- 无随机种子；两次运行对比 `sha256(result.json)` 应一致。

## 7. 已知边界
- 本 Skill 不实现科研绘图算法与 PPT 软件本身；图表重绘仅在数据可复核时进行。
- 脚本只依赖 Python 标准库；PPTX 生成由运行时已装工具（python-pptx/office-renderer）完成。
- 正式案例、白板与评分由平台提供；本包不含预写答案或隐藏测试。
