# 模板与最小调用示例

本文件给出可复用的回复信模板、台账条目模板与最小调用示例；所有"示例"均为示意，不冒充正式案例结果。

## 1. 回复信模板（逐条回应结构）

```markdown
# 回复信（Response to Reviewers）

## 总体回应
（一段：说明本轮处理了 N 条意见、整体立场、关键修改方向）

## 意见 {comment_id}
**审稿意见**：（摘要，不臆造逐字要求）
**子要求**：1) … 2) …
**决策**：accept / partial / reject
**语气**：accept / clarify / rebut / appeal
**回应**：
- （语气框架见下；每条子要求逐一给出明确处理结论，不空话）
**修改定位**：
- 章节 {section}；原文「{before}」→ 改为「{after}」；锚点 `{anchor}`

## 未到位证据清单
| 意见 | 证据项 | 状态 | 来源/无法补做原因 |
|---|---|---|---|
| {id} | 补实验/补数据/补文献/重分析 | done/partial/cannot_do/planned | … |
```

### 语气四档套话框架（务必填充具体内容）
- `accept`：「感谢审稿人的意见。我们已按建议……（具体动作），见 {section} 第…段。修改后原文为：『{after}』。」
- `clarify`：「感谢审稿人的提醒。原文此处意在表达……（重述原意）。为避免误解，我们已在 {section} 处将表述改为……（或说明无需改动及理由）。」
- `rebut`：「我们理解审稿人的关切，但证据显示……（给依据，注明来源）。因此我们未按该建议修改，理由为……。若审稿人认为必要，我们愿意补充说明。」
- `appeal`：「本条我们请求编辑部复核：审稿意见中的前提……与材料/文献记录不符（证据：……）。我们恳请对此判定再作评估。」

硬规则：不冒犯、不过度承诺（不写"我们将补充实验"除非真列入 planned）、不无原则顺从（reject 必须给理由与依据）。

## 2. 台账条目模板（revision-ledger.json）

```json
{
  "comment_id": "R1-C1",
  "comment_type": "data",
  "severity": "major",
  "comment_text": "（意见摘要原文）",
  "sub_requirements": ["补样本量说明", "报告效应量"],
  "decision": "accept",
  "tone": "accept",
  "response_summary": "已补充样本量并报告效应量",
  "changes": [
    {"action": "在方法节补充样本量", "section": "methods",
     "before": "未说明样本量", "after": "样本量为 120 例",
     "anchor": "样本量为 120 例", "status": "done"}
  ],
  "evidence": {"needed": true, "status": "done",
               "source": "materials/manuscript-baseline.md",
               "cannot_do_reason": null},
  "status": "resolved"
}
```

## 3. 最小调用示例（示意，非正式案例）

输入目录结构：
```text
/input/case.json                {"case_id":"example-001"}
/input/task.md                  题目说明
/input/materials/reviewer-comments.md    两条意见
/input/materials/manuscript-baseline.md  基线摘要
```

调用：
```bash
python scripts/main.py --input /input --output /app/output --work /app/work
# Agent 填台账与回复信/修订稿后
python scripts/check_revision.py --ledger /app/output/revision-ledger.json \
  --manuscript /app/output/revised-manuscript.md \
  --response /app/output/response-letter.md --result /app/output/result.json
python scripts/selftest.py --validate /app/output/result.json
```

示意输出（`main.py` stdout，非正式评分结果）：
```json
{"ok": true, "case_id": "example-001", "n_comments": 2,
 "n_sections": 2, "status": "partial", "errors": 0, "degradations": 0}
```

## 4. 边界与维护信息
- 依赖：仅 Python 标准库（≥3.8）；无第三方包、无网络。
- 版本：`SKILL.md` frontmatter `version` 与 `scripts/` 行为一致；改动须同步 `change_log`。
- 维护：新增意见格式时先在 `scripts/main.py::parse_comments` 增补正则，并在 `scripts/selftest.py` 增补一个降级用例。

## 5. 增益可验证性（对照方式）
质量/效率增益须有公平对照，而非文字自述：
1. 固定同一案例输入（相同 `case.json`、`task.md`、`materials/`、相同工具权限）。
2. 分别以"不加载本 Skill 的基础 Agent"与"加载本 Skill 的 Agent"各跑一次，记录产物与耗时。
3. 用四条可核验指标逐条比对：意见覆盖率（是否漏子要求）、定位命中率（每处"已修改"能否在稿件中检索到 `anchor`）、证据到位率（需补项是否标注到位/无法补做）、一致性（回复信↔修改稿↔台账数量是否一致）。
4. 报告差异时附逐条证据（哪条意见在无 Skill 下漏答/答偏/虚假定位），结论写"本 Skill 相对基础 Agent 的增益"，不冒充满分或完整原研究复现。

