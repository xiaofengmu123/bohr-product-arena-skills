# 自检清单与交付前检查

对照题目评分维度逐条勾选；`scripts/selftest.py` 负责可自动化的部分，其余由 Agent 在定稿前人工核对。

## A. 结果正确性（对应“结果正确性 15 分”）

- [ ] 每条实质论断可指回 `evidence_table.jsonl` 的 `[S#]` 行；
- [ ] 每个证据行含 `source_id` + `locator` + 逐字 `quote`；
- [ ] 引述强度（established/suggestive/disputed/not_reported）与原文一致；
- [ ] 文献真实存在、元数据准确，未虚构 DOI/数值/页码。

## B. 结果完整性（对应“结果完整性 5 分”）

- [ ] 覆盖输入文献集的主要主题、方法路线、正反证据与结论分歧；
- [ ] 冲突结论并列呈现并标 `disputed`；
- [ ] 研究空白落到具体证据缺口（`uncertainty.gaps`），不用套话收尾。

## C. 结果可核验性（对应“结果可核验性 5 分”）

- [ ] `screening_log.json` 的筛选标准写在纳排决定之前；
- [ ] 去重记录（SHA-256 / 题名）可回溯；
- [ ] 每个 `source_id` 在 `result.json.sources` 中可查，含元数据与文件定位；
- [ ] `change_log` 每个 Phase 有一条 `{ts, step, action, detail}`。

## D. 任务完成度（对应“任务完成度 15 分”）

- [ ] 四步（筛选 → 主题归纳 → 证据组织 → 草稿）全部跑完；
- [ ] 主产物齐全：`review.md`、`screening_log.json`、`evidence_table.jsonl`、`themes.md`、`conflicts_and_gaps.md`；
- [ ] 结论与研究问题、证据范围一致，不越过证据范围扩展。

## E. 调用与易用性（对应“调用与易用性 3 分”）

- [ ] `result.json` 含 `case_id` + 全部契约字段 + `status/sources/uncertainty/change_log`；
- [ ] `status` 取值合法且诚实（ok/partial/insufficient_evidence/input_error）；
- [ ] 前 3 分钟已落盘 partial 骨架，此后仅增量改进。

## F. 异常处理（对应“异常处理 3 分”）

- [ ] 缺全文 → `abstract_only` / `no_full_text` 且结论降级；
- [ ] 元数据不全 → `metadata_incomplete`，字段标 `not_reported`；
- [ ] 文献重复 → `duplicate_removed`；
- [ ] 结论冲突 → `source_conflict`，并列呈现；
- [ ] 检索为空 / 材料不足 → `insufficient_evidence` + `pending_human`，不编造；
- [ ] 超出上下文 → 明确列出未纳入内容。

## G. 运行稳定性（对应“运行稳定性 4 分”）

- [ ] 脚本只用 Python 标准库，路径相对 `scripts/`；
- [ ] `main.py` 空目录/坏 JSON/坏编码/缺 `pdftotext` 均退出码 0；
- [ ] 同一输入两次运行结果一致（确定性排序、无随机）。

## H. 交付前检查（提交前执行）

```bash
cd <skill-root>
python3 -B scripts/selftest.py --self          # 合成场景自检，应全部 PASS
python3 -B scripts/main.py --help               # 退出码 0
```

- 文件数 ≤ 14，单文件 ≤ 500KB，总体积 ≤ 2MB（本包 6 个文件、目标 60–140KB）；
- 无 symlink，无预制答案 / 正式测试产物；
- `SKILL.md` frontmatter 含 `name` / `description` / `version`，正文 ≤ 180 行。
