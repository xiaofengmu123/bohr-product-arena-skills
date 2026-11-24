# slides-01 自检清单（交付前逐条勾选）

运行 `python3 -B scripts/selftest.py`（退出码 0 才算过），再人工逐条核对下表。

## A. 触发与调用
- [ ] SKILL.md 顶部 frontmatter 含 name / description / version，且 description 写清触发场景。
- [ ] `python3 -B scripts/main.py --help` 退出码 0；`inspect` 能打印 case_id + 契约键名。

## B. 输入契约
- [ ] 先读 `/input/case.json` 的 `case_id` 与 `output_contract`，不假设字段名。
- [ ] 实际 PDF 用 `pdf-text-tool`/`pdftotext` 抽取；读不出写缺口，不凭标签补造。
- [ ] 单文件 >2MB 跳过并记 `degradations`；坏 JSON/坏编码不崩溃。

## C. 前 3 分钟落盘（时间纪律）
- [ ] 开跑即 `python3 -B scripts/main.py scaffold --input /input --output /app/output`，
      立刻得到一份合法 result.json 骨架（所有契约字段占位）。
- [ ] 之后每 40 秒至少重写一次 result.json；连续思考 ≤2 分钟；剩 5 分钟必须已落盘。

## D. 主线与页结构（对应 40 分实际效果）
- [ ] 每页一个主张：问题 → 方法 → 结果 → 讨论/局限 → 结论，非按段落顺序搬运。
- [ ] 每页有 `claim`/`role`/`evidence`；`role` 在允许集合内。
- [ ] 单页信息密度受控：标题 ≤ 12 字、要点 ≤ 5 条、每页 ≤ 1 个主图/表。

## E. 证据溯源（本版强化，逐条硬性）
- [ ] 每条结论有 `source_id` + `locator`（图/表/公式/章节/页码）+ **逐字** `quote`。
- [ ] quote 逐字命中材料原文（数字、单位、方法名、结论表述一致），无改写。
- [ ] claim 中的数字+单位逐字出现在 quote，或显式 `numeric_inference=true`。
- [ ] `kind` 区分 figure/table/data（实验证据）与 schematic（示意图），不张冠李戴。
- [ ] 冲突信息两处都记并标 `source_conflict`；缺失写 `uncertainty`/`pending_human`。

## F. 讲解备注（与页面内容分离）
- [ ] `speaker_notes` 写过渡、强调点、被问时的补充证据，不复读页面文字。

## G. 模板与导出（对应可靠体验）
- [ ] pptx **真继承**模板 master/layout/theme（提供模板时），非「新建空白 + 贴文本框」。
- [ ] 提供模板时用 `build-pptx --template`；无模板时用最小合法空白 deck 并写 exceptions。
- [ ] 导出 PDF 页数 == pptx 页数（`python3 -B scripts/main.py pdf-pages --pdf ...` 核对）。
- [ ] pptx 可打开可编辑；换机/PDF 无字体崩坏（用 `font-discovery` 检查缺失字体）。

## H. result.json 与降级
- [ ] 回填全部 `output_contract` 键，且含 `case_id`/`status`/`sources`/`uncertainty`/
      `pending_human`/`change_log`/`runtime`。
- [ ] `status` 诚实：不足 → `insufficient_evidence`；失败 → `input_error`；部分 → `partial`。
- [ ] 缺数据/缺软件如实写 `degradations`/`exceptions`，绝不编造软件状态或科学数据。

## I. 结果可核验性
- [ ] 提供源文件 pptx + 导出 pdf + evidence_map + report.md（方法/版本/限制/复跑步骤）。
- [ ] 同输入重复运行结果一致（确定性布局、无随机种子漂移）。
