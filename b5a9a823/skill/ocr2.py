# -*- coding: utf-8 -*-
"""
OCR 双后端: RapidOCR(默认, 中文/表格/小字精度远高于 Tesseract) + Tesseract 兜底。
RapidOCR 带置信度, 低于阈值的字段一律标"需人工", 不静默采用。
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import rx, norm_lu

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
RE_LU = rx("re_lu_suf")      # 炉号(批次)可带 -1/-CR/重热 子号后缀, 2026-11-05
# 重量行可能写成 "Ø941/ø350×838×8%=4261kg" —— 前面的尺寸就是**准确的锻件尺寸**
# 注意两种 OCR 变异(2026-11-03 某批 实测):
#   ① Ø 被读成 0 → "05390/04990X198X6%=5377KG", 所以要允许前导 0/O
#   ② 百分比前的乘号被读成大写 X → "X6%", 所以乘号集合要含 x/X/*
RE_WT_DIM = re.compile(
    r"([0OØøΦ⌀φ]?\s*\d{2,4}\s*(?:[/×xX]\s*[0OØøΦ⌀φ]?\s*\d{2,4}){1,3})\s*(?:[×xX*]\s*\d{1,2}\s*%?)?\s*[=≈]\s*(\d{1,5}(?:\.\d)?)\s*[kK][gG]")
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
# 材质: 牌号模式匹配(字典记不全, 如 12Cr2Mo1/06Cr18Ni11Ti 不在老字典里) —— 2026-11-13
#   规则: ① 字母开头牌号 SA508[ Gr.3]/SA105...; ② 数字开头国标牌号(06Cr18Ni11Ti/42CrMo/16Mn...),
#         要求 ≥2 个字母, 且排除 "40X950" 这类尺寸串(数字X数字); ③ 纯数字短牌号(304/316L/45#)
#         必须前后非字母数字, 防命中编码子串(H223495-96450304045N 里的"0304"曾误读 304)
MAT = re.compile(
    r'(?<![0-9A-Za-z])('
    r'SA\d{3}(?:[ -]?Gr\.?[ -]?\d)?'
    r'|Q\d{3}[A-Za-z]?'
    r'|\d{1,2}[A-Za-z][A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*'
    r'|316L|304|45#'
    r')(?![0-9A-Za-z])')

# 尺寸串形状(数字[.]X数字): 不是材质
_NOT_MAT = re.compile(r'[0-9.]+\s*[Xx×*]\s*[0-9.]+')


def parse_fields(blocks, legal_draws=None, valid_lu=None):
    """从带坐标的文本块里抽字段, 每个字段带置信度"""
    f = {"图号": "", "图号conf": 0, "炉号": "", "炉号conf": 0,
         "外径": "", "内径": "", "长度": "", "规格conf": 0, "规格原文": "",
         "材质": "", "锻重kg": "", "名称": "", "名称conf": 0}
    prev_t = ""
    for txt, c, x0, y0, x1, y1 in blocks:
        t = txt.strip()
        if not f["图号"] or c > f["图号conf"]:
            m = RE_DRAW.search(t)
            if m and "HCH" not in t:
                cand = re.sub(r"\s+", "", m.group(0)).upper()      # F24B1373 或 F24B1373-1
                # 质保号子号(2026-11-05): 同图号拆 -1/-2/-3, 后缀必须保留, 否则会把
                # 不同规格的页并成一张图(实测 F24B1373 三页 Ø21×800/850/800 被并成 850)
                ms = re.search(r"[-—–](\d{1,2})$", cand)
                suf, base = (ms.group(0), cand[:ms.start()]) if ms else ("", cand)
                if legal_draws:
                    g = re.search(r"[1-9ABC8](\d{4})", base)
                    near = [d for d in legal_draws if g and d.endswith(g.group(1))]
                    if len(near) == 1:
                        f["图号"], f["图号conf"] = near[0] + suf, c
                    elif not near and re.fullmatch(r"F\d{2}[1-9ABC8]\d{4}", base):
                        # OCR 读到完整图号, 但不在"文件名起~止"范围内(如 F25A3402 而文件名只写到 3401):
                        # 保留原值但降置信 → 复核清单提示人工确认, 而不是整页静默丢掉
                        f["图号"], f["图号conf"] = base + suf, min(c, 0.92)
                else:
                    f["图号"], f["图号conf"] = base + suf, c
        if not f["炉号"] or (c > f["炉号conf"] and "炉号" not in t):
            m = RE_LU.search(t)
            if m:
                cand_lu = norm_lu(m.group(0))
                # 图纸上的炉号即使没有对应热处理卡也采信, 三种依据(卡片不全 ≠ 图纸错,
                # 某批 炉号 炉号B/炉号C 曾被卡片过滤误杀):
                #   ① 同块: "炉号 炉号B" 标签直接引导
                #   ② 上一块就是"炉号"标签(标签和数字常被 OCR 拆成两块)
                #   ③ 本块就是纯炉号数字本身(标题栏里孤立的 6 位数字)
                labeled = (re.search(r"炉号\s*[：:·]?\s*$", t[:m.start()])
                           or re.fullmatch(r"炉号\s*[：:·]?", prev_t)) is not None
                standalone = t == m.group(0).strip()
                # 带子号后缀的炉号: 卡片若是 248446-1 而图纸写 248446, 也认(后面按批次找卡时标待核)
                if (not valid_lu or cand_lu in valid_lu or labeled
                        or (standalone and cand_lu.isdigit() and len(cand_lu) == 6)
                        or (cand_lu.isdigit() and any(v.startswith(cand_lu + "-") for v in valid_lu))):
                    f["炉号"], f["炉号conf"] = cand_lu, c
        mm = RE_TUBE.search(t)
        if mm and c >= 0.6:
            f.update(外径=mm.group(1), 内径=mm.group(2), 长度=mm.group(3),
                     规格conf=c, 规格原文=t)
        elif not f["外径"]:
            mm2 = RE_OD.search(t)
            if mm2 and c >= 0.6 and "HCH" not in t and len(t) < 18:
                f.update(外径=mm2.group(1), 长度=mm2.group(2), 规格conf=c, 规格原文=t)
        ms = [m.group(1) for m in MAT.finditer(t)
              if not _NOT_MAT.fullmatch(m.group(1))]
        if ms:
            # 字母牌号(42Cr2Mo1/06Cr18Ni11Ti...)优先; 纯数字牌号(304/316L)可能是编码子串误读, 仅兜底
            has_letter = [re.search(r'[A-Za-z]', x) is not None for x in ms]
            cand = next(x for x, hl in zip(ms, has_letter) if hl) if any(has_letter) else ms[0]
            # OCR 常把牌号末位 1 读成 l(12Cr2Mo1 -> 12Cr2Mol), 牌号里没有以 l 结尾的, 归一
            if cand.endswith('l') and re.search(r'\d', cand):
                cand = cand[:-1] + '1'
            if not f["材质"] or (not re.search(r'[A-Za-z]', f["材质"])
                                 and re.search(r'[A-Za-z]', cand)):
                f["材质"] = cand
        if not f["锻重kg"]:
            m = RE_WT.search(t)
            if m:
                f["锻重kg"] = m.group(1)
        # 名称: 纯中文、非表头、出现在标题栏下半部
        if (re.fullmatch(r'[一-鿿]{3,12}', t) and t not in
                ("版本", "填图", "复核", "批准", "序号", "名称", "材质", "数量", "炉号")
                and c > f.get("名称conf", 0)):
            f["名称"], f["名称conf"] = t, c
        prev_t = t
    return f
