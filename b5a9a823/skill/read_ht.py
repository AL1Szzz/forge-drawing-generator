# -*- coding: utf-8 -*-
"""
阶段2: 从 CAXA 导出的 DXF 中读取"热处理尺寸"。

背景: .exb 是 CAXA 专有二进制, 内部文字非明文, 无法直接解析;
      CAXA 无 COM 自动化、无命令行转换器。
      => 必须由用户用 CAXA 自带"DWG/DXF批量转换器"做一次导出, 本脚本再读 DXF。

用法:
    python read_ht.py <DXF目录>            # 提取并输出 ht_dims.csv
    文件名需含 6 位炉号, 例: XX单位 300001 42CrMo 2025.10.22.dxf
"""
import os, re, sys, csv, glob
import ezdxf

from config import rx

RE_LU = rx("re_lu")
# 常见写法: Ø490/Ø390×2135  Ø490×Ø390×2135  φ490 φ390 2135  490/390*2135
RE_TRIPLE = re.compile(
    r'[ØøΦ⌀φ]?\s*(\d{2,5})\s*(?:[/×xX*]\s*)[ØøΦ⌀φ]?\s*(\d{2,5})\s*(?:[/×xX*]\s*)[ØøΦ⌀φ]?\s*(\d{3,6})')
RE_PAIR = re.compile(r'[ØøΦ⌀φ]\s*(\d{2,5})\s*[/×xX*]\s*(\d{3,6})')


def texts_of(dxf):
    doc = ezdxf.readfile(dxf)
    out = []
    for e in doc.modelspace():
        t = None
        if e.dxftype() in ('TEXT', 'MTEXT'):
            t = e.dxf.text if e.dxftype() == 'TEXT' else e.text
        elif e.dxftype() == 'DIMENSION':
            t = e.dxf.get('text', '')
        if t:
            out.append(str(t))
    return out


def parse(dxf, verbose=True):
    ts = texts_of(dxf)
    blob = " ".join(ts)
    lu = RE_LU.search(os.path.basename(dxf))
    res = {"炉号": lu.group(0) if lu else "", "来源": os.path.basename(dxf),
           "外径": "", "内径": "", "长度": "", "实心": "", "候选": "", "命中原文": ""}
    m = RE_TRIPLE.search(blob)
    if m:
        a, b, c = (int(x) for x in m.groups())
        # 大的是外径, 小的是内径, 第三个是长度
        res.update(外径=max(a, b), 内径=min(a, b), 长度=c)
    else:
        m2 = RE_PAIR.search(blob)
        if m2:
            res.update(外径=int(m2.group(1)), 内径="", 长度=int(m2.group(2)), 实心="Y")
    cands = [t for t in ts if RE_TRIPLE.search(t) or RE_PAIR.search(t)]
    res["候选"] = " || ".join(cands[:4])
    if verbose and cands:
        print("  %s -> 炉号%s  外径%s 内径%s 长度%s" % (
            res["来源"][:34], res["炉号"], res["外径"], res["内径"], res["长度"]))
    return res


def run(dxf_dir, out_csv=None):
    files = sorted(glob.glob(os.path.join(dxf_dir, "*.dxf")))
    if not files:
        print("目录里没有 dxf:", dxf_dir); return []
    rows = [parse(f) for f in files]
    hit = sum(1 for r in rows if r["外径"])
    out = out_csv or os.path.join(dxf_dir, "ht_dims.csv")
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print("\n%d 份 DXF, 解析出尺寸 %d 份 -> %s" % (len(rows), hit, out))
    miss = [r["来源"] for r in rows if not r["外径"]]
    if miss:
        print("未解析出尺寸(%d): %s" % (len(miss), "; ".join(miss[:6])))
    return rows


if __name__ == "__main__":
    run(sys.argv[1])
