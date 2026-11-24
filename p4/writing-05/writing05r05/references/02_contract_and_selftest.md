# 输出契约与自检（contract_and_selftest）

本文件定义 `result.json` 的完整契约、字段语义、状态机与逐条自检清单。目标：输出可逐条回溯、可独立重跑。

## 1. 契约键回显规则
- case.json 中若存在 `output_contract` / `outputContract` / `contract` / `result_schema`（取第一个非空 dict），则 `result.json` 必须**原样回显其全部键**：不改名、不删除、未知键保留，缺值给 `null` / `[]` / `{}`。
- 若不存在或为空，则按默认三字段补齐：`deliverable`、`sources`、`change_log`（对应题目"目标产物 / 事实与来源映射 / 版本与边界"）。
- `scripts/main.py scaffold/finalize` 用 `core_impl.extract_contract` + `deep_merge` 保证契约键永不被丢弃；Agent 补丁不得删除契约键。

## 2. result.json 结构（示例，非正式案例）
```json
{
  "case_id": "challenge-xxxx",
  "status": "complete",
  "deliverable": {
    "title": "面向临床医生的摘要",
    "format": "markdown",
    "files": ["/app/output/manuscript.md"],
    "summary": "三段交付：3 条论断、4 条证据、1 处待人工确认"
  },
  "claims": [
    {
      "id": "C1",
      "claim": "干预 X 与结局 Y 下降相关",
      "location_in_manuscript": "S1.P2",
      "strength": "B",
      "support_scope": "full",
      "evidence_refs": ["S1"],
      "verb_before": "导致",
      "verb_after": "与…相关",
      "verdict": "revise"
    }
  ],
  "sources": [
    {
      "id": "S1",
      "claim_id": ["C1"],
      "value": "结局 Y 下降 32%",
      "kind": "number",
      "locator": "material.pdf#S3.P2.s1",
      "quote": "Y decreased by 32% (95% CI …)",
      "source_doc": "material.pdf",
      "source_type": "original_research",
      "strength": "B",
      "support_scope": "full",
      "verdict": "accept",
      "change": "未修改"
    }
  ],
  "change_log": [
    {"time": "2026-11-24T…", "action": "scaffold", "detail": "落盘骨架"},
    {"time": "…", "action": "finalize", "detail": "校验 3 项契约键"}
  ],
  "uncertainty": ["S1 原文未报告分层人群数据"],
  "pending_human": ["确认 S2 的二手转引是否可核验"],
  "limitations": ["材料仅含摘要，无全文"],
  "errors": [],
  "degradations": [],
  "runtime": {"scaffolded_at": "…", "finalized_at": "…"}
}
```

## 3. 状态机
| status | 含义 | 触发条件 |
|-|-|-|
| in_progress | 仅骨架阶段 | scaffold 落盘 |
| complete | 全部字段齐全且每条实质论断都有来源 + 强度 | 三段交付齐备、无未解决冲突 |
| partial | 交付了主产物但存在缺口 | 缺来源 / 缺强度 / 有待确认项，已在 limitations / pending_human 列出 |
| insufficient_evidence | 材料不足以产出有据成稿 | 无稿件正文或材料全缺 |
| input_error | 输入解析失败 | 坏 JSON 且无法修复、缺 case.json |
| failed | 运行失败 | 脚本 / 环境异常 |

`complete` 是强声明：任何未回溯的事实、未确认项都不得同时出现；否则降为 `partial`。

## 4. 自检清单（提交前逐条勾选，对应评分维度）
调用与易用
- [ ] `python scripts/selftest.py` 全部 PASS（空输入 / 坏 JSON / 最小案例 / 重复运行）。
- [ ] `python scripts/main.py scaffold/finalize --help` 退出码 0。
实际效果
- [ ] `claims` / `sources` / `deliverable` 三段齐备；每条 source 有 `locator` + `quote` + `source_doc`。
- [ ] 每条 claim 有 `strength` + `support_scope` + `evidence_refs`；动词与强度相称。
- [ ] 引用真实存在且原文支持论断；无过度 / 不足推断。
结果可核验
- [ ] 论断编号、原文摘录、定位、强度理由可被他人独立复核。
可靠体验
- [ ] 缺材料 / 仅摘要 / 冲突 / 格式不支持 / 截断均记录于 limitations / uncertainty。
- [ ] 重复运行不覆盖已改进结果；先落盘 partial 再分析。
生态贡献
- [ ] 文档齐全、依赖仅标准库、路径相对、示例标注为示例、强度分级口径写清。

## 5. 运行命令速查
```bash
cd /skills/weekly-skill
python scripts/main.py scaffold --input /input --output /app/output --work /app/work
python scripts/main.py finalize --input /input --output /app/output --work /app/work --merge /app/work/result_patch.json
python scripts/selftest.py
```

## 6. 失败类型识别与处置（可靠体验 / 生态贡献）
| 场景 | 识别信号 | 处置动作（写入相应字段） |
|-|-|-|
| 文献不可得 | 材料路径缺失或 404/空文件 | `limitations` 记缺口；该引用不用于实质论断；`status=partial` |
| 仅有摘要 | 材料只有 abstract，无全文 | `source_type=abstract_only`，`strength=D`，`pending_human` 提示取全文 |
| 原文与摘要矛盾 | 摘要数字与正文不一致 | `support_scope=conflict`，两处 `quote` 并列，`uncertainty` 说明 |
| 证据相互冲突 | 两条来源同一论断方向相反 | 两条都保留，一条标 `conflict`，`verdict=verify`，不隐藏 |
| 格式不支持 | 无法解析的 PDF/图/表 | `limitations` 记"格式不支持"，改用 txt/md 兜底 |
| 权限不足 | 读取报 PermissionError | `limitations` 记权限，`status=partial` |
| 二手转引 | 正文引用"转引自"或来源为二手 | `source_type=secondary`，`strength=D`，标 `pending_human` 核验原出处 |
| 撤稿 / 版本差异 | 检测到撤稿标注或版本号冲突 | `uncertainty` 记撤稿/版本，该引用不用于实质论断 |
| 图表 / 公式不可读 | 图元无法提取文本 | `limitations` 写"该处有图/公式但无法读取"，不猜内容 |

原则：所有异常都"识别—降级—记录"，绝不静默跳过，绝不编造缺失内容补位。

