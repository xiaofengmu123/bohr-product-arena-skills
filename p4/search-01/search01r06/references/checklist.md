# 自检清单与降级策略

## 1. 交付前自检清单（对应评分维度）

### 实际效果（40 分）
- [ ] 每条 `results[]` 有真实标题或来源（DOI 或 URL）
- [ ] 每条结果有 `evidence[]`（locator + 逐字 quote）或明确 `pending_human`
- [ ] 元数据（作者/年份/期刊/DOI）尽量齐全，缺失进 `uncertainty.missing_metadata`
- [ ] 覆盖主要研究方向，遗漏写进 `uncertainty.coverage_gaps`

### 使用增益（25 分）
- [ ] 多轮查询规划以结构化 `plan.json` 表达（query_steps + 术语扩展核验清单）
- [ ] 关键词扩展（同义词/缩写/跨语言）逐条核验，记录在 plan/evidence
- [ ] 去重 + 透明打分减少人工筛选；`evidence` 可复现每次查询

### 创新增益（15 分）
- [ ] 中间产物（case_summary/plan/records/evidence/rubric/manifest）形成可复核内容指纹链
- [ ] 打分三因子可审计（`score_breakdown`）
- [ ] 增量导入状态（`import_state.json`）保留 upsert 过程

### 可靠体验（10 分）
- [ ] `python3 scripts/main.py --help` 退出码 0
- [ ] `python3 scripts/selftest.py` 全部 PASS（空输入/坏 JSON/缺输入/黄金路径/确定性）
- [ ] 空输入、坏 JSON、缺输入均写合法 result.json 且不崩溃
- [ ] 重复运行结果一致（固定排序键、确定性哈希）

### 生态贡献（10 分）
- [ ] SKILL.md + scripts + references 齐全，依赖仅 Python 标准库
- [ ] 他人可按说明独立复现一次完整检索
- [ ] 文件数 5–8、无符号链接、路径相对

## 2. 降级策略（不编造原则）

| 场景 | 处置 |
|-|-|
| 候选为空 / 材料不足 | `status=insufficient_evidence`，`status_notes`/`limitations` 写明缺失与下一步检索建议，不补造文献 |
| 检索工具不可用 / 限流 / 权限不足 | 记录到 `errors`/`degradations`，改用 `/input/materials/` 本地材料并标注局限 |
| 元数据缺失 | 字段写 `null`，`uncertainty.missing_metadata` 逐项列出 |
| 来源冲突 | 全部记录并标 `source_conflict`（进 `uncertainty.conflicts`），不擅自取舍 |
| 无法判断 | 写入 `pending_human`，不猜测 |
| 读取降级（BOM/坏编码/坏 JSON/超 2MB） | 捕获进 `degradations`，继续处理其余材料 |

## 3. 执行预算约束

- 总预算 900 秒、64 次模型请求、单响应 ≤16384 tokens（含 reasoning）。
- **前 3 分钟内必须落盘一版 `status=partial` 结果骨架**（`run` 子命令即完成 parse→plan→assemble），之后只做增量改进，任何中途失败都不留不完整产物。
- 时间分配建议：检索 40%、复核 30%、规划 15%、组装与自检 10%、缓冲 5%。
