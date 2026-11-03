# -*- coding: utf-8 -*-
"""从 CAXA .exb 直接抽取 (文本, x, y) —— 不需要 CAXA、不需要 DXF 转换。

原理(2026-11-02 逆向得出, 用 125 组 exb+dxf 对照验证):
  exb = OLE 复合文档; 图形库在 *BlockStg/*Blk_* 流里, [20字节头][zlib], 解压后 256KB。
  文字实体记录以 53 44 49 b3 ("SDI\xb3") 开头, 记录内有:
    [uint32 字节长][UTF-16LE 文本] ... [float64 x][float64 y](插入点, x 与 DXF 差 <2mm 锚点语义)
  坐标判定: 字符串之后、下一条记录之前, 第一对"两个值都 >= 5mm"的 float64。

输出与 read_ht2.texts() 同构: [(文本, 'MTEXT', x, y)], 现有流程可直接换用。
"""
import os, re, struct, zlib

MARK = re.escape(b"\x53\x44\x49\xb3")
BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ue000-\uf8ff\ufffe\uffff]")
SOME = re.compile(r"[\u4e00-\u9fff0-9A-Za-z]")
# EXB 特有转义: %c=Ø %x=× %d=° %p=± (DXF 里是 %%c/%%D, CAXA 库内存单百分号)
ESC = [("%c", "Ø"), ("%C", "Ø"), ("%x", "×"), ("%X", "×"),
       ("%d", "°"), ("%D", "°"), ("%p", "±"), ("%P", "±")]


def _blocks(exb_path):
    import olefile
    out = []
    f = olefile.OleFileIO(exb_path)
    for e in f.listdir(streams=True):
        p = "/".join(e)
        if not p.startswith("*BlockStg/"):
            continue
        try:
            d = f.openstream(p).read()
        except Exception:
            continue
        k = d.find(b"\x78\x9c")
        if k < 0:
            continue
        try:
            out.append(zlib.decompress(d[k:]))
        except Exception:
            pass
    f.close()
    return out


def _strings_in(seg):
    """记录内所有 [uint32 len][UTF-16LE] 文本, 返回 [(off_in_seg, text)]"""
    res, i, n = [], 0, len(seg)
    while i < n - 8:
        L = struct.unpack_from("<I", seg, i)[0]
        if 4 <= L <= 1600 and L % 2 == 0 and i + 4 + L <= n:
            try:
                s = seg[i + 4:i + 4 + L].decode("utf-16-le")
            except UnicodeDecodeError:
                i += 2
                continue
            s = s.rstrip("\x00")          # 长度按 4 字节补齐, 尾部 NUL 是填充
            if BAD.search(s) or not SOME.search(s) or not s.strip():
                i += 2
                continue
            res.append((i, s))
            i += 4 + L
            continue
        i += 2
    return res


def _first_xy(seg, start, end):
    """[start,end) 内选"最像插入点"的 float64 对。
    记录里字符串后先有 字高/字宽/行距 等小数值, 再到坐标;
    取所有"两值都>=5"的候选中 x+y 最大的 (坐标都是几百~几千, 格式参数最多几十)。"""
    best = None
    off = start
    while off + 16 <= end:
        a, b = struct.unpack_from("<dd", seg, off)
        if 5.0 <= a <= 1e6 and 5.0 <= b <= 1e6:
            if best is None or a + b > best[0] + best[1]:
                best = (a, b)
        off += 1
    return best


def parse(exb_path):
    """返回 [(text, 'MTEXT', x, y)]"""
    out = []
    for buf in _blocks(exb_path):
        marks = [m.start() for m in re.finditer(MARK, buf)]
        for k, p0 in enumerate(marks):
            p1 = marks[k + 1] if k + 1 < len(marks) else min(len(buf), p0 + 4000)
            seg = buf[p0:p1]
            for off, s0 in _strings_in(seg):
                # EXB 用单百分号转义(%c=Ø %x=×), 先转成 Unicode;
                # 再复用 read_ht2.clean() 剥 MTEXT 格式码(\pxt5; 等),
                # 保证 EXB 与 DXF 文本完全同构, 斜杠对/尺寸正则都能命中。
                s = s0
                for a, b in ESC:
                    if a in s:
                        s = s.replace(a, b)
                import read_ht2
                t = read_ht2.clean(s)
                if not t:
                    continue
                # 注意: 偏移必须用替换前的原始长度 s0 (ESC 替换会改变长度)
                xy = _first_xy(seg, off + 4 + len(s0) * 2,
                               min(len(seg) - 16, off + 4 + len(s0) * 2 + 400))
                if xy:
                    out.append((t, "MTEXT", xy[0], xy[1]))
    return out


def texts(path, *a, **k):
    """read_ht2.texts() 兼容入口"""
    return parse(path)


def extract_one(exb_path):
    """借用 read_ht2.extract_one 的全部判读规则, 只是文本来源换成 EXB 直读"""
    import read_ht2
    orig = read_ht2.texts
    read_ht2.texts = lambda p: parse(p)
    try:
        return read_ht2.extract_one(exb_path)
    finally:
        read_ht2.texts = orig


def run(exb_dir, out_csv=None):
    """扫描目录里的 .exb → ht_dims.csv (与 read_ht2.run 同构)。
    验收(2026-11-03 修正): 125 组 exb+dxf 精确同名对照, extract_one 结果 125/125 (100%) 一致。
      之前 88% 是"\\pxt5; 格式码残留"与"6 位炉号前缀错配"两个假象, 均已修复。
    注意: 一致性是"解析层"结论, 验证集只覆盖批B 一批; 据此出图仍一律标待核稿
      (见 make_batch.py 的 exb_mode), 业务底线是尺寸错一个数字就是报废件。
    已知唯一取不到尺寸的: 300003 (195×160×160 带小数的方块件), 走 read_ht_block 路径。
    """
    import csv, glob
    import read_ht2
    files = sorted(glob.glob(os.path.join(exb_dir, "*.exb")))
    if not files:
        print("目录里没有 exb:", exb_dir)
        return []
    rows = []
    for i, f in enumerate(files, 1):
        try:
            r = extract_one(f)
        except Exception as e:
            r = {"炉号": "", "来源": os.path.basename(f), "外径": "", "内径": "",
                 "长度": "", "实心": "", "规格候选": "", "含取样": "",
                 "全部候选": "", "多行": "", "命中原文": "ERR:%s" % e}
        r["来源"] = "EXB直读: " + r.get("来源", "")
        rows.append(r)
        if r["外径"]:
            print("  %3d/%d 炉号%-8s Ø%s/%s×%-6s «%s»" % (
                i, len(files), r["炉号"], r["外径"], r["内径"] or "-", r["长度"],
                r["命中原文"][:34]), flush=True)
        else:
            print("  %3d/%d 炉号%-8s 未取到(方块件表格卡/无尺寸行?) %s" % (
                i, len(files), r["炉号"], os.path.basename(f)[:30]), flush=True)
    hit = sum(1 for x in rows if x["外径"])
    out = out_csv or os.path.join(os.path.dirname(exb_dir.rstrip("\\/")), "ht_dims.csv")
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print("\n%d 份 EXB 直读, 解析出尺寸 %d, 未解析 %d -> %s" % (len(rows), hit, len(rows) - hit, out))
    return rows


if __name__ == "__main__":
    import sys
    ts = parse(sys.argv[1])
    print("文本 %d 条" % len(ts))
    for t in ts[:50]:
        print("%-30r %10.2f %10.2f" % (t[0][:28], t[2], t[3]))
