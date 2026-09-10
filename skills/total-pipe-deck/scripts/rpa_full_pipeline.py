#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rpa_full_pipeline.py — 阶段 3「RPA 全流程」一条命令跑通，任一步 FAIL 即中断。

为什么需要它
------------
之前 Agent 第一次跑流水线时，阶段 3 只在 CLI 里挑了 3 个命令（normalize-content /
plan / validate-deck），把 preflight、visual-fit-preflight、group-fit-preflight、
figure-placement 全跳过了。原因不是偷懒，是这几条命令的输入格式没有现成模板，
Agent 得先猜；猜不中就绕过。

本脚本把阶段 3 固化成一条命令，输入全部自动装配，并且 fail-fast：

    S1  normalize-content     → status 必须 valid
    S2  plan                  → pipeline_status 必须 plan_complete
    S3  validate-deck         → status 必须 valid
    S4  deckplan2slide.py     → 生成 SlideDSL 骨架（可选，默认开）
    S5  preflight             → preflight_complete 且 status != invalid
    S6  visual-fit-preflight  → 每张图 status 不得 fail
    S7  group-fit-preflight   → 每页 ≥2 图时检查分组几何
    S8  design_land.py        → 出 design_contract.json + 设计任务书（只做准备，恒不 FAIL）

S8 为什么必须有
---------------
版面库契约每页只给 4–8 个槽、正文下限 18pt（实测 layouts.json v2.0.0），骨架忠实执行
这份契约 → 每页 ~8 个大框，观感必然空旷。S8 是「阶段 3 之后、渲染之前」的设计落地层：
它把字号管辖权从 layouts.json 接过来（design_contract.json，默认下限 10.5pt），
并产出一份设计任务书供 Agent 在槽位内部做排版细化。
注意：S8 只做准备，**构图交给人/Agent**，脚本不代写设计。

顺序说明
--------
S5 preflight 会真实查磁盘上的 source_files，所以**必须**先跑 S4 生成骨架，
否则一定报 NO_PAGE_SOURCES（空 slides 目录）。0.3.x 之后 preflight 不再是纯
声明校验，把它排在骨架之后才有意义。

另外 platform 默认跟随宿主机（win32/darwin/linux）。写死 win32 在非 Windows 机器上
必然报 PROJECT_PATH_NOT_DRIVE_ABSOLUTE 并连带触发 FILESYSTEM_VERIFY_SKIPPED
（"无法验证文件是否存在"），把可验证的校验降级成瞎猜。

任一步 FAIL → 立即退出，退出码 1，并打印「怎么修」。

用法
----
    python rpa_full_pipeline.py \
        --rpa-input F:/x/rpa_input.json \
        --out-dir   F:/x \
        --project   F:/x/deck \
        --assets-map F:/x/assets_map.json

图片映射（S5/S6 必需，缺失会 WARN 而不是静默跳过）
------------------------------------------------
S5 需要知道「第几页放了哪张图、图的像素尺寸」。三种来源，按优先级：

  1. --assets-map map.json     {"4": ["assets/fig1.png"], "6": ["assets/fig3a.png"]}
  2. --assets-from-slides      从 <project>/slides/NN.slide 里 grep src="assets/..."，
                               并按文件名数字猜页号（01.slide → 第 1 页）
  3. 都没有                     → S5/S6 标记 SKIP，报告里明确列出「未覆盖的页」

注意：图片尺寸按 magic bytes 嗅探，不看扩展名。本项目的 assets/*.png 实际是
JPEG 内容（从 paperworkflow 的 images/ 复制时只改了后缀），按扩展名解析会崩。
"""

import argparse
import json
import os
import re
import struct
import subprocess
import sys
from datetime import datetime

PX_PER_INCH = 96.0

DEFAULT_RPA_ROOT = r"C:/Users/Beibei/plugins/research-ppt-assistant"
DEFAULT_NODE = r"D:/Node24/node.exe"
DEFAULT_PLATFORM = "darwin" if sys.platform == "darwin" else (
    "win32" if os.name == "nt" else "linux")


# ---------------------------------------------------------------------------- 工具

def die(msg):
    print("[FATAL] " + msg, file=sys.stderr)
    sys.exit(1)


def run_node(node, rpa_root, args, cwd=None):
    r = subprocess.run([node, "server/cli.mjs"] + args, cwd=cwd or rpa_root,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def run_cli_json(node, rpa_root, args):
    """跑 RPA CLI 并解析 JSON；解析失败时把原文前 800 字符抛出来方便排查。"""
    code, out, err = run_node(node, rpa_root, args)
    txt = out.strip()
    try:
        return json.loads(txt)
    except Exception:
        snippet = (txt or err or "")[:800]
        raise RuntimeError("RPA 输出不是合法 JSON（命令: %s）:\n%s" % (" ".join(args), snippet))


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    return path


def predict_contain_fill(src_ar, slot_w, slot_h):
    """预测 contain 策略下的面积填充率（与 RPA metrics.visual_fill.area_ratio 同口径）。"""
    if slot_w <= 0 or slot_h <= 0 or src_ar <= 0:
        return 0.0
    slot_ar = slot_w / slot_h
    if src_ar >= slot_ar:          # 受宽度限制
        rw, rh = slot_w, slot_w / src_ar
    else:                          # 受高度限制
        rh, rh2 = slot_h, slot_h * src_ar
        rw, rh = rh2, slot_h
    return min(1.0, (rw * rh) / (slot_w * slot_h))


def suggest_layouts(node, rpa_root, theme, text_chars, image_count, src_ar, top=3):
    """S5 失败时给替代版面建议：按图槽几何对源图长宽比的 contain 填充率排序。"""
    try:
        d = run_cli_json(node, rpa_root, ["search", "--text-chars", str(int(text_chars)),
                                          "--image-count", str(int(image_count)),
                                          "--k", "12", "--include-slots",
                                          "--theme-id", theme])
    except Exception:
        return []
    out = []
    for r in d.get("results", []):
        slots = [s for s in (r.get("slot_specs") or []) if s.get("slot_type") == "image"]
        if not slots:
            continue
        b = (slots[0].get("pptx_in") or {})
        w, h = float(b.get("w", 0)) * PX_PER_INCH, float(b.get("h", 0)) * PX_PER_INCH
        fill = predict_contain_fill(src_ar, w, h)
        out.append({
            "id": r.get("id"),
            "category": r.get("category"),
            "name_zh": r.get("variant_name_zh") or "",
            "score": r.get("score"),
            "slot_ar": round(w / h, 2) if h else 0,
            "fill": round(fill, 3),
            "pref_ar": (r.get("capacity") or {}).get("preferred_visual_aspect_ratio"),
        })
    out.sort(key=lambda x: -x["fill"])
    return out[:top]


def img_size(path):
    """按 magic bytes 嗅探尺寸 —— 扩展名不可靠（.png 可能是 JPEG）。"""
    with open(path, "rb") as f:
        head = f.read(64)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", head[16:24])
    if head[:2] == b"\xff\xd8":
        with open(path, "rb") as f:
            d = f.read()
        i = 2
        while i < len(d) - 9:
            if d[i] != 0xFF:
                i += 1
                continue
            m = d[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                     0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", d[i + 5:i + 9])
                return w, h
            if m == 0xD8 or m == 0xD9 or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            if m == 0xFF:
                i += 1
                continue
            ln = struct.unpack(">H", d[i + 2:i + 4])[0]
            i += 2 + ln
        raise ValueError("JPEG 里找不到 SOF: " + os.path.basename(path))
    raise ValueError("不支持的图片格式: " + os.path.basename(path))


def get_layout(node, rpa_root, layout_id, theme, cache):
    """取版面几何（带缓存，避免重复调 CLI）。"""
    if layout_id in cache:
        return cache[layout_id]
    d = run_cli_json(node, rpa_root, ["get", layout_id, "--theme-id", theme,
                                      "--detail-level", "full"])
    L = d.get("layout") or d
    cache[layout_id] = L
    return L


def image_slots(layout):
    """版面里 slot_type == image 的槽（按 reading_order 排序）。"""
    order = layout.get("reading_order") or [s["slot_id"] for s in layout.get("slot_specs", [])]
    slots = [s for s in layout.get("slot_specs", []) if s.get("slot_type") == "image"]
    def key(s):
        return order.index(s["slot_id"]) if s["slot_id"] in order else 999
    return sorted(slots, key=key)


def slot_bbox_px(slot):
    b = slot.get("pptx_in") or {}
    return {
        "x": round(float(b.get("x", 0)) * PX_PER_INCH, 3),
        "y": round(float(b.get("y", 0)) * PX_PER_INCH, 3),
        "width": round(float(b.get("w", 0)) * PX_PER_INCH, 3),
        "height": round(float(b.get("h", 0)) * PX_PER_INCH, 3),
    }


# ------------------------------------------------------------------ 图片映射收集

def collect_assets(args, plan):
    """返回 {页号(1-based): [图片绝对路径, ...]}"""
    mapping = {}

    if args.assets_map:
        try:
            raw = json.load(open(args.assets_map, encoding="utf-8"))
        except Exception as e:
            die("读 --assets-map 失败: %s" % e)
        for k, v in raw.items():
            n = int(k)
            lst = v if isinstance(v, list) else [v]
            mapping[n] = [os.path.join(args.project, p) if not os.path.isabs(p) else p
                          for p in lst]

    if not mapping and args.assets_from_slides and args.project:
        sd = os.path.join(args.project, "slides")
        if os.path.isdir(sd):
            for fn in sorted(os.listdir(sd)):
                if not fn.endswith(".slide"):
                    continue
                m = re.match(r"(\d+)", fn)
                if not m:
                    continue
                n = int(m.group(1))
                txt = open(os.path.join(sd, fn), encoding="utf-8", errors="replace").read()
                srcs = re.findall(r'src="(assets/[^"]+)"', txt)
                if srcs:
                    mapping[n] = [os.path.join(args.project, s.replace("/", os.sep))
                                  for s in srcs]

    # 只保留真正存在的文件
    clean = {}
    missing = []
    for n, paths in mapping.items():
        keep = []
        for p in paths:
            if os.path.isfile(p):
                keep.append(p)
            else:
                missing.append((n, p))
        if keep:
            clean[n] = keep
    return clean, missing


# ---------------------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="阶段 3 RPA 全流程（fail-fast）")
    ap.add_argument("--rpa-input", required=True, help="rpa_input.json 路径")
    ap.add_argument("--out-dir", required=True, help="content_model / deck_plan 等产物输出目录")
    ap.add_argument("--project", default="", help="PPT 项目目录（preflight + 骨架输出）")
    ap.add_argument("--rpa-root", default=DEFAULT_RPA_ROOT)
    ap.add_argument("--node", default=DEFAULT_NODE)
    ap.add_argument("--platform", default=DEFAULT_PLATFORM,
                    help="preflight 的 renderer_inputs.platform，默认跟随宿主机"
                         "（写死 win32 在非 Windows 机器上会报 PROJECT_PATH_NOT_DRIVE_ABSOLUTE）")
    ap.add_argument("--theme", default="paper_blue")
    ap.add_argument("--design-assets", default="",
                    help="S8 图片资产根目录（含 assets/ 的目录，或 assets/ 目录本身；"
                         "brief/check 用它读图片真实像素算适配矩形）")
    ap.add_argument("--presentation-type", default="group_meeting")
    ap.add_argument("--slide-count", type=int, default=0, help="目标页数提示（实际由 briefs 数决定）")
    ap.add_argument("--detail-level", default="compact")
    ap.add_argument("--assets-map", default="", help='{"4":["assets/fig1.png"]} 形式的 JSON')
    ap.add_argument("--assets-from-slides", action="store_true",
                    help="从 <project>/slides/NN.slide 里 grep 图片引用")
    ap.add_argument("--no-skeleton", action="store_true", help="跳过 S4 骨架生成")
    ap.add_argument("--no-design", action="store_true",
                    help="跳过 S8 设计落地准备（契约 + 设计任务书）")
    ap.add_argument("--min-font", type=float, default=10.5,
                    help="S8 的字号下限（接管 layouts.json 的 18pt 约束）")
    ap.add_argument("--density", nargs=2, type=int, default=[22, 44],
                    metavar=("LO", "HI"), help="S8 每页元素数软目标区间")
    # 注意：默认输出到 slides_skeleton 而不是 slides，避免一键跑覆盖已完成的页面。
    ap.add_argument("--skeleton-out", default="slides_skeleton",
                    help="骨架输出子目录名（默认 slides_skeleton；"
                         "显式传 slides 才会覆盖正式页面）")
    ap.add_argument("--strict", action="store_true", help="warning 也当失败")
    ap.add_argument("--skeleton-force", action="store_true",
                    help="骨架目录已有 .slide 时强制覆盖")
    ap.add_argument("--allow-visual-fail", action="store_true",
                    help="S5 几何不兼容时仍继续（默认会中断；只在你确认手写 SlideDSL "
                         "不按 RPA 槽位摆位时才用）")
    args = ap.parse_args()

    for p in (args.rpa_input,):
        if not os.path.isfile(p):
            die("找不到输入文件: %s" % p)
    if not os.path.isdir(args.rpa_root):
        die("RPA 源码目录不存在: %s" % args.rpa_root)

    work = os.path.join(args.out_dir, "_rpa")
    os.makedirs(work, exist_ok=True)
    # 清掉上一轮的 visual_fit / group_fit：版面换了之后旧输入会残留并误导排查
    for sub in ("visual_fit", "group_fit"):
        p = os.path.join(work, sub)
        if os.path.isdir(p):
            for fn in os.listdir(p):
                if fn.endswith(".json"):
                    try:
                        os.remove(os.path.join(p, fn))
                    except OSError:
                        pass

    steps = []          # [(id, 名称, 状态, 说明)]
    failed = []
    warned = []

    def record(sid, name, status, note):
        steps.append((sid, name, status, note))
        mark = {"PASS": "  OK  ", "WARN": " WARN ", "FAIL": " FAIL ",
                "SKIP": " SKIP "}[status]
        print("[%s] %-4s %-26s %s" % (mark, sid, name, note))
        if status == "FAIL":
            failed.append(sid)
        elif status == "WARN":
            warned.append(sid)

    print("=" * 78)
    print("阶段 3 · RPA 全流程（fail-fast）")
    print("  rpa_input : %s" % args.rpa_input)
    print("  out_dir   : %s" % args.out_dir)
    print("  产物目录  : %s" % work)
    print("=" * 78)

    # ---------------------------------------------------------------- S1
    cm_path = os.path.join(args.out_dir, "content_model.json")
    d = run_cli_json(args.node, args.rpa_root,
                     ["normalize-content", "--file", args.rpa_input,
                      "--detail-level", args.detail_level])
    write_json(cm_path, d)
    st = d.get("status")
    if st == "valid":
        record("S1", "normalize-content", "PASS",
               "status=valid · evidence=%s citation=%s" % (d.get("evidence_count"),
                                                           d.get("citation_count")))
    else:
        record("S1", "normalize-content", "FAIL",
               "status=%s · violations=%s" % (st, d.get("violations")))
        print("\n怎么修：检查 rpa_input.json 的 claims 是否都有 evidence_ids，"
              "以及文本长度是否超出分类容量。")
        return finish(work, steps, failed, warned, args)

    # ---------------------------------------------------------------- S2
    plan_args = ["plan", "--file", args.rpa_input,
                 "--presentation-type", args.presentation_type,
                 "--detail-level", args.detail_level]
    if args.slide_count:
        plan_args += ["--slide-count", str(args.slide_count)]
    plan = run_cli_json(args.node, args.rpa_root, plan_args)
    plan_path = os.path.join(args.out_dir, "deck_plan.json")
    write_json(plan_path, plan)
    n_slides = len(plan.get("slides", []))
    ps = plan.get("pipeline_status")
    if ps == "plan_complete":
        record("S2", "plan", "PASS",
               "plan_complete · %s 页 · score=%s" % (n_slides, plan.get("design_score")))
    else:
        record("S2", "plan", "FAIL",
               "pipeline_status=%s · violations=%s" % (ps, plan.get("violations")))
        print("\n怎么修：页数由 rpa_input.json 的 briefs 条数决定（不是 --slide-count）。"
              "加页要先改 briefs。")
        return finish(work, steps, failed, warned, args)

    # ---------------------------------------------------------------- S3
    # validate-deck 需要把 content_model 塞进 deck_plan，否则校验不完整
    merged = dict(plan)
    merged["content_model"] = d
    vd_in = os.path.join(work, "validate_deck_input.json")
    write_json(vd_in, merged)
    vd = run_cli_json(args.node, args.rpa_root, ["validate-deck", "--file", vd_in])
    write_json(os.path.join(work, "validate_deck.json"), vd)
    vstat = vd.get("status")
    bad = [r for r in (vd.get("results") or []) if r.get("status") not in (None, "valid", "pass")]
    if vstat == "valid" and not bad:
        record("S3", "validate-deck", "PASS", "status=valid · 0 问题页")
    else:
        record("S3", "validate-deck", "FAIL",
               "status=%s · 问题页=%s" % (vstat, [r.get("index") for r in bad]))
        print("\n怎么修：见 %s 的 results[].issues" % os.path.join(work, "validate_deck.json"))
        return finish(work, steps, failed, warned, args)

    # ---------------------------------------------------------------- S4 骨架
    # preflight 会真实查磁盘上的 source_files，所以骨架必须先于它生成。
    skel_dir = ""
    if args.no_skeleton:
        record("S4", "deckplan2slide 骨架", "SKIP", "--no-skeleton")
    elif not args.project:
        record("S4", "deckplan2slide 骨架", "SKIP", "未给 --project")
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        gen = os.path.join(here, "deckplan2slide.py")
        if not os.path.isfile(gen):
            record("S4", "deckplan2slide 骨架", "SKIP", "找不到 " + gen)
        else:
            skel_dir = os.path.join(args.project, args.skeleton_out)
            cmd = [sys.executable, gen, "--plan", plan_path,
                   "--rpa-root", args.rpa_root, "--out", skel_dir,
                   "--node", args.node]
            if args.skeleton_force:
                cmd.append("--force")
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode == 0:
                record("S4", "deckplan2slide 骨架", "PASS", "输出 → " + skel_dir)
            else:
                record("S4", "deckplan2slide 骨架", "FAIL",
                       (r.stdout or r.stderr or "")[-300:].replace("\n", " "))
                return finish(work, steps, failed, warned, args)

    # ---------------------------------------------------------------- S5
    src_files = []
    if args.project and skel_dir and os.path.isdir(skel_dir):
        rel = os.path.relpath(skel_dir, args.project).replace(os.sep, "/")
        src_files = sorted(rel + "/" + f for f in os.listdir(skel_dir)
                           if f.endswith(".slide"))
    pf_in = {
        "renderer_inputs": {
            "requested_renderer": "slidep",
            "renderer_version": "5.4.4",
            "platform": args.platform,
            "project_path": (args.project or "").replace("\\", "/"),
            "project_exists": bool(args.project) and os.path.isdir(args.project),
            "source_files": src_files,
            "live_watch_requested": False,
        },
        "deck_plan": {"theme_id": args.theme, "slides": plan["slides"]},
    }
    pf_path = os.path.join(work, "preflight_input.json")
    write_json(pf_path, pf_in)
    pf = run_cli_json(args.node, args.rpa_root, ["preflight", "--file", pf_path])
    write_json(os.path.join(work, "preflight.json"), pf)
    pstat = pf.get("status")
    pps = pf.get("pipeline_status")
    pissues = []
    rg = pf.get("renderer_guard") or {}
    pissues += rg.get("issues") or []
    if pps == "preflight_complete" and pstat != "invalid":
        note = "%s · status=%s" % (pps, pstat)
        codes = sorted({i.get("code") for i in pissues if i.get("code")})
        if codes:
            note += " · issues=%s" % ",".join(codes)
        record("S5", "preflight", "WARN" if pstat == "warning" else "PASS", note)
        for i in pissues:
            if i.get("code") == "NON_CANONICAL_PAGE_NAME":
                print("        └ 页码文件缺语义后缀，建议改成 01_cover.slide / 02_method.slide 这种。")
            if i.get("code") == "NO_PAGE_SOURCES":
                print("        └ source_files 为空：先跑骨架生成（去掉 --no-skeleton，"
                      "或 --skeleton-out 指到已有 .slide 的目录）。")
    else:
        record("S5", "preflight", "FAIL", "%s · status=%s" % (pps, pstat))
        print("\n怎么修：检查重复 pageId、.jsx 页面源、无效 Windows 路径、Slot Contract 错误。")
        return finish(work, steps, failed, warned, args)

    # --------------------------------------------------- 收集图片映射（S5/S6 用）
    assets, missing = collect_assets(args, plan)
    if missing:
        print("        ! assets-map 里有 %s 个文件不存在，已忽略：%s"
              % (len(missing), missing[:3]))

    # ---------------------------------------------------------------- S5
    layout_cache = {}
    vfit_rows = []
    vfit_fail = []
    vfit_warn = []
    vfit_skip_pages = []

    # 预读 rpa_input 的 briefs，供 visual_type / text_chars 使用
    try:
        BRIEFS = json.load(open(args.rpa_input, encoding="utf-8"))["slide_briefs"]
    except Exception:
        BRIEFS = []

    def brief_info(slide_idx):
        try:
            return BRIEFS[slide_idx]
        except Exception:
            return {}

    def visual_type_of(slide_idx):
        vs = brief_info(slide_idx).get("visuals") or []
        return vs[0].get("visual_type", "scientific_figure") if vs else "scientific_figure"

    for i, s in enumerate(plan.get("slides", [])):
        page = i + 1
        lid = s.get("layout_id")
        if not lid:
            continue
        try:
            L = get_layout(args.node, args.rpa_root, lid, args.theme, layout_cache)
        except Exception as e:
            vfit_skip_pages.append((page, "取版面失败 %s" % e))
            continue
        slots = image_slots(L)
        if not slots:
            continue
        imgs = assets.get(page, [])
        if not imgs:
            # 版面有图槽但本页没映射图：只有 brief 声明了要放图才算缺漏
            declared = int(brief_info(i).get("image_count") or 0)
            if declared > 0:
                vfit_skip_pages.append((page, "brief 声明 %d 张图但无映射" % declared))
            continue

        for k, slot in enumerate(slots):
            if k >= len(imgs):
                break
            ap_path = imgs[k]
            try:
                w, h = img_size(ap_path)
            except Exception as e:
                vfit_skip_pages.append((page, "读图尺寸失败: %s" % e))
                continue
            bbox = slot_bbox_px(slot)
            vtype = visual_type_of(i)
            inp = {
                "visual_id": "P%02d:%s" % (page, slot["slot_id"]),
                "visual_type": vtype,
                "source": {"width": w, "height": h},
                "source_region": {"x": 0, "y": 0, "width": w, "height": h},
                "visual_intent": {"visual_type": vtype, "crop_policy": "full_figure",
                                  "fit_policy": "contain", "priority": "primary_visual"},
                "visual_container": {"container_id": slot["slot_id"], "outer_bbox": bbox},
                "allocated_visual_bbox": bbox,
            }
            f = os.path.join(work, "visual_fit", "P%02d_%s.json" % (page, slot["slot_id"]))
            write_json(f, inp)
            try:
                r = run_cli_json(args.node, args.rpa_root, ["visual-fit-preflight", "--file", f])
            except Exception as e:
                vfit_fail.append((page, slot["slot_id"], "调用失败: %s" % str(e)[:90]))
                continue
            m = r.get("metrics") or {}
            fill = (m.get("visual_fill") or {}).get("area_ratio") or 0
            vfit_rows.append({
                "page": page, "slot": slot["slot_id"], "layout": lid,
                "asset": os.path.basename(ap_path), "src": "%dx%d" % (w, h),
                "status": r.get("status"), "decision": r.get("decision"),
                "src_ar": round(m.get("source_aspect_ratio") or 0, 2),
                "slot_ar": round(m.get("allocated_aspect_ratio") or 0, 2),
                "area_fill": round(fill, 3),
                "reason": r.get("reason") or "",
            })
            if r.get("status") == "fail":
                vfit_fail.append({
                    "page": page, "slot": slot["slot_id"],
                    "why": "%s · 面积占比 %.1f%%" % (r.get("reason"), fill * 100),
                    "src_ar": m.get("source_aspect_ratio") or 0,
                    "text_chars": brief_info(i).get("text_chars") or 150,
                    "image_count": len(imgs),
                    "layout": lid,
                    "asset": os.path.basename(ap_path),
                })
            elif r.get("status") == "warning":
                vfit_warn.append((page, slot["slot_id"], r.get("reason") or ""))

    if vfit_fail:
        record("S6", "visual-fit-preflight", "FAIL",
               "%s 张图几何不兼容" % len(vfit_fail))
        for f in vfit_fail:
            print("        x P%02d %-8s %s  [%s]" % (f["page"], f["slot"], f["why"], f["asset"]))
            print("          当前版面 %s（源图 %.2f:1）" % (f["layout"], f["src_ar"]))
            alts = suggest_layouts(args.node, args.rpa_root, args.theme,
                                   f["text_chars"], f["image_count"], f["src_ar"])
            if alts:
                print("          建议换成：")
                for a in alts:
                    print("            - %-24s %-10s 图槽 %.2f:1 → 填充 %.0f%%  (score %s)"
                          % (a["id"], a["name_zh"], a["slot_ar"], a["fill"] * 100, a["score"]))
        print("\n怎么修：① 把该页 brief 的 category_hint 改成建议版面对应的分类后重跑 plan；"
              "\n        ② 或允许语义裁切时先跑 figure-placement 裁子图再重跑本脚本。")
        if not args.allow_visual_fail:
            return finish(work, steps, failed, warned, args)
        print("        （--allow-visual-fail 已开，继续后续步骤）")
    if vfit_rows:
        st5 = "WARN" if vfit_warn else "PASS"
        record("S6", "visual-fit-preflight", st5,
               "检查 %s 张图 · %s warning" % (len(vfit_rows), len(vfit_warn)))
        for pg, sl, why in vfit_warn[:5]:
            print("        ! P%02d %s : %s" % (pg, sl, why))
    else:
        record("S6", "visual-fit-preflight", "SKIP", "没有可检查的图（无映射或版面无图槽）")
    if vfit_skip_pages:
        print("        ! 未覆盖 %s 页：%s" % (len(vfit_skip_pages), vfit_skip_pages[:4]))

    # ---------------------------------------------------------------- S6
    multi = {}
    for row in vfit_rows:
        multi.setdefault(row["page"], []).append(row)
    groups = {p: rows for p, rows in multi.items() if len(rows) >= 2}
    if not groups:
        record("S7", "group-fit-preflight", "SKIP", "没有多图同页（<2 图/页）")
    else:
        gdir = os.path.join(work, "group_fit")
        gres = []
        for page, rows in sorted(groups.items()):
            children = []
            allocs = []
            for idx, row in enumerate(rows):
                w, h = (int(x) for x in row["src"].split("x"))
                children.append({
                    "visual_id": row["slot"],
                    "source": {"width": w, "height": h},
                    "visual_intent": {"visual_type": "scientific_figure",
                                      "crop_policy": "full_figure",
                                      "fit_policy": "contain"},
                    "priority": idx + 1,
                })
                slot = None
                L = get_layout(args.node, args.rpa_root, rows[0]["layout"], args.theme, layout_cache)
                for sl in image_slots(L):
                    if sl["slot_id"] == row["slot"]:
                        slot = sl
                        break
                allocs.append({"visual_id": row["slot"],
                               "allocated_visual_bbox": slot_bbox_px(slot) if slot else
                               {"x": 0, "y": 0, "width": 1, "height": 1}})
            inp = {
                "visual_group": {
                    "group_id": "P%02d-group" % page,
                    "semantic_relation": "parallel",
                    "relation_strength": "soft",
                    "children": [c["visual_id"] for c in children],
                },
                "children": children,
                "layout_relation": {
                    "shared_alignment": True,
                    "shared_container_style": True,
                    "equal_size": True,
                    "preserve_order": True,
                },
                "baseline_candidate": {
                    "layout_id": rows[0]["layout"],
                    "allocations": allocs,
                },
                "relation_aware_candidates": [],
                "max_relation_replan_attempts": 1,
            }
            f = os.path.join(gdir, "P%02d.json" % page)
            write_json(f, inp)
            try:
                r = run_cli_json(args.node, args.rpa_root, ["group-fit-preflight", "--file", f])
                gres.append((page, r.get("status"), r.get("decision") or "", r.get("reason") or ""))
            except Exception as e:
                gres.append((page, "error", "", str(e)[:120]))
        gbad = [g for g in gres if g[1] == "fail"]
        if gbad:
            record("S7", "group-fit-preflight", "FAIL",
                   "%s 页分组几何冲突" % len(gbad))
            for pg, stt, dec, why in gbad:
                print("        x P%02d %s %s" % (pg, dec, why))
            return finish(work, steps, failed, warned, args)
        record("S7", "group-fit-preflight", "PASS",
               "检查 %s 个多图页" % len(gres))
        for pg, stt, dec, why in gres:
            print("        · P%02d %s %s" % (pg, stt, dec))

    # ---------------------------------------------------------------- S8
    # 设计落地准备：把字号管辖权从 layouts.json(18pt) 接到 design_contract.json(10.5pt)，
    # 并产出设计任务书。只做准备，**不代写构图**；失败降级为 WARN 而不是 FAIL。
    if args.no_design:
        record("S8", "设计落地准备", "SKIP", "--no-design")
    elif not skel_dir or not os.path.isdir(skel_dir):
        record("S8", "设计落地准备", "SKIP", "没有骨架目录可落地")
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        dl = os.path.join(here, "design_land.py")
        if not os.path.isfile(dl):
            record("S8", "设计落地准备", "SKIP", "找不到 " + dl)
        else:
            dlo, dhi = args.density
            contract_path = os.path.join(args.out_dir, "design_contract.json")
            subprocess.run(
                [sys.executable, dl, "contract", "--out", contract_path,
                 "--min-font", str(args.min_font),
                 "--density", str(dlo), str(dhi)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            brief_out = os.path.join(skel_dir, "_design_brief.md")
            cmd = [sys.executable, dl, "brief", "--skeleton", skel_dir,
                   "--out", brief_out, "--min-font", str(args.min_font),
                   "--density", str(dlo), str(dhi)]
            slots_md = os.path.join(skel_dir, "_slots.md")
            if os.path.isfile(slots_md):
                cmd += ["--slots", slots_md]
            # 图片资产根目录：默认按 skel/../assets 猜；给了 --design-assets 就显式指定。
            # brief 会读图片真实像素，为每张图算出「图框宽高比 = 图片比例」的适配矩形。
            design_assets = getattr(args, "design_assets", None)
            if design_assets:
                cmd += ["--assets", design_assets]
            cb = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
            if cb.returncode == 0:
                record("S8", "设计落地准备", "PASS",
                       "字号下限 %.1fpt · 密度目标 %d-%d · 任务书已出" % (
                           args.min_font, dlo, dhi))
                print("        · 契约   → %s" % contract_path)
                print("        · 任务书 → %s" % brief_out)
                print("        · ⚠ 图片图框宽高比必须 = 图片文件自身比例，否则被 cover 裁掉")
                print("        · 下一步：按任务书在槽位内部做设计落地，然后")
                print("          python %s check --slides <落地页目录> --contract %s%s"
                      % (os.path.join(here, "design_land.py"), contract_path,
                         (" --assets " + design_assets) if design_assets else ""))
            else:
                record("S8", "设计落地准备", "WARN",
                       (cb.stdout or cb.stderr or "")[-200:].replace("\n", " "))

    return finish(work, steps, failed, warned, args)


def finish(work, steps, failed, warned, args):
    report = ["# 阶段 3 · RPA 全流程报告", "",
              "生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "",
              "| 步骤 | 名称 | 状态 | 说明 |", "| :-- | :-- | :-- | :-- |"]
    for sid, name, status, note in steps:
        report.append("| %s | %s | %s | %s |" % (sid, name, status,
                                                 str(note).replace("|", "\\|")[:120]))
    report += ["", "FAIL: %s · WARN: %s" % (len(failed), len(warned)), ""]
    rp = os.path.join(work, "_pipeline_report.md")
    with open(rp, "w", encoding="utf-8") as f:
        f.write("\n".join(report))

    print("-" * 78)
    if failed:
        print("结果：FAIL（%s）→ 报告 %s" % (",".join(failed), rp))
        return 1
    if warned and args.strict:
        print("结果：FAIL(--strict)（warning: %s）→ 报告 %s" % (",".join(warned), rp))
        return 1
    print("结果：PASS（warn %s）→ 报告 %s" % (len(warned), rp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
