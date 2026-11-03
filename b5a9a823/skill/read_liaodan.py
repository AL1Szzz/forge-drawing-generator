# -*- coding: utf-8 -*-
"""
料单(制造大纲)提取: 以"质保号 F24Bxxxx"为锚点, 抓同一行内的
交货规格 / 批次(炉号) / 锻造重量, 用于交叉校验图纸 OCR 的规格。

扫描件倾斜约 0.5°, 先转正再整页 OCR(psm=6), 按行解析。
"""
import os, re, sys, csv, json
import pymupdf as fitz
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ocr import ocr
from config import rx

RE_ANCHOR = rx("re_draw_anchor")
RE_SPEC = re.compile(r'[ØøΦ⌀5S#]?(\d{1,3})\s*[*×xX]\s*(\d{2,5})')
RE_LU = rx("re_lu")
RE_MASS = re.compile(r'(\d{1,4}\.\d|\d{2,4})\s*[kK]?[gG]?')


def clean_line(t):
    return re.sub(r"[ \t]+", " ", t).strip()


def ocr_page(pil, tmp, prefer=None):
    """四主方向 × 微调 试探, 取"质保号命中最多"者。
    prefer=上一页胜出的姿态; 命中达标即复用, 省掉其余 11 次 OCR。"""
    combos = [(tr, a) for tr in (None, Image.ROTATE_90, Image.ROTATE_180, Image.ROTATE_270)
              for a in (-1, 0, 1)]
    if prefer in combos:
        combos.remove(prefer); combos.insert(0, prefer)
    best, bn, bmeta = "", -1, prefer
    for idx, (tr, a) in enumerate(combos):
        base = pil.transpose(tr) if tr else pil
        im = base.rotate(a, expand=True, fillcolor=255) if a else base
        im.save(tmp)
        txt, _ = ocr(tmp, "chi_sim+eng", 6)
        txt = txt or ""
        n = len(RE_ANCHOR.findall(txt))
        if n > bn:
            best, bn, bmeta = txt, n, (tr, a)
        if idx == 0 and n >= 5:      # 复用姿态成功, 提前结束
            break
    return best, bn, bmeta


def load_valid_lu(ht_dir):
    """合法炉号 = 热处理报告文件名里的6位数。用于剔除合同号/代号里的误匹配"""
    s = set()
    for f in os.listdir(ht_dir) if os.path.isdir(ht_dir) else []:
        m = RE_LU.search(f)
        if m:
            s.add(m.group(0))
    return s


def extract(pdf, pages=None, dpi=300, skew=0.5, out_csv=None, valid_lu=None):
    doc = fitz.open(pdf)
    n = doc.page_count if pages is None else min(doc.page_count, pages)
    rows = []
    tmp = "_ld_%d.png" % os.getpid()
    last = None
    for i in range(n):
        doc[i].get_pixmap(dpi=dpi).save(tmp)
        txt, nhit, last = ocr_page(Image.open(tmp).convert("L"), tmp, prefer=last)
        bmeta = last
        if os.path.exists(tmp):
            os.remove(tmp)
        if not txt:
            print("  OCR 失败 第%d页" % (i + 1)); continue
        for line in txt.split("\n"):
            line = clean_line(line)
            m = RE_ANCHOR.search(line)
            if not m:
                continue
            # 行内"名称"是超长中文描述, 会夹在质保号与规格之间 → 整行独立搜索
            tail = line
            sp = RE_SPEC.search(tail)
            if not sp:
                sp = RE_SPEC.search(line[m.end():])
            lu = [x for x in RE_LU.findall(tail)
                  if not valid_lu or x in valid_lu]          # 只认热处理报告里存在的炉号
            rows.append({
                "page": i + 1, "质保号": m.group(1),
                "交货规格": ("%s×%s" % (sp.group(1), sp.group(2))) if sp else "",
                "批次": lu[0] if len(set(lu)) == 1 else "|".join(sorted(set(lu))),
                "行原文": line[:150],
            })
        print("  第%d页 锚点%d 姿态%s 累计%d条" % (i + 1, nhit, bmeta, len(rows)), flush=True)
    out = out_csv or os.path.join(os.path.dirname(pdf), "料单提取.csv")
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["page", "质保号", "交货规格", "批次", "行原文"])
        w.writeheader(); w.writerows(rows)
    ok = sum(1 for r in rows if r["交货规格"])
    print("\n料单 -> %s  (%d 行, 含规格 %d)" % (out, len(rows), ok))
    return out


if __name__ == "__main__":
    pdf = sys.argv[1]
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else None
    ht = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(pdf), "热处理工艺")
    v = load_valid_lu(ht)
    print("合法炉号 %d 个" % len(v))
    extract(pdf, pages, out_csv=os.path.join(os.path.dirname(pdf), "料单提取.csv"), valid_lu=v)
