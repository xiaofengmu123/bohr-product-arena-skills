# 质量规则与常见错误清单

> 对应评分维度「实际效果」与「可靠体验」。每生成一版导图后，逐条自问。

## 1. 层级与粒度（理得清）

- 根节点唯一、主题明确；层级深度合理（一般 ≤4 层），不强制平铺。
- 同级节点粒度一致：同一层要么都是概念，要么都是方法，不混装"大概念"与"具体数值"。
- 父子是"属种/组成/展开"关系，不是任意关联；分类不交叉、不重复。
- 重要信息不埋没在装饰性词语下；装饰词（如"进一步""值得注意的是"）不入节点。
- 节点 `text` 精炼（建议 ≤24 字），完整原句放 `quote`，不放节点标题。

## 2. 关系方向（连得对）

- 每条关系有类型、有方向、有证据；没有依据不连线。
- 检查方向：`supports` 证据→结论；`causes` 因→果；`depends_on` 依赖方→被依赖方；
  `precedes` 先→后；`cites` 引用方→被引方。
- 跨分支的关系用 `relations[]`（arrowlink），同一父子链不重复写 `part_of`。
- 冲突信息不隐藏：两处材料不一致时，双方都记录并标 `source_conflict`，写入 `uncertainty`。

## 3. 证据挂接（可追溯）

- 每个关键节点（concept/question/method/evidence/conclusion/task/risk/gap）
  必须挂 `locator` + 逐字 `quote`。
- 数字、术语、引用、图表、关键论断逐条可回溯；`fact` 的 quote 必须能在原文命中。
- 概括与推断分别标 `summary` / `inference`，不伪装成事实。
- 缺证据的子节点宁可写 `pending_human`，也不补造 quote。

## 4. 布局与可编辑性（实际有用）

- `mindmap.mm` 是真正可编辑的 FreeMind 文件（XMind/FreeMind 可直接打开）。
- `mindmap.svg` 保留 `<text>` 文字、不转曲、节点 id 与 evidence.json 一一对应。
- 布局确定性：同一 map_spec 两次运行产物逐字节一致（固定顺序、固定槽位）。
- 节点规模爆炸时截断到 400 并记录；文本过长折行截断，不溢出画布。

## 5. 常见错误（提交前逐条排除）

- [ ] 只给截图/位图，没有可编辑 `.mm` 或文字版 `.svg`
- [ ] 节点无证据乱连线，或关系方向写反
- [ ] 层级平铺没有树结构，或根节点缺失
- [ ] SVG 与 evidence.json 的节点 id 对不上
- [ ] 把推断当事实、把装饰词当关键节点
- [ ] 编造文献、数据、DOI 或"未安装的软件状态"
- [ ] 材料不足时硬凑完整导图，而不是如实标 `insufficient_evidence`
- [ ] 坏 JSON/空输入直接崩溃，没有留下合法 result.json

## 6. 自检命令

```bash
python3 -B scripts/scaffold.py --input /input --output /app/output --work /app/work
# …Agent 写 /app/work/map_spec.json…
python3 -B scripts/build_map.py --spec /app/work/map_spec.json --output /app/output --work /app/work
python3 -B scripts/validate.py --output /app/output --work /app/work
```

`validate.py` 输出 PASS/FAIL/INFO；FAIL 必须修复，INFO 是覆盖度提示（目标：关键节点证据覆盖率 100%）。
