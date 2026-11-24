# 交付前自检与降级矩阵

本文把 `SKILL.md` 第 6、7 节展开成可逐条勾选的清单与异常处理矩阵。

## 1. 交付前自检（提交/交付前跑一遍）

```bash
# 在 Skill 包根目录执行，全部应退出码 0
python3 -B scripts/main.py --help
python3 -B scripts/validate.py --selftest
```

`--selftest` 会在临时目录构造一个最小演示输入，走完整流程
`run → merge → validate`，并断言抽取结果确定性（同一输入两次运行观察列表一致）。
它只证明脚本可执行、契约自洽，不产出正式案例答案。

## 2. 结果文件检查清单

| # | 检查项 | 通过标准 | 对应评分维度 |
|---|---|---|---|
| 1 | `result.json` 可解析 | `json.load` 成功 | 运行稳定性 |
| 2 | `case_id` 非空 | 取到 id 或记录 fallback | 结果可核验性 |
| 3 | `status` 合法 | ∈ 允许集合，且如实 | 运行稳定性 |
| 4 | `output_contract` 完整 | 键不丢、不改名、缺值 null/[] | 完整性 |
| 5 | finding 字段完整 | 11 字段齐全 | 任务完成度 |
| 6 | `quote` 可回查 | `validate.py` 命中原文 | 正确性 |
| 7 | `rejections` 非空 | 每个排除观察有 reason | 误报抑制 |
| 8 | 十方向覆盖 | 每方向有 checked/pending/not_checked | 完整性 |
| 9 | 依据可核对 | basis 含条款原文/编号或标 not_reported | 正确性 |
| 10 | 异常已记录 | errors/degradations 记录缺失与失败 | 异常处理 |
| 11 | `report.md` 存在 | 可读、可从 result.json 重建 | 易用性 |
| 12 | 重跑一致 | `--selftest` 确定性通过 | 可复核性 |

## 3. 降级矩阵

| 场景 | 处置 | 状态 | 记录位置 |
|---|---|---|---|
| 空输入目录 | 退出码 0，写合法结果 | `insufficient_evidence` | `errors` |
| 坏 JSON | 捕获并记录，继续其余材料 | `partial` | `errors` |
| 坏编码 | utf-8-sig → utf-8 → gb18030 → latin-1 逐级降级 | `partial` | `degradations` |
| 单文件 >2MB | 跳过该文件 | `partial` | `degradations` |
| PDF 无文本层 | pdftotext → pdf-text-tool；均失败记不足 | `insufficient_evidence`/`partial` | `errors`/`degradations` |
| 缺 `output_contract` | 用标准 schema，标 `contract_missing=true` | `partial` | 顶层 `contract_missing` |
| 缺软件/工具 | 标 limitations，不伪造 | `partial` | `degradations` |
| 受限数据 | 只标位置与类型，不复述 | `partial` | `pending_human` |
| 预算不足 | 保留 partial 骨架，如实标注 | `partial` | `change_log` |

## 4. 文件与资源约束

- 交付包：`SKILL.md`（frontmatter 含 name/description/version，正文 ≤180 行）+ `scripts/`（1–3 个标准库脚本）+ `references/`（0–2 个规则/清单）。
- 总文件数 ≤14，总体积 ≤1.2MB，`main.py` <30KB（超出应拆 `core_impl.py`）。
- 脚本路径一律相对路径（`scripts/xxx.py`），默认 `/input`、`/app/output`、`/app/work` 均可被 `--input/--output/--work` 覆盖，便于本地复跑。
- 不提交正式测试案例、白板、预生成答案；示意输出必须标明为示例。

## 5. 维护信息

- 新增检查规则：在 `scripts/extract.py` 的 `build_observations` 中追加一个 scan，保持 rule 顺序固定以保证确定性；同时在 `references/audit-rules.md` 补充该规则的类型/严重度/判定口径。
- 修改 schema：同步更新 `scripts/main.py` 的 `FINDING_REQUIRED` 与 `scripts/validate.py` 的校验逻辑，避免校验与产出脱节。
