# 返修方法论：意见拆解 → 决策 → 定位 → 证据 → 语气 → 台账

本文件是 `SKILL.md` 的方法细则，可被他人直接复用。所有规则只描述"如何做"，不含任何正式案例答案。

## 1. 意见拆解分类法

每条审稿意见先拆成可独立核验的**子要求**，再作答。字段：

| 字段 | 取值 | 说明 |
|-|-|-|
| comment_id | 意见原始编号（R1、意见 1、1 等） | 全程唯一，回复信/台账/改稿共用 |
| comment_type | method / data / writing / format / ethics / other | 决定处理动作类型 |
| severity | critical / major / minor / editorial | 决定优先级与资源投入 |
| sub_requirements | 字符串数组 | 一条意见含多个子要求时全部列出 |
| depends_on | 可选，其他 comment_id | 子要求之间的依赖关系 |

拆解判据：若一条意见同时要求"补方法细节 + 补数据 + 改措辞"，必须拆成三个子要求分别回应；
禁止用一个"感谢审稿人，已按要求修改"笼统覆盖。子要求不可空——至少一项，且每条要有独立处理结论。

## 2. 决策矩阵与语气分级

| decision | 含义 | 适用 |
|-|-|-|
| accept | 采纳并真实落改 | 意见正确且可执行 |
| partial | 部分采纳 | 部分可做、部分不可做 |
| decline | 不采纳但说明理由 | 意见有误或超出范围 |
| clarify | 澄清误解，不改稿 | 审稿人理解偏差 |
| rebut | 礼貌反驳 | 有证据支撑的不同判断 |

语气四档（tone 字段），强度递增：

1. `accept`（接受）：直陈修改，附定位。
2. `clarify`（澄清）：指出理解偏差，给原文依据。
3. `polite_rebuttal`（礼貌反驳）：先认可合理处，再列证据反驳，结尾给可选妥协。
4. `appeal`（申诉）：仅当有决定性证据时使用，语气克制、就事论事。

硬约束：禁止冒犯性表达、禁止"全部照办"式无原则顺从、禁止"我们已完美解决"式过度承诺。
采纳意见必须给 `modifications[]` 实改；澄清/反驳必须给 `rationale` 与证据出处。

## 3. 修改定位锚点规范（改得准 / 对得上）

每处修改给 `{file, location, anchor, status}`：

- `location`：版本 + 章节 + 段落，如 `revised_manuscript.md §3.2 第2段`。
- `anchor`：两种可机检形式——
  - 替换式：`旧文本→新文本`（也接受 `old => new`）。校验规则：新文本在稿、旧文本不在稿。
  - 检索式：可唯一检索的原文锚点片段，如某关键句首 8 字。
- 锚点必须**在改稿后仍可检索**：优先选改后新增的完整句/短语，不要用"第3行"这类改稿后失效的行号。
- `check_revision.py` 会自动校验 status=done 的 anchor：新在旧不在才算 found；仅声明未落改会被标为 gap。

## 4. 证据补充状态机（证得住）

需补实验/数据/文献/重做分析时，每条写 `{need, status, source, reason_if_infeasible}`：

| status | 含义 |
|-|-|
| in_place | 已在材料/稿件中到位，给出处 |
| to_add | 已列入待补计划，给数据出处或计划路径 |
| not_feasible | 无法补做，必须写清原因与替代方案 |
| pending | 待人工确认 |

无法补做时的替代说明三要素：为什么不可行（数据/权限/时间/伦理）、如何用现有证据弥补、
为何不影响结论或影响范围多大。绝不编造数据或冒称"已补做实验"。

## 5. 版本台账（revision_ledger.json）

```json
{
  "case_id": "...",
  "comments": [
    {
      "comment_id": "1",
      "comment_type": "data",
      "severity": "minor",
      "sub_requirements": ["..."],
      "depends_on": [],
      "decision": "accept",
      "rationale": "...",
      "tone": "accept",
      "modifications": [
        {"action": "...", "file": "revised_manuscript.md", "location": "§...", "anchor": "旧→新", "status": "done"}
      ],
      "evidence": [
        {"need": "...", "status": "in_place", "source": "...", "reason_if_infeasible": ""}
      ]
    }
  ]
}
```

多轮返修：上一轮承诺作为新增约束重新过一遍台账；承诺变更必须在 `change_log` 记录，回复信引用的
位置与当前版本一一对齐。回复信、modification_map、修订稿三者共用同一 `comment_id` 与 `anchor`。

## 6. 可复用资源

- `references/checklist.md`：逐条自检清单（对应评分维度）。
- 回复信模板骨架：`scaffold.py` 生成的 `response_letter.md` 表头；逐条段结构 = 结论 → 理由 → 改动定位 → 语气。
- 定位脚手架：`modification_map.md` 四列表（意见编号 / 改动内容 / 定位锚点 / 状态）。
- 评分锚点（题目四问）：答得全、改得准、证得住、对得上；自检逐条映射到这四个词，便于复核。

## 7. 边界

本 Skill 只处理"受控返修演练"：只修订指定基线、逐条处理实际给出的意见，交付修订稿 + 回复信 +
定位 + 证据清单。不联网核验原评审全文、不冒称真实审稿人逐字要求、不冒称补做实验或已接收。
材料缺失时输出 `partial` + `material_gaps`，而不是跳过可执行修改或用示例填充。
