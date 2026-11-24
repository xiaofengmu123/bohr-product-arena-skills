# 本地冒烟协议与核对清单

提交/交付前在**不属于作者的环境**（不同机器或不同 Python 版本）按下面协议跑一遍。所有命令只用标准库，`python -B` 避免写出 `__pycache__`。

## 1. 冒烟步骤

```bash
# 1) 入口可运行
python -B scripts/main.py --help

# 2) 全路径自检（临时目录内生成最小案例并跑通 init/show/check/build/selfcheck）
python -B scripts/selftest.py

# 3) 主路径（用最小案例）
python -B scripts/main.py init      --input ./demo/input --work ./demo/work --output ./demo/output
python -B scripts/main.py show      --input ./demo/input --work ./demo/work --output ./demo/output
# 人工按 show 输出写 ./demo/work/draft.json
python -B scripts/main.py check     --input ./demo/input --work ./demo/work --output ./demo/output
python -B scripts/main.py build     --input ./demo/input --work ./demo/work --output ./demo/output
python -B scripts/main.py selfcheck --input ./demo/input --work ./demo/work --output ./demo/output
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
assert all(k in r for k in ('sources', 'uncertainty', 'change_log', 'result', 'claims'))
for name in ('library_index.json', 'evidence_map.json', 'inventory.md'):
    assert open('./demo/output/' + name, 'rb').read()  # 非空
```

## 4. 证据溯源强化核对（本版重点）

- [ ] 每条 claim 都有 `evidence`，且每条 evidence 含 `source_id` + `locator` + `quote`。
- [ ] 文本材料的 quote 与材料逐字一致；`check` 命令不报 "quote 未逐字命中"。
- [ ] `evidence_map.json` 顶层 key 与 `result.json.claims[].claim_id`、`query:*` 一一对应。
- [ ] 二进制/扫描件证据显式标 `quote_verbatim=false` + `quote_note`，不假装有文本层。

## 5. 交付核对清单（对应评分维度）

- [ ] `SKILL.md` frontmatter 含 `name/description/version`，`name` 为 kebab-case；正文 ≤180 行。
- [ ] `SKILL.md` 引用的每个相对路径（scripts/、references/）都存在。
- [ ] 脚本只用标准库；`scripts/main.py` 可 `--help` 且退出码 0；无 `__pycache__`/`.pyc`/软链接。
- [ ] 实际效果：盘点、去重、版本、别名、冲突与输入一一对应；claim 的 quote 逐字命中。
- [ ] 可核验：同输入两次运行除 `runtime` 外逐字节一致。
- [ ] 可靠体验：空输入/坏 JSON/缺目录/不可读/超大文件都降级，退出码恒 0。
- [ ] 生态贡献：依赖、输入目录约定、输出 schema、示例、边界与维护信息齐全，无本机绝对路径。
- [ ] 不交预制答案；示例输出须标注"示意，非正式案例结果"。
