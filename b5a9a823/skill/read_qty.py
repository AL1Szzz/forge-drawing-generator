# -*- coding: utf-8 -*-
"""
从"制造大纲/料单"扫描件提取 图号+炉号 → 数量(件数), 供锻件图标题栏"数量"格使用。

为什么走制造大纲而不是 SAP 料单:
  - SAP 料单(网络盘 Excel)的"数量"是**棒材根数**(1~2), 与图纸件数不是一个口径。
  - 制造大纲(<批次目录>\批X-XXXX-XX XX单位大连 *.pdf)的"数量"列是**件数**
    (实测 F24B1369/300010 = 3, 与图纸件号 003-(001~003) 一致)。用户 2026-11-02 指定用这个。

做法: 逐页 RapidOCR → 找表头"数量"列(取"批次"列左侧最近的那个"数量") →
      按 y 分行, 行内取 质保号(F24Bxxxx) / 批次(6位炉号) / 数量(该列最近整数)。

用法: python read_qty.py <批次目录> [--pdf <料单PDF>] [--out 数量表.csv]
"""
import os, re, sys, csv, glob

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pymupdf as fitz
from pdfrender import save_page
from ocr2 import ocr_blocks
from config import rx, pat

RE_DH = rx("re_draw_qty")
RE_LU = rx("re_lu_qty")
RE_INT = re.compile(r"^\d{1,4}$")


def find_outline(batch_dir):
    """批次目录里的制造大纲 PDF(排除产品图纸本身)"""
    # 排除本工具自己产出的 PDF(汇总/锻件图/旧稿), 否则会把汇总当成制造大纲
    SKIP = ("汇总", "锻件尺寸图", "锻件图", "_锻件图", "旧稿", "封面")   # 项目里常留 锻件图.pdf/_backup, 也是自家产物
    for f in sorted(glob.glob(os.path.join(batch_dir, "*.pdf"))):
        b = os.path.basename(f)
        if b.startswith("_") or any(k in b for k in SKIP):
            continue
        if "~" in b and re.search(pat("re_pdf_name"), b):
            continue
        if re.search(pat("re_pdf_name"), b):             # 产品图纸(图号范围命名)
            continue
        return f
    return None


def ocr_page(pdf, i, dpi=200, tmpdir=None, pdf_path=None):
    p = os.path.join(tmpdir or ".", "_qty_p%02d.png" % i)
    if pdf_path:
        save_page(pdf_path, i, dpi, p)     # MuPDF 白图时自动换 PDFium
    else:
        pdf[i].get_pixmap(dpi=dpi).save(p)
    return p, ocr_blocks(p, min_conf=0.5)


def parse_page(blocks):
    """返回 [(图号, 炉号, 数量)]"""
    # 1) 表头: 定位"批次"列 与 其左侧最近的"数量"列
    hdr = [b for b in blocks if b[3] < 430]          # 表头在页面上部
    lu_x = None
    for b in hdr:
        if "批次" in b[0]:
            lu_x = (b[2] + b[4]) / 2.0
            break
    qty_x = None
    cands = [((b[2] + b[4]) / 2.0, b[0]) for b in hdr if b[0].strip() == "数量"]
    if lu_x is not None:
        cands = [c for c in cands if c[0] < lu_x]
    if cands:
        qty_x = max(cands, key=lambda c: c[0])[0]     # 取紧邻批次左侧的列
    if qty_x is None:
        return []
    # 2) 按 y 分行
    rows = {}
    for t, c, x, y, x1, y1 in blocks:
        key = int(y // 22)
        rows.setdefault(key, []).append((x, (x + x1) / 2.0, t.strip(), c))
    out = []
    for key in sorted(rows):
        items = rows[key]
        txt = " ".join(t for _, _, t, _ in items)
        dh = RE_DH.search(txt)
        if not dh:
            continue
        lu = None
        lus = [RE_LU.search(t) for _, _, t, _ in items]
        lus = [m.group(1) for m in lus if m]
        if lus:
            lu = lus[0]
        qty = ""
        best, bd = None, 1e9
        for x, xc, t, _ in items:
            if not RE_INT.match(t):
                continue
            d = abs(xc - qty_x)
            if d < bd:
                best, bd = t, d
        if best is not None and bd <= 70:
            qty = str(int(best))
        out.append((dh.group(0), lu or "", qty))
    return out


def run(batch_dir, pdf_path=None, out_csv=None):
    pdf_path = pdf_path or find_outline(batch_dir)
    if not pdf_path or not os.path.exists(pdf_path):
        print("未找到制造大纲 PDF:", batch_dir)
        return {}
    out_csv = out_csv or os.path.join(batch_dir, "数量表.csv")
    tmp = os.path.join(batch_dir, "_qty_tmp")
    os.makedirs(tmp, exist_ok=True)
    pdf = fitz.open(pdf_path)
    print("制造大纲:", os.path.basename(pdf_path), pdf.page_count, "页")
    rows = []
    for i in range(pdf.page_count):
        img, bl = ocr_page(pdf, i, tmpdir=tmp, pdf_path=pdf_path)
        r = parse_page(bl)
        print("  p%-2d 块%3d 行%2d" % (i + 1, len(bl), len(r)))
        rows.extend(r)
        try:
            os.remove(img)
        except OSError:
            pass
    d = {}
    for dh, lu, q in rows:
        if q and (dh, lu) not in d:
            d[(dh, lu)] = q
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["图号", "炉号", "数量"])
        for (dh, lu), q in sorted(d.items()):
            w.writerow([dh, lu, q])
    print("提取 %d 行 -> %s" % (len(d), out_csv))
    return d


if __name__ == "__main__":
    bd = sys.argv[1]
    pdf = None
    for a in sys.argv[2:]:
        if a.endswith(".pdf"):
            pdf = a
    run(bd, pdf_path=pdf)
