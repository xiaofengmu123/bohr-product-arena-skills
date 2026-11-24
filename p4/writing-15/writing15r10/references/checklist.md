# 交付自检清单与失败模式

评分五维度映射到可勾选动作；提交前逐条跑一遍。脚本命令均在 `scripts/` 下、仅标准库、相对路径调用。

## 1. 自检清单（对应评分维度）

**实际效果（40 分）**
- [ ] 稿件是可用章节/改写，不是通用写作建议。
- [ ] 每条论断与所引文献/材料对得上；数据、术语、符号、编号与原文一致，无新增事实。
- [ ] 研究问题、方法、结果、讨论、局限、图表、参考文献、附录齐全。
- [ ] 章节文字可回溯：evidence_map 非空，quote 逐字，locator 有效。

**使用增益（25 分）**
- [ ] 术语/编号/引用由 `--mode check` 统一，无需人工通读全文。
- [ ] 局部修改只动必要处；change_log 含 before/after/reason。
- [ ] 同概念全篇同写法（term_variants 报告为空或已裁决）。

**创新增益（15 分）**
- [ ] 术语变体、编号/交叉引用、引用缺口、证据定位均可脱离模型由脚本完成。
- [ ] 能证明相对基础 Agent/通用工具的边界（脚本输出作为证据，不做文字自述代替）。

**可靠体验（10 分）**
- [ ] `python3 -B scripts/main.py --help` 退出码 0。
- [ ] 空目录 / 坏 JSON / 缺输入三场景各跑一次：退出码 0、status 诚实、result.json 合法。
- [ ] 同一输入重复运行结果一致；不同章节类型与篇幅均产出可用文本。

**生态贡献（10 分）**
- [ ] SKILL.md 写明用途、依赖、配置、示例、适用范围、篇幅上限、失败模式。
- [ ] 术语表/证据映射/change_log/章节规划模板可直接复用。
- [ ] 其他 Agent 可仅凭 SKILL.md + 脚本独立复现一轮流程。

## 2. 最小自测（提交前在干净环境跑）

```bash
# 空目录
python3 -B scripts/main.py --mode run --input /tmp/empty --output /tmp/o --work /tmp/w
# 坏 JSON：case.json 写入非法内容后重跑
python3 -B scripts/main.py --mode run --input /tmp/bad --output /tmp/o2 --work /tmp/w2
# 校验
python3 - <<'PY'
import json, os
for p in ("/tmp/o/result.json", "/tmp/o2/result.json"):
    r = json.load(open(p, encoding="utf-8"))
    assert r.get("case_id") and r.get("status") in {"ok","partial","insufficient_evidence","input_error"}
    assert isinstance(r.get("sources"), list) and isinstance(r.get("uncertainty"), list) and isinstance(r.get("change_log"), list)
    print("ok", p, r["status"])
PY
```

## 3. 已知失败模式与处置

| 失败模式 | 处置 |
|---|---|
| 无 case.json | status=`input_error`，errors 记 `case_json_missing`，仍写 result.json |
| case.json 坏 JSON | 记 `degradations`，用默认契约字段，case_id 从 task.md 兜底 |
| 材料超 2MB | 跳过并记 `degradations`，继续其余材料 |
| PDF 无 pdftotext | 记 `pdf_text_unavailable`，改由预装 pdf-text-tool 提取 |
| 引用/结论冲突 | 两处都保留，标 `source_conflict`，写 uncertainty |
| 术语多写法 | 填入 term_table variants，改稿统一 canonical |
| 编号不连续 | check_report 报 gap，重排后重跑 check |
| 写盘失败 | 降级写 /app/work/result.json.fallback，退出码 0 |

## 4. 范围与限制

- 单文件解析上限 2MB；脚本仅标准库，PDF/Office 文本提取依赖预装工具或 pdftotext。
- 不产出一键式全自动论文；文字与论证必须由模型产出，脚本负责核查与溯源。
- 不确定项一律标 `pending_human`，不编造数值、引用、DOI、分数。
