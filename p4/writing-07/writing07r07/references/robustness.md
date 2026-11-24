# 鲁棒性与降级细则

本 Skill 的鲁棒性变体保证：**任何输入状态都先产出合法 partial 结果，再增量改进，绝不崩溃、不留空产物。**

## 1. 五类场景的确定性处置

| 场景 | 处置 | 退出码 |
|---|---|---|
| 空输入目录 | scan.py 返回空材料清单；emit_skeleton 写 `status=partial` 的合法 result.json | 0 |
| 坏 JSON（case.json） | scan.py 记录 `errors[].reason=bad_json`，继续读取其余材料；骨架 `status=input_error` | 0 |
| 坏编码 | 编码回退链 utf-8-sig → utf-8 → gbk；全部失败记 `degradations[].reason=undecodable` 并跳过内容（fail-loud，不落入 latin-1 乱码） | 0 |
| 缺参数 | 脚本缺省 `--input /input --work /app/work --output /app/output` | 0 |
| 超大文件（>2MB） | 跳过内容，记 `degradations[].reason=oversized`，其余材料继续 | 0 |
| 写盘失败 | 降级写最小合法 result.json（`status=input_error` + `errors`），进程不崩溃 | 0 |

> 编码链为何不加 latin-1：latin-1 能解码任意字节，看似“永不失败”，但会把 GBK 中文解码成乱码，随后可能被 Agent 当作事实来源。对“逐字 quote 绑定事实”的写作任务，静默乱码比明确标 `undecodable` 更危险，因此采用 fail-loud 策略：解不出就跳过并记录，绝不拿乱码当证据。

## 2. 原子写盘

所有 JSON 写盘统一走“临时文件 + `os.replace`”：

```python
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(obj, fh, ensure_ascii=False, indent=2)
os.replace(tmp, path)
```

`os.replace` 是原子操作：进程在任何时刻被杀，目标路径要么是旧的完整文件，要么是新的完整文件，不会出现半截 JSON。这是“前 3 分钟先落骨架、之后只做增量改进”能安全成立的底层保证。

## 3. 确定性（重复运行结果一致）

- 文件遍历结果按相对路径排序；manifest/result 的列表顺序稳定。
- 不写入墙钟时间戳到 manifest/result 内容；`result.runtime` 是最终写盘时的一次性测量，其余内容字段确定性一致。
- 无随机数；若写作流程需要随机成分（如改写建议），必须固定种子并在 `change_log` 记录。
- `selftest.py` 的 `determinism` 场景对同一输入跑两次 scan，断言两份 manifest 结构内容完全一致。

## 4. 自检方法

```bash
python3 -B scripts/selftest.py
```

六类场景：空输入、坏 JSON、坏编码、超大文件、缺参数默认值、确定性。全部 PASS 退出码 0，任一 FAIL 退出码 1。自检在临时目录内构造合成输入（合成输入仅用于自检，不冒充正式案例），跑完自动清理。

## 5. 诚实降级原则

- 不编造软件状态：缺 pdf-text-tool / ocr-tool / latex-compiler 时记 `degradations`，用现有文本继续，不声称“已解析 PDF”。
- 不编造科学数据：材料没有的数值写 `MATERIAL GAP` / `not_reported`，不用示例填充正文。
- 不伪造评测分数：Skill 运行不记录、不声称任何 benchmark 得分；平台统一记录运行轨迹与对照结果。
- partial 结果必须标识其局限：写入 `degradations`、`uncertainty`、`pending_human`，让下游能继续编辑。

## 6. 最小调用示例（示意，非正式案例）

```bash
# 本地演示（目录为示例，非正式案例）
python3 -B scripts/scan.py --input ./demo/input --work ./demo/work
python3 -B scripts/emit_skeleton.py --work ./demo/work --output ./demo/output
# Agent 在两次脚本之间完成正文起草并回填 result.json
python3 -B scripts/validate.py --work ./demo/work --output ./demo/output
```

示意输出（标明为示例，不冒充正式案例）：

```json
{"case_id": "demo-001", "status": "partial",
 "contract_fields": [{"name": "introduction", "shape": "scalar"},
                     {"name": "related_work", "shape": "scalar"}],
 "sources": [{"key": "S1", "locator": "materials/paper.md#p2",
              "quote": "示例：材料逐字片段（此处为演示，非真实数据）", "kind": "fact"}]}
```

