# 模板规格反推与保真度核验清单

本文件把“学术格式与模板适配”拆成四类可逐项勾选的动作，与 `scripts/core_impl.py` 的确定性输出一一对应。目标模板与补充规定中的每一条要求，都要落到一个“规格项 + 取值 + 出处（模板文件路径/样式名/页码）”，禁止凭经验猜测。

## A. 认得准：从模板反推规格（对应 core_impl 的 spec）

docx（`extract_docx_spec`，读 styles.xml/sectPr/numbering.xml）：

- [ ] 页面尺寸与页边距：`w:pgSz`（w/h，单位 twip，1cm≈567twip）与 `w:pgMar`（top/bottom/left/right/header/footer）。
- [ ] 正文默认字体字号：`w:docDefaults` 的 `w:rFonts`（ascii/hAnsi/eastAsia 三者分别记）与 `w:sz`（半磅，`sz/2=pt`）。
- [ ] 标题层级样式：`w:style` 中 name 含 `heading`/`标题`/`標題` 的样式，逐级记字体/字号/加粗/行距/缩进。
- [ ] 图表题注样式：name 含 `caption`/`题注` 的样式（字号、位置——表注在上、图注在下）。
- [ ] 行距：`w:spacing w:line` + `w:lineRule`——`auto` 时 `line/240` 为倍数，`exact/atLeast` 时 `line/20` 为磅。
- [ ] 多级编号：numbering.xml 的 `w:lvl/@w:ilvl` 与 `w:numFmt`（decimal/upperRoman/lowerLetter…）。

tex（`extract_tex_spec`，正则读 preamble，不执行 latex）：

- [ ] `\documentclass[..]{..}` 的字号选项与单双栏；`\geometry{..}` 的页边距。
- [ ] `\setmainfont{..}` 与 `fontspec` 的字体；`\bibliographystyle{..}` 的参考文献样式。
- [ ] 包清单（caption/booktabs/titlesec/hyperref 等决定题注、表格线、标题、交叉引用行为）。

## B. 排得对：样式绑定与结构对齐

- [ ] 标题层级按模板样式绑定，而不是只改字号/加粗导致目录错位。
- [ ] 章节编号规则（如“1 / 1.1 / 1.1.1”）逐级一致；交叉引用用“图1/表2/式(3)”模板口径，不用硬编码数字。
- [ ] 图表题注位置与字号符合模板：表注在表上方、图注在图下方。
- [ ] 参考文献条目按模板字段顺序（作者截断、标点、中英文混排、DOI/页码），不照搬原稿样式。
- [ ] 页眉页脚（页码位置、字体）按模板，页码与目录可刷新。

## C. 转得全：内容单元保真（对应 core_impl 的 inventory）

原稿内容单元盘点后，转换完成后逐一比对，任何单元不得被删除、合并、改写或图片化：

- [ ] 标题（heading_styles/`\section`）、段落（`w:p`）、表格（`w:tbl`/`table`）、图（`w:drawing`/`includegraphics`）、公式（`equation`/`align`）、脚注（`footnotes.xml`/`\footnote`）、引用（`\cite`/`\bibitem`）、目录域（`TOC`/`\tableofcontents`）。
- [ ] 域代码（修订、批注、题注自动编号、目录）在转换后仍为可编辑结构，未变成静态文本。
- [ ] 转换前后单元数记录进 `version_boundary`，减少的必须逐条说明原因（属人工确认项）。

## D. 交得出：导出核验

- [ ] 可编辑源文件（docx/tex/md）与渲染预览（pdf/png）均产出并回填 `deliverable.artifact_paths`。
- [ ] 字体已嵌入或用平台提供的 Liberation Serif 回退；缺失字体标 `font_missing`，不静默替换。
- [ ] 页面尺寸/页边距实测与模板一致（可打开导出件用平台工具实测，非仅声明）。
- [ ] 目录与页码可刷新、交叉引用可跳转。
- [ ] 他人能按 `fact_source_map` 的 locator 回查每一处改动。

冲突规则：同一规格项在模板与补充规定不一致、或模板版本不明时，两条都记，标 `source_conflict`，正文用“存在冲突/需人工确认”，不偏单边。
