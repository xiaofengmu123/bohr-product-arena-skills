# 运行自检清单（references/checklist.md）

提交/交付前逐条确认，对应 `SKILL.md` 第 6 节与评分维度。

## A. 接口与触发（调用与易用性）
- [ ] SKILL.md frontmatter 含 name/description/version，description 说清何时使用；
- [ ] 输入默认 `/input`，输出默认 `/app/output`，工作目录 `/app/work`；
- [ ] `python3 -B scripts/main.py --help` 退出码 0；
- [ ] 用户能说清源语言、目标语种、术语要求并走完一次完整检索。

## B. 运行与产物（稳定性/完整性）
- [ ] 空输入目录：`init` 仍写出合法 result.json（`status=input_error`），退出码 0；
- [ ] 坏 JSON/坏编码：记录到 `errors`/`degradations`，继续处理其余材料；
- [ ] 骨架在 3 分钟内落盘；中途异常不留下半截 JSON（原子写）；
- [ ] 同一输入重复运行结果一致（无随机性、change_log 用单调 seq 排序）；
- [ ] `validate` 通过：case_id 存在、契约字段齐、status 合法。

## C. 中间表示（本版核心）
- [ ] `/app/work/plan.json` 已落盘且含 terminology_table、queries；
- [ ] `/app/work/evidence.json` 已落盘且含 hits、dedup、missing、conflicts；
- [ ] result.json 含 `intermediate.plan_sha256` / `intermediate.evidence_sha256`；
- [ ] `assemble` 能从中间产物重建 result.json，且重算哈希一致。

## D. 检索质量（实际效果/增益）
- [ ] research_question 明确源/目标语种与检索范围；
- [ ] 术语表逐条带 type/basis/verification；
- [ ] 逐语种查询记录含翻译服务与版本、原文-译文对照；
- [ ] 每条命中带 locator + 逐字 quote，language/version/body_language/attribution 标注清楚；
- [ ] 同论文多语版本已去重并记录依据，关键非英语文献被覆盖；
- [ ] missing/conflicts/权限与接口异常如实说明。

## E. 诚实与复核（可核验/异常处理）
- [ ] 无编造引用、DOI、数值；缺失标 pending_human/not_reported；
- [ ] 术语表、查询式、语种判断依据可被第三方独立复核；
- [ ] status 与 uncertainty 与证据强度一致；
- [ ] 翻译失败/限额、语种不确定、原文不可得等异常有处置记录。

## F. 交付（文档/复用）
- [ ] SKILL.md 正文 ≤180 行；文件总数 ≤14；总体积 ≤1.2MB；
- [ ] 脚本仅标准库；路径用相对路径（scripts/xxx.py）；
- [ ] 说明与提交版本一致；无预写正式答案或白板。
