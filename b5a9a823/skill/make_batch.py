# -*- coding: utf-8 -*-
"""
锻件尺寸图批量生成 —— 主调度

流程:
  manifest.csv (图纸页 -> 图号/炉号/规格/重量)
  + ht_dims.csv (炉号 -> 热处理尺寸, 来自 CAXA 导出的 DXF)
  → 按炉号配对 → 套用余量档 → 逐件出图 → 合并为单个 PDF

余量档(用户 2026-10-31 指定): 按锻重
  ≤1T     → +10
  1~3T    → +15
  >3T     → +20
  含义: 热处理尺寸已含一次余量, 热处理后收缩, 故在热处理尺寸基础上再补该值(双边)。
  内径方向取负(孔径做小), 长度方向取正。

用法:
  python make_batch.py <批次目录> [--dry-run]
"""
import os, re, sys, csv, json, subprocess, glob
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from config import rx, norm_lu

TIERS = [(1000, 10), (3000, 15), (float("inf"), 20)]   # (上限kg, 双边补加mm)
MIN_SOLID_D = 50                                     # 圆棒原始直径下限(用户 2026-11-13 改口径)
BLOCK_ADD = 10                                       # 方块件: 热处理尺寸长宽高每向+10(用户 2026-11-02)
FRAME = {"x0": 61, "y0": 74, "x1": 563.5, "y1": 807.5}
ROWS = [703.5, 743, 782, 807.5]
# 标题栏: 版本|填图|复核|批准|名称|材质 (已去掉"序号/数量"两列, 2026-11-02)
COLS = [61, 90, 190, 290, 390, 500, 563.5]
SIG_DIR = "C:/Users/Administrator/Pictures/新建文件夹"
SIGS = [[1, SIG_DIR + "/捕2获.PNG"], [2, SIG_DIR + "/1捕1获.PNG"], [3, SIG_DIR + "/捕获.PNG"]]


def tier_of(mass_kg):
    if mass_kg is None:
        return None, "缺重量"
    for up, a in TIERS:
        if mass_kg <= up:
            return a, ("≤1T" if up == 1000 else ("1~3T" if up == 3000 else ">3T"))
    return None, "超范围"


def _num(x):
    v = float(x)
    return int(v) if v == int(v) else round(v, 1)


_D = r"[ØøΦ⌀φ]?"
_N = r"\d{2,5}(?:\.\d+)?"
RE_RAW_TUBE = re.compile(r"(%s\s*%s)\s*[/]\s*(%s\s*%s)\s*[×xX*]\s*(%s)" % (_D, _N, _D, _N, _N))
RE_RAW_SOLID = re.compile(r"(%s)\s*(%s)\s*[×xX*]\s*(%s)" % (_D, _N, _N))


def parse_spec(row, raw=None):
    """manifest 拆列(外径/内径/长度); 或直接解析规格串如 'Ø941/ø350×838'"""
    if raw:
        m = RE_RAW_TUBE.search(raw)
        if m:
            a = _num(re.sub(r"[^\d.]", "", m.group(1)))
            b = _num(re.sub(r"[^\d.]", "", m.group(2)))
            return {"shape": "tube", "od": max(a, b), "id": min(a, b), "L": _num(m.group(3))}
        m = RE_RAW_SOLID.search(raw)
        if m:
            return {"shape": "solid", "D": _num(m.group(2)), "L": _num(m.group(3))}
        return None
    g = lambda k: (row.get(k) or "").strip()
    od, ln = g("外径"), g("长度")
    if not od or not ln:
        return None
    idd = g("内径")
    try:
        if idd:
            return {"shape": "tube", "od": _num(od), "id": _num(idd), "L": _num(ln)}
        return {"shape": "solid", "D": _num(od), "L": _num(ln)}
    except ValueError:
        return None


def load_csv(path, key):
    if not os.path.exists(path):
        return {}
    d = defaultdict(list)
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            k = (r.get(key) or "").strip()
            if k:
                d[k].append(r)
    return d


def build(batch_dir, dry=False):
    man_p = os.path.join(batch_dir, "manifest.csv")
    ht_p = os.path.join(batch_dir, "ht_dims.csv")
    exb_mode = False
    if not os.path.exists(ht_p):
        # 热处理卡目录约定(2026-11-03 定):
        #   转换了 DXF -> DXF 直接放 批次\热处理工艺\, 原 EXB 放 批次\热处理工艺\exb\
        #   没转换     -> EXB 直接放 批次\热处理工艺\
        # 兼容旧批次: DXF 也可放 批次\DXF\ 或 批次\热处理DXF\, EXB 也可放 批次\EXB\
        def _has(d, ext):
            return os.path.isdir(d) and glob.glob(os.path.join(d, ext))
        dxf_dirs = [d for d in (os.path.join(batch_dir, "热处理工艺"),
                                os.path.join(batch_dir, "DXF"),
                                os.path.join(batch_dir, "热处理DXF")) if _has(d, "*.dxf")]
        exb_dirs = [d for d in (os.path.join(batch_dir, "热处理工艺", "exb"),
                                os.path.join(batch_dir, "热处理工艺"),
                                os.path.join(batch_dir, "EXB")) if _has(d, "*.exb")]
        if dxf_dirs:
            import read_ht2
            print("热处理卡: DXF @ %s (%d 份) -> 正式稿" % (
                os.path.relpath(dxf_dirs[0], batch_dir), len(glob.glob(os.path.join(dxf_dirs[0], "*.dxf")))))
            read_ht2.run(dxf_dirs[0], out_csv=ht_p)
        elif exb_dirs:
            # 没有 DXF: 直接解析 exb (2026-11-02 逆向打通, 结果一律待核)
            import read_exb
            print("热处理卡: EXB @ %s (%d 份) -> 待核稿" % (
                os.path.relpath(exb_dirs[0], batch_dir), len(glob.glob(os.path.join(exb_dirs[0], "*.exb")))))
            read_exb.run(exb_dirs[0], out_csv=ht_p)
            exb_mode = True
            print("!! EXB 直读模式: 热处理尺寸未经 DXF 校验, 凡据此出图者标待核稿; "
                  "图纸重量行直给件不依赖 EXB, 仍为正式稿")
        else:
            print("!! 未找到热处理卡(DXF/exb), 本批只能走『图纸重量行直给』或『成品尺寸+余量(待核)』")
    if not os.path.exists(man_p):
        print("缺少 manifest.csv, 先跑 build_manifest.py"); return 1
    man = list(csv.DictReader(open(man_p, encoding="utf-8-sig")))
    ht = load_csv(ht_p, "炉号")
    print("图纸行 %d, 热处理尺寸行 %d" % (len(man), sum(len(v) for v in ht.values())))

    # 按 图号+炉号 去重
    # 料单可反查: 批次(炉号) -> 质保号(图号), 补图纸OCR漏掉的图号
    lu2draw = {}
    ld_p = os.path.join(batch_dir, "料单提取.csv")
    if os.path.exists(ld_p):
        for x in csv.DictReader(open(ld_p, encoding="utf-8-sig")):
            for m in rx("re_lu").findall(x.get("批次") or ""):
                lu2draw.setdefault(m, set()).add(x["质保号"])

    jobs, seen = [], set()
    for r in man:
        dh, lu = (r.get("图号") or "").strip(), (r.get("炉号") or "").strip()
        if not dh and lu and len(lu2draw.get(lu, ())) == 1:
            dh = next(iter(lu2draw[lu]))
            r["图号"] = dh
            r["图号置信"] = (r.get("图号置信") or "") + "|料单反查"
        if not dh or not lu:
            continue
        if (dh, lu) in seen:
            continue
        seen.add((dh, lu))
        jobs.append(r)
    print("去重后待生成 %d 件" % len(jobs))

    out_dir = os.path.join(batch_dir, "_锻件图")
    os.makedirs(out_dir, exist_ok=True)
    # 出图前把上一轮的 pdf/json 归档到 _旧稿_xx, 免得旧图混进合并件
    # (dry-run 只看不写, 不归档 —— 2026-11-03 实测发现 dry-run 也会搬走旧 PDF)
    import time, shutil
    _old = [f for f in os.listdir(out_dir)
            if (f.endswith(".pdf") or f.endswith(".json")) and not f.startswith("_")]
    if _old and not dry:
        bak = os.path.join(out_dir, "_旧稿_" + time.strftime("%m%d%H%M"))
        os.makedirs(bak, exist_ok=True)
        for f in _old:
            shutil.move(os.path.join(out_dir, f), os.path.join(bak, f))
        print("旧稿 %d 份 -> %s" % (len(_old), bak))
    # 取样件尺寸(热处理报告里的取样行) —— 加样件要另出一张"质保号+样"
    smp_p = os.path.join(batch_dir, "样件尺寸.csv")
    if not os.path.exists(smp_p) and os.path.isdir(os.path.join(batch_dir, "DXF")):
        try:
            import read_ht_sample
            read_ht_sample.run(os.path.join(batch_dir, "DXF"), out_csv=smp_p)
        except Exception as e:
            print("取样件尺寸提取失败:", e)
    smp = {}
    if os.path.exists(smp_p):
        for x in csv.DictReader(open(smp_p, encoding="utf-8-sig")):
            smp[(x.get("炉号") or "").strip()] = x
    print("取样件尺寸 %d 个炉号" % len(smp))
    # 方块件(长方体)三向尺寸 —— 卡片里"产品/热处理"两列, 锻件图按热处理尺寸每向+10(用户 2026-11-02)
    blk_p = os.path.join(batch_dir, "块件尺寸.csv")
    if not os.path.exists(blk_p) and os.path.isdir(os.path.join(batch_dir, "DXF")):
        try:
            import read_ht_block
            read_ht_block.run(batch_dir)
        except Exception as e:
            print("方块件尺寸提取失败:", e)
    blk = {}
    if os.path.exists(blk_p):
        for x in csv.DictReader(open(blk_p, encoding="utf-8-sig")):
            lu_k = (x.get("炉号") or "").strip()
            try:
                body = (_num2(x["热处理A"]), _num2(x["热处理B"]), _num2(x["热处理C"]))
            except (KeyError, ValueError):
                continue
            if min(body) <= 0:
                continue
            s = (_num2(x["取样A"]), _num2(x["取样B"]), _num2(x["取样C"]))
            blk[lu_k] = {"body": body,
                         "samp": s if min(s) > 0 and s != body else None,
                         "der": (x.get("本体推算") or "").strip() == "1"}
    print("方块件尺寸 %d 个炉号" % len(blk))
    # 合锻棒料: 同炉几件合锻成一根 → 出一张棒料图, 质保号/炉号都写在一起(用户 2026-11-02)
    cf_p = os.path.join(batch_dir, "合锻表.csv")
    if not os.path.exists(cf_p) and os.path.isdir(os.path.join(batch_dir, "DXF")):
        try:
            import read_ht_coforge
            read_ht_coforge.run(batch_dir)
        except Exception as e:
            print("合锻棒料提取失败:", e)
    cf = {}
    if os.path.exists(cf_p):
        for x in csv.DictReader(open(cf_p, encoding="utf-8-sig")):
            lu_k = (x.get("炉号") or "").strip()
            htA, htC = _num2(x.get("热处理A")), _num2(x.get("热处理C"))
            comp = [_num2(v) for v in (x.get("组成件长度") or "").split("|") if str(v).strip().isdigit()]
            if min(htA, htC) <= 0 or not comp:
                continue
            cf[lu_k] = {"shape": x.get("形状") or "round", "D": htA, "L": htC,
                        "comp": comp, "prod": _num2(x.get("产品长度"))}
    print("合锻棒料 %d 个炉号" % len(cf))
    cf_members = {}

    report, made, prov_made, groups = [], [], [], {}
    # 合锻件: 标"与序号N合锻"的行没有单件重量, 档位按同炉号(合锻伙伴)的锻重取(用户 2026-11-02)
    lu_w = {}
    for r0 in jobs:
        _m0 = (r0.get("锻重kg") or "").strip()
        if re.match(r"^[\d.]+$", _m0):
            try:
                lu_w[r0["炉号"]] = max(lu_w.get(r0["炉号"], 0.0), float(_m0))
            except ValueError:
                pass
    for r in jobs:
        dh, lu = r["图号"], r["炉号"]
        # 规格优先取标题栏拆列; 标题栏没有规格行时(如 某批 质保号X 只有成品小尺寸),
        # 用图纸重量行直给串兜底解析
        spec = parse_spec(r) or parse_spec(r, raw=(r.get("图纸锻件尺寸") or "").strip())
        _m = (r.get("锻重kg") or "").strip()
        mass = float(_m) if re.match(r'^[\d.]+$', _m) else None
        # 合锻件: 本行无重量 → 用同炉号伙伴件的重量定档(同炉合锻, 档位粗分下不影响结果)
        derived_note = ""
        if mass is None:
            mc = re.search(r"与序号\s*(\d+)\s*合锻", r.get("原始OCR") or "")
            if mc and lu_w.get(lu):
                mass = lu_w[lu]
                derived_note = ("与序号%s合锻(单件无重量), 档位按同炉号合锻件%.1fkg取"
                                % (mc.group(1), mass))
        allow, tier = tier_of(mass)
        # 锻重缺失但规格齐全: 用锻件尺寸上限推算重量, 若确定 ≤1T 则按 ≤1T 档出图并标注待确认
        if allow is None and spec:
            a0 = TIERS[0][1]                     # ≤1T 档双边补加
            if spec["shape"] == "solid":
                d_up = max(spec["D"], MIN_SOLID_D) + a0
                w_up = 3.编码X / 4.0 * d_up * d_up * (spec["L"] + a0) * 7.85e-6
            else:
                fo_up, fi_up = spec["od"] + a0 / 2.0, max(spec["id"] - a0 / 2.0, 0.0)
                w_up = 3.编码X / 4.0 * (fo_up * fo_up - fi_up * fi_up) * (spec["L"] + a0) * 7.85e-6
            if w_up <= 1000:
                allow, tier = a0, "≤1T"
                derived_note = "锻重未识别,按锻件尺寸推算上限%.0fkg≤1T,档位待确认" % w_up
        _rows, _lu_key, _lu_exact = [], None, True
        for _cand in str(lu).split("|"):
            _rows, _lu_key, _lu_exact = ht_get(ht, _cand)
            if _rows:
                break
        _h0 = _rows[0] if _rows else {}
        # 单质保号跨多批次(如 质保号X-4: 炉号X=42件/炉号X=70件/炉号X=70件):
        # 热处理尺寸取第一个有卡片的批次(各批卡片同规格时一致), 出图标待复核
        try:
            import read_ht2
            h = read_ht2.pick_for(_h0, r.get("外径"), r.get("内径"), r.get("长度"))
        except Exception:
            h = _h0
        problems = []
        if not spec:
            problems.append("规格未识别")
        if mass is None:
            problems.append("锻重未识别")
        provisional = False
        if _rows and not _lu_exact:
            problems.append("热处理卡按批次%s近似匹配(图纸炉号%s)" % (_lu_key, lu))
            provisional = True
        try:
            if spec and float(r.get("规格conf") or 0) < 0.80:
                problems.append("规格置信低(%.2f)" % float(r.get("规格conf") or 0))
                provisional = True
        except ValueError:
            pass
        # 长方体件: 卡片里是 3 个尺寸(如 110×326×450), 不是圆棒/筒
        # 尺寸取卡片"热处理尺寸"那列, 锻件图每向 +10(用户 2026-11-02)
        # 注意: pick_for 会把"命中原文"改写成"按成品尺寸匹配", 故块判定要看原始行
        _raw_hit = " ".join(x for x in (h.get("命中原文"), _h0.get("命中原文")) if x)
        mb = RE_BLOCK.search(_raw_hit or "")
        bk = blk.get(lu)
        if bk:
            problems.append("方块件: 热处理尺寸每向+10")
            if bk["der"]:               # 卡片只写了取样件尺寸 → 本体是推算值
                problems.append("卡片未标本体热处理尺寸(按产品+10推算)")
                provisional = True
        if not h.get("外径") and not mb and not bk:
            problems.append("热处理尺寸缺失")
            provisional = True          # 无热处理尺寸 → 只能出"待核稿"
        note = "; ".join(problems)
        if derived_note:
            note = (note + "; " + derived_note).strip("; ")
        row = {"图号": dh, "炉号": lu, "材质": r.get("材质", ""),
               "规格": ("Ø%s×%s" % (spec["D"], spec["L"])) if spec and spec["shape"] == "solid"
               else ("Ø%s/Ø%s×%s" % (spec["od"], spec["id"], spec["L"]) if spec else ""),
               "锻重kg": r.get("锻重kg", ""),
               "件号范围": "",
               "余量档": tier, "补加mm": allow,
               "稿件类型": "待核稿" if provisional else "正式稿",
               "置信": r.get("图号置信", ""),
               "问题": note}
        if not spec or allow is None:
            # 缺规格或缺重量 → 连档都定不了, 不出图
            row["稿件类型"] = "不出图"
            report.append(row)
            continue
        # 有热处理尺寸则用它, 否则退回产品规格(标记为待核)
        # 基准优先级(用户 2026-11-02 明确):
        #   1) 图纸"重量行"直接写了尺寸 → 那就是准确锻件尺寸, 原样照用, 不再加余量
        #   2) 否则: 热处理尺寸 + 余量档
        #   3) 热处理尺寸也没有 → 退回成品尺寸加余量, 标"待核稿"
        wd = (r.get("图纸锻件尺寸") or "").strip()
        if wd:
            ws = parse_spec({"外径": "", "内径": "", "长度": ""}, raw=wd)
            if ws:
                cfg = make_cfg(r, dh, lu, ws, allow, out_dir, False, spec_override=wd)
                row.update(稿件类型="正式稿(图纸直给)", 补加mm="0(照用)",
                           问题="锻件尺寸为图纸重量行直给, 原样照用, 不需要热处理尺寸")
                # 分组口径(用户 2026-11-05): 同尺寸+同批次(炉号) → 合一张图, 质保号都写上、
                # 数量求和; 尺寸或批次不同 → 分开。键里不能带图号, 否则同炉同尺寸的
                # 两个质保号(如 质保号X/质保号X 同一份图纸两页)会被拆成两张图。
                groups.setdefault(("直给", lu, cfg["info_row"]["append_spec"]),
                                  {"items": []})["items"].append(
                    {"r": r, "lu": lu, "cfg": cfg, "row": row, "prov": False})
                continue

        base = spec
        if bk:
            base = {"shape": "block", "od": bk["body"][0], "id": bk["body"][1],
                    "L": bk["body"][2]}
        elif mb:
            base = {"shape": "block", "od": int(mb.group(1)), "id": int(mb.group(2)),
                    "L": int(mb.group(3))}
        elif h.get("外径"):
            base = dict(spec)
            base["od"] = int(h["外径"]); base["L"] = int(h["长度"] or spec.get("L"))
            if spec["shape"] == "solid":
                base["D"] = int(h["外径"])      # 实心棒画图读 D, 之前漏了 → 直径一直用成品值
            if spec["shape"] == "tube":
                if h.get("内径"):
                    base["id"] = int(h["内径"])
                elif spec.get("id"):
                    # 卡片筒类只写"外径×长", 内径按卡片口径(产品-10)推
                    base["id"] = max(int(spec["id"]) - 10, 1)
                    problems.append("热处理报告未标内径, 按卡片口径(产品-10)取")
        if exb_mode:
            provisional = True         # EXB 直读没有经过 DXF 校验, 一律待核(2026-11-02)
            problems.append("热处理尺寸来自EXB直读(125组对照100%一致, 仍请复核)")
        cfg = make_cfg(r, dh, lu, base, allow, out_dir, provisional)
        if cfg.get("备注"):
            row["问题"] = (row["问题"] + "; " + cfg["备注"]).strip("; ")
        # 一个质保号一张图(用户 2026-11-02): 多批次/多页先归到同一组, 输出时再合并
        # 合锻件: 与同炉另一件合锻成一根 → 不单独出图, 并入棒料图
        cfr = cf.get(lu)
        if cfr and spec and spec.get("shape") != "block":     # cf 只处理圆棒棒料
            try:
                _L0 = int(round(float(spec.get("L") or 0)))
            except (TypeError, ValueError):
                _L0 = 0
            if _L0 and any(abs(_L0 - c) <= 2 for c in cfr["comp"]):
                cf_members.setdefault(lu, []).append(
                    {"r": r, "dh": dh, "lu": lu, "row": row, "mass": mass})
                continue
        # 分组口径(用户 2026-11-05): 同尺寸+同批次 → 合一张图, 质保号(含 -1/-2 子号)都写上;
        # 尺寸或批次不同 → 分开做。键 = 基底图号 + 炉号 + 最终锻件尺寸
        groups.setdefault((_base_dh(dh), lu, _dims_key(cfg)),
                          {"items": []})["items"].append(
            {"r": r, "lu": lu, "cfg": cfg, "row": row, "prov": provisional, "bk": bk})

    def emit(cfg, show_dh, lu_txt, row, prov, r, out_name=None, piece=None):
        """写 JSON + 出图"""
        _piece, wx, _q = _trace(r)
        if piece is None:
            piece = _piece
        fname = out_name or ("%s_%s" % (show_dh, lu_txt))
        cfg["output_pdf"] = os.path.join(out_dir, "%s.pdf" % fname.replace("/", "_"))
        # 信息行只留 图号(WX号)炉号 三要素: 件号范围不上图(用户 2026-11-05)，改记在生成清单
        cfg["info_row"]["items"] = [show_dh, wx, lu_txt]
        row["件号范围"] = piece
        # 多个质保号(子号合并)时把拆分列表给 forge_gen, 图上竖排(用户 2026-11-05)
        if "/" in show_dh:
            cfg["info_row"]["dh_lines"] = show_dh.split("/")
        p = os.path.join(out_dir, os.path.basename(cfg["output_pdf"])[:-4] + ".json")
        json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        if dry:
            return None
        q = subprocess.run([sys.executable, "-X", "utf8", os.path.join(HERE, "forge_gen.py"), p],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if "OK" in (q.stdout or "") and os.path.exists(cfg["output_pdf"]):
            (prov_made if prov else made).append(cfg["output_pdf"])
            return cfg["output_pdf"]
        err = (q.stderr or q.stdout or "").strip()
        # traceback 里挑最后一条真正的错误行, 比截尾 160 字符可读
        msgs = [l.strip() for l in err.splitlines()
                if re.search(r"Error|Exception|no such file|错误|失败", l, re.I)]
        brief = msgs[-1] if msgs else (err.splitlines()[-1] if err else "无输出")
        print("   出图失败 %s %s: %s" % (show_dh, lu_txt, brief[:220]), flush=True)
        return None

    samp_lu_done = set()      # 已出样图的炉号(每个炉号只出一张样图)
    for key, g in groups.items():
        items = g["items"]
        dhs = sorted({it["row"]["图号"] for it in items})       # 质保号(含 -N 子号)
        dh0 = dhs[0]
        show_dh = "/".join(dhs)
        lus = sorted({it["lu"] for it in items})
        # 炉号串里的 "|" = 单质保号跨多批次(一页图写不下拆开记), 拍平后再合并显示
        lus_flat = sorted({t for x in lus for t in x.split("|") if t})
        lu_txt = merge_lu(lus_flat, set(ht.keys()))
        # 同组尺寸已按键保证一致; 代表件仍取最长, 绝不静默丢尺寸
        rep = max(items, key=lambda it: it["cfg"]["part"].get("L") or it["cfg"]["part"].get("A") or 0)
        sizes = sorted({"%s" % it["cfg"]["part"].get("L") for it in items})
        row = rep["row"]
        row["图号"] = show_dh
        row["炉号"] = lu_txt
        note = []
        if len(lus) > 1:
            note.append("合并%d个批次" % len(lus))
        if any("|" in x for x in lus):
            note.append("单质保号跨%d个批次(%s), 热处理尺寸按首个有卡片的批次取, 请复核"
                        % (len(lus_flat), lu_txt))
        if len(dhs) > 1:
            note.append("同批次同尺寸合并质保号%s" % "+".join(dhs))
        if len(sizes) > 1:
            note.append("多尺寸(长度%s), 取最长" % "/".join(sizes))
        # 数量: 各页件号范围独立, 合并质保号时求和并留痕(绝不静默丢数量)
        pieces, qty_parts = [], []
        for it in items:
            _p, _w, _q = _trace(it["r"])
            if _p and _p not in pieces:
                pieces.append(_p)
            if str(_q).isdigit():
                qty_parts.append(_q)
        if len(dhs) > 1 and qty_parts:
            if len(qty_parts) > 1:
                note.append("数量%s=%d" % ("+".join(qty_parts), sum(int(x) for x in qty_parts)))
            else:
                note.append("数量合计%d(部分页件号范围未识别, 请复核)" % sum(int(x) for x in qty_parts))
        if note:
            row["问题"] = (row["问题"] + "; " + "; ".join(note)).strip("; ")
        emit(rep["cfg"], show_dh, lu_txt, row, rep["prov"], rep["r"],
             out_name=("__".join(dhs) + "_" + lu_txt) if len(dhs) > 1 else None,
             piece="+".join(pieces) if len(pieces) > 1 else None)
        # 清单显示最终锻件尺寸(不是成品尺寸); 规格已挪进 info_row.append_spec
        row["规格"] = rep["cfg"]["info_row"].get("append_spec") or ""
        report.append(row)

        # 加样件: 卡片有取样件尺寸且尺寸与本体不同 → 另出一张 "质保号+样"
        # 加样件路径⓪(用户 2026-11-13 某批): 图纸/料单直接印了
        # "/样Ø70×1030×3‰=32.1kg" → 这就是加样后的最终锻件尺寸, 原样直给,
        # 不加余量、不套 φ50 下限、不再用取样行+10 推算。优先级高于下面三条。
        _direct_samp = False
        for it in items:
            if it["lu"] in samp_lu_done:
                continue
            raw = it["r"].get("原始OCR") or ""
            m = RE_SAMPLE_DIM.search(raw)
            if not m:
                continue
            D_s, L_s = int(m.group(1)), int(m.group(2))
            mw = RE_SAMPLE_KG.search(raw[m.end():m.end() + 45])
            sw = mw.group(1) if mw else ""
            srow = dict(it["row"])
            srow["图号"] = dh0 + "样"
            srow["炉号"] = it["lu"]
            scfg = make_cfg(it["r"], srow["图号"], it["lu"],
                            {"shape": "solid", "D": D_s, "L": L_s}, 0, out_dir, False,
                            spec_override="Ø%d×%d" % (D_s, L_s))
            scfg["weight"] = (sw + "kg") if sw else ""
            srow.update(规格=scfg["info_row"]["append_spec"], 余量档="", 补加mm="0(照用)",
                        锻重kg=sw, 稿件类型="正式稿(图纸直给)", 置信=it["row"]["置信"],
                        问题="加样件, 尺寸取自图纸/料单『/样』直给, 原样照用不加余量")
            emit(scfg, srow["图号"], it["lu"], srow, False, it["r"])
            srow["规格"] = scfg["info_row"]["append_spec"]
            report.append(srow)
            samp_lu_done.add(it["lu"])
            _direct_samp = True
            break                                # 同一炉号只出一张样图
        if _direct_samp:
            continue

        _blk_done = False
        for it in items:
            bk = it.get("bk")
            if not (bk and bk["samp"]):
                continue
            srow = dict(it["row"])
            srow["图号"] = dh0 + "样"
            srow["炉号"] = it["lu"]
            sA, sB, sC = bk["samp"]
            scfg = make_cfg(it["r"], dh0 + "样", it["lu"],
                            {"shape": "block", "od": sA, "id": sB, "L": sC}, 0, out_dir, False)
            srow.update(规格=scfg["info_row"]["append_spec"], 余量档="", 补加mm="10(每向)",
                        稿件类型="正式稿", 置信=it["row"]["置信"],
                        问题="加样件, 尺寸取自卡片取样行+10(请复核)")
            emit(scfg, dh0 + "样", it["lu"], srow, False, it["r"])
            srow["规格"] = scfg["info_row"]["append_spec"]
            report.append(srow)
            samp_lu_done.add(it["lu"])
            _blk_done = True
            break                                # 同一质保号只出一张样图
        if _blk_done:
            continue

        # 加样件: 图纸上写"本体kg/样xxkg"且重量不一致 → 另出一张 "质保号+样"
        for it in items:
            r = it["r"]
            m = RE_SAMPLE_W.search(r.get("原始OCR") or "")
            if not m:
                continue
            try:
                bw, sw = float(m.group(1)), float(m.group(2))
            except ValueError:
                continue
            if abs(bw - sw) < 0.05:
                continue                       # 与本体重量一致 → 不用另出
            srow = dict(it["row"])
            srow["图号"] = dh0 + "样"
            srow["炉号"] = it["lu"]
            srow["锻重kg"] = sw
            s = smp.get(it["lu"])
            if not s or not (s.get("取样长度") or "").strip():
                srow.update(稿件类型="不出图", 问题="有加样(样%.1fkg≠本体%.1fkg)但取样件尺寸未取到" % (sw, bw))
                report.append(srow)
                continue
            shape = (s.get("形状") or "solid").strip() or "solid"
            L_s = _num2(s["取样长度"])
            if shape == "tube":
                sbase = {"shape": "tube", "od": _num2(s["取样外径"]), "id": _num2(s["取样内径"]), "L": L_s}
            elif shape == "block":
                sbase = {"shape": "block", "od": _num2(s["取样外径"]), "id": _num2(s["取样内径"]),
                         "L": L_s}
            else:
                sbase = {"shape": "solid", "D": _num2(s["取样外径"]), "L": L_s}
            a_s, t_s = tier_of(sw)
            if a_s is None:
                srow.update(稿件类型="不出图", 问题="加样件重量异常, 定不了余量档")
                report.append(srow)
                continue
            scfg = make_cfg(r, dh0 + "样", it["lu"], sbase, a_s, out_dir, False)
            scfg["weight"] = ""          # 样坯重量与本体不同, 重量行留空不猜(2026-11-02)
            _sq = (s.get("取样件数") or "").strip()
            srow.update(规格=scfg_part_txt(scfg), 余量档=t_s, 补加mm=a_s,
                        稿件类型="正式稿", 置信=it["row"]["置信"],
                        问题=("加样件%s, 尺寸取自大纲/卡片取样行+10(请复核)"
                              % (("(%s件)" % _sq) if _sq else ""))
                       + (("; " + scfg["备注"]) if scfg.get("备注") else ""))
            emit(scfg, dh0 + "样", it["lu"], srow, False, r)
            srow["规格"] = scfg["info_row"]["append_spec"]
            report.append(srow)
            samp_lu_done.add(it["lu"])
            break                                # 同一质保号只出一张样图

        # 加样件兜底③: 大纲取样行有尺寸就出样图(图纸"/样xxKG"标记可能被 OCR 漏读,
        # 某批 质保号X 样因此丢过一张, 2026-11-13); 每炉号只出一张
        for it in items:
            if it["lu"] in samp_lu_done:
                continue
            s = smp.get(it["lu"])
            if not s or not (s.get("取样长度") or "").strip():
                continue
            shape = (s.get("形状") or "solid").strip() or "solid"
            L_s = _num2(s["取样长度"])
            if shape == "tube":
                sbase = {"shape": "tube", "od": _num2(s["取样外径"]), "id": _num2(s["取样内径"]), "L": L_s}
            elif shape == "block":
                sbase = {"shape": "block", "od": _num2(s["取样外径"]), "id": _num2(s["取样内径"]),
                         "L": L_s}
            else:
                sbase = {"shape": "solid", "D": _num2(s["取样外径"]), "L": L_s}
            srow = dict(it["row"])
            srow["图号"] = re.sub(r"-\d+$", "", it["row"]["图号"].split("/")[0]) + "样"
            srow["炉号"] = it["lu"]
            scfg = make_cfg(it["r"], srow["图号"], it["lu"], sbase, 10, out_dir, False)
            scfg["weight"] = ""
            _sq = (s.get("取样件数") or "").strip()
            srow.update(规格=scfg["info_row"]["append_spec"], 余量档="", 补加mm="10(每向)",
                        稿件类型="正式稿", 置信=it["row"]["置信"],
                        问题=("加样件%s, 尺寸取自大纲取样行+10(请复核)"
                              % (("(%s件)" % _sq) if _sq else "")))
            emit(scfg, srow["图号"], it["lu"], srow, False, it["r"])
            srow["规格"] = scfg["info_row"]["append_spec"]
            report.append(srow)
            samp_lu_done.add(it["lu"])
            break

    # 合锻棒料图: 一根棒料一张, 图上把所有质保号、炉号写在一起(用户 2026-11-02)
    for lu, mem in sorted(cf_members.items()):
        cfr = cf[lu]
        dhs = sorted({m["dh"] for m in mem})
        lus = sorted({m["lu"] for m in mem})
        dh_txt = "/".join(dhs)
        lu_txt = "/".join(lus)
        rep = mem[0]
        masses = [m["mass"] for m in mem if m["mass"]]
        mass = max(masses) if masses else None
        allow, tier = tier_of(mass)
        prov = allow is None
        if prov:
            allow, tier = TIERS[0][1], "≤1T"
        r2 = dict(rep["r"])
        r2["锻重kg"] = ""            # 棒料是几件合锻, 单件重量不适用 → 留空不猜
        cfg = make_cfg(r2, dh_txt, lu_txt, {"shape": "solid", "D": cfr["D"], "L": cfr["L"]},
                       allow, out_dir, prov)
        row = {"图号": dh_txt, "炉号": lu_txt, "材质": rep["r"].get("材质", ""),
               "规格": "", "锻重kg": ("%.1f" % mass) if mass else "",
               "余量档": tier, "补加mm": allow,
               "稿件类型": "待核稿" if prov else "正式稿",
               "置信": rep["r"].get("图号置信", ""),
               "问题": "合锻棒料(%s+切断=%d, 卡片热处理Ø%d×%d), 质保号/炉号写在同一张"
                       % ("+".join(str(c) for c in cfr["comp"]), cfr["prod"], cfr["D"], cfr["L"])}
        emit(cfg, dh_txt, lu_txt, row, prov, r2,
             out_name="-".join(dhs) + "_" + "-".join(lus))
        row["规格"] = cfg["info_row"]["append_spec"]
        report.append(row)

    rp = os.path.join(out_dir, "生成清单.csv")
    if not report:
        # 一行都没出来: 必然是 manifest 的 图号/炉号 缺了, 或热处理尺寸没读到。
        # 写一份带表头的空清单(避免下游读到 0 字节坏文件), 并返回非零码, 防止误判成功。
        print("!! 0 件可出图, 未生成任何图纸。")
        print("   ① 看 manifest.csv 的『图号』『炉号』两列是否为空 —— OCR 只出草稿值, 必须人工补;")
        print("   ② 看热处理卡位置: DXF 放 批次\\DXF\\, exb 放 批次\\热处理工艺\\ (或 \\exb\\ 子目录)。")
        with open(rp, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow(["图号", "炉号", "材质", "规格", "锻重kg",
                                    "余量档", "补加mm", "稿件类型", "置信", "问题"])
        return 2
    with open(rp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(report[0].keys()))
        w.writeheader(); w.writerows(report)
    print("正式稿 %d, 待核稿 %d / 共 %d 件; 清单 -> %s" % (len(made), len(prov_made), len(report), rp))
    return made, prov_made


def batch_abs(name):
    return name if os.path.isabs(name) else os.environ.get("BATCH_DIR", ".")


def make_cfg(r, dh, lu, base, allow, out_dir, provisional=False, spec_override=None):
    """锻件尺寸来源优先级: 图纸重量行直给 > 热处理尺寸+余量 > 成品尺寸+余量(待核)"""
    if spec_override:
        # 图纸"重量行"直接写了尺寸 → 那就是准确锻件尺寸, 原样照用, 不再加余量
        _note = ""
        m51 = re.search(r"(\d+\s*合\s*\d+)", spec_override)   # 如 "(5合1)": 5件合锻1根
        if m51:
            _note = "(%s)" % re.sub(r"\s", "", m51.group(1))
        if base["shape"] == "tube":
            part = {"shape": "tube", "fo": base["od"], "fi": base["id"], "L": base["L"],
                    "ht_do": base["od"], "ht_di": base["id"], "wall_exaggerate": 3.2}
            spec_txt = "\u00d8%d\u00d7\u00d8%d\u00d7%s" % (base["od"], base["id"], base["L"])
        elif base["shape"] == "solid" and base["L"] < base["D"] * 0.9:
            # 厚度<直径的实心件 = 圆饼/盘类, 用双视图(俯视圆+侧视矩形), 2026-11-02 某批
            part = {"shape": "disc", "D": base["D"], "L": base["L"]}
            spec_txt = "\u00d8%d\u00d7%s" % (base["D"], base["L"])
        else:
            part = {"shape": "solid", "D": base["D"], "L": base["L"], "width_exaggerate": 6.0}
            spec_txt = "\u00d8%d\u00d7%s" % (base["D"], base["L"])
        spec_txt += _note
        return _pack(r, dh, lu, part, spec_txt, out_dir, "图纸直给")

    # 余量档是"双边补加mm": 每个方向都加这么多(用户 2026-11-02 口径 = 热处理尺寸上每向+余量)
    a2 = (allow or 0)
    bump_note = ""
    if base["shape"] == "block":
        # 长方体: 热处理尺寸长宽高三向各 +10(用户 2026-11-02), 与锻重档位无关
        # 图上画上下两视图(俯视 a×b, 主视 a×L), 规格写 a×b×L, 不加 Ø、不标公差
        a = base["od"] + BLOCK_ADD
        b = base["id"] + BLOCK_ADD
        L = base["L"] + BLOCK_ADD
        part = {"shape": "block", "A": a, "B": b, "L": L}
        spec_txt = "%d×%d×%d" % (a, b, L)
        return _pack(r, dh, lu, part, spec_txt, out_dir, "待核" if provisional else "算出")
    if base["shape"] == "tube":
        fo = base["od"] + int(round(a2))
        fi = base["id"] - int(round(a2))     # 内径加余量 = 变小
        L = base["L"] + (allow or 0)
        part = {"shape": "tube", "fo": fo, "fi": fi, "L": L,
                "ht_do": base["od"], "ht_di": base["id"], "wall_exaggerate": 3.2}
        spec_txt = "\u00d8%d\u00d7\u00d8%d\u00d7%s" % (fo, fi, L)
    else:
        # φ50 口径(用户 2026-11-13 改): 原始直径不满 φ50 就"直接视为 φ50"再加余量;
        # 旧口径是"加完余量后不足 50 才抬到 50", 已交付批次不回溯。
        # 图纸直给(spec_override)不走这里, 写了尺寸就原样照抄, 不受 φ50 影响。
        D0 = base["D"]
        if D0 < MIN_SOLID_D:
            bump_note = ("圆棒Ø%d<φ%d, 按φ%d计再加余量%d"
                         % (D0, MIN_SOLID_D, MIN_SOLID_D, int(round(a2))))
            D0 = MIN_SOLID_D
        D = D0 + int(round(a2))
        L = base["L"] + (allow or 0)
        part = {"shape": "solid", "D": D, "L": L, "width_exaggerate": 6.0}
        spec_txt = "\u00d8%d\u00d7%s" % (D, L)
    cfg = _pack(r, dh, lu, part, spec_txt, out_dir, "待核" if provisional else "算出")
    if bump_note:
        cfg["备注"] = bump_note
    return cfg


RE_PIECE = re.compile(r"(\d{2,3})\s*[-—]?\s*[(（]\s*(\d{2,3})\s*[~～]\s*(\d{2,3})\s*[)）]")
RE_WX = re.compile(r"WX\s*(\d{8,})")
NAME_STOP = ("名称", "材质", "数量", "填图", "复核", "批准", "版本", "序号", "炉号",
             "张娟", "钱达", "与序", "低应力", "标记", "合锻", "无锡", "锻件尺寸图")


RE_SAMPLE_W = re.compile(r"(\d+(?:\.\d+)?)\s*kg.{0,14}?样\s*(\d+(?:\.\d+)?)\s*kg", re.I | re.S)
# 图纸/料单重量行把"加样后的尺寸"也直接印出来: "/样Ø70×1030×3‰=32.1kg"
# (用户 2026-11-13: 图纸上写了尺寸的必须原样直给, 无论是不是加样件)
RE_SAMPLE_DIM = re.compile(
    r"[/／]\s*样\s*[ØΦφøα中oO]?\s*(\d{2,4})\s*[×xX*]\s*(\d{2,5})(?:\s*[×xX*]\s*\d{2,4})?",
    re.I)
RE_SAMPLE_KG = re.compile(r"(\d+(?:\.\d+)?)\s*[kK][gG]")
# 长方体: 三个尺寸 110×326×450 (无 Ø), 区别于圆棒 ØD×L
RE_BLOCK = re.compile(r"(?<![\d.])(\d{2,4})\s*[×xX*]\s*(\d{2,4})\s*[×xX*]\s*(\d{2,4})(?![\d.])")


def merge_lu(lus, known=None):
    """多批次写成一段: 连续(或缺失的中间炉号是本批已知炉号)则用 首～尾, 否则逐个～相连。
    带子号后缀的炉号(炉号X-1/炉号X重热)不参与数字连写, 原样列在后面(绝不丢, 2026-11-05)。"""
    v, suf = [], []
    for x in lus:
        s = norm_lu(x)
        if s.isdigit():
            v.append(int(s))
        elif s:
            suf.append(s)
    v = sorted(set(v))
    seg = []
    if v:
        if len(v) == 1:
            seg.append(str(v[0]))
        else:
            known = known or set()
            miss = [n for n in range(v[0], v[-1] + 1) if n not in set(v)]
            if not miss or all(str(n) in known for n in miss):
                seg.append("%d～%d" % (v[0], v[-1]))
            else:
                seg.extend(str(x) for x in v)
    seg.extend(sorted(set(suf)))
    if not seg:
        return "～".join(norm_lu(x) for x in lus)
    return "～".join(seg)


def ht_get(ht, lu):
    """按炉号取热处理卡行。返回 (rows, 命中键, 是否精确)。
    图纸写 炉号X-1 而卡片只有 炉号X, 或反之, 都允许近似命中(出图标待核)。"""
    lu = norm_lu(lu)
    if lu in ht:
        return ht[lu], lu, True
    m = re.match(r"(\d{6})", lu)
    base = m.group(1) if m else lu
    if base != lu and base in ht:
        return ht[base], base, False
    var = sorted(k for k in ht if k.startswith(base) and k != base)
    if var:
        return ht[var[0]], var[0], False
    return [], None, False


def _num2(x):
    try:
        v = float(str(x).strip())
        return int(v) if v == int(v) else round(v, 1)
    except (ValueError, AttributeError):
        return 0


def _base_dh(dh):
    """质保号去掉 -1/-2 子号后缀(2026-11-05: 拆分子号要分别做图)"""
    return re.sub(r"-\d{1,2}$", "", dh or "")


def _dims_key(cfg):
    """最终锻件尺寸的分组键: 同尺寸+同批次才允许合并一张图"""
    p = cfg.get("part") or {}
    s = p.get("shape")
    if s == "tube":
        return ("tube", p["fo"], p["fi"], p["L"])
    if s == "block":
        return ("block", p["A"], p["B"], p["L"])
    if s == "disc":
        return ("disc", p["D"], p["L"])
    return ("solid", p.get("D"), p.get("L"))


def scfg_part_txt(cfg):
    """从 cfg 反推规格串, 供生成清单使用"""
    p = cfg["part"]
    if p["shape"] == "tube":
        return "Ø%s/Ø%s×%s" % (p["fo"], p["fi"], p["L"])
    return "Ø%s×%s" % (p["D"], p["L"])


def _trace(r):
    """从图纸 OCR 原文提取 件号范围 / WX追溯号 / 数量(由件号范围推算)。
    提取不到就留空, 绝不猜(2026-11-02 用户指定保留的元素)。"""
    raw = (r.get("原始OCR") or "")
    piece = wx = qty = ""
    m = RE_PIECE.search(raw)
    if m:
        piece = "%s-(%s~%s)" % (m.group(1), m.group(2), m.group(3))
        qty = str(int(m.group(3)) - int(m.group(2)) + 1)
    m = RE_WX.search(raw)
    if m:
        wx = "WX" + m.group(1)
    return piece, wx, qty


def _name(r):
    """标题栏"名称": manifest 有值就用; 否则从 OCR 原文找中文名(优先含"毛坯");
    都没有就写"锻件"(用户 2026-11-02)。"""
    n = (r.get("名称") or "").strip()
    if n:
        return n[:14]
    cands = []
    for b in (r.get("原始OCR") or "").split("|"):
        b = b.strip()
        if not b or len(b) < 3:
            continue
        if re.search(r"[A-Za-z]", b):
            continue
        if len(re.findall(r"[\u4e00-\u9fff]", b)) / len(b.replace(" ", "")) < 0.6:
            continue
        if any(k in b for k in NAME_STOP):
            continue
        cands.append(b)
    if not cands:
        return "锻件"
    pref = [c for c in cands if "毛坯" in c or "锻件" in c]
    return max(pref or cands, key=len)[:14]


def _pack(r, dh, lu, part, spec_txt, out_dir, src):
    title = {"图纸直给": "无锡市法兰锻造有限公司锻件尺寸图",
             "待核": "锻件尺寸图 · 待核"}.get(src, "无锡市法兰锻造有限公司锻件尺寸图")
    piece, wx, qty = _trace(r)
    name = _name(r)
    return {
        "source_pdf": os.path.join(batch_abs(r["pdf"]), r["pdf"]) if "pdf" in r else r["pdf"],
        "source_page": int(r["page"]),
        "title": title,
        "尺寸来源": src,
        "output_pdf": os.path.join(out_dir, "%s_%s.pdf" % (dh, lu)),
        "background": "clean", "part": part,
        "frame": FRAME, "title_block": {"rows": ROWS, "cols": COLS, "vtop": ROWS[0]},
        "draw_area": [172, 110, 452, 600], "dim_fontsize": 22, "dim_v_offset": 42,
        "dim_v_mode": "right", "ref_contour": {"show": False},
        "white_boxes": [], "signatures": SIGS,
        "weight": ((r.get("锻重kg") or "") + "kg") if (r.get("锻重kg") or "").strip() else "",
        "weight_x": 505, "weight_y": 822,
        "texts": [],
        "info_row": {"y": 683, "fs": 12.5, "x0": 68, "x1": 556,
                     "items": [dh, piece, wx, lu],   # 材质只在标题栏格子里, 此处不重复
                     # 规格串: 信息行放得下就并到行尾(2026-11-03), 放不下画在上方一行
                     "append_spec": spec_txt},
        "cells": [[1, 2, 0, 1, "A", 13],
                  [1, 2, 4, 5, name, 11],
                  [1, 2, 5, 6, (r.get("材质") or "")[:12], 10],
                  [2, 3, 0, 1, "版本", 12], [2, 3, 1, 2, "填图", 12], [2, 3, 2, 3, "复核", 12],
                  [2, 3, 3, 4, "批准", 12], [2, 3, 4, 5, "名称", 12], [2, 3, 5, 6, "材质", 12]],
    }


if __name__ == "__main__":
    os.environ["BATCH_DIR"] = os.path.abspath(sys.argv[1])
    r = build(sys.argv[1], dry="--dry-run" in sys.argv)
    # 正常返回 (made, prov_made); 出错返回 int(缺 manifest=1 / 0 件可出图=2)
    # 以前这里忽略返回值, 退出码永远是 0, 跑批会把"一件没出"当成功继续合并
    sys.exit(r if isinstance(r, int) else 0)
