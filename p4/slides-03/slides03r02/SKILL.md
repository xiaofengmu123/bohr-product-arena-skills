---
name: scholar-talk-organizer
description: 把固定论文/研究材料组织为证据可追溯、可编辑、符合模板的学术汇报（报告目标→听众→叙事→逐页唯一主张与证据→问答准备→渲染自检），并以“阶段时间预算 + 前三分钟落盘 partial 骨架 + 只做增量改进”保证任何中断都留下合法结果。用于把科研案例做成可上台、可复核的演示产物。
version: 1.1.0
---

# 学术汇报内容组织 Skill（时间预算与早落盘版）

## 1. 角色与何时使用

本 Skill 负责“从已有研究材料到可讲页面”的组织与交付：提取研究问题、方法、结果与限制形成主线；设计页级唯一主张、图表引用与讲解顺序；按模板产出可编辑演示文件与讲稿备注；为每页保留论文定位并检查导出渲染。判断是否做对，不看页数与模板数量，只看三件事：每页论断能否在源材料定位、叙事是否服务于指定听众与时长、取舍过程能否被他人复核。

任一条件满足即启用：

- 任务要求把论文 / 研究材料组织成演示文稿，且需逐页标注主张、图表与证据来源；
- 输入含听众、时长、页数、模板等约束，需要内容取舍与叙事顺序设计；
- 评分要求证据可追溯、导出可打开、结论强度与证据匹配。

不适用：论文精读总结、学术写作成稿、科研绘图本身的素材生产（相邻课题负责）。

核心方法：先定报告目标与听众，再定“问题—方法—结果—结论”主线与页级取舍；每页一条唯一主张 + 支撑证据 + 证据强度，配套讲稿与问答准备；最后逐页回填来源定位并检查渲染导出。

## 2. 输入契约

运行时原始输入（只读，勿改）：

- `/input/case.json`：案例标识、任务约束、材料清单；若含 `output_contract`，则以其中字段为准并**原样保留全部键**，未知键不得删除或改名。
- `/input/task.md`：任务说明。
- 材料文件（PDF / 图片 / 模板 / 起始 deck）按 `case.json` 清单定位，通常位于 `/input/` 或 `/app/work/`。
- 可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`、`scientific-python`。

工作目录 `/app/work/`；最终产物写入 `/app/output/`。所有读取以实际文件为准，不依据文件名标签臆测内容；单文件读入上限 2MB，超限跳过并记入 `uncertainty`；文本按 `utf-8-sig` 读取并对 BOM / 坏编码 / 坏 JSON 降级，不崩溃。

## 3. 输出契约

必须产出 `/app/output/result.json`。若 `case.json` 给出 `output_contract`，回填其全部字段（缺值给 `null`/`[]`，不改名、不删未知键）；否则使用默认契约（字段定义见 `references/output_contract.md`）：

- `case_id`：案例标识（优先 `case.json.id`，其次 `armiId`）。
- `report_goal` / `audience` / `narrative` / `pages` / `editable_deck` / `evidence_map` / `qa_prep` / `rendering`：见 `output_contract.md`。
- `status`：`complete | partial | failed | insufficient_evidence | input_error`（诚实标注）。
- `sources`：实际读取的来源清单（路径、类型、是否读取、超限说明）。
- `uncertainty`：无法判断、材料缺失、证据不足等逐条说明。
- `pending_human`：需要人工确认或补录的事项，不得编造补齐。
- `change_log`：时间戳 + 动作 + 影响范围的增量记录。

可编辑演示文件（PPTX / HTML / ODP）与渲染预览（PDF / PNG）一并写入 `/app/output/`。未产出的字段显式标 `null` 或 `[]`，不得编造。每个关键结论必须带 `locator`（文件/章节/页码/图表定位）与 `quote`（逐字片段）；无定位时 `evidence_strength` 标 `low` 并记入 `pending_human`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

铁律：**前 180 秒内必须由 `scripts/scaffold.py` 落盘一版合法 partial 骨架**，此后只做增量合并，任何中断都保留合法结果；每步输出先落盘、后改进。时间与请求均为预算上限，不追求用完；到 850 秒即收尾并写最终 result.json。

| 阶段 | 时间预算 | 请求预算 | 输入 → 输出 | 落盘动作 |
|-|-|-|-|-|
| P0 读取核对 | 0–60s | 2–4 | case.json/task.md/材料 → `work/materials.json` | 记录来源清单 |
| P1 落盘骨架 | 60–180s（前三分钟铁律） | 1–2 | 步骤 0 的 case_id/契约 → partial result.json | `scaffold.py` 写骨架 |
| P2 主线规划 | 180–360s | 10–16 | 材料全文 → report_goal/audience/narrative/pages 初稿 | `scaffold.py --update` |
| P3 页级证据 | 360–600s | 15–24 | pages+来源定位 → 逐页主张、evidence_map | `scaffold.py --update` |
| P4 生成演示件 | 600–780s | 10–18 | pages+模板 → editable_deck+speaker_notes | `scaffold.py --update` |
| P5 渲染自检 | 780–880s | 4–8 | deck+导出件 → PDF/PNG 预览 | `selftest.py` |
| P6 增量收尾 | 880–900s | 余量 | 自检报告 → 修复缺陷、更新 change_log | `scaffold.py --update` |

增量协议：所有更新走 `scripts/scaffold.py --update '{"字段": …}'`（或 `--update-file patch.json`），只合并、不覆盖既有字段，自动追加 change_log；`scaffold.py` 记录墙钟时间戳，用于核对“前三分钟已落盘”闸门。每阶段开始先检查阶段预算是否已消耗，超预算直接进入 P6 收尾。

## 5. 自检清单（对应评分维度，逐条可勾选）

- [ ] 实际效果：report_goal/audience/narrative/pages/qa_prep 齐全且形成闭环（40 分维度）。
- [ ] 每页恰好一条 `single_claim`，无摘要/Results 逐段搬页。
- [ ] 每页数字、图表引用与源材料一致；`evidence_strength` 与证据匹配（对比 claims 用 high，机制推测用 low）。
- [ ] 六方面齐全：论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染。
- [ ] 证据可核验：evidence_map 每页均有 `locator`+`quote`，`selftest.py` 无 error。
- [ ] 使用增益：取舍决策（为什么某图不上页、为什么调序）写入 narrative/change_log，可复核。
- [ ] 创新增益：audience/pages/qa_prep 有落地字段（非空话）。
- [ ] 可靠体验：editable_deck + PDF/PNG 预览均存在，`selftest.py` 无阻断项。
- [ ] 异常处理：缺图/低分辨率/公式丢失/字体缺失/模板冲突/导出失败/超时均如实标注。
- [ ] 生态贡献：脚本仅标准库、相对路径调用、`--help` 可查，references 说明完整。
- [ ] 收尾必查：result.json 含 case_id、output_contract 全字段、status/sources/uncertainty/pending_human/change_log；status 如实，未编造评测分数。

## 6. 降级策略

- 材料缺失或无法定位：保留 `single_claim`，`evidence_strength=low`，写入 uncertainty/pending_human，不补造出处。
- 缺图 / 低分辨率 / 公式丢失：页面注明“图缺失/不可用”，写入 rendering，不生成伪造图。
- 字体 / 模板 / office-renderer 不可用：退回纯文本大纲或 HTML deck，status 标 `partial`，说明降级原因。
- 导出失败或超时：保留可编辑源文件与部分结果，status 如实标 `partial`/`failed`，记入 change_log。
- 缺失软件状态或科学数据一律只标 limitations，禁止编造。

## 7. 脚本调用（相对路径，仅标准库）

```bash
python3 scripts/scaffold.py --input /input --task /input/task.md --work /app/work --output /app/output
python3 scripts/scaffold.py --update '{"pages": [{"page_index": 1, "single_claim": "..."}]}' --output /app/output
python3 scripts/scaffold.py --update-file /app/work/patch.json --output /app/output   # 大补丁首选，跨平台最稳
python3 scripts/selftest.py --result /app/output/result.json --work /app/work --output /app/output
python3 scripts/main.py --input /input --task /input/task.md --work /app/work --output /app/output
```

脚本路径相对 SKILL.md 所在目录；均可用 `--help` 查看参数。字段定义见 `references/output_contract.md`，评分对应清单见 `references/checklist.md`。
