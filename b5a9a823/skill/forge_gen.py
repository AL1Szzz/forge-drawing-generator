# -*- coding: utf-8 -*-
"""
锻件尺寸图自动生成 —— 试点脚本
输入: 产品图纸PDF(扫描) + 一份JSON参数(热处理尺寸/锻件尺寸/图号等)
输出: 与手工示范同样式的锻件尺寸图PDF

用法: python forge_gen.py config.json
"""
import json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pymupdf as fitz
from PIL import Image, ImageEnhance, ImageFont
from config import get as cfg_get

CN_FONT = cfg_get("cn_font")
EN_FONT = cfg_get("en_font")
_font_cache = {}


def text_w(text, fs, fontfile):
    """用 PIL 精确测量文字宽度(pt), 支持中文"""
    key = (fontfile, int(fs * 4))
    f = _font_cache.get(key)
    if f is None:
        f = _font_cache[key] = ImageFont.truetype(fontfile, int(fs * 4))
    return f.getlength(text) / 4.0


# ---------- 基础绘图工具 ----------
def arrow(page, p1, p2, w=0.9, size=7.0):
    """在 p1->p2 方向画箭头(实心), p2 为箭头尖端"""
    ang = math.atan2(p2.y - p1.y, p2.x - p1.x)
    a1 = ang + math.radians(160)
    a2 = ang - math.radians(160)
    tip = p2
    b1 = fitz.Point(tip.x + size * math.cos(a1), tip.y + size * math.sin(a1))
    b2 = fitz.Point(tip.x + size * math.cos(a2), tip.y + size * math.sin(a2))
    sh = page.new_shape()
    sh.draw_polyline([tip, b1, b2, tip])
    sh.finish(color=(0, 0, 0), fill=(0, 0, 0), width=w)
    sh.draw_line(p1, tip)
    sh.finish(color=(0, 0, 0), width=w)
    sh.commit()


def _asize(span):
    """箭头长度随尺寸线长度自适应, 保证不越界"""
    return max(3.0, min(9.0, span * 0.22))


def dim_h(page, y, x1, x2, text, fs=30, text_dy=-8):
    """箭头始终向内, 尖端落在尺寸界线上(同示范图)"""
    span = abs(x2 - x1)
    a = _asize(span)
    mid = (x1 + x2) / 2.0
    arrow(page, fitz.Point(x1 + a, y), fitz.Point(x1, y), size=a)
    arrow(page, fitz.Point(x2 - a, y), fitz.Point(x2, y), size=a)
    page.draw_line(fitz.Point(x1 + a, y), fitz.Point(x2 - a, y), color=(0, 0, 0), width=0.9)
    tw = text_w(text, fs, EN_FONT)
    page.insert_text(fitz.Point(mid - tw / 2, y + text_dy), text,
                     fontsize=fs, fontfile=EN_FONT, fontname="helv", color=(0, 0, 0))


def dim_v(page, x, y1, y2, text, fs=30, mode="right"):
    span = abs(y2 - y1)
    a = _asize(span)
    mid = (y1 + y2) / 2.0
    arrow(page, fitz.Point(x, y1 + a), fitz.Point(x, y1), size=a)
    arrow(page, fitz.Point(x, y2 - a), fitz.Point(x, y2), size=a)
    page.draw_line(fitz.Point(x, y1 + a), fitz.Point(x, y2 - a), color=(0, 0, 0), width=0.9)
    tw = text_w(text, fs, EN_FONT)
    if mode == "left":
        page.insert_text(fitz.Point(x - fs - 2, mid + tw / 2), text, fontsize=fs,
                         fontfile=EN_FONT, fontname="helv", color=(0, 0, 0), rotate=90)
    elif mode == "right":
        page.insert_text(fitz.Point(x + fs * 0.85, mid + tw / 2), text, fontsize=fs,
                         fontfile=EN_FONT, fontname="helv", color=(0, 0, 0), rotate=90)
    else:
        th = fs * 0.72
        page.draw_rect(fitz.Rect(x - th / 2 - 2, mid - th / 2 - 1, x + th / 2 + 2, mid + th / 2 + 1),
                       color=None, fill=(1, 1, 1))
        page.draw_line(fitz.Point(x, y1 + a), fitz.Point(x, mid - th / 2 - 1), color=(0, 0, 0), width=0.9)
        page.draw_line(fitz.Point(x, mid + th / 2 + 1), fitz.Point(x, y2 - a), color=(0, 0, 0), width=0.9)
        page.insert_text(fitz.Point(x - th / 2, mid + th / 2), text, fontsize=fs,
                         fontfile=EN_FONT, fontname="helv", color=(0, 0, 0))


# ---------- 锻件示意图(筒体/管段类) ----------
def draw_tube(page, cx, cy, avail_w, avail_h, fo, fi, L, ht_do, ht_di, fs=26, cfg_ref=None, cfg_xr=None, cfg_vmode="float", cfg_exag=None):
    """按可用画框自动等比缩放, 居中绘制筒体剖切示意"""
    # 需要容纳: 宽 = fo + 左右尺寸线余量, 高 = L + 上下尺寸线余量
    sx = avail_w / (fo + 0.42 * fo)
    sy = avail_h / (L + 0.30 * L)
    scale = min(sx, sy)
    # 示意图可用 exag>1 把壁厚夸大(仅影响内径线视觉位置, 标注值不变)
    exag = float(cfg_exag or 1.0)
    # 美观优先(用户 2026-11-03, 适用于一切图形): 扁环形件(如 Ø900/Ø500×200)
    # 按真实比例高度只剩几 pt, 像一条缝 —— 宽高比超过 AR_MAX 时拉伸高度,
    # 只影响视觉, 标注值不变; 壁厚过厚时(相对件高)相应回退夸大倍数
    AR_MAX = 4.5
    Wo_true, H_true = fo * scale, L * scale
    H = H_true
    if Wo_true / max(H_true, 0.01) > AR_MAX:
        H = min(Wo_true / AR_MAX, avail_h * 0.8)
    wall = min((fo - fi) / 2.0 * exag * scale, H * 0.55)   # 视觉壁厚不超过件高的 55%
    Wo, Wi = fo * scale, fo * scale - 2 * wall
    x1, x2 = cx - Wo / 2, cx + Wo / 2
    y1, y2 = cy - H / 2, cy + H / 2
    ib1, ib2 = cx - Wi / 2, cx + Wi / 2
    w = 1.1

    # 剖面线(左右两壁, 45° 线段, 数学裁剪到矩形内)
    for a, b in ((x1, ib1), (ib2, x2)):
        wid = b - a
        step = max(5.0, min(13.0, wid / 4.0))
        c = a + y1                       # c = x + y, 副对角线(左下-右上)
        cmax = b + y2
        while c <= cmax:
            xs = max(a, c - y2)
            xe = min(b, c - y1)
            if xe > xs + 0.01:
                page.draw_line(fitz.Point(xs, c - xs), fitz.Point(xe, c - xe),
                               color=(0, 0, 0), width=0.7)
            c += step

    # 外轮廓 + 内孔
    page.draw_rect(fitz.Rect(x1, y1, x2, y2), color=(0, 0, 0), width=w)
    for a in (ib1, ib2):
        page.draw_line(fitz.Point(a, y1), fitz.Point(a, y2), color=(0, 0, 0), width=w)
    for yb in (y1, y2):
        page.draw_line(fitz.Point(x1, yb), fitz.Point(ib1, yb), color=(0, 0, 0), width=w)
        page.draw_line(fitz.Point(ib2, yb), fitz.Point(x2, yb), color=(0, 0, 0), width=w)

    # 热处理尺寸虚线
    dash = (3, 2)
    # 参考轮廓: 默认取"成品尺寸"(热处理报告上的产品名义尺寸), 必须落在锻件轮廓之内
    ro = cfg_ref.get("outer", ht_do) if cfg_ref else ht_do
    ri = cfg_ref.get("inner", ht_di) if cfg_ref else ht_di
    ho1, ho2 = cx - ro * scale / 2, cx + ro * scale / 2
    hi1, hi2 = cx - ri * scale / 2, cx + ri * scale / 2
    if cfg_ref and cfg_ref.get("show", True) and ro and ri:
        page.draw_rect(fitz.Rect(ho1, y1, ho2, y2), color=(0, 0, 0), width=0.9, dashes=dash)
        page.draw_line(fitz.Point(hi1, y1), fitz.Point(hi1, y2), color=(0, 0, 0), width=0.9, dashes=dash)
        page.draw_line(fitz.Point(hi2, y1), fitz.Point(hi2, y2), color=(0, 0, 0), width=0.9, dashes=dash)

    # 中心线
    page.draw_line(fitz.Point(cx, y1 - 16), fitz.Point(cx, y2 + 16),
                   color=(0, 0, 0), width=0.7, dashes=(10, 3, 2, 3))

    # 尺寸标注
    dim_h(page, y1 - 40, x1, x2, "Ø%d" % fi, fs=fs)
    page.draw_line(fitz.Point(ib1, y1 - 4), fitz.Point(ib1, y1 - 46), color=(0, 0, 0), width=0.7)
    page.draw_line(fitz.Point(ib2, y1 - 4), fitz.Point(ib2, y1 - 46), color=(0, 0, 0), width=0.7)
    dim_h(page, y2 + 46, x1, x2, "Ø%d" % fo, fs=fs)
    page.draw_line(fitz.Point(x1, y2 + 4), fitz.Point(x1, y2 + 52), color=(0, 0, 0), width=0.7)
    page.draw_line(fitz.Point(x2, y2 + 4), fitz.Point(x2, y2 + 52), color=(0, 0, 0), width=0.7)
    xr = x2 + cfg_xr if cfg_xr is not None else x2 + 90
    dim_v(page, xr, y1, y2, "%d" % L, fs=fs, mode=cfg_vmode)
    page.draw_line(fitz.Point(x2, y1), fitz.Point(xr + 8, y1), color=(0, 0, 0), width=0.7)
    page.draw_line(fitz.Point(x2, y2), fitz.Point(xr + 8, y2), color=(0, 0, 0), width=0.7)
    return scale



# ---------- 图框与标题栏(矢量重绘, 替代扫描背景) ----------
def draw_frame(page, W, H, cfg):
    """按实测坐标画外框 + 标题栏网格"""
    fb = cfg.get("frame", {"x0": 61, "y0": 74, "x1": 563.5, "y1": 795})
    x0, y0, x1, y1 = fb["x0"], fb["y0"], fb["x1"], fb["y1"]
    w = 1.4
    page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=(0, 0, 0), width=w)
    tb = cfg.get("title_block", {})
    rows = tb.get("rows", [692, 731.5, 769, 795])
    cols = tb.get("cols", [61, 90.8, 174.1, 259.3, 343.1, 385.4, 470.4, 526.2, 563.5])
    for y in rows[1:-1]:
        page.draw_line(fitz.Point(cols[0], y), fitz.Point(cols[-1], y), color=(0, 0, 0), width=1.0)
    page.draw_line(fitz.Point(cols[0], rows[-1]), fitz.Point(cols[-1], rows[-1]),
                   color=(0, 0, 0), width=w)
    vtop = tb.get("vtop", rows[1])          # 竖线起点: 默认标签行顶, 不穿过规格文字
    for c in cols[1:-1]:
        page.draw_line(fitz.Point(c, vtop), fitz.Point(c, rows[-1]), color=(0, 0, 0), width=1.0)
    page.draw_line(fitz.Point(cols[0], vtop), fitz.Point(cols[-1], vtop), color=(0, 0, 0), width=1.0)
    return (rows, cols)


def cell_text(page, rows, cols, r0, r1, c0, c1, txt, fs=12, dy_frac=0.68):
    txt = str(txt)
    if not txt:
        return
    cn = any("一" <= ch <= "鿿" for ch in txt)
    ff = CN_FONT if cn else EN_FONT
    fn = "cn" if cn else "helv"
    box_w = (cols[c1] - cols[c0]) - 4        # 名称/数量等长文本自动缩字号, 不撑破格子
    while fs > 7 and text_w(txt, fs, ff) > box_w:
        fs -= 0.5
    x = (cols[c0] + cols[c1]) / 2 - text_w(txt, fs, ff) / 2
    y = rows[r1] - (rows[r1] - rows[r0]) * (1 - dy_frac)
    page.insert_text(fitz.Point(x, y), txt, fontsize=fs, fontfile=ff, fontname=fn)


def draw_solid(page, cx, cy, avail_w, avail_h, D, L, fs=26, xr_extra=None, phi=True):
    """实心棒/饼: 矩形剖面 + 剖面线 + ØD 与 L 两向标注。
    示意图不按真实比例: 长径比 L/D 截断到 [1.2, 3.6], 以版面好看为准(用户 2026-11-02)。"""
    ar = max(1.2, min(3.6, L / float(D)))
    W = min(avail_w / 1.9, (avail_h / 1.35) / ar)
    H = W * ar
    x1, x2 = cx - W / 2, cx + W / 2
    y1, y2 = cy - H / 2, cy + H / 2
    page.draw_rect(fitz.Rect(x1, y1, x2, y2), color=(0, 0, 0), width=1.2)
    c = x1 + y1
    step = max(5.0, min(13.0, W / 4.0))
    while c <= x2 + y2:
        xs, xe = max(x1, c - y2), min(x2, c - y1)
        if xe > xs + 0.01:
            page.draw_line(fitz.Point(xs, c - xs), fitz.Point(xe, c - xe), color=(0, 0, 0), width=0.7)
        c += step
    page.draw_line(fitz.Point(cx, y1 - 16), fitz.Point(cx, y2 + 16),
                   color=(0, 0, 0), width=0.7, dashes=(10, 3, 2, 3))
    dim_h(page, y1 - 40, x1, x2, ("\u00d8%d" if phi else "%d") % D, fs=fs)
    page.draw_line(fitz.Point(x1, y1 - 4), fitz.Point(x1, y1 - 46), color=(0, 0, 0), width=0.7)
    page.draw_line(fitz.Point(x2, y1 - 4), fitz.Point(x2, y1 - 46), color=(0, 0, 0), width=0.7)
    xr = x2 + (xr_extra if xr_extra is not None else fs * 1.6)
    dim_v(page, xr, y1, y2, "%d" % L, fs=fs, mode="right")
    page.draw_line(fitz.Point(x2, y1), fitz.Point(xr + 8, y1), color=(0, 0, 0), width=0.7)
    page.draw_line(fitz.Point(x2, y2), fitz.Point(xr + 8, y2), color=(0, 0, 0), width=0.7)


def draw_disc(page, cx, cy, avail_w, avail_h, D, L, fs=26):
    """圆饼/盘类(锻坯为实心圆盘, 如 Ø323×68): 上圆(俯视 ØD) 下矩形(侧视 D×L),
    两视图同比例、左右对齐(用户 2026-11-02 第9条要求, 仿方块双视图风格)。"""
    r2 = max(0.16, min(0.55, L / float(D)))       # 侧视图 高/宽
    gap = fs * 1.5
    ypad = fs * 2.0
    W = min(avail_w * 0.68, (avail_h - ypad - gap) / (1.0 + r2))
    W = max(W, 60.0)
    H = W * r2
    # --- 俯视: 圆 ---
    cy1 = cy - (H + gap) / 2.0                    # 让 [圆+gap+矩形] 整体居中
    R = W / 2.0
    page.draw_circle(fitz.Point(cx, cy1), R, color=(0, 0, 0), width=1.3)
    page.draw_line(fitz.Point(cx - R - 12, cy1), fitz.Point(cx + R + 12, cy1),
                   color=(0, 0, 0), width=0.7, dashes=(10, 3, 2, 3))
    page.draw_line(fitz.Point(cx, cy1 - R - 12), fitz.Point(cx, cy1 + R + 12),
                   color=(0, 0, 0), width=0.7, dashes=(10, 3, 2, 3))
    dy = fs * 1.15
    dim_h(page, cy1 - R - dy, cx - R, cx + R, "Ø%d" % D, fs=fs)
    page.draw_line(fitz.Point(cx - R, cy1 - 4), fitz.Point(cx - R, cy1 - R - dy - 4),
                   color=(0, 0, 0), width=0.7)
    page.draw_line(fitz.Point(cx + R, cy1 - 4), fitz.Point(cx + R, cy1 - R - dy - 4),
                   color=(0, 0, 0), width=0.7)
    # --- 侧视: 矩形(全剖视图风格, 画剖面线) ---
    y1, y2 = cy1 + R + gap, cy1 + R + gap + H
    x1, x2 = cx - W / 2, cx + W / 2
    page.draw_rect(fitz.Rect(x1, y1, x2, y2), color=(0, 0, 0), width=1.2)
    c = x1 + y1
    step = max(5.0, min(13.0, W / 5.0))
    while c <= x2 + y2:
        xs, xe = max(x1, c - y2), min(x2, c - y1)
        if xe > xs + 0.01:
            page.draw_line(fitz.Point(xs, c - xs), fitz.Point(xe, c - xe), color=(0, 0, 0), width=0.7)
        c += step
    xr = x2 + fs * 1.6
    dim_v(page, xr, y1, y2, "%d" % L, fs=fs, mode="right")
    page.draw_line(fitz.Point(x2, y1), fitz.Point(xr + 8, y1), color=(0, 0, 0), width=0.7)
    page.draw_line(fitz.Point(x2, y2), fitz.Point(xr + 8, y2), color=(0, 0, 0), width=0.7)


def draw_block(page, cx, cy, avail_w, avail_h, A, B, L, fs=26):
    """长方体: 上下两视图 —— 上为俯视(A×B), 下为主视(A×L), 两图左右对齐。
    示意图不按真实比例: 两个视图的高宽比分别截断到好看区间(用户 2026-11-02),
    数值仍是真实尺寸, 不标公差。"""
    r1 = max(0.44, min(0.78, B / float(A)))        # 俯视图 深/宽
    r2 = max(0.46, min(1.15, L / float(A)))        # 主视图 高/宽
    gap = fs * 1.3                                  # 两视图间距
    ypad = fs * 1.9                                 # 上方尺寸线留白
    xpad = fs * 1.9                                 # 右侧竖向尺寸留白
    W = min(avail_w - xpad * 2, (avail_h - ypad - gap) / (r1 + r2))
    W = max(W, 40.0)
    H1, H2 = W * r1, W * r2
    total = H1 + gap + H2
    x1, x2 = cx - W / 2, cx + W / 2
    y0 = cy - total / 2 + ypad * 0.25
    y1a, y1b = y0, y0 + H1                          # 俯视图
    y2a, y2b = y0 + H1 + gap, y0 + total            # 主视图
    for a, b in ((y1a, y1b), (y2a, y2b)):
        page.draw_rect(fitz.Rect(x1, a, x2, b), color=(0, 0, 0), width=1.3)
    # 宽度 A: 标注在俯视图上方
    dy = fs * 1.15
    dim_h(page, y1a - dy, x1, x2, "%d" % A, fs=fs)
    page.draw_line(fitz.Point(x1, y1a - 4), fitz.Point(x1, y1a - dy - 4),
                   color=(0, 0, 0), width=0.7)
    page.draw_line(fitz.Point(x2, y1a - 4), fitz.Point(x2, y1a - dy - 4),
                   color=(0, 0, 0), width=0.7)
    # 深度 B / 高度 L: 各标注在右侧, 文字竖排自下而上(同圆棒图风格)
    xr = x2 + fs * 1.6
    for yy0, yy1, v in ((y1a, y1b, B), (y2a, y2b, L)):
        dim_v(page, xr, yy0, yy1, "%d" % v, fs=fs, mode="right")
        page.draw_line(fitz.Point(x2, yy0), fitz.Point(xr + 8, yy0), color=(0, 0, 0), width=0.7)
        page.draw_line(fitz.Point(x2, yy1), fitz.Point(xr + 8, yy1), color=(0, 0, 0), width=0.7)


# ---------- 主流程 ----------
def build(cfg):
    src, spage = cfg["source_pdf"], int(cfg.get("source_page", 0)) - 1
    out = cfg["output_pdf"]
    doc = fitz.open(src)
    page = doc[spage]
    rect = page.rect

    # 1) 底图: clean=纯白底(矢量重绘图框); faded=原扫描淡背景
    mode = cfg.get("background", "clean")
    tmp = None
    new = fitz.open()
    npg = new.new_page(width=rect.width, height=rect.height)
    if mode == "faded":
        pix = page.get_pixmap(dpi=200)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
        img = ImageEnhance.Brightness(img).enhance(1.55)
        img = ImageEnhance.Contrast(img).enhance(0.45)
        tmp = out + ".bg.png"
        img.convert("RGB").save(tmp)
        npg.insert_image(rect, filename=tmp)
        frame = draw_frame(npg, rect.width, rect.height, cfg)
    else:
        npg.draw_rect(rect, color=None, fill=(1, 1, 1))
        frame = draw_frame(npg, rect.width, rect.height, cfg)
    rows, cols = frame

    # 3) 标题条(白底 + 黑字)
    title = cfg.get("title", "无锡市法兰锻造有限公司锻件尺寸图")
    fs_t = 30
    # 标题过长则自动缩字号, 保证不溢出图框
    maxw = rect.width * 0.90
    while fs_t > 14 and text_w(title, fs_t, CN_FONT) > maxw:
        fs_t -= 1
    tw = text_w(title, fs_t, CN_FONT)
    npg.insert_text(fitz.Point((rect.width - tw) / 2, 52), title,
                    fontsize=fs_t, fontfile=CN_FONT, fontname="cn")

    # 4) 锻件示意图
    p = cfg["part"]
    area = cfg.get("draw_area", [70, 90, 525, 630])
    ax0, ay0, ax1, ay1 = area
    if p.get("shape", "tube") == "block":
        draw_block(npg, (ax0 + ax1) / 2, (ay0 + ay1) / 2, ax1 - ax0, ay1 - ay0,
                   p["A"], p["B"], p["L"], fs=cfg.get("dim_fontsize", 26))
    elif p.get("shape", "tube") == "disc":
        draw_disc(npg, (ax0 + ax1) / 2, (ay0 + ay1) / 2, ax1 - ax0, ay1 - ay0,
                  p["D"], p["L"], fs=cfg.get("dim_fontsize", 26))
    elif p.get("shape", "tube") == "solid":
        draw_solid(npg, (ax0 + ax1) / 2, (ay0 + ay1) / 2, ax1 - ax0, ay1 - ay0,
                   p["D"], p["L"], fs=cfg.get("dim_fontsize", 26),
                   xr_extra=cfg.get("dim_v_offset"), phi=not p.get("no_phi"))
    else:
        draw_tube(npg, (ax0 + ax1) / 2, (ay0 + ay1) / 2, ax1 - ax0, ay1 - ay0,
                  p["fo"], p["fi"], p["L"], p.get("ht_do"), p.get("ht_di"),
                  fs=cfg.get("dim_fontsize", 26), cfg_ref=cfg.get("ref_contour"),
                  cfg_xr=cfg.get("dim_v_offset"), cfg_vmode=cfg.get("dim_v_mode", "float"),
                  cfg_exag=cfg.get("wall_exaggerate"))

    # 5) 覆盖框 + 重绘文字(图号/规格/炉号等)
    for rb in cfg.get("white_boxes", []):
        npg.draw_rect(fitz.Rect(rb), color=None, fill=(1, 1, 1))
    for t in cfg.get("texts", []):
        x, y, txt, fsz = t[0], t[1], t[2], (t[3] if len(t) > 3 else 14)
        align = t[4] if len(t) > 4 else "left"
        cn = any("一" <= ch <= "鿿" for ch in txt)
        ffile = CN_FONT if cn else EN_FONT
        fname = "cn" if cn else "helv"
        if align == "center":
            x -= text_w(txt, fsz, ffile) / 2
        elif align == "right":
            x -= text_w(txt, fsz, ffile)
        npg.insert_text(fitz.Point(x, y), txt, fontsize=fsz,
                        fontfile=ffile, fontname=fname)

    # 5b) 信息行: 图号/件号/追溯号/材质/炉号 按实测宽度均匀排一行(用户 2026-11-02)
    #     规格串有空余位置就并到这一行末尾(用户 2026-11-03); 塞不下才保持上方单独一行
    ir = cfg.get("info_row")
    spec_above = None
    if ir:
        items = [str(s) for s in ir.get("items", []) if str(s or "").strip()]
        spec = str(ir.get("append_spec") or "").strip()
        y, fsz = ir.get("y", 683), ir.get("fs", 12.5)
        x0, x1 = ir.get("x0", 68), ir.get("x1", 556)

        def _tw(s, size):
            cn = any("一" <= ch <= "鿿" for ch in s) or "～" in s
            return text_w(s, size, CN_FONT if cn else EN_FONT)

        if spec and spec not in items:
            gap_min = 14.0
            used = sum(_tw(s, fsz) for s in items) + _tw(spec, fsz) + gap_min * len(items)
            if used <= (x1 - x0):
                items = items + [spec]
            else:
                spec_above = spec          # 回退: 画在信息行上方单独一行
        if items:
            widths = [_tw(s, fsz) for s in items]
            gap = (x1 - x0 - sum(widths)) / max(1, len(items) - 1)
            cx = x0
            for s, wd in zip(items, widths):
                cn = any("一" <= ch <= "鿿" for ch in s) or "～" in s   # ～ 用中文字体, 免得变"-"
                npg.insert_text(fitz.Point(cx, y), s, fontsize=fsz,
                                fontfile=CN_FONT if cn else EN_FONT,
                                fontname="cn" if cn else "helv")
                cx += wd + gap
        if spec_above is not None:
            cx = 312 - _tw(spec_above, 13) / 2
            cn = any("一" <= ch <= "鿿" for ch in spec_above) or "～" in spec_above
            npg.insert_text(fitz.Point(cx, y - 20), spec_above, fontsize=13,
                            fontfile=CN_FONT if cn else EN_FONT,
                            fontname="cn" if cn else "helv")

    # 6) 标题栏单元格(版本/填图/复核/批准/序号/名称/材质/数量 + 上方两行)
    for cell in cfg.get("cells", []):
        r0, r1, c0, c1, txt = cell[0], cell[1], cell[2], cell[3], cell[4]
        fsz = cell[5] if len(cell) > 5 else 11
        cell_text(npg, rows, cols, r0, r1, c0, c1, txt, fsz)

    # 6b) 签名图片(按列号放入对应单元格)
    for sg in cfg.get("signatures", []):
        cidx, path = sg[0], sg[1]
        pad = sg[2] if len(sg) > 2 else 4
        r0, r1 = (sg[3], sg[4]) if len(sg) > 4 else (1, 2)
        box = fitz.Rect(cols[cidx] + pad, rows[r0] + pad, cols[cidx + 1] - pad, rows[r1] - pad)
        try:
            im = Image.open(path); iw, ih = im.size
            ar = iw / ih
            if box.width / box.height > ar:
                h = box.height; w = h * ar
            else:
                w = box.width; h = w / ar
            cx0 = box.x0 + (box.width - w) / 2
            npg.insert_image(fitz.Rect(cx0, box.y0, cx0 + w, box.y0 + h), filename=path, keep_proportion=True)
        except Exception as e:
            print("  签名插入失败", path, e)

    # 7) 重量(右下角, 图框外)
    if cfg.get("weight"):
        npg.insert_text(fitz.Point(cfg.get("weight_x", 505), cfg.get("weight_y", 812)),
                        cfg["weight"], fontsize=cfg.get("weight_fs", 12),
                        fontfile=EN_FONT, fontname="helv")

    new.save(out, garbage=3, deflate=True)
    if tmp and os.path.exists(tmp):
        os.remove(tmp)
    print("OK ->", out)


if __name__ == "__main__":
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    build(cfg)
