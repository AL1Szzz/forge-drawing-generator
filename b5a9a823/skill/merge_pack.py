# -*- coding: utf-8 -*-
"""把一批锻件尺寸图 PDF 合并成单个 PDF, 并在首页插入"待复核总表"。"""
import os, sys, glob, csv
import pymupdf as fitz

CN = r"C:/Windows/Fonts/simsun.ttc"


def merge(pdf_dir, out_path, cover_csv=None, title="锻件尺寸图 汇总"):
    def _keep(f):
        b = os.path.basename(f)
        # 只收本次生成的单张图: 排除汇总本身、旧稿归档(以 _ 开头)、任何含"汇总"的备份
        return (b != os.path.basename(out_path)
                and not b.startswith("_")
                and "汇总" not in b)
    files = sorted(f for f in glob.glob(os.path.join(pdf_dir, "*.pdf")) if _keep(f))
    if not files:
        print("没有可合并的 PDF:", pdf_dir); return None
    out = fitz.open()
    # 封面: 复核表
    if cover_csv and os.path.exists(cover_csv):
        rows = list(csv.DictReader(open(cover_csv, encoding="utf-8-sig")))
        pg = out.new_page(width=595, height=842)
        pg.insert_text(fitz.Point(40, 50), title, fontsize=20, fontfile=CN, fontname="cn")
        cols = [c for c in rows[0].keys()]
        y = 80
        pg.insert_text(fitz.Point(40, y), "  ".join(cols), fontsize=8, fontfile=CN, fontname="cn")
        y += 14
        for r in rows:
            if y > 810:
                pg = out.new_page(width=595, height=842); y = 60
            pg.insert_text(fitz.Point(40, y),
                           "  ".join(str(r.get(c, ""))[:14] for c in cols),
                           fontsize=7.5, fontfile=CN, fontname="cn")
            y += 11
        print("  封面 %d 行" % len(rows))
    for f in files:
        d = fitz.open(f)
        out.insert_pdf(d)
        d.close()
    out.save(out_path, garbage=4, deflate=True)
    n = out.page_count
    out.close()
    print("合并 %d 份 -> %s (%d 页)" % (len(files), out_path, n))
    return out_path


if __name__ == "__main__":
    d = sys.argv[1]
    # 传批次根目录时自动下钻到 _锻件图(防止把根目录的产品图纸 PDF 误合并, 2026-11-03 实测踩坑)
    fig = os.path.join(d, "_锻件图")
    if os.path.isdir(fig):
        d = fig
    merge(d, os.path.join(d, "锻件尺寸图_汇总.pdf"),
          cover_csv=os.path.join(d, "生成清单.csv"))
