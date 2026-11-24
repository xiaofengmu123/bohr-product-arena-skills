# 最小调用示例（示例，非正式案例结果）

> 本文件全部输入与输出均为**示意示例**，用于说明接口与完整使用流程，不代表任何正式案例结果，也不冒充正式评测产物。

## 1. 输入布局（示意）

```text
/input/
├── case.json
├── task.md
└── materials/
    └── paper.md
```

`/input/case.json`（示意）：

```json
{
  "case_id": "demo-case-001",
  "output_contract": {
    "title": "string",
    "abstract": "string",
    "manuscript_tex": "path"
  },
  "task": "依据材料生成论文摘要与 .tex 初稿"
}
```

`/input/materials/paper.md`（示意）：

```text
The membrane has a mean pore size of 12.6 nm and a water flux of 38 L m-2 h-1.
```

## 2. 调用流程（示意）

```bash
# P0 引导：落盘契约骨架（全部契约字段=null）
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work

# 发现材料并索引取证
python3 scripts/evidence.py index --root /input --out /app/work/source_index.json
python3 scripts/evidence.py match --file /input/materials/paper.md --pattern 'pore'

# 逐字取证（用于 sources 的 quote）
python3 scripts/evidence.py quote --file /input/materials/paper.md --start 1 --end 1

# P1 回填契约字段（自动追加 change_log，state 转 filled）
python3 scripts/main.py contract-set --key title --value '"A membrane study"' \
    --source 'materials/paper.md#L1' --output /app/output --work /app/work
python3 scripts/main.py contract-set --key abstract --value '"Abstract text."' \
    --output /app/output --work /app/work
python3 scripts/main.py contract-set --key manuscript_tex --value '"main.tex"' \
    --output /app/output --work /app/work

# P3/P4 查看填表状态、收尾、自检
python3 scripts/main.py status    --input /input --output /app/output --work /app/work
python3 scripts/main.py finalize  --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck --input /input --output /app/output --work /app/work
```

## 3. 示意输出（示例）

`status` 输出（示意）：

```json
{
  "case_id": "demo-case-001",
  "status": "partial",
  "contract_keys": [
    {"key": "title", "state": "filled", "has_value": true},
    {"key": "abstract", "state": "filled", "has_value": true},
    {"key": "manuscript_tex", "state": "filled", "has_value": true}
  ],
  "change_log_count": 4
}
```

`selfcheck` 输出（示意）：

```json
{"ok": true, "case_id": "demo-case-001", "status": "partial", "problems": []}
```

## 4. 提交前自检（示例）

```bash
python3 scripts/selftest.py   # 空输入/坏 JSON/完整契约 三场景，全部通过退出码 0
```
