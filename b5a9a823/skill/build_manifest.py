# -*- coding: utf-8 -*-
"""
阶段1: 扫描一批资料, 建立"图纸页 -> 图号/炉号/规格/重量"清单, 并用字典约束纠正 OCR 错字。

输入(一个批次目录):
  图纸PDF          : 产品图纸(扫描), 标题栏含 图号/炉号/规格/材质/名称/重量
  热处理工艺/*.exb : 热处理报告, 文件名含 6 位炉号
  料单PDF          : 制造大纲(可选, 用于交叉校验重量)

输出: manifest.csv
"""
import os, re, sys, csv, json, glob
import pymupdf as fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ocr2 import ocr_blocks, parse_fields, parse_weight_line
from config import rx, pat

# ---- 字段正则(容忍 OCR 错字) ----
RE_DRAW = rx("re_draw")
RE_LU = rx("re_lu")                              # 6 位炉号
RE_LU7 = rx("re_lu7")                            # 长炉号(图纸上另写的)
RE_SPEC = re.compile(r'[ØøΦ⌀5S]?\s*(\d{2,3})\s*[*×xX]\s*(\d{3,5})(?![\d])')
RE_MASS = re.compile(r'(\d{1,4}(?:\.\d)?)\s*[kK][gG]\s*$')
MAT_PAT = pat("materials")


def norm_draw(s):
    """把 OCR 结果规整成图号形式, 再与合法集合比对。
    月份字母支持 A/B 两个系列(F24B1369 / F25A3395); OCR 常把 B 读成 8, 这里纠正。"""
    t = re.sub(r'[^0-9A-Za-z]', '', s).upper()
    m = re.search(r'F(\d{2})([AB8])(\d{3,4})', t)
    if not m:
        return None
    mon = 'B' if m.group(2) == '8' else m.group(2)
    return 'F' + m.group(1) + mon + m.group(3)


def build(batch_dir, out_csv=None, pages_limit=None, dpi=400):
    drawings = sorted(glob.glob(os.path.join(batch_dir, "*.pdf")))
    ht_dir = os.path.join(batch_dir, "热处理工艺")
    # 1) 合法炉号集合 = 热处理报告文件名
    ht_files = (sorted(glob.glob(os.path.join(ht_dir, "*.exb"))) +
                sorted(glob.glob(os.path.join(ht_dir, "exb", "*.exb"))))
    ht_map = {}
    for f in ht_files:
        m = RE_LU.search(os.path.basename(f))
        if m:
            ht_map.setdefault(m.group(0), []).append(os.path.basename(f))
    print("热处理报告 %d 份, 唯一炉号 %d 个" % (len(ht_files), len(ht_map)))

    # 2) 合法图号集合 = 图纸文件名里的"起~止"范围(如 XXX1362~1424)
    legal_draws = set()
    for f in drawings:
        m = rx("re_range").search(os.path.basename(f))
        if m:
            a, b = int(m.group(2)), int(m.group(3))
            legal_draws |= {"%s%04d" % (m.group(1), i) for i in range(a, b + 1)}
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
        for i in range(n):
            p = doc[i]
            R = p.rect
            tmp = "_tb_%d.png" % os.getpid()
            # 标题栏区
            p.get_pixmap(dpi=dpi, clip=fitz.Rect(R.width*.06, R.height*.70, R.width*.96, R.height*.97)).save(tmp)
            blocks = ocr_blocks(tmp)
            f = parse_fields(blocks, legal_draws=legal_draws, valid_lu=set(ht_map))
            # 图框下方那一行: 可能是 "1880kg", 也可能是 "Ø900/ø350×800×8%=4000kg"
            #                          后者前面的尺寸就是**准确的锻件尺寸**, 直接采用
            p.get_pixmap(dpi=dpi, clip=fitz.Rect(R.width*.05, R.height*.90, R.width*.98, R.height*1.0)).save(tmp)
            w_mass, w_dim = parse_weight_line(ocr_blocks(tmp))
            if os.path.exists(tmp):
                os.remove(tmp)
            if not (w_mass and w_dim):
                # 重量行有时落在标题栏区(0.70~0.97)而不是底部 10% 的裁剪里,
                # 用已经 OCR 过的标题栏 blocks 兜底再抓一次
                m2, d2 = parse_weight_line(blocks)
                w_mass = w_mass or m2
                w_dim = w_dim or d2
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
