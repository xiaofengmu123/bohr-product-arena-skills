# 自检与降级清单（提交前逐条勾选）

对应题目五个评分维度（实际效果 / 使用增益 / 创新增益 / 可靠体验 / 生态贡献），
以及本变体侧重的“自检与确定性”。运行 `python3 scripts/selftest.py` 可自动验证
其中带 `[A]` 的条目。

## 实际效果（40 分）

- [ ] 每条 `results` 都有真实标题 + 来源（DOI 或 URL）+ 逐字 `evidence.quote`
- [ ] 每条 `results` 都有 `relevance_reason`，相关性判断可复核
- [ ] 作者/年份/期刊/DOI 尽量齐全，缺失项进入 `uncertainty.missing_metadata`
- [ ] 覆盖主要研究方向，遗漏写入 `uncertainty.coverage_gaps`
- [ ] 排序由 `score` 驱动，`score` 的计算式可复核（见 result_schema.md）

## 使用增益（25 分）

- [ ] 多轮查询规划 + 关键词扩展清单写入 `plan.json`，比基础 Agent 提高 Recall
- [ ] 去重（DOI/标题）与透明打分减少人工筛选，每条证据可复现
- [ ] 每次检索都通过 `evidence` 命令记录 query/source/database/rationale
- [ ] `candidates.md` 可打开、可编辑，便于人工复核

## 创新增益（15 分）

- [ ] 查询规划 / 关键词扩展核验清单 / 跨库去重 / 透明打分由脚本固化、可复现
- [ ] 打分权重与来源权重为模块常量（core_impl.py），评审可对照
- [ ] 确定性指纹 + 重复运行检查证明了创新点相对“一次性提示词”的边界

## 可靠体验（10 分）

- [ ] [A] `python3 scripts/main.py --help` 退出码 0
- [ ] [A] 空输入目录 → `status=insufficient_evidence`，退出码 0，不抛异常
- [ ] [A] 坏 JSON / 坏编码 → 记录到 `degradations`，继续处理其余材料
- [ ] [A] 同输入两次运行 → result.json 逐字节一致（确定性）
- [ ] 写盘失败 / 缺参数 → 降级到最小合法产物，不让进程崩溃
- [ ] 缺数据 / 缺软件 → 如实标注 `limitations`/`errors`/`degradations`，不编造

## 生态贡献（10 分）

- [ ] SKILL.md 写清用途、适用条件、调用方式、输入输出与异常处理
- [ ] 脚本纯标准库、相对路径调用，他人可独立复现一次完整检索
- [ ] references/ 讲清 schema 与检查清单，与提交版本一致

## 确定性专项（本变体核心）

- [ ] [A] `python3 scripts/selftest.py` 全量通过（help / 空输入 / 坏 JSON / 确定性 / selfcheck）
- [ ] [A] `python3 scripts/selftest.py --quick` 只跑确定性，快速复核
- [ ] result.json 不含墙钟时间；时间顺序由 `change_log[].seq` 表达
- [ ] `determinism.fingerprint` 非空，且与重复运行的 result.json 内容一致

## 降级策略（不允许编造）

- 候选为空 → `status=insufficient_evidence`，`status_notes`/`limitations` 写缺失与
  下一步检索建议，**不补造文献**。
- 检索工具不可用 / 限流 / 权限不足 → 记录到 `errors`/`degradations`，改用本地
  `materials/`（PDF 等）补充证据并标注局限。
- 元数据缺失 → 标 `null` 并列入 `uncertainty.missing_metadata`。
- 同指标多来源不一致 → 全部记录并标 `source_conflict`，不擅自取舍。
- 无法判断的字段 → 写 `pending_human`，不猜测。
