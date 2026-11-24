# 审稿回复 Skill 参考规则与检查清单

本文件是 SKILL.md 第 5 节自检清单的细则，供 Agent 在执行时逐条落实、供评审者复核。

## 1. 意见分类法（拆解台账字段）
每条意见拆成一条或多条台账记录（`/app/work/comment_ledger.json` 的 `comments[]`）：
- `id`：稳定编号（C01、C01a、C01b…）；一条意见含多个子要求时拆成带字母后缀，父条/子条用 `parent` 字段关联。
- `reviewer`：R1/R2…（未知填 `null`）。
- `type`：`method|data|writing|format|ethics|other`。
- `severity`：`major|minor|cosmetic`（影响改动优先级与语气强度）。
- `decision`：`accept|partial|reject|clarify|n/a`。
- `sub_requirements`：一条意见内多个可执行子要求，**逐项**给结论，防止"只答一半"。
- `actions`：具体改动动作（动词短语，如"在 Methods 第 2 段补充样本量说明"），不用"已修改"空话。
- `evidence_status`：`provided|not_needed|cannot_provide|pending|alternative`。
- `locator`：定位表达式，见第 2 节。
- `anchor_quote`：从修改稿中复制的**逐字锚句**（≤120 字符）。
- `status`：`to_process|done|blocked|skipped`。

## 2. 定位脚手架（改得准）
要求"改后仍可定位"，因此不以易失效的页码/行号作唯一依据，用三段式 locator：
```
<章节标题> > <段首词/段落号> > "逐字锚句"
```
- 锚句必须逐字出现在当前版本修改稿中；`check_revision.py` 做空白归一后的精确子串匹配。
- 每个 accept/partial 的意见至少一条 locator + anchor_quote；reject/clarify 给出理由与"未改"说明，不伪造定位。
- 版本一致性：locator 引用当前版本；多轮返修保留上一轮承诺的历史台账，保证"上一轮承诺仍然成立"。

## 3. 语气控制（四档 + 红牌词）
| 语气 | 适用 | 句式骨架 |
|---|---|---|
| accept（接受） | 意见成立且可改 | "We thank the reviewer… we have revised…" |
| clarify（澄清） | 意见源于误解 | "We apologize for the unclear wording; the intended meaning is…" |
| rebut（礼貌反驳） | 意见有误且证据充分 | "We respectfully disagree because…" |
| appeal（申诉） | 涉及核心事实争议 | "With respect, the existing evidence indicates…" |

红牌词（避免）："obviously wrong"、"the reviewer failed to"、"of course"、"绝对"、"必然"、"无需解释"。
不得过度承诺（"guarantee"、"definitely solved"），不得无原则顺从（对错误意见直接"已按要求修改"却不说明为何错误）。

## 4. 证据清单（证得住）
对需要补实验 / 补数据 / 补文献 / 重做分析的意见，逐条写：
- 到位状态：`provided / pending / cannot_provide / alternative`。
- 数据出处：来源文件路径 + 逐字引用（`quote`）。
- 无法补做时：明确写"为何无法补做"（材料边界、时间、伦理、数据缺失），并给替代说明
  （重新分析已有数据 / 限制声明 / 敏感性分析），不得假装已补。

## 5. 逐条自检清单（对应评分维度，逐条勾选）
### 实际效果（40 分）
- [ ] 每条意见及其子要求都有一条台账记录，无漏项、无"答一半"、无笼统致谢。
- [ ] 每条意见有明确 decision（accept/partial/reject/clarify）。
- [ ] 每条 accept/partial 的"已修改"落到具体动作 + locator + anchor_quote。
- [ ] 每条定位能在当前版本修改稿中被独立检索到。
- [ ] 补实验/补数据/补文献类意见有证据状态或"无法补做"的替代说明。
- [ ] 回复信、修改稿、版本三者一致，跨轮承诺仍在。
- [ ] 每条数字/术语/引用/关键论断可回溯到来源或修改记录，不新增材料外事实。
### 可靠体验（10 分）
- [ ] 意见冲突、意见有误、稿件缺章节、无法补实验、版本不匹配、权限不足均被识别并处置。
- [ ] 长稿件/多意见/多轮返修/重复运行下，编号与定位不漂移。
- [ ] 缺材料先写 material_gaps 再标注假设推进，不编造。
### 使用/创新/生态（35 分）
- [ ] 产出可编辑 markdown/docx 修改稿 + 回复信；Office 产出另渲染 PDF/PNG。
- [ ] 脚本纯标准库、相对路径可执行；SKILL.md 与提交版本一致。
- [ ] 提供可复跑自检（selftest.py）与运行中自检（check_revision.py），非文字自述。
- [ ] 意见分类法、回复信模板、定位脚手架、证据清单可被他人复用。

## 6. 回复信模板（骨架）
对每条意见固定四要素：Decision → Action → Location → Evidence。
示意（**示例，非正式案例答案**）：
> **C01 (R1, method, minor) — accept**
> Decision: accept.
> Action: clarified sample size in Methods.
> Location: `Methods > Participants > "A total of N=40 patients were enrolled."`
> Evidence: baseline `materials/manuscript-baseline.md`; revised anchor quoted above.

## 7. 适用边界
- 本 Skill 针对"受控基线修订 + 意见摘要"场景：给定组织方基线摘要与意见摘要，只修订该指定基线，
  逐条处理实际给出的意见，交付可编辑修订稿、回复信草案、修改定位和未到位证据清单。
- 不做整篇全文返修、不做真实补做实验、不冒称原审稿逐字全文或编辑部已接收。
- 公开论文/报告链接是来源身份，不表示已联网核验原评审全文。
