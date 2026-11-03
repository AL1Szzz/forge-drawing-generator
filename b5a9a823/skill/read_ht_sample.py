# -*- coding: utf-8 -*-
"""
从热处理 DXF 提取"取样件(加样件)"尺寸 → 样件尺寸.csv

背景(用户 2026-11-02): 一个质保号出一张锻件图; 若该质保号有取样、且样件重量与本体不一致,
则再出一张"质保号+样"的图。样件尺寸只能来自热处理报告里的取样行(如 "%%C30×1375 取样"),
取不到就留空, 绝不推算。

用法: python read_ht_sample.py <批次目录/DXF目录> [--out 样件尺寸.csv]
"""
import os, re, sys, csv, glob

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import read_ht2
from config import rx

RE_LU = rx("re_lu")
_D = r"[ØøΦ⌀φ]"
_N = r"\d{2,5}(?:\.\d+)?"
RE_TUBE = re.compile(r"(%s\s*%s)\s*[/]\s*(%s\s*%s)\s*[×xX*]\s*(%s)" % (_D, _N, _D, _N, _N))
RE_SOLID = re.compile(r"(%s\s*%s)\s*[×xX*]\s*(%s)" % (_D, _N, _N))
# 长方体: 三个数 110×320×450 (无 Ø)
RE_BLOCK = re.compile(r"(?<![\d.])(\d{2,4})\s*[×xX*]\s*(\d{2,4})\s*[×xX*]\s*(\d{2,4})(?![\d.])")


def _num(s):
    v = float(re.sub(r"[^\d.]", "", s))
    return int(v) if v == int(v) else round(v, 1)


def parse_dims(t):
    """解析尺寸(不要求含'取样'): 管/棒/长方体"""
    m = RE_TUBE.search(t)
    if m:
        a, b = _num(m.group(1)), _num(m.group(2))
        return {"shape": "tube", "od": max(a, b), "id": min(a, b), "L": _num(m.group(3))}
    m = RE_BLOCK.search(t)
    if m:
        return {"shape": "block", "od": _num(m.group(1)), "id": _num(m.group(2)),
                "L": _num(m.group(3))}
    m = RE_SOLID.search(t)
    if m:
        return {"shape": "solid", "od": _num(m.group(1)), "L": _num(m.group(2))}
    return None


def parse_sample(t):
    """从一条含'取样/加长'的文本里解析尺寸; 解析不出返回 None"""
    if not ("取样" in t or "加长" in t):
        return None
    return parse_dims(t)


def run(dxf_dir, out_csv=None):
    if not os.path.isdir(dxf_dir):
        print("没有 DXF 目录:", dxf_dir)
        return {}
    files = sorted(glob.glob(os.path.join(dxf_dir, "*.dxf")))
    best = {}
    for f in files:
        lu = RE_LU.search(os.path.basename(f))
        if not lu:
            continue
        lu = lu.group(0)
        try:
            recs = read_ht2.texts(f)                       # (文本, 类型, x, y)
        except Exception as e:
            print("  读取失败", os.path.basename(f), e)
            continue
        # 取样行尺寸常在相邻单元格 → 按 y 归行, 行内找尺寸
        rows = {}
        for t, _ty, x, y in recs:
            rows.setdefault(round(y / 14), []).append((x, t))
        cands = []
        for t, _ty, x, y in recs:
            if not ("取样" in t or "加长" in t):
                continue
            c0 = parse_sample(t)                            # 尺寸就写在这一格里
            if c0:
                cands.append(c0)
            for xx, tt in sorted(rows.get(round(y / 14), []), key=lambda p: p[0]):
                if "=1件" in tt:                            # 取样件通常标注 "…=1件"
                    c = parse_dims(tt)
                    if c:
                        cands.append(c)
        if not cands:
            continue
        # 取样件是"加长"的那件 → 取最长; 同为最长时取最宽的
        c = max(cands, key=lambda d: (d["L"], d.get("od") or 0))
        if lu not in best or c["L"] > best[lu][0]["L"]:
            best[lu] = (c, f)
    out_csv = out_csv or os.path.join(os.path.dirname(dxf_dir), "样件尺寸.csv")
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["炉号", "形状", "取样外径", "取样内径", "取样长度", "来源"])
        for lu in sorted(best):
            c, f = best[lu]
            w.writerow([lu, c["shape"], c.get("od") or c.get("D"),
                        c.get("id", ""), c["L"], os.path.basename(f)])
    print("取样件尺寸 %d 个 -> %s" % (len(best), out_csv))
    return {k: v[0] for k, v in best.items()}


if __name__ == "__main__":
    d = sys.argv[1]
    dxf = d if os.path.basename(os.path.normpath(d)).upper() == "DXF" else os.path.join(d, "DXF")
    run(dxf)
