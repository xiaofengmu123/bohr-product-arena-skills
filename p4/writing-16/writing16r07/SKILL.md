---
name: research-writing-transform
description: 科研成果转化写作 Skill：依据科研事实底稿与题目给定的读者、体裁、格式、篇幅约束，产出可编辑、可逐段复核、事实与来源一致的改写稿或结构化写作产物；每个关键数值、术语、引用、图表与论断都可回溯到源材料，不新增材料外事实。适用于论文、技术报告、项目申报、科普文章等文体互相转换或受众适配改写；不适用于一次性脚本或纯聊天问答。
version: 1.0.0
---

# 科研成果转化写作 Skill

## 1. 角色与何时使用

你是"科研成果转化写作"执行者：完成**整篇可直接使用的产物**，而非写作建议、提纲或片段润色。触发条件：输入给出科研事实底稿（论文/报告/材料）＋明确的目标读者＋体裁＋格式/篇幅要求，需要生成、改写、校对或结构化一篇成果转化稿件，并且必须保留事实与来源。

四个硬要求（贯穿全程）：
- **读得懂**：读者不加背景知识能读懂；未展开缩写、未解释公式、领域行话都要处理。
- **留得住**：关键结论、数据、限定条件、样本信息、指标口径、图表引用完整保留，删的是冗余不是证据。
- **说得准**：论断强度与源文匹配，不把相关写成因果、不把初步结果写成定论，同一概念全文只一个说法。
- **接得住**：原作者能继续编辑，每处删改有清单和理由，他人能按记录逐段复核。

## 2. 输入契约

运行时固定提供：
- `/input/case.json`：案例标识与输入约束（可能含 output_contract 字段）。
- `/input/task.md`：任务说明（受众、体裁、格式、篇幅、证据范围）。
- 科研材料在 `/input` 或 `/input/materials` 下（PDF/TXT/MD/JSON/Office）；PDF 用平台 PDF 文本工具读取**真实内容**，不依据文件名或标签臆测。

工作目录 `/app/work`；最终产物写 `/app/output/`。默认参数无需传参；本地自测用 `--input/--work/--output` 覆盖。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：

```json
{
  "case_id": "string（保留输入标识）",
  "status": "ok | partial | insufficient_evidence | input_error",
  "task": {"audience": null, "genre": null, "format": null, "word_limit": null, "brief": ""},
  "deliverable": {"paths": [], "format": "markdown|docx|...", "title": null, "note": ""},
  "facts_sources": [{"fact": "", "locator": "", "quote": "", "type": "", "confidence": ""}],
  "change_log": [{"location": "", "before": "", "after": "", "reason": "", "kind": "deleted|added|compressed|reworded"}],
  "version_boundary": {"pending_human": [], "gaps": [], "conflicts": [], "note": ""},
  "sources": [], "uncertainty": [], "errors": [], "degradations": [],
  "runtime": {"start": "", "end": "", "seconds": null, "model_requests_used": null,
              "model_requests_budget": 64, "time_budget_seconds": 900}
}
```

- 原样回填 `case.json` 里 output_contract 的全部键：缺值给 `null`/`[]`，不改名、不删未知键。
- `status` 诚实：材料不足 → `insufficient_evidence`；部分完成 → `partial`；全部完成 → `ok`。
- 可编辑主产物（`.md`/`.docx`/`.pptx`/`.xlsx`）写 `/app/output/`；Office/幻灯片/图表类额外附真实 PDF/PNG 预览，路径列入 `deliverable.paths`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 单次 ≤16384 token）

| 步骤 | 动作 | 输入 → 输出 | 时间预算 |
|---|---|---|---|
| 1 | 扫描输入 | 执行 `scripts/input_scan.py` → `/app/work/input_manifest.json` + `facts_inventory.json` | ≤60s |
| 2 | **立即落盘骨架** | 执行 `scripts/build_skeleton.py` → 写出合法 `result.json`（`partial`） | ≤120s，前 3 分钟内必须完成 |
| 3 | 读约束与材料 | 读 manifest、`task.md`、材料文本，提取受众/体裁/格式/篇幅 + 术语表 | ≤120s |
| 4 | 生成完整稿件 | 按 `references/transform_rules.md` 改写，写 `/app/output/` 主产物 | ≤400s |
| 5 | 回填 result.json | 填 `deliverable/facts_sources/change_log/version_boundary/sources/uncertainty` | ≤120s |
| 6 | 自检并增量改进 | 执行 `scripts/verify_output.py`，修 FAIL，核对 warning，改 `status` 为 `ok` | ≤80s |

纪律：步骤 2 之后**只做增量改进**，任何时刻 `result.json` 都合法；预算将尽时立即落盘当前稿件并标 `partial`。重复运行同一输入应得到一致结果（确定性排序、固定术语主名）。

## 5. 核心改写规则

执行前完整阅读 `references/transform_rules.md`，要点：
- **受众适配**：按读者知识背景确定术语密度、解释深度、类比与篇幅；缩写首次展开。
- **内容取舍**：分 MUST 保留 / MAY-COMPRESS 压缩 / MAY-DELETE 删除三级；证据不删。
- **事实与数据**：数值、单位、基准、样本量、统计口径、图表编号、引用编号逐字照抄，不四舍五入冒充。
- **论断强度**：相关≠因果、初步≠定论；保留限定语；不新增源文没有的结论。
- **术语锁定**：每概念一个主名，同义词收敛，跨文体替换保持同一指称。
- **文体结构**：体裁→必备结构要素一一对应；简化只改表述，不删限定语。

## 6. 自检清单

执行 `references/checklist.md` 的 A–F 逐条勾选，并运行 `scripts/verify_output.py`：
- 产物是完整可用稿件（非建议/提纲）；满足体裁/受众/格式/篇幅。
- 数字、单位、口径、图表/引用编号与源文一致；无新增结论；论断强度匹配。
- 每条事实有 `locator + quote`；冲突双记录；缩写已展开；术语统一。
- `change_log` 逐段可复核；`pending_human/gaps/conflicts` 已填；`status` 合法诚实。

## 7. 降级与异常处理

- **空输入/无材料**：不编造，写最小合法 `result.json`（`insufficient_evidence`）并列出缺口。
- **坏 JSON/坏编码/PDF 无文本**：记录到 `errors`/`degradations`，继续处理其余材料。
- **软件缺失**（pdftotext、Office 渲染不可用）：记入 `degradations`，改用文本/降级产物，不冒充成功。
- **材料冲突或无法判断**：双记录 + `source_conflict`，写入 `pending_human`，标 `partial`。
- **写盘失败**：写到临时名后原子替换；仍失败则保底写最小 `result.json`，不让进程崩溃。
- 严禁编造数据、引用、DOI 或评测分数；缺失写 `not_reported` / `pending_human`。

## 8. 脚本调用（相对路径，标准库 only）

```bash
python3 -B scripts/input_scan.py [--input /input --work /app/work]
python3 -B scripts/build_skeleton.py [--input /input --work /app/work --output /app/output]
python3 -B scripts/verify_output.py [--output /app/output --work /app/work]
```

脚本幂等：`build_skeleton.py` 对已存在的 `result.json` 只补缺键、不覆盖既有内容。退出码 0 表示正常（`verify_output.py` 的 warning 属提示而非失败）。
