# 实验参数优化建议 Skill —— 使用与方法说明

把一个"研究问题 + 设备/材料/安全/成本约束 + 科研材料"转成可执行、可复核的参数方案。
面向**有界参数优化**：参数有有限定义域、有量程/耐温/安全阈值、有可测量响应指标。

## 目录结构

```text
weekly-skill/
├── SKILL.md                      # 主入口：角色、输入/输出契约、执行流程、自检清单、降级策略
├── README.md                     # 本文件：人类可读的使用与方法说明
├── scripts/
│   ├── main.py                   # 单一编排器（init/finalize/doe/validate/check/selftest）
│   └── selftest.py               # 空输入/坏 JSON/正常输入三场景自检
└── references/
    └── output_contract.md        # result.json/plan.json 字段定义 + 推理规则 + 完成标准
```

## 调用方式

平台把 Skill 安装到 `/skills/weekly-skill` 后，Agent 按 `SKILL.md` 第 4 节执行：
`init`（先落盘 partial 骨架）→ 读材料与填 `plan.json` → `validate` → `doe` → 补齐成本/风险 →
`finalize` → `check`，全部增量改进、固定 seed、可复现。

## 输入 / 输出

- 输入：`/input/case.json`、`/input/task.md`、按 topic 提供的科研材料（PDF 用 `pdf-text-tool`）。
- 输出：`/app/output/result.json`（含 `case_id`、`status`、`plan`、`sources`、`uncertainty`、
  `pending_human`、`change_log` 等全部契约字段）+ 可编辑产物 `plan.md`、`plan.json`。

## 脚本用法（仅标准库，相对 Skill 根路径）

```bash
python scripts/main.py init --input /input --output /app/output --work /app/work
python scripts/main.py validate /app/work/plan.json --report /app/work/plan.validation_report.json
python scripts/main.py doe --work /app/work --seed 0
python scripts/main.py finalize --input /input --output /app/output --work /app/work
python scripts/main.py check /app/output/result.json --output-dir /app/output
python scripts/selftest.py   # 交付前自检，退出码 0=通过
```

退出码约定：`0`=通过；`1`=阻断错误；`2`=仅警告。

## 关键方法

- **可行域校验**：`suggested_value ∈ feasible_range ∈ hard_bounds`，越界即阻断错误并强制降级或转人工。
- **证据溯源**：每个关键结论带 `locator` + 逐字 `quote`；冲突来源两条都记，不隐藏。
- **确定性复现**：候选点由 `doe --seed 0` 生成；`change_log` 记录每步，可独立复核。
- **诚实降级**：缺数据标 `insufficient_evidence`/`limitations`，缺软件只在 artifacts 保留真实格式，不编造。

## 适用边界

不适用：无材料/约束的开放头脑风暴、纯文献综述、照抄既定 SOP。脚本不依赖第三方包，可离线运行。

## 维护信息

- version: 1.0.0（见 `SKILL.md` frontmatter）
- 依赖：Python 3 标准库；资源上限 50 文件 / 500KB 单文件 / 2MB 总量 / 无符号链接。
