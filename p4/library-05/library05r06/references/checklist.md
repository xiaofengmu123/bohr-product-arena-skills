# 本地冒烟协议与核对清单

提交/交付前在**不属于作者的环境**（不同机器或不同 Python 版本）跑一遍。全部只用标准库。

## 1. 冒烟步骤

```bash
python -B scripts/main.py --help
python -B scripts/selftest.py
```

`selftest.py` 会在临时目录构造最小案例，跑 `init → show → check → build → selfcheck` 全链路，
并覆盖空输入、坏 JSON、缺 case.json、无模型直接 build、重复运行确定性五种场景，退出码 0 为通过。

## 2. 五场景期望（对应可靠体验评分）

| 场景 | 期望 |
|---|---|
| 正常案例 | `status=ok/partial`，契约键逐键回填，产物齐全 |
| 空输入目录 | `status=insufficient_evidence` |
| 坏 JSON case.json | `status=input_error`，`errors` 记录 `invalid_case_json` |
| 缺 case.json（有材料） | `status=input_error`，仍盘点材料 |
| 无模型直接 build | `status=partial`，不冒充完整 |

## 3. 产物校验

```python
import json
r = json.load(open('<output>/result.json', encoding='utf-8'))
assert r.get('case_id') and r.get('status')
for k in ('sources', 'uncertainty', 'change_log', 'pending_human'):
    assert k in r
for name in ('library_index.json', 'evidence_map.json', 'inventory.md',
             'plan.json', 'model.json'):
    assert open('<output>/' + name, 'rb').read()
```

## 4. 交付核对清单（对应评分维度）

- [ ] SKILL.md frontmatter 含 name/description/version，name 为 kebab-case；正文 ≤180 行。
- [ ] SKILL.md 引用的每个相对路径（scripts/、references/）真实存在。
- [ ] 脚本只用标准库；`main.py --help` 退出码 0；无 `__pycache__`/`.pyc`/软链接。
- [ ] **实际效果·完成度**：文件/重复/版本/别名/冲突与输入一一对应，闭环走完盘点→建模→入库→检索。
- [ ] **实际效果·正确性**：每条 claim 的 quote 逐字命中；定位用 rel_path + sha256，不张冠李戴。
- [ ] **实际效果·完整性**：覆盖文件类型、元数据、目录层级、重复、版本、权限、来源、检索问题。
- [ ] **实际效果·可核验性**：plan/model/evidence_map 中间表示齐备；同输入两次运行除 runtime 外逐字节一致。
- [ ] **使用增益**：init 一步落盘盘点+骨架+计划；check 拦截编造与错源；返工少。
- [ ] **可靠体验**：子命令均退出码 0；空输入/坏 JSON/缺目录/无文本层/损坏/权限不足均降级不崩溃。
- [ ] **生态贡献**：说明齐全、依赖明确、schema 写清、可独立复跑；无本机绝对路径。
- [ ] 不交预制答案；示例输出须标注"示意，非正式案例结果"。

## 5. 维护信息

- 版本：见 SKILL.md frontmatter；依赖：仅 Python 标准库。
- 边界：只做文件库的确定性盘点与可复核交接，不做 PDF/OCR 语义抽取（外部工具职责），
  缺失时在 `degradations` 标 `no_tool:<name>` 或 `no_text_layer`，不假装有文本层。
