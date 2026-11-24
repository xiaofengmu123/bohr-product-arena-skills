# 自检清单与验收标准

## A. 契约验收（逐条勾选）

- [ ] `/app/output/result.json` 存在、UTF-8、合法 JSON，可反复增量覆盖。
- [ ] `case_id` 取自输入，原样保留。
- [ ] 题目 `output_contract` 的全部键已回填，未改名、未删未知键。
- [ ] `status` 诚实：`complete | partial | insufficient_evidence | input_error`。
- [ ] `sources`、`uncertainty`、`pending_human`、`change_log`、`runtime` 齐全。
- [ ] 可编辑产物（Markdown/JSON）在 `/app/output/`；Office/图类产物有 PDF/PNG 预览。

## B. 证据溯源验收

- [ ] 每条关键结论都有 `locator`（文件+章节+行号）。
- [ ] 每条结论都有逐字 `quote`（原文片段，省略中缀用 `…`）。
- [ ] 每个数值/术语/引用/图表的来源或修改记录可逐条回溯，不新增材料外事实。
- [ ] 冲突指标两条都记录，标 `source_conflict`；缺失标 `not_reported`。
- [ ] 六维 diagnosis 的每个判断都能指回对应 claim_id。

## C. 结构诊断验收（对应题目四个问题）

- [ ] 诊得准：问题定位到具体章节/段落/句子，并给出六维类别。
- [ ] 改得动：给出可执行编辑动作（移动/合并/拆分/收窄/补限制），含 original/proposed。
- [ ] 链得通：研究问题—方法—结果—结论闭环，论断有论据，跨章节呼应成立。
- [ ] 说得清：verdict 让作者能判定采纳/部分采纳/驳回。
- [ ] 不虚构不存在缺陷、不漏真实断裂、不把风格偏好当结构错误。

## D. 健壮性验收（脚本实测）

```bash
cd /skills/weekly-skill
python -B scripts/bootstrap.py --help
python -B scripts/evidence.py --help
python -B scripts/verify.py --help
python -B scripts/main.py --help
python -B scripts/selftest.py   # 输出 {"ok": true, ...}
```

- [ ] 空输入目录：退出码 0，写 `insufficient_evidence`，不抛异常。
- [ ] 坏 JSON / 坏编码：捕获并记录到 warnings/errors，继续处理其余材料。
- [ ] 缺参数：`--case/--input/--work/--out` 缺省指向 `/input`、`/app/work`、`/app/output`。
- [ ] 写盘失败：降级到最小合法产物，不让进程崩溃。
- [ ] 重复运行：同一输入两次运行结果一致（无随机状态）。
- [ ] 只依赖 Python 标准库，路径用相对路径 `scripts/xxx.py`。

## E. 提交前核对

- [ ] 文件数 ≤ 14、总体积 ≤ 1.2MB、`main.py < 30KB`。
- [ ] 未预写正式测试答案；示例输出已标注为示例。
- [ ] 未伪造评测分数、软件状态或科学数据。
