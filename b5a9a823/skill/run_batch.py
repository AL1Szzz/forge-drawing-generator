# -*- coding: utf-8 -*-
"""一键跑批: 批次目录 → 锻件尺寸图汇总 PDF + 复核清单。

用法: python run_batch.py "<批次目录>"   (或把批次文件夹拖到 一键跑批.bat 上)

流程:
  1) manifest.csv 缺失 → build_manifest.py 自动 OCR 建清单 (需人工核对!)
  2) 热处理尺寸: 有 DXF 用 DXF; 没有 → EXB 直读 (自动, 结果标待核)
  3) make_batch 出图 (图纸重量行直给 > 热处理+余量 > 成品+余量待核)
  4) merge_pack 合并 + 复制 <批次>_锻件尺寸图_汇总.pdf 到批次目录
  5) 生成 复核清单.md (列出所有待核项)
"""
import os, sys, csv, shutil, glob, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PY = sys.executable


def sh(script, *args):
    q = subprocess.run([PY, "-X", "utf8", os.path.join(HERE, script)] + list(args),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (q.stdout or "") + (("\n[stderr] " + q.stderr) if q.stderr.strip() else "")
    print(out.strip())
    return q.returncode == 0


def main(batch):
    batch = os.path.abspath(batch)
    if not os.path.isdir(batch):
        print("批次目录不存在:", batch); return 1
    name = os.path.basename(batch.rstrip("\\/"))
    print("=" * 60)
    print("批次:", name)
    print("=" * 60)
    # 单位配置: 批次目录里放 unit_profile.json 即切换单位; 环境变量传给所有子进程
    prof = os.path.join(batch, "unit_profile.json")
    if os.path.exists(prof):
        os.environ["FORGE_PROFILE"] = prof
        print("单位配置:", prof)

    # 1) manifest
    man = os.path.join(batch, "manifest.csv")
    if not os.path.exists(man):
        print("\n[1/5] 无 manifest.csv, 自动 OCR 建清单 (之后必须人工核对!)")
        sh("build_manifest.py", batch)
    else:
        print("\n[1/5] manifest.csv 已存在, 跳过 OCR")
    if not os.path.exists(man):
        print("manifest.csv 仍未生成, 停止"); return 1

    # 1.5) 输入校验: manifest 引用的原始图纸 PDF 必须存在
    #   forge_gen 用它裁底图/取图框, 缺了会让每一张图都失败(且报错被 traceback 淹没)
    missing = set()
    for r in csv.DictReader(open(man, encoding="utf-8-sig")):
        p = (r.get("pdf") or "").strip()
        if not p:
            continue
        full = p if os.path.isabs(p) else os.path.join(batch, p)
        if not os.path.exists(full):
            missing.add(p)
    if missing:
        print("\n!! 缺少原始图纸 PDF, 无法出图, 已提前停止:")
        for p in sorted(missing):
            print("   -", p)
        print("   请把产品图纸 PDF 放进批次目录后重跑。")
        return 1

    # 2+3) 热处理尺寸(make_batch 内部自动选 DXF/EXB) + 出图
    print("\n[2/5] 热处理尺寸 (DXF 优先, 无 DXF 则 EXB 直读)")
    print("[3/5] 生成锻件尺寸图")
    if not sh("make_batch.py", batch):
        print("出图失败, 停止"); return 1

    # 4) 合并 + 交付
    #    不删/不改名旧汇总: 环境有 safe-delete 钩子会拦 os.remove, 预览程序还可能占用
    #    文件导致 rename 失败(2026-11-03 两次实测)。改用"本次是否生成了新的 src"判断:
    #    merge_pack 成功且 src 的 mtime 是本次的 → 才覆盖交付; 否则绝不覆盖旧文件。
    print("\n[4/5] 合并汇总")
    fig = os.path.join(batch, "_锻件图")
    src = os.path.join(fig, "锻件尺寸图_汇总.pdf")
    dst = os.path.join(batch, "%s_锻件尺寸图_汇总.pdf" % name)
    t0 = time.time()
    merged = sh("merge_pack.py", fig)
    fresh = os.path.exists(src) and os.path.getmtime(src) >= t0 - 2
    if merged and fresh:
        shutil.copyfile(src, dst)
        print("交付 ->", dst)
    else:
        print("!! 合并失败或未生成新汇总 PDF, 本次不交付。")
        print("   目录里若仍存在汇总 PDF, 那是上一版, 请勿使用; 请看上方报错后重跑。")

    # 5) 复核清单: 区分"必须人工复核"与"正式稿", 不再把说明文字一律算待核
    print("\n[5/5] 复核清单")
    cl = os.path.join(fig, "生成清单.csv")
    md = os.path.join(batch, "复核清单.md")
    RISK = ("请复核", "待核", "缺失", "置信低", "推算", "未标", "EXB直读",
            "异常", "未识别", "未取到", "无热处理")
    rows_all = []
    if os.path.exists(cl):
        rows_all = list(csv.DictReader(open(cl, encoding="utf-8-sig")))
    def need_check(r):
        t = r.get("稿件类型") or ""
        q = r.get("问题") or ""
        return ("待核" in t) or ("不出图" in t) or any(k in q for k in RISK)
    todo = [r for r in rows_all if need_check(r)]
    ok = [r for r in rows_all if not need_check(r)]
    n_bad = sum(1 for r in rows_all if "不出图" in (r.get("稿件类型") or ""))
    n_draft = sum(1 for r in rows_all if "待核" in (r.get("稿件类型") or ""))
    lines = ["# 复核清单 — %s" % name, "",
             "生成时间: %s" % __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
             "",
             "- 共 %d 张图：正式稿 %d、待核稿 %d、不出图 %d" % (
                 len(rows_all), len(rows_all) - n_bad - n_draft, n_draft, n_bad),
             "- **需人工复核 %d 项**（下表）" % len(todo),
             ""]
    if todo:
        lines += ["## 需人工复核", "",
                  "| 图号 | 炉号 | 规格 | 稿件类型 | 原因 |", "|---|---|---|---|---|"]
        for r in todo:
            lines.append("| %s | %s | %s | %s | %s |" % (
                r.get("图号", ""), r.get("炉号", ""), r.get("规格", ""),
                r.get("稿件类型", ""), (r.get("问题") or "").replace("|", "/")))
        lines.append("")
    lines += ["## 正式稿（无需复核）", "",
              "| 图号 | 炉号 | 规格 | 稿件类型 | 说明 |", "|---|---|---|---|---|"]
    for r in ok:
        lines.append("| %s | %s | %s | %s | %s |" % (
            r.get("图号", ""), r.get("炉号", ""), r.get("规格", ""),
            r.get("稿件类型", ""), (r.get("问题") or "—").replace("|", "/")))
    open(md, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("  ->", md, "(需复核 %d 项, 不出图 %d 项)" % (len(todo), n_bad))
    if not (merged and fresh):
        print("  !! 本次汇总 PDF 未生成, 请检查上方 [4/5] 报错")
    print("\n完成。汇总 PDF 与 复核清单.md 都在批次目录里。")
    return 0 if (merged and fresh) else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(r'用法: 把批次文件夹拖到 一键跑批.bat 上, 或: python run_batch.py "<批次目录>"')
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
