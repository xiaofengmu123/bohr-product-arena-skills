# 输出契约与中间表示（output_contract）

本 Skill 的产物分三层：**最终结果** `result.json`、**内容中间表示** `plan.json`、**图形中间表示** `figure_model.json`。三者都带 `schema` 版本标签，可被 `main.py verify` 确定性校验；这是本包相对"只把 page_plan 塞进 result.json"打法的关键取舍：内容模型与图形模型从最终结果里拆出来，独立落盘、独立校验、独立重跑。

## 1. result.json（/app/output/result.json）

必须包含 `case_id` 与题目 `output_contract` 的全部字段，另加运行状态与溯源字段。若 `case.json` 自带 `output_contract` 模式，以其为准并补齐下列五组。

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | string | 固定 `academic-slides/result@1` |
| `case_id` | string | 取自 case.json 的 `case_id`/`id`/`challenge_id`/`armiId`；缺失时回退输入目录名并记入 `uncertainty` |
| `status` | string | `ok \| partial \| insufficient_evidence \| input_error \| failed \| in_progress`；`in_progress` 仅允许作为骨架阶段的瞬态值，收口时必被 `finalize` 收敛 |
| `presentation` | object | 主产物：`pptx_path` / `pdf_path` / `png_paths` / `slide_count` / `page_plan[]` |
| `evidence_map` | array | 页级证据映射，每项 `{page, claim, refs[], evidence_status}` |
| `render_record` | object | 渲染与版本记录：`source_files` / `template_file` / `layouts_used` / `fonts` / `exports[]` / `integrity` / `media[]` |
| `intermediate` | object | 中间表示路径：`plan` / `figure_model` / `inventory`，指向 `/app/work/` 下的真实文件 |
| `sources` | array | 源文件清单，每项 `{path, sha256}` |
| `uncertainty` | array | 未满足项/未定位证据/无法自动验证的点，逐条 `{code, page?, detail}` |
| `pending_human` | array | 必须人工裁决的项（如字体替换是否可接受、裁切取舍） |
| `errors` / `degradations` | array | 出错与降级记录，带 `step` 与 `detail` |
| `runtime` | object | `started_at` / `finished_at` / `duration_sec` / `steps[]` |
| `change_log` | array | 追加式，每项 `{ts, event, detail}`；幂等合并，绝不覆盖 |

**`presentation.page_plan[]`** 每页：`{index, title, claim, role, layout_key, figure_ids[], notes, refs[]}`，是 `plan.json` 的页面快照。

**`evidence_map[]`** 每页：`{page, claim, refs[], evidence_status}`；`refs[]` 每项 `{locator, path, quote, status}`，`quote` 是提取文本中的**逐字片段**（`resolved` 时非空）。`evidence_status ∈ {complete, partial, missing}`。

**`render_record.integrity`**：`pptx_zip` / `slide_masters` / `slide_layouts` / `slides` / `notes` / `pdf_magic` 等由 `verify` 写入的检查结果，用于证明"母版/版式继承真实发生、导出件与 pptx 一致"。

## 2. plan.json（/app/work/plan.json，内容/论证模型）

把"讲什么"与"怎么摆"分开。页面只写主张、讲解顺序与证据指向，不写字号像素。

```json
{
  "schema": "academic-slides/plan@1",
  "case_id": "...",
  "narrative": {
    "research_question": null, "approach": null,
    "key_results": [], "limitations": []
  },
  "pages": [
    {
      "index": 1,
      "role": "cover|story|method|result|limit|conclusion|appendix",
      "title": "...",
      "claim": "一句话页级主张（可证伪、可定位）",
      "figure_ids": ["fig:1"],
      "refs": ["paper.txt#L12-L18"],
      "layout_key": "cover|title_content|two_column|figure_full|compare",
      "notes": "讲稿/备注",
      "status": "planned|drafted|rendered|verified"
    }
  ],
  "uncertainty": []
}
```

`narrative` 是论文主线四要素（研究问题/方法/结果/限制），页计划必须由它推导，保证"能围绕主线讲清楚"。

## 3. figure_model.json（/app/work/figure_model.json，图形/排版模型）

把"图是什么"与"图放哪、怎么缩放"分开；等比缩放策略可解释、可复核。

```json
{
  "schema": "academic-slides/figure-model@1",
  "case_id": "...",
  "figures": [
    {
      "id": "fig:1",
      "source": "materials/fig1.png",
      "kind": "raster|vector|unknown",
      "native_size": {"w": 1200, "h": 900, "unit": "px"},
      "aspect_ratio": 1.3333,
      "caption": "Figure 1: ...",
      "number": 1,
      "target_container": "content_8x6in",
      "scale_mode": "fit|fill_crop|none|pending",
      "refs": ["paper.txt#L120"],
      "evidence": {"locator": "paper.txt#L120", "quote": "..."},
      "status": "ok|missing|unreadable|unknown_dimensions"
    }
  ],
  "rules": { "scale": "...", "numbering": "..." }
}
```

**等比缩放规则（可解释，写死在 `rules.scale`）**：容器宽高 `(cw,ch)` 与原生宽高 `(nw,nh)` 已知时，`fit` 取 `scale=min(cw/nw, ch/nh)`（letterbox 留边）；`fill_crop` 取 `scale=max(cw/nw, ch/nh)` 并居中裁切、记录裁切框；**任何模式都不改变原生宽高比**；原生尺寸未知时 `scale_mode=pending`，禁止拉伸。图号按材料出现顺序编号，caption 与正文引用必须同源同号。

## 4. 证据定位语法（locator）

`path#L12-L18`（行区间）/ `path#L12`（单行）/ `path#P3`（段，按空行切分）/ `path#S2`（第 2 个 Markdown 标题）。`path` 相对 `/app/work`（提取文本缓存目录）；`resolve_locator` 找不到时先按 basename 回退搜索，仍找不到记为 `unresolved` 并进 `uncertainty`，**绝不伪造 quote**。

## 5. 模板准备与复用规范（供他人复跑）

**模板要求**：必须是带母版与版式（slideMaster/slideLayout）的真实 `.pptx`/`.potx`；优先使用主题字体与主题色，不整段粘贴到默认模板。若题目只给一份"起始 deck"要求在原稿上适配，先读 `ppt/presentation.xml` 与 `ppt/slideLayouts/*.xml` 确认可编辑结构，再按 `plan.json` 逐页改写。

**导出设置（写入 `render_record.exports`）**：office-renderer 导出 PDF 时记录 `{format, pages, dpi, fonts_embedded}`；导出 PNG 记录 `{page, width_px, height_px}`。字体替换、图片丢失、备注错位必须在 `uncertainty` 逐条注明，不静默吞掉。

**字体要求（写入 `render_record.fonts`）**：中英文搭配用主题字体；字体缺失时用 font-discovery 查可用字体并记录回退链 `{requested, fallback, reason}`，不得假装字体存在。字号层级阶梯（标题/正文/注释三级及以上）与主题色/强调色取值写入 `plan.json` 的 `layout_key` 对应备注，保证跨页一致。

**限制与维护信息**：本包脚本只用 Python 标准库，可在任意 Python 3.9+ 运行；`main.py` 的尺寸解析覆盖 PNG/JPEG/GIF/BMP/SVG，TIFF 标记 `unknown_dimensions` 并走 `pending`；`inspect_pptx` 只做 zip 与母版/版式/备注的静态检查，渲染一致性（pptx↔PDF）须由 office-renderer 的真实导出件配合人工核对，两者边界在 `verify` 报告中显式区分。

## 6. 状态语义与诚实约定

`status` 由 `finalize` 依据产物与证据自动收敛：有 pptx+PDF → `ok`；仅有骨架/部分证据或任一导出失败 → `partial`；无 `input_inventory.json`（输入为空）→ `insufficient_evidence`；case.json 无法解析 → `input_error`。脚本不写"运行分数"、不写"软件状态已安装"之类未经验证的声明；`pending_human` 只放真正需要人工裁决的项（字体替换观感、fill_crop 裁切取舍、主观版式判断），并给出可接受范围与不确定性。

