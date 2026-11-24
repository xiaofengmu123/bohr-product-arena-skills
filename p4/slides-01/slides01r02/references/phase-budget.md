# 阶段预算与早落盘协议（references/phase-budget.md）

本版设计侧重点：把 900 秒 / 64 次模型请求拆成阶段预算，**前 3 分钟先落盘
partial 骨架，之后只做增量改进**，用"checkpoint 协议"保证任何时刻都可交接。

## 1. 阶段预算总表

| 阶段 | 时间窗 | 请求预算 | 产出 | 门禁 |
|---|---|---|---|---|
| P0 落地骨架 | 0:00–3:00 | ≤6 | result.json 骨架 + plan.md 七槽位 | result.json 是合法 JSON |
| P1 读材料·抽主线 | 3:00–7:00 | ≤16 | draft.json 页级主张（每项 locator+quote） | 每页有证据定位或缺口 |
| P2 成稿·继承模板 | 7:00–11:30 | ≤22 | presentation.pptx + 讲稿备注 | pptx 可打开、页数 ≤ 上限 |
| P3 导出·自检 | 11:30–13:30 | ≤12 | presentation.pdf + report.md + selftest | PDF 页数 == pptx 页数 |
| P4 增量补全 | 13:30–15:00 | ≤8 | 补 content_map/sources，最终 status | 最终 status 诚实 |

## 2. 早落盘协议（checkpoint）

1. **P0 一到 3 分钟，无论读到什么都先写骨架**：`result.json` 含 case_id +
   全部契约字段（null/[]/{} 占位）+ 信封字段；`status="partial"`。
2. 骨架不是空占位：`plan.md` 先写七个槽位（title/background/method/result/
   discussion/limitation/conclusion），超时也能交出一份主线可见、缺口可查的结果。
3. 每个阶段结束 = 原子重写一次 `result.json`（同目录临时文件 + replace，杜绝半截
   JSON）+ 追加一条 `change_log`。
4. 永不推倒重来：P4 只改缺口/渲染问题，不开新长流程。
5. 脚本调用是可选单次工具（`scripts/main.py --validate/--stats`、
   `scripts/selftest.py`），不串流水线；内容写作由 Agent 完成。

## 3. 时间与请求纪律

- 连续思考 ≤ 90 秒；剩 5 分钟必须已在 P3/P4 且已落盘。
- 每阶段按预算消费；接近该阶段上限即进入"只读补充模式"（只改已落盘文件）。
- 请求预算为估计值：不精确计数，但每阶段边界应重写一次 result.json 记录进度。

## 4. 降级决策表

| 情况 | 处置 | 记录位置 |
|---|---|---|
| case.json 缺失/坏 JSON/坏编码 | 仍写合法骨架，status=input_error | errors/degradations |
| 图表无法提取 / 公式为图片 / PDF 加密 | 交付最优产物，注明限制 | exceptions/pending_human |
| 模板占位符不足 / 字体缺失 / 页数超限 | 降级排布或换版式，逐条说明 | exceptions |
| 缺 pptx 软件 | 给真实 SVG+PNG 替代 | exceptions |
| 缺渲染工具 | PDF 缺失也写 pages:null 与原因 | export_artifact/exceptions |
| 材料不足 | status=insufficient_evidence，列缺口 | uncertainty/pending_human |

任何情况都**不编造分数、DOI、数值或运行结果**；材料未报告的写 `not_reported`。
