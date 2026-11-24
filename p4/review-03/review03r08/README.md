# 研究方法与统计审查 Skill（评分维度对齐版）— 交付说明

本目录为 `review` 第 3 题「研究方法与统计审查」的 Skill 包，可被官方 DeepSeek Harness
加载到 `/skills/weekly-skill` 后对 3 个固定案例运行。

## 文件清单

| 路径 | 作用 |
|---|---|
| `SKILL.md` | 角色、输入/输出契约、执行流程与预算、自检清单、降级策略 |
| `scripts/main.py` | 薄入口，分派 discover/scaffold/finalize/selftest 子命令 |
| `scripts/core_impl.py` | 全部实现（纯标准库） |
| `scripts/selftest.py` | 交付前自检（薄包装） |
| `references/checklist.md` | 五检查面、六检查类、缺陷类型、严重度、方法—数据匹配表 |
| `references/result_schema.md` | result.json 契约、finding 字段、中间产物说明 |

## 运行方式

```text
cd /skills/weekly-skill
python -B scripts/main.py discover
python -B scripts/main.py scaffold
python -B scripts/main.py finalize
python -B scripts/selftest.py
```

`--input/--output/--work` 缺省为 `/input`、`/app/output`、`/app/work`。

## 核心设计

1. 评分维度（实际效果/使用增益/创新增益/可靠体验/生态贡献）逐条映射到流程步骤与
   可勾选自检项（A1–E2）。
2. 前 3 分钟由 `scaffold` 落盘合法 partial result.json，之后只做增量改进；`output_contract`
   字段原样回填顶层并保留未知键。
3. `finalize` 硬校验每条 finding 的 locator 是否存在于 segments、quote 是否逐字子串匹配；
   数值一致性三规则只产出 candidate 供人工确认。
4. 降级策略：空输入→`insufficient_evidence`，缺 case.json 且无材料→`input_error`，坏 JSON/
   坏编码/缺 pdftotext/扫描版→如实记录，绝不编造。
