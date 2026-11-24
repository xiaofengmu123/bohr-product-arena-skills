# 运行自检清单（references/checklist.md）

提交/交付前逐条确认，对应 SKILL.md 第 6 节与评分维度。自动检查用 `python3 -B scripts/selftest.py`；结果检查用 `python3 -B scripts/main.py validate`。

## A. 接口与触发（可靠体验 · 调用与易用性 3 分）
- [ ] SKILL.md frontmatter 含 name/description/version，description 说清何时使用与两种模式；
- [ ] 输入默认 `/input`，输出默认 `/app/output`，工作目录 `/app/work`；
- [ ] `python3 -B scripts/main.py --help` 退出码 0；
- [ ] 用户能说清源语言、目标语种、术语要求并走完一次完整检索。

## B. 运行与产物（可靠体验 · 运行稳定性 4 分）
- [ ] 空输入目录：`init` 仍写出合法 result.json（`status=input_error`），退出码 0；
- [ ] 坏 JSON/坏编码：记录到 `errors`/`degradations`，继续处理其余材料；
- [ ] 骨架在 3 分钟内落盘；中途异常不留下半截 JSON（原子写）；
- [ ] 同一输入重复运行结果一致（无随机性、无时间依赖的排序差异）；
- [ ] `validate` 通过：case_id 存在、契约字段齐、status 合法。

## C. 检索质量（实际效果 40 分）
- [ ] research_question 明确模式、源/目标语种与检索范围（完成度）；
- [ ] 术语表存在且逐条带 type/basis/verification（完成度 + 正确性）；
- [ ] 逐语种查询记录含翻译服务与版本、原文-译文对照（可核验性）；
- [ ] 每条命中带 locator + 逐字 quote，语言与版本标注清楚（正确性 + 可核验性）；
- [ ] 声明语种均有结果，关键非英语文献覆盖，同论文多语版本已去重（完整性）；
- [ ] missing/conflicts/权限与接口异常如实说明（完整性）。

## D. 诚实与复核（可靠体验 · 异常处理 3 分 + 使用增益可验证性）
- [ ] 无编造引用、DOI、数值；缺失标 pending_human/not_reported；
- [ ] 术语表、查询式、语种判断依据可被第三方独立复核；
- [ ] status 与 uncertainty 与证据强度一致；
- [ ] 翻译失败/限额、语种不确定、原文不可得等异常有处置记录；
- [ ] change_log 记录每步增量，去重后计数可与来源逐条对应。

## E. 创新与生态（创新增益 15 分 + 生态贡献 10 分）
- [ ] 语言识别与路由、版本归并、回译复核在 method.md 有明确规则与边界说明；
- [ ] 术语表模板、查询模板、去重规则以字段 schema 形式可复用；
- [ ] 文档完整：SKILL.md + method.md + checklist.md 覆盖用途、依赖、限制、维护；
- [ ] 无预写正式答案或白板；示例输出标明为示例。

## F. 交付（生态贡献 · 资源可复用性）
- [ ] SKILL.md 正文 ≤180 行；文件总数 ≤14；总体积 ≤1.2MB；
- [ ] 脚本仅标准库；SKILL.md 内脚本路径用相对路径（scripts/xxx.py）；
- [ ] `selftest.py` 全绿：PASS 覆盖空输入、坏 JSON、带 contract、patch 增量、--help。

## 降级速查（对应 SKILL.md 第 7 节）
| 异常 | 处置 |
|---|---|
| 缺 case.json/task.md | `status=input_error`，列缺口，退出码 0 |
| 坏 JSON/坏编码 | `errors`/`degradations` 记录，继续处理其余材料 |
| 检索工具不可用 | `insufficient_evidence`/`partial`，说明已完成/未完成 |
| 翻译失败/限额 | 回退术语表查询，未验证译文标 `unverified_translation` |
| 语种不确定 | `language:"undetermined"`，写入 `uncertainty` |
| 仅摘要/原文不可得 | 版本 `abstract_only`/`undetermined`，不冒充全文 |
| 缺关键数值 | `not_reported`/`pending_human`，不补造 |

## 契约字段速查（result.json 顶层）
必含 `case_id`、`status`（ok/partial/insufficient_evidence/input_error）、契约全部字段，以及 `sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`pending_human`、`runtime`。默认主结果字段：`research_question`、`terminology_table`、`queries`、`results`、`evidence_map`、`dedup`、`missing`、`conflicts`。
