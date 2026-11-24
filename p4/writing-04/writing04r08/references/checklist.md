# 提交前自检清单（逐条对应评分维度）

交付前逐条勾选；任何未勾选项必须在 `result.json` 的 `errors / degradations / pending_human / uncertainty` 中如实说明，不得静默跳过。括号内为评分点分值。

## 实际效果（40 分）

- [ ] 任务完成度（15）：从真实稿件 + 真实文献库出发，完成引用插入定位、编号更新、去重、格式转换、来源核验，交付可编辑 `manuscript.md` 与同步 `references.md`，并覆盖题目六类动作（见 `result.json.coverage`）。
- [ ] 结果正确性（15）：每条引用绑定真实、可定位的 `entry_id`；元数据仅来自材料；`match_reason` 说明论断与所引文献主题/结论匹配；不新增材料外事实。
- [ ] 结果完整性（5）：覆盖任务要求的动作、全部章节、全部引用点、全部文献条目，`consistency_report.json` 无意外漏项。
- [ ] 结果可核验性（5）：`evidence_map.json` 每项含 `locator + 逐字 quote + 匹配依据`；`change_log` 记录每步改动；`library_snapshot.json` 可回溯原文库；`merge_evidence` 记录合并依据。

## 使用增益（25 分）

- [ ] 质量增益（15）：编号连续、条目完整、引用位置在论断句处，优于整段末尾挂引用的基线。
- [ ] 效率增益（5）：编号重排、去重、格式转换由 `scripts/main.py` 自动完成，人工只做语义确认（写 `citation_plan.json`）。
- [ ] 增益可验证性（5）：同一输入重复运行内容一致（脚本确定性），可用 `--self-test` 与同输入对照验证。

## 创新增益（15 分）

- [ ] 新增能力（5）：编号连锁更新（正文标记 ↔ 文末列表 ↔ 证据映射三者同步）；同一文献多条论断共享一个编号。
- [ ] 方法改进（5）：去重判据可解释（DOI / 规范化题名+年份+第一作者姓氏，union-find 联合求并），合并依据写入 `merge_evidence`。
- [ ] 创新证据（5）：论断级匹配逐句记录（`evidence_map.json`）；`result.json.coverage` + `scoring_alignment` 说明相对基础 Agent / 通用工具的价值边界。

## 可靠体验（10 分）

- [ ] 调用与易用（3）：`python3 -B scripts/main.py --help` 退出码 0；默认参数指向 `/input /app/output /app/work`。
- [ ] 运行稳定性（4）：空目录、坏 JSON、坏编码、缺字段、重复运行均不崩溃且产出合法 `result.json`。
- [ ] 异常处理（3）：字段缺失、DOI 解析失败、条目歧义、编号冲突、格式不支持、文件损坏均识别并写入对应字段。

## 生态贡献（10 分）

- [ ] 文档完整性（3）：用途、适用条件、输入输出、异常处理、限制、维护信息齐全（见 `SKILL.md`）。
- [ ] 资源可复用（4）：脚本纯标准库；规则与检查清单位于 `references/`；去重规则、格式映射可独立复用（见 `references/rules.md`）。
- [ ] 分享价值（3）：沉淀失败模式与实践经验，不依赖作者个人文件或手工操作（见 `references/rules.md` 第 7 节）。

## 最终校验命令

```bash
python3 -B scripts/main.py --help                 # 退出码 0
python3 -B scripts/selftest.py                    # 退出码 0
python3 -B scripts/main.py                        # 跑通 /input -> /app/output
python3 - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
assert r.get('case_id') is not None
assert r.get('status') in {'ok', 'partial', 'insufficient_evidence', 'input_error'}
assert isinstance(r.get('coverage'), dict) and isinstance(r.get('scoring_alignment'), dict)
print('result ok:', r['status'])
PY
```

- 产物检查：`manuscript.md`、`references.md`、`evidence_map.json`、`consistency_report.json`、`library_snapshot.json` 均存在且内容合法（允许空列表表示无该问题）。
- 空目录 / 坏 JSON / 缺输入三种情况各跑一次，均应退出码 0 且如实标注状态（空目录 → `insufficient_evidence`）。
