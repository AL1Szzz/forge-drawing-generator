# -*- coding: utf-8 -*-
"""
从 CAXA 导出的热处理报告 DXF 中提取"热处理尺寸"。

CAXA 的 MTEXT 用字面反斜杠写格式码(\\fnsimsun|...;  \\pxt..;  \\P),
ezdxf 自带的 plain_text 不认, 这里自己剥。
"""
import os, re, sys, csv, glob

import ezdxf

from config import rx

# 炉号: 文件名里 6 位
RE_LU = rx("re_lu")

# 判读规则(关键): 只有带 Ø 标记的数才是直径, 否则是表格序号列
#   Ø911×Ø430×390        → 筒体 外径911 内径430 长390
#   Ø85×990 / Ø95×1000   → 两根实心棒, 取第一根
#   64×210×250           → 序号64, 实心棒 Ø210×250
RE_D = r"[ØøΦ⌀φ]"
NUM = r"\d{1,5}(?:\.\d+)?"
RE_TUBE_M = re.compile(
    r"(%s\s*%s)\s*[/×xX*]\s*(%s\s*%s)\s*[/×xX*]\s*(%s)" % (RE_D, NUM, RE_D, NUM, NUM))
RE_SOLID = re.compile(r"(?<![\d.])(%s\s*)(%s)\s*[/×xX*]\s*(%s)(?![\d.])" % (RE_D, NUM, NUM))
# "产品规格 / 热处理尺寸" 两列: Ø15×1000 / Ø30×1010 → 右列(30, 1010)是热处理尺寸
RE_PAIR = re.compile(r"%s\s*%s\s*[×xX*]\s*%s\s*[/]\s*%s?\s*(\d{1,5}(?:\.\d+)?)\s*[×xX*]\s*(\d{1,5}(?:\.\d+)?)"
                     % (RE_D, NUM, NUM, RE_D))
RE_LEADIDX = re.compile(
    r"(?:^|[\s/×xX*])(\d{1,3})\s*[×xX*]\s*(%s)\s*[×xX*]\s*(%s)(?![\d.])" % (NUM, NUM))

# CAXA/AutoCAD 文本转义码
DECODE = [("%%C", "Ø"), ("%%c", "Ø"), ("%%D", "°"), ("%%P", "±"),
          ("%%d", "°"), ("%%p", "±"), ("%%% ", "%"), ("%%%%", "%")]

# 规格表列名(用于定位)
SPEC_HDR = re.compile(r"产品规格|Specification|热处理尺寸|锻后")

_FONT = re.compile(r"\\f[^;]*;", re.I)
_PARA = re.compile(r"\\p[^;]*;", re.I)
_OTHER = re.compile(r"\\[ACFHQSTO][0-9.]+;?", re.I)


def clean(t):
    """剥 CAXA/AutoCAD MTEXT 格式码 + 解码 %%C 等转义, 保留正文"""
    if not t:
        return ""
    t = t.replace("\\P", "\n").replace("\\~", " ")
    t = _FONT.sub("", t)
    t = _PARA.sub("", t)
    t = _OTHER.sub("", t)
    t = t.replace("{", "").replace("}", "")
    for a, b in DECODE:
        t = t.replace(a, b)
    return re.sub(r"[ \t]+", " ", t).strip()


def texts(dxf_path):
    """返回 [(清洗后文本, 类型, x, y)] — 坐标用于按表格行列归位"""
    doc = ezdxf.readfile(dxf_path)
    out = []
    for e in doc.modelspace():
        t, xy = None, (0, 0)
        try:
            if e.dxftype() == "MTEXT":
                t, xy = e.text, (e.dxf.insert.x, e.dxf.insert.y)
            elif e.dxftype() == "TEXT":
                t, xy = e.dxf.text, (e.dxf.insert.x, e.dxf.insert.y)
            elif e.dxftype() == "DIMENSION":
                t = e.dxf.get("text", "") or ""
            elif e.dxftype() == "INSERT":
                t = e.dxf.get("name", "")
        except Exception:
            continue
        c = clean(t) if t else ""
        if c:
            out.append((c, e.dxftype(), xy[0], xy[1]))
    return out


def _n(x):
    """数值化: 整数保持整数, 小数保留一位, 去掉 .0 尾巴"""
    v = float(x)
    return int(v) if v == int(v) else round(v, 1)


def extract_one(dxf_path):
    ts = texts(dxf_path)
    lu = RE_LU.search(os.path.basename(dxf_path))
    r = {"炉号": lu.group(0) if lu else "", "来源": os.path.basename(dxf_path),
         "外径": "", "内径": "", "长度": "", "实心": "", "规格候选": "",
         "含取样": "", "命中原文": "", "全部候选": "", "多行": ""}
    cands = []
    for txt, kind, x, y in ts:
        if kind == "INSERT":
            continue
        # 表头是"产品规格 / 热处理尺寸"两列: 斜杠右边才是锻后送热处理的尺寸(本批 339/339 对都是 +10)
        mp = RE_PAIR.search(txt)
        if mp:
            d2 = float(mp.group(1))
            l2 = float(mp.group(2))
            cands.append((txt + " [热处理]", "solid", _n(d2), "", _n(l2)))
        m = RE_TUBE_M.search(txt)
        if m:
            a = float(re.sub(r"[^\d.]", "", m.group(1)))
            b = float(re.sub(r"[^\d.]", "", m.group(2)))
            c = float(m.group(3))
            cands.append((txt, "tube", _n(max(a, b)), _n(min(a, b)), _n(c)))
            continue
        ms = RE_SOLID.search(txt)
        if ms:
            cands.append((txt, "solid", _n(ms.group(2)), "", _n(ms.group(3))))
            continue
        ml = RE_LEADIDX.search(txt)          # "64×210×250" → 序号+实心棒
        if ml:
            cands.append((txt, "solid", _n(ml.group(2)), "", _n(ml.group(3))))
    # 斜杠右列(热处理尺寸)排前面: 后续一律以热处理尺寸为基准(用户 2026-11-02)
    cands.sort(key=lambda c: 0 if "热处理" in c[0] else 1)
    if cands:
        # 优先"非取样件"那一行 —— 取样件长度被加长过, 不是产品本体
        main = [c for c in cands if "取样" not in c[0]]
        pick = (main or cands)[0]
        txt, kind, od, idd, ln = pick
        r["外径"], r["内径"], r["长度"] = od, idd, ln
        r["实心"] = "Y" if kind == "solid" else ""
        r["命中原文"] = txt[:70]
        r["含取样"] = "Y" if any("取样" in c[0] for c in cands) else ""
    r["全部候选"] = " || ".join(
        "%s|%s|%s%s" % (k, o, l, "|1" if "热处理" in t else "")
        for t, k, o, i, l in cands[:20])
    if cands:
        # 优先"非取样件"那一行 —— 取样件长度被加长过, 不是产品本体
        main = [c for c in cands if "取样" not in c[0]]
        pick = (main or cands)[0]
        txt, kind, od, idd, ln = pick
        r["外径"], r["内径"], r["长度"] = od, idd, ln
        r["实心"] = "Y" if kind == "solid" else ""
        r["命中原文"] = txt[:70]
        r["含取样"] = "Y" if any("取样" in c[0] for c in cands) else ""
        # 一份热处理报告常含多行产品(同炉不同长度), 全留下供按成品尺寸匹配
        r["多行"] = "Y" if len(main or cands) > 1 else ""
    return r


def pick_for(r, prod_od, prod_id, prod_l):
    """从该炉号的全部候选里, 挑出这一件的"热处理尺寸"行。
    卡片两列 产品规格/热处理尺寸 的关系是长度 +10(本批 339/339 对), 所以按"成品长度+10"找。"""
    cands = []
    for seg in (r.get("全部候选") or "").split("||"):
        seg = seg.strip()
        if not seg:
            continue
        parts = seg.split("|")
        if len(parts) >= 3:
            cands.append((parts[0], parts[1], parts[2], len(parts) > 3 and parts[3] == "1"))
    if not cands:
        return r
    try:
        pl = float(prod_l) if prod_l else None
        po = float(prod_od) if prod_od else None
    except ValueError:
        return r
    best, bs = None, None
    for kind, od, ln, is_ht in cands:
        try:
            o, l = float(od), float(ln)
        except ValueError:
            continue
        if pl is not None and l < pl - 1:      # 热处理长度不应短于成品
            continue
        if po is not None and o < po - 1:
            continue
        tgt = (pl + 10) if pl is not None else l
        score = abs(l - tgt) + (0 if is_ht else 3)     # 优先取热处理列
        if bs is None or score < bs:
            best, bs = (kind, o, l), score
    if not best:
        return r
    kind, o, l = best
    out = dict(r)
    out.update(外径=int(o) if o == int(o) else o, 长度=int(l) if l == int(l) else l,
               内径="", 实心="Y" if kind == "solid" else "",
               命中原文="热处理尺寸: %s×%s" % (o, l))
    return out


def run(dxf_dir, out_csv=None):
    files = sorted(glob.glob(os.path.join(dxf_dir, "*.dxf")))
    if not files:
        print("目录里没有 dxf:", dxf_dir)
        return []
    rows = []
    for i, f in enumerate(files, 1):
        try:
            r = extract_one(f)
        except Exception as e:
            r = {"炉号": "", "来源": os.path.basename(f), "外径": "", "内径": "",
                 "长度": "", "实心": "", "规格候选": "", "含取样": "",
                 "全部候选": "", "多行": "", "命中原文": "ERR:%s" % e}
        rows.append(r)
        if r["外径"]:
            print("  %3d/%d 炉号%-8s Ø%s/%s×%-6s %s«%s»" % (
                i, len(files), r["炉号"], r["外径"], r["内径"] or "-", r["长度"],
                "多行 " if r.get("多行") else "", r["命中原文"][:34]), flush=True)
    hit = sum(1 for x in rows if x["外径"])
    out = out_csv or os.path.join(os.path.dirname(dxf_dir.rstrip("\\/")), "ht_dims.csv")
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print("\n%d 份 DXF, 解析出尺寸 %d, 未解析 %d -> %s" % (len(rows), hit, len(rows) - hit, out))
    return rows


if __name__ == "__main__":
    run(sys.argv[1])
