# 自检清单 × 评分维度对照（checklist）

本表把题面评分维度映射到"可勾选条目 + 脚本证据"。交付前逐条执行；脚本证据用命令验证，不依赖口头自述。

## 对照表

| 评分维度 | 分值 | 自检条目 | 脚本/命令证据 |
|---|---|---|---|
| 实际效果·任务完成度 | 15 | 交付"润色全文 + 逐句修改表"，非建议/整段重写 | `finalize` 后 `manuscript.md` 含"润色后文本"与"逐句修改说明"两节 |
| 实际效果·结果正确性 | 15 | 限定强度、数据、单位、引用、图表指向未被改坏 | `python3 -B scripts/main.py verify` → `check_report.json` 的 `errors == 0` |
| 实际效果·结果完整性 | 5 | 指定章节逐段处理、术语表建全、缩写首次统一、双语对照齐全 | `glossary.md` 术语表非空；`edits.json.units` 覆盖全部句子 |
| 实际效果·结果可核验性 | 5 | 原文—改文—类型—理由足以让第三方判断该不该改 | `manuscript.md` 每句含四要素；`fact_source_map` 每项有 `locator+quote` |
| 使用增益·质量增益 | 15 | 相同输入/术语表下，比基础 Agent 更稳：术语一致、语义保持、语域贴合 | `verify` 的 `term_variant`/`hedge_removed` 清零；`target_lang`/`genre` 已写入 `edits.json` |
| 使用增益·效率增益 | 5 | 减少人工返修与逐句核对条目，成本不后移 | 校验器自动捕获数字/引用漂移，无需逐条人工核对 |
| 使用增益·增益可验证性 | 5 | 对照条件一致、可盲评 | 同一 `edits.json` 可重复 `verify`/`finalize`，输出确定 |
| 创新增益·新增能力 | 5 | 术语表驱动的全篇一致性回扫、限定强度守恒检查、双语术语对齐 | `checks.check_terms` / `checks.extract_hedges` 确定性实现 |
| 创新增益·方法改进 | 5 | 最小编辑、分句级编辑、双通道审校、改后回读/回译 | SKILL.md 阶段 3 双通道；`split_sentences` 分句模板 |
| 创新增益·创新证据 | 5 | 证明相对基础 Agent/通用工具/人工基线的边界 | `selftest.py` 变异测试：伪造数字被 `measurement_mismatch` 捕获 |
| 可靠体验·调用与易用性 | 3 | 该润色时触发、纯内容问答不触发；纯文本/Markdown/LaTeX 可直接用 | SKILL.md §1 使用/不使用边界；文本容错读取 |
| 可靠体验·运行稳定性 | 4 | 长文本/分章节/重复运行/多学科稳定；同段重复润色不漂移 | `selftest.py` 确定性重放：两次 `finalize` 字节一致 |
| 可靠体验·异常处理 | 3 | 超长/编码混乱/公式引用混杂/中英日混排/术语冲突/文体不明处置 | `selftest.py` 空输入/坏 JSON/坏编码三例；SKILL.md §6 降级策略 |
| 生态贡献·文档完整性 | 3 | SKILL.md、术语表/风格规则格式、示例、适用范围、限制、维护信息齐全 | 本目录 `SKILL.md` + `references/*.md` |
| 生态贡献·资源可复用性 | 4 | 术语表、风格规则、测试文本、评分表、脚本可被他人直接用于自己稿件 | `scripts/checks.py`（可扩展词表）、`references/preservation-rules.md` |
| 生态贡献·分享推广价值 | 3 | 沉淀可分享案例、评测集、术语治理经验 | 合成夹具在 `selftest.py` 内，可作为最小评测集复用 |

## 交付前命令序列

```bash
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
# 阶段 1–4：读材料、填 edits.json/glossary.json、反复 verify 修复
python3 -B scripts/main.py verify   --work /app/work
python3 -B scripts/main.py finalize --work /app/work --output /app/output
python3 -B scripts/main.py selfcheck --input /input --output /app/output --work /app/work
python3 -B scripts/selftest.py      # 提交前自检（16 项断言，应全绿）
```

## 判定口径

- `errors == 0` 且 `warnings == 0` 且底稿非空 → `status=ok`。
- `errors > 0` 或 `warnings > 0` → `status=partial`（诚实标注，不冒充完成）。
- 无底稿/无材料 → `insufficient_evidence`；输入目录缺失 → `input_error`。
- 缺软件/缺解析工具 → 记入 `degradations`/`pending_human`，不伪造渲染结果或数据。
