# 自检清单、一致性规则与失败模式

## 交付前自检清单（逐条勾选，对应评分维度）

### 实际效果
- [ ] 章节按提纲/编辑工单**真正写出或改出可用文本**，不是通用写作建议
- [ ] 每条论断与所引文献对得上；数据、术语、符号、编号与原文一致
- [ ] 提纲要求的章节、研究问题/方法/结果/讨论/局限等要素齐全
- [ ] 图表、参考文献、附录等著录项齐全
- [ ] 每句关键结论可回溯到 `evidence_map`（locator + quote）

### 使用增益
- [ ] 术语表/符号表/定义清单/结论清单生成，跨章节一致
- [ ] 编号与交叉引用重排后连续、无空引用
- [ ] 减少手工逐条补引用与统一术语成本（改动记录可证明）

### 可靠体验
- [ ] 空目录、坏 JSON、缺材料三种情况均退出码 0 且如实标注
- [ ] 不同章节类型、不同篇幅、重复运行均产出可用文本（确定性）
- [ ] 材料缺失、引用找不到、结论冲突、编号不连续、格式不支持、超长截断均有识别与处置

### 生态贡献
- [ ] SKILL.md 写清用途、适用条件、调用方式、输入输出、异常处理
- [ ] 脚本可独立复用（`--help` 退出码 0）
- [ ] references 中的模板/清单/失败模式可被他人复用

## 一致性规则（check_consistency.py 的判据）

1. **标题编号**：同层递增，跨层只回退到父层；重复/回退/跳号记录。
2. **图/表/式编号**：caption 定义集与正文交叉引用集对齐；引用不存在的编号标 warn。
3. **术语/缩写**：同一缩写只允许一个展开；首次使用必须先定义后使用。
4. **符号表**：符号表内的符号应在正文出现，否则提示未使用。

检查报告只给 findings，不自动改原文；是否修改由 Agent 决定，修改后需再次运行检查确认收敛。

## 证据验证（check_evidence.py 的判据）

- `missing_source`：源文件不存在 → 必须改写或转 `pending_human`。
- `bad_locator_line`：行号越界 → 修正 locator。
- `quote_mismatch`：quote 与源文不一致 → 换用逐字 quote 或降级为转述并标注。
- `unreadable_source`：源文件坏编码 → 记 `degradations`，改用 pdf-text-tool 重新提取。

## 已知失败模式

| 模式 | 表现 | 处置 |
|---|---|---|
| 标签即内容假设 | 按领域标签补造数据 | 一律读实际文件，不依据标签生成事实 |
| 引用悬空 | 引了文献但结论被改写/文献不存在 | 补绑定或标 `not_reported`/`pending_human` |
| 术语漂移 | 同一概念多种写法 | 建术语表，只保留一个规范写法与唯一缩写 |
| 编号跳号/空引用 | 图 3 缺失、正文引"见图 5"但无图 5 | 重排编号并修复交叉引用 |
| 结论冲突 | 前后章节结论矛盾 | 两条都记录并标 `source_conflict`，请作者定夺 |
| 改稿漂移 | 局部修改牵动全文 | 只动目标段落，change_log 记录 before/after |
| 编造数据 | 缺数值时硬填 | 写 `not_reported`，缺关键材料写 `insufficient_evidence` |

## 空输入/坏输入验证命令

```bash
python3 -B scripts/discover.py --help
python3 -B scripts/discover.py --input /tmp/empty --output /tmp/out --work /tmp/work
python3 -B scripts/discover.py --input /tmp/badjson --output /tmp/out --work /tmp/work
```

预期：两次退出码均为 0，result.json 合法，status 分别为 `insufficient_evidence` 与 `partial`（或 `input_error`），errors/degradations 记录原因。
