# 提交前自检与引用规则

交付前逐条勾选；未勾选项必须在 `result.json` 的 `errors/degradations/pending_human/uncertainty` 中如实说明，不得静默跳过。

## 一、自检清单（逐条对应评分维度）

### 实际效果（40 分）

- [ ] 任务完成度：从真实稿件 + 真实文献库出发，完成引用插入定位、编号更新、去重、格式转换、来源核验，交付 `manuscript.md` + `references.md`。
- [ ] 结果正确性：每条引用落到真实、可定位条目；元数据来自材料；论断与所引文献主题/结论匹配。
- [ ] 结果完整性：覆盖任务要求的动作、全部章节、全部引用点、全部文献条目。
- [ ] 结果可核验性：从成品回溯原稿、文献库快照、匹配依据（`evidence_map.json`）与每步改动（`change_log`）。

### 使用增益（25 分）

- [ ] 质量增益：加载本 Skill 后编号连续、条目完整、引用位置正确，优于未加载基线。
- [ ] 效率增益：编号重排、去重、格式转换由 `scripts/main.py` 自动完成，人工只做语义确认。
- [ ] 增益可验证性：同一输入重复运行一致（脚本确定性），`--self-test` 与同输入对照可复现。

### 创新增益（15 分）

- [ ] 新增能力：编号连锁更新（正文标记 ↔ 文末列表 ↔ 图表注 ↔ 附录引用同步）。
- [ ] 方法改进：去重判据可解释（DOI / 规范化题名 + 第一作者 + 年份），合并依据写入 `merge_evidence`。
- [ ] 创新证据：论断级匹配逐句记录，能说明相对基础 Agent / 通用工具的价值边界。

### 可靠体验（10 分）

- [ ] 调用与易用：`python3 -B scripts/main.py --help` 退出码 0；默认 `--input /input --output /app/output --work /app/work`。
- [ ] 运行稳定性：空目录、坏 JSON、坏编码、缺字段、重复运行均不崩溃且产出合法 `result.json`。
- [ ] 异常处理：字段缺失、DOI 解析失败、条目歧义、编号冲突、格式不支持、文件损坏均识别并写入对应字段。

### 生态贡献（10 分）

- [ ] 文档完整性：用途、适用条件、输入输出、异常处理、限制、维护信息齐全（见 `SKILL.md`）。
- [ ] 资源可复用：脚本纯标准库；规则与清单位于 `references/`；去重规则、格式映射可独立复用。
- [ ] 分享价值：沉淀失败模式与实践经验，不依赖作者个人文件或手工操作。

## 二、引用规则（语义阶段 Agent 必须遵守）

### 2.1 引用定位（挂得对）

- 引用必须落在需要来源支撑的**论断句末尾**，而非整段末尾或图表注附近。
- 一句多引用 `[2,5]`；多句一引：每句各自标注，不共享一个段落末引用。
- 每处引用在 `evidence_map.json` 记三项：`entry_key`、`locator`（如 `manuscript.md#L12`）、`quote`（正文论断逐字片段）与匹配依据（`claim` → 文献摘要/结论的关键词对应）。

### 2.2 编号重排（编得顺）

- 数字顺序编码制：文末列表按正文**首次出现顺序**编号；正文标记、图表注、附录引用同步。
- Agent 产出 `work/citation_map.json`：

```json
{
  "style": "numeric",
  "assignments": [
    {"old_number": 1, "entry_key": "e0f1a2b3c4d5", "locator": "manuscript.md#L12",
     "quote": "深度学习可提升气候预测精度"}
  ]
}
```

- 脚本据此重排旧编号为新编号，并展开/压缩区间（如 `[1-3,5]`）。

### 2.3 去重判据（收得净，可解释）

- 优先判据：DOI 规范化后相等。
- 次级判据：题名规范化（NFKC + 去标点/空格）+ 第一作者（规范化）+ 年份一致。
- 仅同名作者、同题名但年份/版本不同（预印本 vs 正式版）时，记录 `source_conflict` 并交由 `pending_human`，不自动合并。
- 合并依据写入 `merge_evidence.json`（`criterion`、`source_files`、`merged_count`）。

### 2.4 格式映射（字段补齐）

- 目标规范：GB/T 7714—2015（顺序编码，`[J]/[M]/[C]/[D]/[Z]`）或 APA（著者—出版年，`&` 连接，DOI 尾缀）。
- 脚本 `format_entry(entry, style)` 输出近似格式；DOI/卷期页/文献类型标识必须保留。
- 中英文混排：中文姓名保留，英文姓名转“姓 + 首字母”；标点差异由脚本统一。

### 2.5 来源核验（对得上）

- 每条引用必须能指回 `parsed_entries.json` 中的真实条目；无法指回 → `unresolved_citations`。
- DOI 存在但未能通过材料确认 → `not_verified`，写入 `uncertainty`；不声称已核验。
- 论断与文献主题不符 → 标记 `mismatch` 并记录证据，交由 `pending_human`。

## 三、最终校验命令

```bash
python3 -B scripts/main.py --self-test          # 退出码 0
python3 -B scripts/main.py                      # 跑通 /input -> /app/output
python3 - <<'PY'
import json
r = json.load(open('/app/output/result.json', encoding='utf-8'))
assert r.get('case_id') is not None and r.get('status') in {
    'ok', 'partial', 'insufficient_evidence', 'input_error'}
print('result ok:', r['status'])
PY
```
