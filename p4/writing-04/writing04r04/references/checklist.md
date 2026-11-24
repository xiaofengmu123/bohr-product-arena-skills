# 提交前自检清单（逐条对应评分维度）

交付前逐条勾选；任何未勾选项必须在 `result.json` 的
`errors / degradations / pending_human / uncertainty / limitations` 中如实说明，
不得静默跳过。分值标注对应题面评分维度。

## 实际效果（40 分）

- [ ] 任务完成度（15）：从真实稿件 + 真实文献库出发，完成引用插入定位、
      编号更新、去重、格式转换、来源核验，交付可编辑稿件
      （`manuscript.md`）与同步文献表（`references.md`）。
- [ ] 结果正确性（15）：每条引用落到真实、可定位的文献条目；元数据来自
      材料；论断与所引文献主题/结论匹配。
- [ ] 结果完整性（5）：覆盖任务要求的动作、全部章节、全部引用点、全部条目。
- [ ] 结果可核验性（5）：`evidence_map.json`（每条引用含 `locator + quote +
      match_evidence`）与 `change_log` 可回溯原稿、文献库快照与每步改动。

## 使用增益（25 分）

- [ ] 质量增益（15）：编号连续、条目完整、引用位置正确，优于未加载基线。
- [ ] 效率增益（5）：编号重排、去重、格式转换由 `scripts/main.py` 自动完成，
      人工只做语义确认。
- [ ] 增益可验证性（5）：同一输入重复运行结果一致（确定性），可用
      `--self-test` 与 `scripts/selftest.py` 验证。

## 创新增益（15 分）

- [ ] 新增能力（5）：编号连锁更新（正文 ↔ 列表同步）、论断级引用匹配记录。
- [ ] 方法改进（5）：去重判据可解释（DOI / 题名+第一作者+年份），合并依据
      写入 `merge_evidence`，字段冲突单独列出。
- [ ] 创新证据（5）：`uncertainty/limitations` 显式化，缺数据不编造，能说明
      相对基础 Agent 的价值边界。

## 可靠体验（10 分）

- [ ] 调用与易用性（3）：`python3 -B scripts/main.py --help` 与 `--self-test`
      退出码 0；默认 `--input /input --output /app/output --work /app/work`。
- [ ] 运行稳定性（4）：空目录、坏 JSON、坏编码、缺字段、重复运行均不崩溃，
      且产出合法 `result.json`。
- [ ] 异常处理（3）：字段缺失、DOI 解析失败、条目歧义、编号冲突、格式不支持、
      文件损坏均识别并写入对应字段。

## 生态贡献（10 分）

- [ ] 文档完整性（3）：用途、适用条件、输入输出、异常处理、限制、维护信息
      齐全（见 `SKILL.md`）。
- [ ] 资源可复用（4）：脚本纯标准库；`references/citation_rules.md` 与
      `references/checklist.md` 可独立复用。
- [ ] 分享价值（3）：沉淀失败模式与实践经验，不依赖作者个人文件或手工操作。

## 最终校验命令

```bash
python3 -B scripts/main.py --self-test          # 退出码 0
python3 -B scripts/selftest.py                  # 退出码 0（合成用例全通过）
python3 -B scripts/main.py                      # 跑通 /input -> /app/output
python3 - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
assert r.get('case_id') is not None
assert r.get('status') in {'ok','partial','insufficient_evidence','input_error'}
print('result ok:', r['status'])
PY
```

## 本轮侧重：降级与不确定性（已显式化为第一等字段）

- [ ] 每个无法确证的事实/引用/数值都在 `uncertainty` 有 `id + kind + locator
      + claim + severity + resolution`。
- [ ] 系统性边界（缺渲染器、缺检索、超限文件）都在 `limitations` 列出。
- [ ] `severity=high` 项同步出现在 `pending_human`，且未决时 `status ≠ ok`。
- [ ] 缺失字段用 `not_reported` / `[字段缺失]`，绝不补造卷期页、年份、DOI。
