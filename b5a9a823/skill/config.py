# -*- coding: utf-8 -*-
"""单位配置层 —— 换单位不改代码, 只换一份 profile JSON。

查找顺序(优先级从高到低):
  1) 环境变量 FORGE_PROFILE 指向的 json (run_batch.py 会自动把批次目录的设成它)
  2) 命令行参数里那个批次目录中的 unit_profile.json
  3) skill/profiles/default.json
  4) 内置 DEFAULT (一重口径, 保证已有批次行为完全不变)

profile 字段说明见 profiles/default.json 同目录的 README.md。
注意: 正则在模块 import 时编译, 所以 profile 必须在进程启动前确定;
      run_batch.py 用环境变量把它传给所有子进程, 直接跑单个脚本时由 (2) 自动探测。
"""
import os, sys, json, re

HERE = os.path.dirname(os.path.abspath(__file__))

# 内置默认 = 一重口径, 与 2026-11-03 定稿代码逐字一致
DEFAULT = {
    "name": "一重",
    # 月份位: 1~9 月用数字, 10/11/12 月用 A/B/C; OCR 场景额外允许 8(B 的常见误读, 由图号字典纠正)
    # 2026-11-05: 质保号可拆分成 -1/-2/-3 子号(同图号不同规格/件数), 图号捕获要带可选后缀
    "re_draw":        r"[Ff]\s*[0-9B][0-9AB][1-9ABC]\s*\d{4}(?:\s*[-—]\s*\d{1,2})?",
    "re_draw_ocr":    r"[Ff]\d{2}[1-9ABC8]\d{4}(?:\s*[-—–]\s*\d{1,2})?",
    "re_draw_anchor": r"(F\d{2}[1-9ABC]\d{4})",
    "re_draw_qty":    r"\bF\d{2}[0-9A-Z]\d{3,5}(?:-\d{1,2})?\b",
    "re_range":       r"([FG]\d{2}[1-9ABC])(\d{4})[~-](\d{4})",
    "re_pdf_name":    r"[FG]\d{2}[1-9ABC]\d{4}",
    "re_lu":          r"2[3-6]\d{4}",
    # 炉号(批次)带子号后缀: 248421-1、248446-CR、248446重热、248446二次重热 (2026-11-05)
    # 前后加数字边界: 防止 8 位印章号(如 炉号X)被截前 6 位误读 (2026-11-13)
    "re_lu_suf":      r"(?<!\d)2[3-6]\d{4}(?:\s*[-—]\s*[0-9A-Za-z]{1,3}|二次重热|重热|返修|回火|正火)?(?!\d)",
    "re_lu_qty":      r"\b(2[3-6]\d{5}(?:-\d{1,2})?)\b",
    "re_lu7":         r"2[0-9]{7}",
    "materials":      r"(SA508[ -]?Gr[.\s]*3|42CrMo|20MnMo|45#|SA105|SA350|Q345R|16Mn)",
    "cn_font":        r"C:/Windows/Fonts/simsun.ttc",
    "en_font":        r"C:/Windows/Fonts/arial.ttf"
}


def _guess_from_argv():
    """命令行参数里第一个存在的目录, 若含 unit_profile.json 就用它。"""
    for a in sys.argv[1:]:
        if os.path.isdir(a):
            p = os.path.join(a, "unit_profile.json")
            if os.path.exists(p):
                return p
    return None


def _load():
    p = os.environ.get("FORGE_PROFILE") or _guess_from_argv() \
        or os.path.join(HERE, "profiles", "default.json")
    d = dict(DEFAULT)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8-sig") as f:
                d.update(json.load(f))
        except Exception as e:
            print("[config] profile 读取失败 %s (%s), 改用内置默认" % (p, e))
    d["_path"] = p
    return d


P = _load()


def pat(key):
    """取正则字符串(未编译)"""
    return P[key]


def rx(key):
    """取已编译正则"""
    return re.compile(P[key])


def get(key, default=None):
    return P.get(key, default)


def norm_lu(s):
    """炉号(批次)规范化: 去空格, 破折号/长横统一成半角 '-', 大写。
    248421 — 1 -> 248421-1; 248446二次重热 原样保留。"""
    t = re.sub(r"\s+", "", str(s or ""))
    t = t.replace("—", "-").replace("－", "-").replace("_", "-")
    return t.upper()
