# -*- coding: utf-8 -*-
"""从热处理工艺 DXF 里找"合锻棒料"(几件合锻成一根)。

卡片上的线索有两种, 都写成证据:
  1) 文字里带 "合锻" / "N合1" / "合锻成净" / "与序号N合锻"
     (如 `Ø23×1415=7件(2合1)`, `与序号2合锻成净 Ø23×1415=7件(2合1)`)
  2) 没有标记, 但某个尺寸的长度 ≈ 另两个件长度之和 + 切断量(≤40)
     (如 炉号300007: 1365 ≈ 850 + 500 + 15)

输出 合锻表.csv:
  炉号, 形状, 热处理A, 热处理B, 热处理C(=长度), 产品长度, 组成件长度, 件数, 来源

"热处理A/B/C" 是卡片上合锻棒料对应的热处理尺寸(绘图基准); 取不到就退回产品尺寸并留空标记。
"""
import os
import re
import csv
import sys
import glob
import itertools

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import read_ht2

D2 = re.compile(r"(?:Ø|%%C)?\s*(\d{2,4})\s*[×xX*]\s*(\d{2,4})(?![\d.])")          # 圆棒 Ød×l
D3 = re.compile(r"(?<![\d.,])(\d{2,4})\s*[×xX*]\s*(\d{2,4})\s*[×xX*]\s*(\d{2,4})(?![\d.])")
QTY = re.compile(r"=\s*(\d+)\s*件")
MARK = re.compile(r"合锻|合\s*1\s*[)）]")
CUT_MAX = 20          # 切断/锯口余量上限(实测卡片上都是 +15)


def _n(s):
    return int(round(float(s)))


def collect(f):
    """返回 (尺寸列表[(shape,dims)], 标记文本)"""
    dims, marks = [], []
    for t, _ty, _x, _y in read_ht2.texts(f):
        if MARK.search(t):
            marks.append(t.strip())
        has_phi = ("Ø" in t or "%%C" in t)
        if has_phi:
            for m in D2.finditer(t):
                dims.append(("round", (_n(m.group(1)), _n(m.group(2)))))
        elif D3.search(t):
            for m in D3.finditer(t):
                dims.append(("block", (_n(m.group(1)), _n(m.group(2)), _n(m.group(3)))))
    return dims, marks


def _is_ht(x, pool, li):
    """x 是不是某个产品尺寸的热处理版(长度 +10, 直径不小于产品)"""
    return any(y[li] == x[li] - 10 and y[0] <= x[0] for y in pool if y != x)


def bar_of(dims, marks):
    """找合锻棒料(只处理圆棒; 方块件已由块件规则按合锻后的尺寸出图)。
    判定: 产品尺寸(排除热处理版)里, 长度 ≈ 另两件之和 + 切断量(≤CUT_MAX)"""
    pool = [d for s, d in dims if s == "round"]
    if not pool:
        return None, None
    li = 1
    prod = [d for d in pool if not _is_ht(d, pool, li)]
    for d in sorted(prod, key=lambda d: -d[li]):
        if _is_bar(d, prod, li):
            return "round", d
    return None, None


def _is_bar(d, pool, li):
    """d 的长度是否≈另外两件产品长度之和(+切断量≤CUT_MAX)"""
    others = [x for x in pool if x != d]
    for a, b in itertools.combinations(others, 2):
        if 1 <= d[li] - (a[li] + b[li]) <= CUT_MAX:   # 必须留出切断量, 凑巧相等的排除
            return True
    return False


def components(bar, pool, li):
    """棒料由哪几件组成(产品件长度列表)"""
    others = [x for x in pool if x != bar]
    for a, b in itertools.combinations(others, 2):
        if 0 <= bar[li] - (a[li] + b[li]) <= CUT_MAX:
            return [a[li], b[li]]
    return []


def ht_of(bar, pool, li):
    """棒料对应的热处理尺寸: 长度 +10 且直径不小于产品"""
    cand = [x for x in pool if x[li] == bar[li] + 10 and x[0] >= bar[0]]
    return max(cand, key=lambda x: x[0]) if cand else None


def run(batch_dir):
    dxf = os.path.join(batch_dir, "DXF")
    res = {}
    for f in sorted(glob.glob(os.path.join(dxf, "*.dxf"))):
        lu = "".join(re.findall(r"\d{6}", os.path.basename(f)))[:6]
        if not lu or "重热" in os.path.basename(f):
            continue
        try:
            dims, marks = collect(f)
        except Exception as e:
            print("  读取失败", os.path.basename(f), e)
            continue
        if not dims:
            continue
        shape, bar = bar_of(dims, marks)
        if not bar:
            continue
        pool = [d for s, d in dims if s == "round"]
        li = 1 if shape == "round" else 2
        ht = ht_of(bar, pool, li)
        comp = components(bar, pool, li)
        res[lu] = {"shape": shape, "ht": ht, "prod": bar, "comp": comp,
                   "src": "; ".join(marks)[:120] or "%s" % (bar,)}
    path = os.path.join(batch_dir, "合锻表.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["炉号", "形状", "热处理A", "热处理B", "热处理C", "产品长度",
                    "组成件长度", "来源"])
        for lu in sorted(res):
            d = res[lu]
            ht = d["ht"] or ()
            if d["shape"] == "block":
                a, b, c = (tuple(ht) + ("", "", ""))[:3]
                prod_l = d["prod"][2]
            else:                                   # 圆棒: A=直径, C=长度
                a, b, c = (tuple(ht) + ("", ""))[0], "", (tuple(ht) + ("", ""))[1]
                prod_l = d["prod"][1]
            w.writerow([lu, d["shape"], a, b, c, prod_l,
                        "|".join(str(x) for x in d["comp"]), d["src"]])
    print("合锻棒料: %d 个炉号 -> %s" % (len(res), path))
    return res


if __name__ == "__main__":
    d = sys.argv[1]
    run(d if os.path.isdir(d) and os.path.isdir(os.path.join(d, "DXF")) else os.path.dirname(d) or d)
