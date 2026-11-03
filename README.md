# 锻件尺寸图批量生成工具

给一批「产品图纸 PDF + 制造大纲 + 热处理工艺卡」，自动产出**锻件尺寸图 PDF**（合并为单个文件）和**复核清单**。
跑批是纯本地脚本，**不调用大语言模型、不联网**，断网也能跑。

> 本仓库**只包含运行过程**（代码、配置、文档），**不含任何实际图纸、料单、热处理卡或生成的 PDF**。

## 目录结构

```
forge-drawing-generator/
├─ README.md
├─ unit_profile.example.json        单位配置示例(复制到批次目录里改名为 unit_profile.json)
├─ b5a9a823/skill/                  核心代码
│   ├─ SKILL.md                     完整业务规则与数据源地图(知识库)
│   ├─ config.py                    单位配置层
│   ├─ run_batch.py                 一键跑批入口
│   ├─ build_manifest.py            建清单(OCR + 人工核对)
│   ├─ read_exb.py / read_ht2.py   热处理尺寸(EXB 直读 / DXF 解析)
│   ├─ make_batch.py                尺寸判型、余量、逐件出图
│   ├─ forge_gen.py                 PDF 矢量绘图
│   ├─ merge_pack.py                合并汇总
│   └─ profiles/                    单位配置目录
├─ 工具/
│   ├─ 一键跑批/一键跑批.bat        ← 日常用这个
│   └─ EXB转图/                     辅助看卡(非必需)
└─ docs/新批次使用说明.md           面向操作者的完整使用手册
```

`工具\一键跑批\` 与 `b5a9a823\skill\` 的**相对位置不能改**（BAT 用 `%~dp0..\..` 推导代码路径）。

## 环境

- Windows + **Python 3.11**（默认 `C:\Program Files\Python311\python.exe`，换机器可设环境变量 `FORGE_PYTHON`）
- 依赖：`pip install pymupdf ezdxf rapidocr-onnxruntime olefile Pillow`
- 建议再装 `pypdfium2`（约 3MB，无模型）：个别扫描件的灰度 JPEG 会让 MuPDF 整页渲染成白图，
  装了会自动换 PDFium 重渲染；不装则少这层兜底，不影响正常批次
- 中文字体 `simsun.ttc` / `arial.ttf`（Windows 自带；路径可在 profile 里改）
- 可选：CAXA 电子图板（导出 DXF 时用；不导出也能跑，见下）
- 可选：Tesseract（仅 `ocr.py` 料单辅助脚本用；主流程用 RapidOCR）

## 快速开始

1. 在**任意位置**建一个批次文件夹，放入：
   - 产品图纸 PDF（必需，文件名用"图号起~止"格式，如 `F24B0001~0099.pdf`）
   - 制造大纲 PDF（必需，看红笔批注与取样列）
   - `热处理工艺\*.exb`（可选，热处理卡原文件）或 `DXF\*.dxf`（可选，CAXA 导出的 DXF）
2. 把该文件夹**拖到** `工具\一键跑批\一键跑批.bat` 上。
3. 跑完在批次目录得到 `<批次名>_锻件尺寸图_汇总.pdf` 和 `复核清单.md`。
4. **必须人工核对** `manifest.csv`（若为 OCR 自动生成）与复核清单里的每一项。

尺寸取值优先级：**图纸重量行直给 > 热处理尺寸+余量 > 成品尺寸+余量(待核稿)**。
详见 `b5a9a823/skill/SKILL.md` 与 `docs/新批次使用说明.md`。

## 换一家单位

**只换编号规则时不用改代码**：复制 `unit_profile.example.json` 到批次目录，改名 `unit_profile.json`，
按新单位改里面的正则（图号、图纸文件名范围、炉号、材质字典、字体）。字段说明见
`b5a9a823/skill/profiles/README.md`。不放该文件就用 `profiles/default.json`（默认口径）。

**版式/卡片格式不同则需要人工适配一次**：图框坐标与标题栏、CAXA 卡片格式、大纲表头列序、
以及余量档/判型/合锻/取样等**工艺约定（必须与客户确认，不要照搬）**。详见 profiles 的 README。

## 已知限制

- EXB 直读是针对 **CAXA 电子图板**文件格式逆向的；CAXA 无 COM/命令行接口，EXB→DXF 无法自动转换。
- EXB 直读结果一律标「待核稿」；导出了 DXF 才出正式稿。
- 图框白盒坐标、标题栏栏位是按现有图纸校准的，换版式要重新量。
- OCR 只出草稿值，**尺寸错一个数字就是报废件**，人工核对不可省。

## 验证状态（2026-11-03）

- EXB 直读 vs DXF：125 组对照，尺寸解析 **125/125 一致**
- 一键跑批 BAT：批A 7 张 / 8 页、批B 回归 74 张 / 76 页，退出码 0、0 出图失败
- 20 个 py 语法检查 0 失败，关键模块 import 0 失败

## 建议设为私有仓库

`SKILL.md` 含内部业务术语、客户名称与网络路径示例，代码注释中也有历史批次编号；
虽然不含任何实际图纸或数据，仍建议**私有仓库**，避免内部信息外流。
