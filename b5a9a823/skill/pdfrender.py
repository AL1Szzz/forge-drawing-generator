# -*- coding: utf-8 -*-
"""PDF 页面渲染: PyMuPDF 优先, 渲染结果为全白时自动换 PDFium(Chrome 同款引擎)。

背景(2026-11-03 实测): 个别扫描件里的灰度 JPEG 会让 MuPDF 整页渲染成纯白,
OCR 于是拿到空图 → 整页字段全空, 而且不报错。同一页换 PDFium 渲染正常。
仅需 pypdfium2(约 3MB, 无模型), 未安装时自动退回原行为。
"""
import os


def _all_white(pix):
    try:
        return pix.samples.count(255) == len(pix.samples)
    except Exception:
        return False


def save_page(pdf_path, idx, dpi, out, frac=None, rot=None):
    """把第 idx 页渲染到 out。

    frac: (x0, y0, x1, y1) 页面比例区域, 缺省整页
    rot : 绝对旋转角(0/90/180/270); None = 沿用 PDF 自带旋转(推荐)
          注意: PDFium 的 set_rotation 是绝对值, 传 0 会覆盖页面自带旋转, 导致横版图变竖版
    返回 True 表示 out 已可用。
    """
    try:
        import fitz
        doc = fitz.open(pdf_path)
        try:
            p = doc[idx]
            old = p.rotation
            if rot is not None and rot % 360 != old % 360:
                p.set_rotation(rot % 360)
            try:
                R = p.rect
                clip = None if not frac else fitz.Rect(
                    R.width * frac[0], R.height * frac[1],
                    R.width * frac[2], R.height * frac[3])
                pix = p.get_pixmap(dpi=dpi, clip=clip)
                if not _all_white(pix):
                    pix.save(out)
                    return True
            finally:
                p.set_rotation(old)
        finally:
            doc.close()
    except Exception:
        pass
    # MuPDF 出白图(或不可用) → PDFium 重渲染
    try:
        import pypdfium2 as pdfium
        d = pdfium.PdfDocument(pdf_path)
        pg = d[idx]
        if rot is not None:
            pg.set_rotation(rot % 360)
        img = pg.render(scale=dpi / 72.0).to_pil()
        if frac:
            W, H = img.size
            img = img.crop((int(W * frac[0]), int(H * frac[1]),
                            int(W * frac[2]), int(H * frac[3])))
        img.save(out)
        return True
    except Exception:
        return False
