# 输出契约与自检清单（slides-01）

本文件是 `SKILL.md` 的契约细则与提交前自检表，供 Agent 与复核者逐条勾选。

## 1. 契约字段对齐规则（不可违背）

1. `result.json` 的顶层字段**逐一对齐** `/input/case.json` 的 `output_contract`：
   - `output_contract` 是 dict → 取全部 key，顺序保持原样；
   - 是 list → 取字符串项或 `name/key/field/field_id` 值；
   - 缺 `output_contract` → 兜底 `case_id / editable_source / export_artifact /
     content_map / compliance_checks / exceptions`。
2. `case_id` 永远排第一，取 `case.json` 的 `case_id`；读不到写 `"unknown-case"`。
3. **未知字段不改名、不删除**：类型按名字推断（`_map/_list/_checks` 等 → `[]`，
   `_source/_artifact` 等 → `{}`，其余 → `null`）。无法匹配到产物的字段保持默认值，
   并在 `change_log` 里记录 `action=default`，绝不臆造内容。
4. 统一信封（与契约字段并列追加）：`status / sources / uncertainty / pending_human /
   change_log / errors / degradations / runtime`；若契约字段已含同名键则不重复。
5. `status` 只取四值之一：`ok | partial | insufficient_evidence | input_error`。

## 2. 主产物指向

- 可编辑源：`presentation.pptx` → 写入 `editable_source`（或语义匹配的契约字段）。
- 导出件：`presentation.pdf`（页数必须与 pptx 一致）→ 写入 `export_artifact`。
- 每页主张 → `content_map`；机械校验 → `compliance_checks`；异常 → `exceptions`。
- 无 pptx 能力时降级为 SVG+PNG 并把原因写进 `exceptions` / `degradations`，
  不得声称生成了 pptx。

## 3. 自检清单（提交/交付前逐条勾选）

- [ ] `SKILL.md` 有合法 YAML frontmatter：`name / description / version` 三项齐全。
- [ ] 正文写明：角色与适用条件、输入契约、输出契约、执行流程（含时间预算）、
      自检清单、降级策略、禁止事项。
- [ ] 脚本只用标准库，路径为相对路径（`scripts/xxx.py`）。
- [ ] 跑 `python3 scripts/selftest.py` 退出码 0，无 FAIL。
- [ ] 空目录 / 坏 JSON / 缺 case.json 三种输入各跑一次，均退出码 0 且写合法
      `result.json`（`input_error`），不抛异常。
- [ ] `result.json` 含 case_id + 全部契约字段 + 信封（含 `change_log`）。
- [ ] 每页 `content_map[i]` 有 `slide_no / role / claim / evidence`，evidence 内
      `source_id + locator + quote` 齐全且 quote 非空。
- [ ] `presentation.pptx` 是合法 OOXML 包（`[Content_Types].xml` 存在、slide≥1）。
- [ ] `presentation.pdf` 以 `%PDF-` 开头，页数与 pptx 幻灯片数一致。
- [ ] pptx 包内含 `slideLayouts`/`slideMasters`/`theme`（证明继承模板而非空白新建）。
- [ ] 页面数不超过材料/任务的页数上限，超限在 `exceptions` 说明。
- [ ] 所有数值、单位、方法名、结论表述与材料一致，未改动事实。
- [ ] 无占位空文件、无编造的引用/DOI/分数/运行结果。
