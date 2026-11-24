# 本地冒烟协议与核对清单

提交/交付前，在一个**不属于作者的环境**（不同机器或不同 Python 版本）按下面协议跑一遍。
脚本只用标准库；用 `python -B` 避免写出 `__pycache__`。

## 1. 冒烟步骤

```bash
# 1) 入口可运行
python -B scripts/main.py --help

# 2) 自检（构造四类兜底场景 + 完整场景 + 确定性）
python -B scripts/selftest.py

# 3) 主路径（最小案例）
python -B scripts/main.py init      --input ./demo/input --work ./demo/work --output ./demo/output
python -B scripts/main.py show      --input ./demo/input --work ./demo/work --output ./demo/output
# Agent 阅读 show 输出后写 ./demo/work/draft.json
python -B scripts/main.py check     --input ./demo/input --work ./demo/work --output ./demo/output
python -B scripts/main.py build     --input ./demo/input --work ./demo/work --output ./demo/output
python -B scripts/main.py selfcheck --input ./demo/input --work ./demo/work --output ./demo/output
```

## 2. 兜底四场景（每个退出码 0 且 result.json 合法）

| 场景 | 期望 |
|---|---|
| 空输入目录 | `status=insufficient_evidence` |
| 坏 JSON case.json | `status=input_error`，`errors` 记录原因 |
| 缺 case.json（有材料） | `status=input_error`，仍盘点材料 |
| 无 draft 直接 build | `status=partial`，`degradations` 说明 `no_draft` |

## 3. 产物校验（片段）

```python
import json
r = json.load(open('./demo/output/result.json', encoding='utf-8'))
assert r.get('case_id') and r.get('status')
assert all(k in r for k in ('sources', 'uncertainty', 'change_log'))
for name in ('library_index.json', 'evidence_map.json', 'inventory.md'):
    assert open('./demo/output/' + name, 'rb').read()
```

## 4. 评分维度 → 流程/自检项映射

| 评分维度 | 流程保障 | 自检项 |
|---|---|---|
| 实际效果·完成度 | `init` 一步盘点 + `build` 无条件收口，契约字段全回填 | 每个文件/重复/版本/别名/冲突与输入一一对应 |
| 实际效果·正确性 | `check` 对每条 quote 做逐字命中校验；定位用 `rel_path + sha256` | claim 的 quote 在材料中逐字命中 |
| 实际效果·完整性 | 覆盖文件类型/元数据/目录层级/重复/版本/权限/来源 | `show` 清单覆盖全部类别 |
| 实际效果·可核验性 | `evidence_map.json` 含 locator + quote；同输入两次运行除 `runtime` 外一致 | `selftest.py` 场景 F 断言 library_index.json 逐字节一致 |
| 使用增益·质量/效率 | 确定性盘点 + 倒排检索，减少人工返工 | `show`/`check` 一步完成，`check` 拦截编造 |
| 使用增益·可验证 | 同输入对照、哈希复算、机械校验 | `selftest.py` 全场景可复跑 |
| 创新增益 | 归一化 stem 版本分组 + office zip 文本层 + CJK bigram 索引 + 冲突分栏 | `contract.md` §4 证据规范，`library_index.json` 保留关系 |
| 可靠体验·易用/稳定 | 五子命令退出码恒 0，缺省路径直跑 | `--help` 退出码 0；四场景降级不崩溃 |
| 可靠体验·异常处理 | 加密/损坏/无文本层/OCR 低/旧版本/重复/权限不足逐条记录 | `errors`/`degradations` 全量自洽 |
| 生态贡献·文档/复用 | `SKILL.md` + `references/` 契约与清单，纯标准库相对路径 | `SKILL.md` 引用的每个相对路径存在 |

## 5. 交付核对清单

- [ ] `SKILL.md` frontmatter 含 `name/description/version`，`name` 为 kebab-case；正文 ≤180 行。
- [ ] `SKILL.md` 引用的每个相对路径（scripts/、references/）都存在。
- [ ] 脚本只用标准库；`scripts/main.py` 可 `--help` 且退出码 0；无 `__pycache__`/`.pyc`/软链接。
- [ ] 实际效果：盘点、去重、版本、别名、冲突与输入一一对应；claim 的 quote 逐字命中。
- [ ] 可核验：同输入两次运行除 `runtime` 外逐字节一致。
- [ ] 可靠体验：空输入/坏 JSON/缺目录/不可读/超大文件都降级，退出码恒 0。
- [ ] 生态贡献：依赖、输入目录约定、输出 schema、示例、边界与维护信息齐全，无本机绝对路径。
- [ ] 不交预制答案；示例输出须标注"示意，非正式案例结果"。
