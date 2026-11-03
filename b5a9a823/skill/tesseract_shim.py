# -*- coding: utf-8 -*-
"""
Tesseract OCR 封装。
- 可执行: C:/Tesseract/tesseract.exe
- 语言包: 复用 MasterPDF Editor 自带的 chi_sim, 不重复下载
"""
import os, subprocess, sys, json, re

TESS = r"C:/Program Files/Tesseract-OCR/tesseract.exe"
TESSDATA = [r"E:/backup/MasterPDFEditor/Data/MasterPDFEditor/tesseract-5/tessdata",
            r"E:/backup/MasterPDFEditor/App/DefaultData/MasterPDFEditor/tesseract-5/tessdata",
            r"C:/Tesseract/tessdata"]


def env():
    e = dict(os.environ)
    for d in TESSDATA:
        if os.path.isdir(d):
            e["TESSDATA_PREFIX"] = d
            return e, d
    return e, None


def ocr(img, lang="chi_sim+eng", psm=6):
    if not os.path.exists(TESS):
        return None, "tesseract.exe 未安装"
    e, td = env()
    r = subprocess.run([TESS, img, "stdout", "-l", lang, "--psm", str(psm)],
                       capture_output=True, text=True, encoding="utf-8", env=e)
    return r.stdout, r.stderr


# 标题栏字段抽取
PATTERNS = {
    "图号":   r"[Ff]\s*\d{6,8}",
    "规格":   r"[ØøΦ⌀]?\s*\d{2,4}\s*[/×xX*]\s*[ØøΦ⌀]?\s*\d{2,4}\s*[/×xX*]?\s*\d{2,5}",
    "材质":   r"(SA508|SA105|SA182|SA350|SA321|304|316L|20MnMo|16Mn|20Mn2|Q345R|Q245R|B16\.5|F\d{2}V|2\.25Cr|1\.25Cr)",
    "炉号":   r"炉\s*号\s*[:：]?\s*(\d{5,7})",
    "数字串": r"\b\d{4,7}\b",
}


def parse_title(text):
    out = {}
    t = text.replace(" ", "")
    for k, pat in PATTERNS.items():
        m = re.findall(pat, text if k != "数字串" else t)
        if m:
            out[k] = m[:6]
    return out


def ocr_tess(img, lang="chi_sim+eng", psm=6):
    return ocr(img, lang, psm)


if __name__ == "__main__":
    img = sys.argv[1]
    lang = sys.argv[2] if len(sys.argv) > 2 else "chi_sim+eng"
    out, err = ocr(img, lang)
    if out is None:
        print(err); sys.exit(1)
    print(out)
    print("---- 解析 ----")
    print(json.dumps(parse_title(out), ensure_ascii=False, indent=1))
