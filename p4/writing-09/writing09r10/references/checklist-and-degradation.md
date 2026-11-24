# 检查清单与降级规则

本文件供“评审人视角自检”和 scripts/check 使用；每条对应 SKILL.md 自检清单的一项。

## 1. 逐条检查清单（提交前勾选）

**结构与契约**
- [ ] result.json 可被 `json.load` 解析，含 `case_id` + 全部 output_contract 字段 + status/sources/uncertainty/pending_human/change_log。
- [ ] `case_id` 与 /input/case.json 一致；output_contract 字段名原样保留、不增删改名。
- [ ] `status` 取 `partial | complete | failed | insufficient_evidence`，与真实执行一致。

**结果与讨论本体**
- [ ] 交付可编辑 Results 与 Discussion 章节稿（不是建议/提纲/模板）。
- [ ] Results 只写观察；Discussion 承担解释、文献对照、局限。
- [ ] 每个数值/单位/误差棒含义/统计量可回溯到图、表或统计输出。
- [ ] 阴性结果、不一致文献、局限如实交代。
- [ ] 趋势/组间差异/因果表述的措辞强度 ≤ 证据强度（见 claim-strength-and-boundaries.md）。

**来源与证据**
- [ ] facts_sources 覆盖关键论断，每条含 locator + verbatim quote + strength。
- [ ] 区分“已完成（observed/measured）”与“拟开展/推测（speculated）”。
- [ ] 无“首次/国际领先/填补空白/颠覆性”等无据措辞（check 脚本逐条报出后人工复核）。

**运行与效率**
- [ ] 前 3 分钟已落盘 partial result.json 骨架，之后只做增量改进。
- [ ] 每次改动已写回文件，change_log 带时间戳与动作。

## 2. 降级规则（禁止编造）

| 情况 | 处置 |
|-|-|
| 材料缺失 / 字段不明 | 写入 `uncertainty` 或 `version_boundaries`，标注“缺失/无法判断”，字段留空或 null |
| 引用无法核实 | 保留原文位置并标 `unverified`，不伪造 DOI/URL |
| 软件/工具不可用（无 pdftotext、office 渲染） | 写入 `limitations`，交付纯文本/结构化产物，不假装已渲染 |
| 输入超长 / 跨度过大 | 截取关键段落，在 `uncertainty` 说明“已截断/未覆盖” |
| 坏 JSON / 坏编码 / 空目录 | 捕获并记录，继续处理其余材料；空输入返回 `insufficient_evidence` |
| 运行失败 | `status=failed`，change_log 记录失败原因与已完成中间产物，result.json 仍须落盘 |

## 3. 脚本与自检

```bash
python scripts/main.py --help          # 退出码 0
python scripts/main.py read            # 归一化输入
python scripts/main.py skeleton        # 落 partial 骨架
python scripts/main.py claim           # 抽数建 claim 台账
python scripts/main.py draft           # 由台账生成章节骨架
python scripts/main.py merge           # 台账写回 result.json
python scripts/main.py check           # 结构+一致性+禁词+来源回检
python scripts/main.py selftest        # 空目录/坏 JSON/缺输入三态自检，退出码 0 通过
```

`selftest` 不依赖正式案例；它用临时目录生成示意输入，验证 result.json 结构、空输入降级与坏输入容错，不冒充正式案例结果。
