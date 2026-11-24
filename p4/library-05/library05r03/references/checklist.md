# 本地冒烟协议与可勾选自检清单

提交/交付前在**不属于作者的环境**（不同机器或不同 Python 版本）按下面协议跑一遍。所有命令只用标准库，`python -B` 避免写出 `__pycache__`。

## 1. 冒烟步骤

```bash
# 1) 入口可运行
python -B scripts/main.py --help

# 2) 五命令主路径（--input/--work/--output 覆盖默认目录）
python -B scripts/main.py init      --input ./demo/input --work ./demo/work --output ./demo/output
python -B scripts/main.py show      --input ./demo/input --work ./demo/work --output ./demo/output
# 人工按 show 输出把语义裁定写进 ./demo/work/draft.json
python -B scripts/main.py check     --input ./demo/input --work ./demo/work --output ./demo/output
python -B scripts/main.py build     --input ./demo/input --work ./demo/work --output ./demo/output
python -B scripts/main.py selfcheck --input ./demo/input --work ./demo/work --output ./demo/output

# 3) 自动化自检（构造临时夹具，验证五命令 + 确定性 + 四降级场景）
python -B scripts/selftest.py
```

## 2. 兜底四场景（每个都要退出码 0 且产出合法 result.json）

| 场景 | 期望 |
|---|---|
| 空输入目录 | `status=insufficient_evidence` |
| 坏 JSON case.json | `status=input_error`，`errors` 记录原因 |
| 缺 case.json（有材料） | `status=input_error`，仍盘点材料 |
| 无 draft 直接 build | `status=partial`，`degradations` 说明 `no_draft` |

## 3. 产物校验

```python
import json
r = json.load(open('./demo/output/result.json', encoding='utf-8'))
assert r.get('case_id') and r.get('status')
assert all(k in r for k in ('sources', 'uncertainty', 'change_log'))
for name in ('library_index.json', 'evidence_map.json', 'inventory.md'):
    assert open('./demo/output/' + name, 'rb').read()  # 非空
```

## 4. 交付核对清单（对应评分维度）

### 实际效果
- [ ] `result.json` 可解析，`case_id` 与输入一致；文件/重复/版本/别名/冲突与输入一一对应。
- [ ] 每条 claim 的 `quote` 在材料中逐字命中；定位用 `rel_path + sha256`，不张冠李戴。
- [ ] 覆盖各类文件类型、元数据字段、目录层级、重复、版本差异、权限与来源。
- [ ] `evidence_map.json` 含 locator + quote；同输入两次运行除 `runtime` 外逐字节一致。

### 使用增益
- [ ] `init` 一步完成盘点，人工返工少；`check` 拦截编造与错源。

### 创新增益
- [ ] 版本/别名冲突不擅自合并，标 `source_conflict` 进 `pending_human`；显式行政备注可覆盖启发式分组。

### 可靠体验
- [ ] 五个子命令均退出码 0；空输入/坏 JSON/缺目录/无文本层/损坏/权限不足均降级不崩溃。

### 生态贡献
- [ ] `SKILL.md` 说明齐全；脚本纯标准库、相对路径、可独立复跑。
- [ ] `SKILL.md` frontmatter 含 `name/description/version`，`name` 为 kebab-case；正文 ≤180 行。
- [ ] `SKILL.md` 引用的每个相对路径（scripts/、references/）都存在。
- [ ] 无 `__pycache__`/`.pyc`/软链接/本机绝对路径；不交预制答案，示例输出须标注“示意，非正式案例结果”。
