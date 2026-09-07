#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Total-pipe 组件覆盖率门禁 (stage 6 gate)

跑完流水线后执行，检查「前段产物是否过期 / 组件是否被漏用 / 残留是否清理」。
只要出现 FAIL 就不许收工。

用法:
  python pipe_coverage_audit.py --project <PPT项目目录> [--plan <deck_plan.json>] [--json]

退出码: 0 = 无 FAIL; 1 = 有 FAIL
"""
import argparse
import json
import os
import re
import sys
import zipfile
from collections import Counter

# tencent-pptx 全部可用组件（component-*.md）
ALL_COMPONENTS = [
    "Slide", "Box", "Text", "Image", "Table", "Chart", "Diagram", "Svg",
    "FaIcon", "Math", "CodeBlock", "QrCode", "Hyperlink", "Animation",
]
# 手搭表格特征：深蓝表头行 + 用 borderTop:'none' 拼出来的斑马纹行
HAND_TABLE_HEADER_RE = re.compile(r"background: '#1E4FA8', borderRadius: \d+, flexDirection: 'row'")
HAND_TABLE_ROW_RE = re.compile(r"borderTop: 'none'")

RESIDUE_GLOBS = ("_v*.pptx", "_rebuild.pptx", "*deck_plan_cm.json", "~$*.pptx", "~$*.pptx")


def pptx_page_count(path):
    try:
        with zipfile.ZipFile(path) as z:
            return len([n for n in z.namelist()
                        if re.match(r"ppt/slides/slide\d+\.xml$", n)])
    except Exception:
        return None


def max_page_in_md(path, pattern):
    """从 STORY.md / DESIGN.md 里抓最大页号"""
    if not os.path.exists(path):
        return None
    txt = open(path, encoding="utf-8", errors="replace").read()
    hits = [int(m) for m in re.findall(pattern, txt)]
    return max(hits) if hits else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="PPT 项目目录（含 slides/ 与 *.pptx）")
    ap.add_argument("--plan", default=None, help="deck_plan.json 路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    proj = os.path.abspath(args.project)
    sdir = os.path.join(proj, "slides")
    results = []

    def add(cid, status, detail, fix=""):
        results.append({"check": cid, "status": status, "detail": detail, "fix": fix})

    # ---- 收集 slide 文件 ----
    slide_files = sorted(f for f in os.listdir(sdir) if f.endswith(".slide")) if os.path.isdir(sdir) else []
    n_slide_files = len(slide_files)
    if n_slide_files == 0:
        add("slide_sources", "FAIL", "slides/ 下没有 .slide 文件", "")
        return dump(results, args.json)

    slide_texts = {}
    for f in slide_files:
        slide_texts[f] = open(os.path.join(sdir, f), encoding="utf-8", errors="replace").read()
    all_text = "\n".join(slide_texts.values())

    # ---- 1. pptx 实际页数 ----
    pptx_files = [f for f in os.listdir(proj)
                  if f.endswith(".pptx") and not f.startswith("~$") and not f.startswith("_")]
    n_pptx = pptx_page_count(os.path.join(proj, pptx_files[0])) if pptx_files else None
    if n_pptx is None:
        add("pptx_pages", "WARN", "未找到可解析的 pptx", "")
    elif n_pptx != n_slide_files:
        add("pptx_pages", "FAIL",
            f"pptx {n_pptx} 页 != slides/ {n_slide_files} 个源文件",
            "重新 create 空文件后按序 upsert-dsl 全部页")
    else:
        add("pptx_pages", "PASS", f"pptx {n_pptx} 页 = slides/ {n_slide_files} 源文件", "")

    # ---- 2. deck_plan 页数对齐 + 时效 ----
    plan_path = args.plan
    if plan_path and os.path.exists(plan_path):
        try:
            plan = json.load(open(plan_path, encoding="utf-8"))
            n_plan = len(plan.get("slides", []))
            if n_plan != n_slide_files:
                add("plan_pages", "FAIL",
                    f"deck_plan {n_plan} 页 != 实际 {n_slide_files} 页（规划已过期）",
                    f"重跑: cli.mjs plan --slide-count {n_slide_files}")
            else:
                add("plan_pages", "PASS", f"deck_plan {n_plan} 页对齐", "")

            newest_slide = max(os.path.getmtime(os.path.join(sdir, f)) for f in slide_files)
            if os.path.getmtime(plan_path) < newest_slide:
                add("plan_freshness", "FAIL",
                    "slides/ 修改时间晚于 deck_plan.json（改过页面但没回跑规划）",
                    "重跑 plan，或确认改动不影响版式后手动 touch")
            else:
                add("plan_freshness", "PASS", "deck_plan 不早于 slide 源文件", "")
        except Exception as e:
            add("plan_pages", "WARN", f"deck_plan 解析失败: {e}", "")
    else:
        add("plan_pages", "WARN", "未提供 --plan，跳过规划对齐检查", "")

    # ---- 3. STORY.md / DESIGN.md 页数同步 ----
    for fname, label, key in (("STORY.md", "story_pages", "story"),
                              ("DESIGN.md", "design_pages", "design")):
        p = os.path.join(proj, fname)
        n = max_page_in_md(p, r"### P(\d+)")
        if n is None:
            n = max_page_in_md(p, r"\bP(\d{1,2})\b")
        if n is None:
            add(label, "WARN", f"{fname} 里没找到页号标记（P1/P2...）", "按 story-principle.md 写页面大纲")
        elif n < n_slide_files:
            add(label, "FAIL",
                f"{fname} 最大页号 P{n} < 实际 {n_slide_files} 页（文档已过期）",
                f"把 {fname} 的页面大纲/配色分配补到 P{n_slide_files}")
        else:
            add(label, "PASS", f"{fname} 覆盖到 P{n}", "")

    # ---- 4. 表格必须用原生 Table ----
    n_table = len(re.findall(r"<Table\b", all_text))
    hand_pages = []
    for f, t in slide_texts.items():
        n_hdr = len(HAND_TABLE_HEADER_RE.findall(t))
        n_row = len(HAND_TABLE_ROW_RE.findall(t))
        if n_hdr >= 1 and n_row >= 2:
            hand_pages.append(f"{f}(表头{n_hdr}/行{n_row})")
    if hand_pages:
        add("table_component", "FAIL",
            f"检测到手搭表格: {', '.join(hand_pages)}；原生 Table 使用 {n_table} 次",
            "改用 component-table.md 的 <Table cells={...}>，百分比尺寸 + defaultCellStyle 斑马纹")
    elif n_table == 0:
        add("table_component", "WARN", "本牌未使用 Table 组件（也可能本就不需要表格）", "")
    else:
        add("table_component", "PASS", f"原生 Table 使用 {n_table} 次，无手搭表格", "")

    # ---- 5. 组件覆盖率 ----
    used = [c for c in ALL_COMPONENTS if re.search(r"<" + c + r"\b", all_text)]
    core = [c for c in used if c != "Slide"]
    if len(core) <= 3:
        add("component_coverage", "WARN",
            f"仅用 {len(core)}/13 个组件: {', '.join(core)}",
            "对照 references/component-*.md 看是否有更合适的原生组件")
    else:
        add("component_coverage", "PASS", f"使用 {len(core)}/13 个组件: {', '.join(core)}", "")

    # ---- 6. 证据溯源 ----
    evs = re.findall(r"EV\d{3,}", all_text)
    if not evs:
        add("evidence_trace", "WARN",
            "成品 slide 里 0 个 EV 编号（证据链在渲染层断裂）",
            "关键数据旁补 EV 角标，并产出 evidence→slide 对照表")
    else:
        add("evidence_trace", "PASS", f"引用 {len(set(evs))} 个 EV id（共 {len(evs)} 处）", "")

    # ---- 7. 图片资产是否都用上 ----
    adir = os.path.join(proj, "assets")
    if os.path.isdir(adir):
        assets = [f for f in os.listdir(adir) if os.path.splitext(f)[1].lower() in
                  (".png", ".jpg", ".jpeg", ".webp", ".gif")]
        unused = [a for a in assets if f'assets/{a}' not in all_text]
        if unused:
            add("asset_usage", "WARN", f"{len(unused)} 张图未使用: {', '.join(unused[:5])}", "")
        else:
            add("asset_usage", "PASS", f"{len(assets)} 张图全部引用", "")
    else:
        add("asset_usage", "WARN", "无 assets/ 目录", "")

    # ---- 7.5 阶段 3 是否走完全流程（rpa_full_pipeline.py 的报告） ----
    # 找 _rpa/_pipeline_report.md：优先项目同级 out-dir，其次项目内
    rep = None
    for cand in (os.path.join(proj, "_rpa", "_pipeline_report.md"),
                 os.path.join(os.path.dirname(proj), "_rpa", "_pipeline_report.md")):
        if os.path.isfile(cand):
            rep = cand
            break
    if rep is None:
        add("stage3_full", "FAIL",
            "没找到 _rpa/_pipeline_report.md（阶段 3 没用 rpa_full_pipeline.py 跑）",
            "python scripts/rpa_full_pipeline.py --rpa-input ... --out-dir ... --project ...")
    else:
        txt = open(rep, encoding="utf-8", errors="replace").read()
        done = set(re.findall(r"\| (S\d) \|", txt))
        need = {"S1", "S2", "S3", "S4", "S5", "S6", "S7"}
        miss = sorted(need - done)
        if miss:
            add("stage3_full", "FAIL",
                f"阶段 3 只跑了 {len(done)}/7 步，缺 {'/'.join(miss)}",
                "重跑 rpa_full_pipeline.py（它会串完 S1–S7）")
        elif "结果：FAIL" in txt or "| FAIL |" in txt:
            add("stage3_full", "FAIL", "阶段 3 报告里有 FAIL 步骤，见 _pipeline_report.md",
                "按报告里的「怎么修」处理")
        else:
            add("stage3_full", "PASS", "阶段 3 七步齐全且无 FAIL", "")

    # ---- 8. 残留文件 ----
    import glob as _glob
    residue = []
    for g in RESIDUE_GLOBS:
        residue += [os.path.basename(p) for p in _glob.glob(os.path.join(proj, g))]
    residue = sorted(set(residue))
    if residue:
        add("residue_files", "WARN", f"待清理: {', '.join(residue)}", "rm 掉中间副本与锁文件")
    else:
        add("residue_files", "PASS", "无残留中间文件", "")

    return dump(results, args.json)


def dump(results, as_json):
    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        icon = {"PASS": "[OK]  ", "WARN": "[WARN]", "FAIL": "[FAIL]"}
        print("\nTotal-pipe 组件覆盖率门禁\n" + "-" * 78)
        for r in results:
            print(f"{icon[r['status']]} {r['check']:20} {r['detail']}")
            if r["fix"] and r["status"] in ("FAIL", "WARN"):
                print(f"{'':26}-> {r['fix']}")
        nf = sum(1 for r in results if r["status"] == "FAIL")
        nw = sum(1 for r in results if r["status"] == "WARN")
        print("-" * 78)
        print(f"FAIL {nf}  WARN {nw}  ->  " + ("放行" if nf == 0 else "不许收工，先修 FAIL"))
    sys.exit(1 if any(r["status"] == "FAIL" for r in results) else 0)


if __name__ == "__main__":
    main()
