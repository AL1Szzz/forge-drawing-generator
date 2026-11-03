# -*- coding: utf-8 -*-
"""
OCR 双后端: RapidOCR(默认, 中文/表格/小字精度远高于 Tesseract) + Tesseract 兜底。
RapidOCR 带置信度, 低于阈值的字段一律标"需人工", 不静默采用。
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import rx

_R = None


def rapid():
    global _R
    if _R is None:
        from rapidocr_onnxruntime import RapidOCR
        _R = RapidOCR()
    return _R


def ocr_blocks(img, min_conf=0.5):
    """返回 [(文本, 置信度, x0, y0, x1, y1)], 按上→下 左→右 排序"""
    res = rapid()(img)[0] or []
    out = []
    for box, txt, conf in res:
        c = float(conf)
        if c < min_conf:
            continue
        xs = [p[0] for p in box]; ys = [p[1] for p in box]
        out.append((txt, c, min(xs), min(ys), max(xs), max(ys)))
    out.sort(key=lambda t: (round(t[3] / 22), t[2]))
    return out


def ocr(img, lang="chi_sim+eng", psm=6):
    """兼容旧接口: 只回文本(按行拼接), 供 ocr_batch/其他脚本复用"""
    bl = ocr_blocks(img)
    if not bl:
        from tesseract_shim import ocr_tess
        return ocr_tess(img, lang, psm)
    return "\n".join(b[0] for b in bl), ""


# ---- 字段解析(RapidOCR 输出质量下可直接用) ----
RE_OD = re.compile(r'[0OØøΦ⌀]?(\d{2,4})\s*[X×x*]\s*(\d{2,6})')
RE_TUBE = re.compile(r'[0OØøΦ⌀]?(\d{2,4})\s*[/]\s*[0OØøΦ⌀]?(\d{2,4})\s*[X×x*]\s*(\d{2,6})')
RE_DRAW = rx("re_draw_ocr")
RE_LU = rx("re_lu")
# 重量行可能写成 "Ø900/ø350×800×8%=4000kg" —— 前面的尺寸就是**准确的锻件尺寸**
RE_WT_DIM = re.compile(
    r"([ØøΦ⌀φ]?\s*\d{2,4}\s*(?:[/×xX]\s*[ØøΦ⌀φ]?\s*\d{2,4}){1,3})\s*(?:×\s*\d{1,2}\s*%?)?\s*[=≈]\s*(\d{1,5}(?:\.\d)?)\s*[kK][gG]")
RE_WT = re.compile(r"(\d{1,5}(?:\.\d+)?)\s*[kK][gG]")


def parse_weight_line(blocks):
    """图框下方那一行: 返回 (锻重kg, 图纸直接给的锻件尺寸原文)"""
    mass, dim = "", ""
    for txt, c, *_ in blocks:
        t = txt.replace(" ", "")
        m = RE_WT_DIM.search(t)
        if m and c >= 0.6:
            return m.group(2), re.sub(r"^0(?=[1-9])", "Ø", m.group(1))
        if not mass:
            m2 = RE_WT.search(t)
            if m2 and c >= 0.6 and "HCH" not in t:
                mass = m2.group(1)
    return mass, dim
MAT = re.compile(r'(SA508[ -]?Gr\.?3|42CrMo|20MnMo|45#|SA105|SA350|Q345R|16Mn|SA182|304|316L)')


def parse_fields(blocks, legal_draws=None, valid_lu=None):
    """从带坐标的文本块里抽字段, 每个字段带置信度"""
    f = {"图号": "", "图号conf": 0, "炉号": "", "炉号conf": 0,
         "外径": "", "内径": "", "长度": "", "规格conf": 0, "规格原文": "",
         "材质": "", "锻重kg": "", "名称": "", "名称conf": 0}
    for txt, c, x0, y0, x1, y1 in blocks:
        t = txt.strip()
        if not f["图号"] or c > f["图号conf"]:
            m = RE_DRAW.search(t)
            if m and "HCH" not in t:
                cand = m.group(0).upper()
                if legal_draws:
                    g = re.search(r"[AB8](\d{4})", cand)
                    near = [d for d in legal_draws if g and d.endswith(g.group(1))]
                    if len(near) == 1:
                        f["图号"], f["图号conf"] = near[0], c
                else:
                    f["图号"], f["图号conf"] = cand, c
        if not f["炉号"] or (c > f["炉号conf"] and "炉号" not in t):
            m = RE_LU.search(t)
            if m and (not valid_lu or m.group(0) in valid_lu):
                f["炉号"], f["炉号conf"] = m.group(0), c
        mm = RE_TUBE.search(t)
        if mm and c >= 0.6:
            f.update(外径=mm.group(1), 内径=mm.group(2), 长度=mm.group(3),
                     规格conf=c, 规格原文=t)
        elif not f["外径"]:
            mm2 = RE_OD.search(t)
            if mm2 and c >= 0.6 and "HCH" not in t and len(t) < 18:
                f.update(外径=mm2.group(1), 长度=mm2.group(2), 规格conf=c, 规格原文=t)
        if not f["材质"]:
            m = MAT.search(t)
            if m:
                f["材质"] = m.group(1)
        if not f["锻重kg"]:
            m = RE_WT.search(t)
            if m:
                f["锻重kg"] = m.group(1)
        # 名称: 纯中文、非表头、出现在标题栏下半部
        if (re.fullmatch(r'[一-鿿]{3,12}', t) and t not in
                ("版本", "填图", "复核", "批准", "序号", "名称", "材质", "数量", "炉号")
                and c > f.get("名称conf", 0)):
            f["名称"], f["名称conf"] = t, c
    return f
