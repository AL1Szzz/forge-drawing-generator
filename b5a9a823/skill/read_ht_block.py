# -*- coding: utf-8 -*-
"""从热处理工艺 DXF 提取"长方体(方块)"件的三向尺寸。

热处理卡片里一行写着 "产品规格/热处理尺寸", 例如:
    110×320×450 / 120×330×460=31件      → 产品 110×320×450, 热处理(锻后) 120×330×460
    1件450方向加长450 取样               → 取样行
    x;120×336×910=1件                   → 取样件的热处理尺寸
本脚本按此结构解析, 输出 块件尺寸.csv:

    炉号,产品A,产品B,产品C,热处理A,热处理B,热处理C,取样A,取样B,取样C,本体推算,来源

三条经验规则(依据 2026-11-02 抽样 12 个炉号的实际排版):
  * 斜杠左边 = 产品, 斜杠右边 = 热处理; 同尺寸文字里带"取样"的 = 取样件
  * 同一 (A,B) 截面下件数最多的是本体, 件数 1 且更长的那个是取样件
  * 卡片没写本体热处理尺寸的(只写了取样件), 按"产品每向+10"推算, 并在"本体推算"列标记 1
绝不凭空补尺寸: 取不到就留空, 由 make_batch 判定出待核稿。
"""
import os
import re
import csv
import sys
import glob
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import read_ht2

TR = re.compile(r"(?<![\d.])(\d{2,4}(?:\.\d+)?)\s*[×xX*]\s*(\d{2,4}(?:\.\d+)?)\s*[×xX*]\s*(\d{2,4}(?:\.\d+)?)")
QTY = re.compile(r"=\s*(\d+)\s*件")
ADD = re.compile(r"加长\s*(\d{2,4})")


def _n(s):
    return int(round(float(s)))


def collect(f):
    """返回一个炉号里的尺寸出现记录 (A,B,C,qty,侧面,取样标记), 以及卡片上写的加长量"""
    out, add = [], 0
    for t, _ty, _x, _y in read_ht2.texts(f):
        if "Ø" in t or "%%C" in t:
            continue
        m2 = ADD.search(t)
        if m2:
            add = max(add, _n(m2.group(1)))
        for m in TR.finditer(t):
            A, B, C = _n(m.group(1)), _n(m.group(2)), _n(m.group(3))
            if min(A, B, C) <= 0:
                continue
            slash = "/" in t
            pos = 0 if slash and m.end() <= t.index("/") else (1 if slash else -1)
            q = QTY.search(t)
            out.append((A, B, C,
                        int(q.group(1)) if q else 0,
                        pos,                    # 0=斜杠左(产品) 1=斜杠右(热处理) -1=未标注
                        1 if "取样" in t else 0))
    return out, add


def pick(recs, add):
    """从一堆出现记录里挑出 产品 / 热处理本体 / 取样件。

    判定链(全部基于卡片原文, 不凭空补):
      产品  = 斜杠左边里体积最小的
      热处理 = 截面(A,B)最接近"产品+10"的那组(卡片里热处理尺寸恒为产品每向+10)
      本体  = 该截面里件数最多的; 长度远超产品的那件只可能是取样件, 此时本体按产品+10推算
      取样件 = 同截面里更长的那件, 优先取"本体长度+卡片写的加长量"
    """
    if not recs:
        return None, None, None, 0
    agg = {}
    for A, B, C, q, pos, smp in recs:
        k = (A, B, C)
        cur = agg.get(k)
        if not cur:
            agg[k] = {"q": q, "smp": smp, "pos": pos}
        else:
            cur["q"] = max(cur["q"], q)
            cur["smp"] = cur["smp"] or smp
            if cur["pos"] == -1:
                cur["pos"] = pos
    # 产品: 优先取"能找到 +10 搭档"的斜杠左侧尺寸(3合1 合锻件的单个件尺寸会挑错)
    pool = [k for k, v in agg.items() if v["pos"] == 0] or list(agg)

    def _partner(P, keys):
        return sum(1 for k in keys
                   if k[0] == P[0] + 10 and k[1] == P[1] + 10 and k[2] == P[2] + 10)
    prod = max(pool, key=lambda P: (_partner(P, [k for k in agg if k != P]),
                                    P[0] * P[1] * P[2]))
    cands = [k for k in agg if k != prod]
    if not cands:
        return prod, None, None, 0
    # 热处理截面 = 最接近 产品(A,B)+10 的一组
    groups = {}
    for k in cands:
        groups.setdefault((k[0], k[1]), []).append(k)
    key = min(groups, key=lambda g: abs(g[0] - prod[0] - 10) + abs(g[1] - prod[1] - 10))
    grp = sorted(groups[key], key=lambda k: k[2])
    # 长度远超本体(=产品+10)的只能是取样件
    real = [k for k in grp if k[2] <= 1.8 * (prod[2] + 10)] or []
    if real:
        body = max(real, key=lambda k: (agg[k]["q"], -k[2]))
        rest = [k for k in grp if k != body]
        if not rest:
            return prod, body, None, 0
        want = body[2] + add if add else max(k[2] for k in rest)
        samp = min(rest, key=lambda k: abs(k[2] - want)) if add else max(rest, key=lambda k: k[2])
        return prod, body, samp, 0
    # 卡片只写了取样件 → 本体按"产品每向+10"推算, 并在返回值里标记
    samp = max(grp, key=lambda k: k[2])
    return prod, (grp[0][0], grp[0][1], prod[2] + 10), samp, 1


def run(batch_dir):
    dxf = os.path.join(batch_dir, "DXF")
    res = {}
    for f in sorted(glob.glob(os.path.join(dxf, "*.dxf"))):
        lu = "".join(re.findall(r"\d{6}", os.path.basename(f)))[:6]
        if not lu:
            continue
        # 同一炉号可能有"重热"卡(返工热处理), 只认正式卡, 重热卡仅作兜底
        if "重热" in os.path.basename(f) and lu in res:
            continue
        try:
            recs, add = collect(f)
        except Exception as e:
            print("  读取失败", os.path.basename(f), e)
            continue
        if not recs:
            continue
        prod, body, samp, derived = pick(recs, add)
        if body is None:
            continue
        src = "; ".join(sorted({"%d×%d×%d" % r[:3] for r in recs}))
        res[lu] = {"prod": prod, "body": body, "samp": samp, "der": derived, "src": src}
    path = os.path.join(batch_dir, "块件尺寸.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["炉号", "产品A", "产品B", "产品C", "热处理A", "热处理B", "热处理C",
                    "取样A", "取样B", "取样C", "本体推算", "来源"])
        for lu in sorted(res):
            d = res[lu]
            w.writerow([lu] + list(d["prod"] or ("", "", "")) + list(d["body"]) +
                       list(d["samp"] or ("", "", "")) + [d["der"], d["src"][:110]])
    print("块件尺寸: %d 个炉号 -> %s" % (len(res), path))
    return res


if __name__ == "__main__":
    d = sys.argv[1]
    run(d if os.path.isdir(d) and os.path.isdir(os.path.join(d, "DXF")) else os.path.dirname(d) or d)
    if os.path.isdir(os.path.join(d, "DXF")):
        pass
