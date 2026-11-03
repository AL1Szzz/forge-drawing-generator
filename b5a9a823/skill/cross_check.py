# -*- coding: utf-8 -*-
"""
交叉校验: 图纸 manifest × 料单提取 → 定稿规格
规则:
  1. 料单"交货规格"是权威值(打印体, 且为成品名义尺寸), 用它纠正图纸 OCR 的外径
  2. 图纸 OCR 的长度若与料单一致 → 采纳; 不一致 → 标"需复核"
  3. 两边都缺或冲突 → 进复核清单, 不猜
"""
import os, re, sys, csv
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from config import rx


def num(s):
    m = re.search(r'\d{1,5}', s or "")
    return int(m.group(0)) if m else None


def split_spec(s):
    if not s:
        return None, None
    m = re.search(r'(\d{1,4})\s*[×*xX/]\s*(\d{2,6})', s)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def cross(manifest_csv, liaodan_csv, out_csv):
    man = [r for r in csv.DictReader(open(manifest_csv, encoding="utf-8-sig")) if r.get("图号")]
    ld = list(csv.DictReader(open(liaodan_csv, encoding="utf-8-sig")))
    by_draw = defaultdict(list)
    for r in ld:
        if r.get("质保号"):
            by_draw[r["质保号"]].append(r)
    by_lu = defaultdict(list)
    for r in ld:
        for x in rx("re_lu").findall(r.get("批次") or ""):
            by_lu[x].append(r)

    rows, stat = [], {"料单定稿": 0, "图纸自洽": 0, "需复核": 0, "无料单记录": 0}
    for r in man:
        dh, lu = r["图号"], (r.get("炉号") or "").strip()
        d_od, d_ln = split_spec(r.get("规格"))
        cands = by_draw.get(dh) or by_lu.get(lu) or []
        l_od = l_ln = None
        for c in cands:
            a, b = split_spec(c.get("交货规格"))
            if a and b:
                l_od, l_ln = a, b
                break
        src, flag = "", ""
        if l_od and l_ln:
            src = "料单"
            od, ln = l_od, l_ln
            if d_ln and abs(d_ln - ln) > max(2, ln * 0.05):
                flag = "长度与图纸不符(%s→%s)" % (d_ln, ln)
            if d_od and abs(d_od - od) > od:
                flag = (flag + "; " if flag else "") + "外径差异大(图纸%s/料单%s)" % (d_od, od)
            stat["料单定稿" if not flag else "需复核"] += 1
        elif d_od and d_ln:
            src, od, ln = "图纸", d_od, d_ln
            stat["需复核"] += 1
            flag = "无料单规格, 图纸OCR外径不可靠"
        else:
            src, od, ln = "", "", ""
            stat["无料单记录" if not cands else "需复核"] += 1
            flag = "规格两源皆缺"
        rows.append({"图号": dh, "炉号": lu, "页": r["page"], "规格来源": src,
                     "成品外径": od, "成品长度": ln,
                     "图纸OCR规格": r.get("规格", ""), "料单规格": ("%s×%s" % (l_od, l_ln)) if l_od else "",
                     "材质": r.get("材质", ""), "锻重kg": r.get("锻重kg", ""),
                     "复核标记": flag})
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print("校验 -> %s" % out_csv)
    for k, v in stat.items():
        print("   %-10s %d" % (k, v))
    return rows


if __name__ == "__main__":
    b = sys.argv[1]
    cross(os.path.join(b, "manifest.csv"), os.path.join(b, "料单提取.csv"),
          os.path.join(b, "规格定稿.csv"))
