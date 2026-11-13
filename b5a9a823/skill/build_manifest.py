# -*- coding: utf-8 -*-
"""
阶段1: 扫描一批资料, 建立"图纸页 -> 图号/炉号/规格/重量"清单, 并用字典约束纠正 OCR 错字。

输入(一个批次目录):
  图纸PDF          : 产品图纸(扫描), 标题栏含 图号/炉号/规格/材质/名称/重量
  热处理工艺/*.exb : 热处理报告, 文件名含 6 位炉号
  料单PDF          : 制造大纲(可选, 用于交叉校验重量)

输出: manifest.csv
"""
import os, re, sys, csv, json, glob, tempfile
import pymupdf as fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ocr2 import ocr_blocks, parse_fields, parse_weight_line
from config import rx, pat, norm_lu

# ---- 字段正则(容忍 OCR 错字) ----
RE_DRAW = rx("re_draw")
RE_LU = rx("re_lu_suf")                          # 6 位炉号 + 可选子号后缀(-1/-CR/重热)
RE_LU7 = rx("re_lu7")                            # 长炉号(图纸上另写的)
RE_SPEC = re.compile(r'[ØøΦ⌀5S]?\s*(\d{2,3})\s*[*×xX]\s*(\d{3,5})(?![\d])')
RE_MASS = re.compile(r'(\d{1,4}(?:\.\d)?)\s*[kK][gG]\s*$')
MAT_PAT = pat("materials")


def norm_draw(s):
    """把 OCR 结果规整成图号形式, 再与合法集合比对。
    月份位 1~9/A/B/C 共 12 个月; OCR 常把 B 读成 8, 这里给出两种候选(以字典命中为准)。
    质保号子号后缀(-1/-2/-3)原样保留(2026-11-05)。"""
    ms = re.search(r'[-—–](\d{1,2})\s*$', s)
    suf = ms.group(0) if ms else ""
    if ms:
        s = s[:ms.start()]
    t = re.sub(r'[^0-9A-Za-z]', '', s).upper()
    m = re.search(r'F(\d{2})([1-9ABC8])(\d{3,4})', t)
    if not m:
        return None
    mons = ('B', '8') if m.group(2) == '8' else (m.group(2),)
    return 'F' + m.group(1) + mons[0] + m.group(3) + suf


def build(batch_dir, out_csv=None, pages_limit=None, dpi=400):
    drawings = sorted(glob.glob(os.path.join(batch_dir, "*.pdf")))
    # 1) 合法炉号集合 = 热处理报告文件名(DXF 与 EXB 都算, 目录见 make_batch 约定)
    ht_files = []
    for pat in ("热处理工艺/*.dxf", "DXF/*.dxf", "热处理DXF/*.dxf",
                "热处理工艺/*.exb", "热处理工艺/exb/*.exb", "EXB/*.exb"):
        ht_files += glob.glob(os.path.join(batch_dir, pat))
    ht_files.sort()
    ht_map = {}
    for f in ht_files:
        m = RE_LU.search(os.path.basename(f))
        if m:
            ht_map.setdefault(norm_lu(m.group(0)), []).append(os.path.basename(f))
    print("热处理报告 %d 份, 唯一炉号 %d 个" % (len(ht_files), len(ht_map)))

    # 2) 合法图号集合 = 图纸文件名里的"起~止"范围(如 XXX1362~1424);
    #    单图号文件名(如 质保号X.pdf, 不带~范围)也算 1 个合法图号
    legal_draws = set()
    for f in drawings:
        bn = os.path.basename(f)
        m = rx("re_range").search(bn)
        if m:
            a, b = int(m.group(2)), int(m.group(3))
            legal_draws |= {"%s%04d" % (m.group(1), i) for i in range(a, b + 1)}
        else:
            m1 = rx("re_pdf_name").search(bn)
            if m1:
                legal_draws.add(re.sub(r"\s+", "", m1.group(0)).upper())
    print("图号全集 %d 个: %s ... %s" % (len(legal_draws),
          min(legal_draws) if legal_draws else '-', max(legal_draws) if legal_draws else '-'))

    # 3) 逐页 OCR 标题栏
    rows = []
    for pdf in drawings:
        bn = os.path.basename(pdf)
        # 只处理"图号范围"命名的图纸PDF; 料单/大纲等跳过
        if not rx("re_pdf_name").search(bn):
            print("跳过非图纸PDF:", bn); continue
        doc = fitz.open(pdf)
        n = doc.page_count if pages_limit is None else min(doc.page_count, pages_limit)
        # 备用渲染引擎: PDFium(Chrome 同款)。个别扫描件的灰度 JPEG 会让 MuPDF 整页渲染成白图,
        # 同一页换 PDFium 就能正常出图(2026-11-03 实测)
        try:
            import pypdfium2 as _pdfium
            _pdfium_doc = _pdfium.PdfDocument(pdf)
        except Exception:
            _pdfium_doc = None
        for i in range(n):
            p = doc[i]
            # 临时 PNG 放系统 temp 且固定名复用(每页覆盖), 不逐页删除 ——
            # 上百页的逐页 os.remove 会触发批量删除安全确认, 中断跑批(2026-11-05 实测)
            tmp = os.path.join(tempfile.gettempdir(), "_forge_tb_%d.png" % os.getpid())

            def _render(rot, frac, path):
                """按页面比例渲染一块区域; MuPDF 出白图时自动换 PDFium 重渲染"""
                old = p.rotation
                if rot % 360 != old % 360:
                    p.set_rotation(rot % 360)
                try:
                    R2 = p.rect
                    clip = fitz.Rect(R2.width * frac[0], R2.height * frac[1],
                                     R2.width * frac[2], R2.height * frac[3])
                    pix = p.get_pixmap(dpi=dpi, clip=clip)
                    if pix.samples.count(255) != len(pix.samples):   # 不是全白
                        pix.save(path)
                        return True
                finally:
                    p.set_rotation(old)
                if _pdfium_doc is None:
                    return False
                try:
                    pg = _pdfium_doc[i]
                    pg.set_rotation(rot % 360)
                    img = pg.render(scale=dpi / 72.0).to_pil()
                    W, H = img.size
                    img.crop((int(W * frac[0]), int(H * frac[1]),
                              int(W * frac[2]), int(H * frac[3]))).save(path)
                    return True
                except Exception:
                    return False

            def _try(rot):
                b = []
                # 标题栏区
                if _render(rot, (0.06, 0.70, 0.96, 0.97), tmp):
                    b = ocr_blocks(tmp)
                f_ = parse_fields(b, legal_draws=legal_draws, valid_lu=set(ht_map))
                # 图框下方那一行: 可能是 "1880kg", 也可能是 "Ø941/ø350×838×8%=4261kg"
                #                          后者前面的尺寸就是**准确的锻件尺寸**, 直接采用
                wm, wd = "", ""
                if _render(rot, (0.05, 0.90, 0.98, 1.0), tmp):
                    wm, wd = parse_weight_line(ocr_blocks(tmp))
                if not (wm and wd):
                    # 重量行有时落在标题栏区(0.70~0.97)而不是底部 10% 的裁剪里,
                    # 用已经 OCR 过的标题栏 blocks 兜底再抓一次(某批实测: 3 张漏抓)
                    m2, d2 = parse_weight_line(b)
                    wm = wm or m2
                    wd = wd or d2
                return b, f_, wm, wd
            blocks, f, w_mass, w_dim = _try(p.rotation)
            if not (f["图号"] and f["炉号"]):
                def _score(f_):
                    return (1 if f_["图号"] else 0) + (1 if f_["炉号"] else 0)
                s0 = _score(f)
                best = (blocks, f, w_mass, w_dim)
                bs = s0
                # 兜底1: 页面渲染全白(个别灰度 JPEG MuPDF 解不出, 某批 p001):
                #        直接抽出嵌入的整页扫描图, 用 PIL 按比例裁剪后 OCR
                try:
                    from PIL import Image
                    big = None
                    for im in p.get_images(full=True):
                        rects = p.get_image_rects(im[0])
                        if rects and (big is None or
                                      rects[0].width * rects[0].height > big[0].width * big[0].height):
                            big = (rects[0], im[0])
                    if big is not None:
                        info = doc.extract_image(big[1])
                        import io as _io
                        pim = Image.open(_io.BytesIO(info["image"]))
                        W, H = pim.size
                        pim.crop((int(W*.06), int(H*.70), int(W*.96), int(H*.97))).save(tmp)
                        b2 = ocr_blocks(tmp)
                        f2 = parse_fields(b2, legal_draws=legal_draws, valid_lu=set(ht_map))
                        pim.crop((int(W*.05), int(H*.90), int(W*.98), int(H*1.0))).save(tmp)
                        wm2, wd2 = parse_weight_line(ocr_blocks(tmp))
                        if not (wm2 and wd2):
                            m3, d3 = parse_weight_line(b2)
                            wm2 = wm2 or m3
                            wd2 = wd2 or d3
                        if _score(f2) > bs:
                            best, bs = (b2, f2, wm2, wd2), _score(f2)
                            print("p%d 页面渲染空白, 已改用嵌入扫描图识别" % (i + 1))
                except ImportError:
                    pass
                # 兜底2: 扫描件内容横放但页面 rotation=0 时 OCR 全空:
                #        自动转 90/180/270 重试, 取识别效果最好的一档
                for ang in (90, 180, 270):
                    if bs == 2:
                        break
                    b2, f2, wm2, wd2 = _try(p.rotation + ang)
                    s = _score(f2)
                    if s > bs:
                        best, bs = (b2, f2, wm2, wd2), s
                        print("p%d 页面内容横放, 已旋转 %d° 识别" % (i + 1, ang))
                blocks, f, w_mass, w_dim = best
            if w_mass:
                f["锻重kg"] = w_mass
            f["图纸锻件尺寸"] = w_dim
            f["锻件尺寸来源"] = ("图纸重量行直接给出" if w_dim else
                             ("需算(热处理+余量)" if f["炉号"] else ""))
            f["名称"] = ""          # 名称易误抓签名人, 宁可留空
            f["pdf"] = os.path.basename(pdf)
            f["page"] = i + 1
            f["原始OCR"] = " | ".join(b[0] for b in blocks)[:300]
            conf = max(f["图号conf"], 0)
            f["图号置信"] = ("exact" if conf > .95 else "字典纠正" if conf else "未识别")
            rows.append(f)
            print("  p%03d 图号=%-10s 炉号=%-8s 规格=Ø%s×%s 重量=%-6s 材质=%s" % (
                i + 1, f["图号"] or '?', f["炉号"] or '?', f["外径"] or '?', f["长度"] or '?',
                f["锻重kg"] or '?', f["材质"] or '?'), flush=True)
    # 图号补漏(2026-11-13 某批): 标题栏图号被 OCR 读坏(如 F26/4187, 数字 1 读成斜杠),
    # 而 PDF 文件名已给出连续图号范围时, 按"一页一号、只剩一个号、只剩一页空"补上。
    # 条件严格: 页数==图号数, 已认领号数==图号数-1, 空页恰 1, 剩余号恰 1 —— 绝不猜测分配。
    by_pdf = {}
    for r in rows:
        by_pdf.setdefault(r["pdf"], []).append(r)
    for bn, prs in by_pdf.items():
        m = rx("re_range").search(bn)
        if not m:
            continue
        a, b = int(m.group(2)), int(m.group(3))
        expect = {"%s%04d" % (m.group(1), i) for i in range(a, b + 1)}
        if len(expect) != len(prs):
            continue
        used = {r["图号"].split("-")[0] for r in prs if r["图号"]}
        rest = expect - used
        blanks = [r for r in prs if not r["图号"]]
        if len(rest) == 1 and len(blanks) == 1 and len(used) == len(expect) - 1:
            blanks[0]["图号"] = rest.pop()
            blanks[0]["图号置信"] = "文件名范围补漏"
            print("  p%s 图号补漏 -> %s (标题栏OCR失效, 按文件名范围唯一余号)" %
                  (blanks[0]["page"], blanks[0]["图号"]), flush=True)

    out = out_csv or os.path.join(batch_dir, "manifest.csv")
    keys = ["pdf","page","图号","图号_ocr","图号置信","炉号","外径","内径","长度","规格conf","规格原文",
            "材质","锻重kg","名称","图纸锻件尺寸","锻件尺寸来源","原始OCR"]
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    ok = sum(1 for r in rows if r.get("图号") and r.get("炉号"))
    print("\n清单 -> %s  (%d 行, 其中图号+炉号双确认 %d 行)" % (out, len(rows), ok))
    return out, ht_map


if __name__ == "__main__":
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else None
    build(sys.argv[1], pages_limit=lim)
