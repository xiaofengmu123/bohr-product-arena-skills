# 提交前自检清单（逐条对应评分维度）

交付前逐条勾选；任何未勾选项必须在 `result.json` 的
`errors / degradations / pending_human / uncertainty` 中如实说明，不得静默跳过。

## 实际效果（40 分）

- [ ] 任务完成度（15）：从真实稿件 + 真实文献库出发，完成引用插入定位、编号更新、
      去重、格式转换与来源核验，交付可编辑稿件 `manuscript.md` 与同步文献表 `references.md`。
- [ ] 结果正确性（15）：每条引用落到真实、可定位的文献条目；元数据来自材料；
      正文论断与所引文献主题/结论匹配，逐条有依据。
- [ ] 结果完整性（5）：覆盖任务要求的动作（插入/更新/去重/转换/核验）、
      全部章节、全部引用点与全部文献条目。
- [ ] 结果可核验性（5）：能从成品回溯原始稿件、文献库快照
      （`work/library_snapshot.json`）、每条引用匹配依据（`evidence_map.json`）
      与每步改动（`change_log`）。

## 使用增益（25 分）

- [ ] 质量增益（15）：加载本 Skill 后编号连续、条目完整、引用位置正确、去重可解释，
      优于未加载基线。
- [ ] 效率增益（5）：解析、去重、编号重排、格式转换由 `scripts/main.py` 自动完成，
      人工只做语义确认与来源核验。
- [ ] 增益可验证性（5）：同一输入重复运行产物字节一致（`content_hash` 稳定）；
      `scripts/selftest.py` 退出码 0。

## 创新增益（15 分）

- [ ] 新增能力（5）：正文标记 ↔ 文末列表 ↔ 图表注 ↔ 附录引用 的编号连锁更新。
- [ ] 方法改进（5）：去重判据可解释（DOI / 规范化题名+第一作者+年份），
      合并依据写入 `merge_evidence`；跨语言/预印本变体仅标候选，不自动合并。
- [ ] 创新证据（5）：论断级匹配逐句记录，能说明相对基础 Agent / 通用工具的价值边界。

## 可靠体验（10 分）

- [ ] 调用与易用性（3）：`python3 -B scripts/main.py --help` 退出码 0；
      缺省 `--input /input --output /app/output --work /app/work`。
- [ ] 运行稳定性（4）：空目录、坏 JSON、坏编码、缺字段、重复运行均不崩溃，
      产出合法 `result.json`。
- [ ] 异常处理（3）：字段缺失、DOI 解析失败、条目歧义、编号冲突、格式不支持、
      文件损坏均识别并写入对应字段。

## 生态贡献（10 分）

- [ ] 文档完整性（3）：用途、适用条件、输入输出、异常处理、限制、维护信息齐全
      （见 `SKILL.md`）。
- [ ] 资源可复用（4）：脚本纯标准库；规则与检查清单在 `references/`；
      去重规则、格式映射可独立复用。
- [ ] 分享价值（3）：沉淀失败模式与实践经验，不依赖作者个人文件或手工操作。

## 最终校验命令

```bash
python3 -B scripts/main.py --self-test      # 退出码 0
python3 -B scripts/main.py                  # 跑通 /input -> /app/output
python3 - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
assert r.get('case_id') is not None and r.get('status') in {
    'ok', 'partial', 'insufficient_evidence', 'input_error'}
print('result ok:', r['status'])
PY
```

## 确定性承诺（本包侧重点）

- 可复现性定义：**除 `result.json` 的 `runtime.seconds` 与 `runtime.started_at` 外**，
  同一 `/input` 两次运行的所有产物（`manuscript.md`、`references.md`、
  `evidence_map.json`、`consistency_report.json`、`manifest.md`）字节一致，
  `content_hash` 一致。
- `selftest.py` 在内存中构造最小案例，运行两遍并断言字节一致；任何破坏
  确定性的改动都会让自检失败。
