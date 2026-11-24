# 改稿自检与验收口径

提交前逐条核对；每条对应 `result.json` 的一个可核验字段。任何一条不满足，改稿不得标 `status=ok`。

## 1. 完成度（对应 15 分）

- [ ] 产出的是完整改写稿，不是提纲、建议或片段润色。
- [ ] 满足 case 指定的体裁、受众、格式与篇幅上限。
- [ ] 主产物是可编辑文件（md/docx/tex），路径写入 `deliverables`。

## 2. 正确性（对应 15 分）

- [ ] 每个数值/单位/百分比与源文逐字一致，无四舍五入、无口径改变。
- [ ] 每个引用、DOI、图表编号与源文一致，无补造。
- [ ] 无源文未支持的新结论；推断类表述标为推断。
- [ ] 论断强度与源文匹配，相关未写成因果，初步未写成定论。

## 3. 完整性（对应 5 分）

- [ ] 关键结论、数据、样本量、统计口径、方法要点、图表引用、限定条件全部保留。
- [ ] 删掉的是冗余，不是证据；每条删除在 `change_log` 有理由。

## 4. 可核验性（对应 5 分）

- [ ] `evidence_map` 每项含 `locator` + 逐字 `quote`，可回溯到源文件。
- [ ] `change_log` 每项含 `id/paragraph/action/before/after/reason`。
- [ ] `pending_human` 列出全部需人工确认项。

## 5. 增益与稳定（对应使用增益 / 可靠体验）

- [ ] 术语表锁定，同一概念全文同一说法。
- [ ] 同一输入重复运行结果一致（无随机性）。
- [ ] 长文分段处理不丢上下文；多文体切换不漂移事实。
- [ ] 空输入、坏 JSON、缺工具三场景均能返回诚实 status 而不崩溃。

## 6. 诚实标注（对应异常处理）

- [ ] 材料不足 → `insufficient_evidence`；缺工具 → `partial` + `degradations`。
- [ ] 数据冲突 → 两条都记并标 `source_conflict`。
- [ ] 源文没有的事实/引用 → `not_reported` / `pending_human`，绝不补造。

## 7. 运行自检命令（交付前在独立环境跑一遍）

```bash
python3 scripts/main.py --help                              # 退出码 0
python3 scripts/main.py --input ./demo/input --work ./demo/work --output ./demo/output
python3 scripts/extract.py --input ./demo/input --work ./demo/work
python3 scripts/validate.py --output ./demo/output          # 骨架阶段预期 exit 0，仅警告
python3 - <<'PY'
import json
r = json.load(open('./demo/output/result.json', encoding='utf-8'))
assert 'case_id' in r and 'status' in r
assert r['status'] in {'ok','partial','insufficient_evidence','input_error'}
print('ok', r['status'])
PY
```

空目录、坏 JSON、缺输入各跑一次；产物主文件存在、`result.json` 可解析、SVG 可解析 / PDF 以 `%PDF-` 开头 / Office 可打开。
