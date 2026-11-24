# 本地冒烟协议与核对清单

提交/交付前在**不属于作者的环境**（不同机器或不同 Python 版本）跑一遍。所有脚本只用标准库，
`python -B` 避免写出 `__pycache__`。

## 1. 冒烟步骤

```bash
# 1) 入口可运行
python -B scripts/main.py --help

# 2) 一键自检（6 个场景，30 项断言）
python -B scripts/selftest.py

# 3) 主路径（用临时/最小输入目录）
python -B scripts/main.py init      --input <in> --work <work> --output <out>
python -B scripts/main.py show      --input <in> --work <work> --output <out>
# 人工按 show 输出写 <work>/draft.json
python -B scripts/main.py check     --input <in> --work <work> --output <out>
python -B scripts/main.py build     --input <in> --work <work> --output <out>
python -B scripts/main.py selfcheck --input <in> --work <work> --output <out>
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
r = json.load(open('<out>/result.json', encoding='utf-8'))
assert r.get('case_id') and r.get('status')
assert all(k in r for k in ('sources', 'uncertainty', 'change_log'))
for name in ('library_index.json', 'evidence_map.json', 'inventory.md'):
    assert open('<out>/' + name, 'rb').read()  # 非空
```

## 4. 交付核对清单（对应评分维度）

### 实际效果（40 分）
- [ ] `result.json` 可解析，`case_id` 与输入一致，`output_contract` 全部键回填。
- [ ] 文件/重复/版本/别名/冲突与输入一一对应；claim 的 `quote` 逐字命中（`check` 验证）。
- [ ] 覆盖文件类型、元数据、目录层级、重复、版本差异、权限与来源。
- [ ] 同输入两次运行除 `runtime` 外逐字节一致。

### 使用增益（25 分）
- [ ] `init` 一步完成盘点，`show` 输出带 locator，人工返工少。
- [ ] `check` 机械拦截编造与错源，`build` 无条件收口。

### 创新增益（15 分）
- [ ] 超大文件分块流式哈希（不因体积跳过）；多编码 BOM→utf-8→gb18030→latin-1 容错。
- [ ] 行政版本备注可覆盖启发式冲突；manifest 交叉核对自动登记缺失文件。

### 可靠体验（10 分）
- [ ] 五个子命令 + 未知参数均退出码 0；`selftest.py` 全绿。
- [ ] 空输入/坏 JSON/缺目录/无文本层/损坏/超大文件均降级不崩溃。

### 生态贡献（10 分）
- [ ] `SKILL.md` frontmatter 含 name/description/version，正文 ≤180 行。
- [ ] 脚本纯标准库、相对路径、无软链接、无 `__pycache__`/`.pyc`；引用路径全部存在。
- [ ] 示例输出标注"示意，非正式案例结果"，不交预制答案。

## 5. 不交预制答案

示例输出只用于说明接口；正式案例由平台提供，本包不预写任何正式测试产物或答案。
