#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deck_plan.json -> SlideDSL 骨架生成器（RPA 与渲染器之间缺失的那座桥）

为什么需要它：
  RPA 的 deck_plan 里带着 layout_id 与 slot_specs[].pptx_in 几何，按官方 USAGE
  本应由"外部渲染器读取 layout_id / slot_specs / slot_assignments 生成 PPTX"。
  但 slidep 吃的是手写 SlideDSL，没有适配器 —— 于是 Agent 会绕开 RPA 直接手写，
  plan 沦为一次性产物。本脚本把这一步补上：没有合格的 deck_plan 就生成不了骨架。

用法:
  python deckplan2slide.py --plan deck_plan.json --rpa-root <RPA目录> --out slides/
                           [--theme paper_blue] [--force] [--node <node路径>]

产物:
  slides/NN.slide   每页骨架（slot 几何已摆好，内容占位）
  slides/_slots.md  每页 slot 容量清单（max_chars / 字号 hint），填内容时照着写

退出码: 0 正常；1 = 前置条件不满足（没有 plan / plan 未完成 / 目标目录非空），
        此时禁止手写页面 —— 这正是"逼 Agent 先跑 RPA"的机制。

注意: JSX 模板统一用 % 格式化，避免 f-string 把 {{ }} 吃掉（踩过坑）。
"""
import argparse
import json
import os
import subprocess
import sys

PX_PER_IN = 96.0          # 13.333in -> 1280px
PT_TO_PX = 96.0 / 72.0    # 1pt = 1.333px

FONT = "Microsoft YaHei"

# 主题 token（从 <rpa-root>/assets/layout-library/themes.json 读取，失败回退）
THEME = {
    "bg": "#F8FAFC", "panel": "#FFFFFF", "text": "#0F172A",
    "muted": "#64748B", "accent1": "#2563EB", "accent2": "#0EA5E9",
    "accent3": "#14B8A6", "border": "#CBD5E1",
}


def load_theme(rpa_root, theme_id):
    global THEME
    p = os.path.join(rpa_root, "assets", "layout-library", "themes.json")
    try:
        d = json.load(open(p, encoding="utf-8"))
        for t in d.get("themes", []):
            if t.get("id") == theme_id:
                THEME = {k: t[k] for k in
                         ("bg", "panel", "text", "muted", "accent1",
                          "accent2", "accent3", "border")}
                return
        print("  [warn] themes.json 无 %s，用默认 paper_blue token" % theme_id)
    except Exception as e:
        print("  [warn] 读 themes.json 失败(%s)，用默认 token" % e)


# 8 位 hex：accent1 淡底（约 10% 不透明度），用于强调条/淡底卡片
def _fade(hex6, alpha="1A"):
    return (hex6 + alpha) if hex6.startswith("#") and len(hex6) == 7 else hex6

IMAGE_TYPES = {"image", "figure", "visual", "photo", "chart", "diagram"}
TABLE_TYPES = {"table", "matrix", "grid"}
TITLE_IDS = {"title", "headline", "kicker", "subtitle"}

# ---------------- JSX 模板（% 格式化，大括号原样保留） ----------------

TPL_HEAD = """<Slide style={{ width: '1280px', height: '720px', background: '%(bg)s', position: 'relative' }}>
  {/* 自动生成骨架 · layout_id=%(lid)s · category=%(cat)s · theme=%(theme)s */}
  {/* 标题：%(title)s */}
  {/* evidence_ids: %(evs)s */}
"""

# 标题：accent 竖条 + 文字（pos_bar/pos_txt 由 render_slot 预计算）
TPL_TITLE = """  {/* [%(sid)s] %(label)s%(cap)s · %(fs)dpx */}
  <Box style={{ %(pos_bar)s, background: '%(accent1)s' }} />
  <Box style={{ %(pos_txt)s, flexDirection: 'column', justifyContent: 'center' }}>
    <Text style={{ fontSize: %(fs)d, color: '%(text)s', fontFamily: '%(font)s', fontWeight: 'bold' }}>
      TODO: %(label)s
    </Text>
  </Box>
"""

# 正文槽：语义色竖条（版面库 SVG 的视觉语言）+ 可选卡片底（%(card)s；重叠槽留空避免遮挡）
TPL_TEXT = """  {/* [%(sid)s] %(label)s%(cap)s · %(fs)dpx */}
  <Box style={{ %(pos_bar)s, background: '%(bar_c)s' }} />
  <Box style={{ %(pos)s%(card)s, flexDirection: 'column', justifyContent: %(jc)s, paddingTop: %(pt)d, paddingLeft: 24, paddingRight: 24 }}>
    <Text style={{ fontSize: %(fs)d, color: '%(color)s', fontFamily: '%(font)s', fontWeight: '%(weight)s' }}>
      TODO: %(label)s
    </Text>
  </Box>
"""

# 图片槽：可选卡片底
TPL_IMAGE = """  {/* [%(sid)s] %(label)s%(cap)s */}
  <Box style={{ %(pos_bar)s, background: '%(bar_c)s' }} />
  <Box style={{ %(pos)s%(card)s }}>
    <Image src="assets/TODO.png" style={{ width: '100%%', height: '100%%', objectFit: 'contain' }} />
  </Box>
"""

TPL_TABLE = """  {/* [%(sid)s] %(label)s%(cap)s */}
  <Box style={{ %(pos_bar)s, background: '%(bar_c)s' }} />
  <Box style={{ %(pos)s%(card)s, padding: 6 }}>
    <Table
      style={{ width: '100%%', height: '100%%' }}
      defaultTextStyle={{ fontSize: 14, color: '%(text)s', fontFamily: '%(font)s', textAlign: 'left' }}
      defaultCellStyle={{ padding: 12, border: { left: { width: 1, color: '%(border)s' }, right: { width: 1, color: '%(border)s' }, top: { width: 1, color: '%(border)s' }, bottom: { width: 1, color: '%(border)s' } } }}
      cells={ [ ['列1', '列2'], ['TODO', 'TODO'] ] }
    />
  </Box>
"""

TPL_FOOT = """  {/* C 页脚区 660-720（母版三区硬约束） */}
  <Box style={{ position: 'absolute', top: 660, left: 40, right: 40, height: 60, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
    <Box style={{ width: '100%%', height: 1, background: '%(border)s', position: 'absolute', top: 0, left: 0, right: 0 }} />
    <Text style={{ fontSize: 14, color: '%(muted)s', fontFamily: '%(font)s', marginTop: 14 }}>%(title)s</Text>
    <Text style={{ fontSize: 16, color: '%(accent1)s', fontFamily: '%(font)s', fontWeight: 'bold', marginTop: 14 }}>%(nn)02d / %(total)d</Text>
  </Box>
</Slide>
"""


def run_get_layout(rpa_root, node, layout_id, theme, cache_dir):
    """调 RPA `get` 拿版面定义，结果缓存"""
    os.makedirs(cache_dir, exist_ok=True)
    cf = os.path.join(cache_dir, layout_id + ".json")
    if os.path.exists(cf):
        return json.load(open(cf, encoding="utf-8"))
    out = subprocess.run(
        [node, os.path.join(rpa_root, "server", "cli.mjs"),
         "get", layout_id, "--theme-id", theme, "--detail-level", "full"],
        cwd=rpa_root, capture_output=True, text=True, encoding="utf-8"
    )
    raw = (out.stdout or "").strip()
    start = raw.find("{")
    if start < 0:
        print("  [warn] 取版面 %s 失败: %s" % (layout_id, (out.stderr or "")[:200]))
        return None
    d = json.loads(raw[start:])
    json.dump(d, open(cf, "w", encoding="utf-8"), ensure_ascii=False)
    return d


def to_px(box_in):
    return "position: 'absolute', top: %d, left: %d, width: %d, height: %d" % (
        round(box_in["y"] * PX_PER_IN), round(box_in["x"] * PX_PER_IN),
        round(box_in["w"] * PX_PER_IN), round(box_in["h"] * PX_PER_IN),
    )


def font_px(capacity):
    return int(round((capacity or {}).get("font_pt_hint", 20) * PT_TO_PX))


# 语义竖条配色：问题/缺口类用 accent2，其余用 muted（对齐版面库 SVG 的槽位配色语言）
BAR_WARM = ("gap", "problem", "limitation", "limit", "challenge", "risk", "question")


def bar_color(sid, stype):
    s = (sid or "").lower()
    t = (stype or "").lower()
    if any(k in s for k in BAR_WARM) or any(k in t for k in BAR_WARM):
        return THEME["accent2"]
    return THEME["muted"]


def slice_flags(specs):
    """标记与其它槽位几何重叠的槽（重叠面积占自身 >10%）。

    版面库里存在层叠式版面（如 RM-GAP-04 的 gap/ours 与 contribution 上下叠放）。
    这类槽不给卡片底，避免实体遮挡与 ELEMENT_COLLISION；竖条仍保留，视觉不塌。
    """
    boxes = [(s.get("pptx_in") or s.get("box") or
              {"x": 0, "y": 0, "w": 0, "h": 0}) for s in specs]
    flags = []
    for i, a in enumerate(boxes):
        hit = False
        for j, b in enumerate(boxes):
            if i == j:
                continue
            ox = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
            oy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
            area = a["w"] * a["h"] or 1.0
            if (ox * oy) / area > 0.10:
                hit = True
                break
        flags.append(hit)
    return flags


def render_slot(slot, overlap=False):
    sid = slot.get("slot_id", "slot")
    stype = (slot.get("slot_type") or "text").lower()
    cap = slot.get("capacity") or {}
    fs = font_px(cap)
    maxchars = cap.get("max_chars")
    box = slot.get("pptx_in") or slot.get("box") or {"x": 0, "y": 0, "w": 1, "h": 1}
    t, l = round(box["y"] * PX_PER_IN), round(box["x"] * PX_PER_IN)
    h, w = round(box["h"] * PX_PER_IN), round(box["w"] * PX_PER_IN)
    card = ("" if overlap else
            ", background: '%s', borderRadius: 12, border: '1px solid %s'"
            % (THEME["panel"], THEME["border"]))
    ctx = {
        "sid": sid,
        "label": slot.get("label_zh") or sid,
        "cap": (" · ≤%d字" % maxchars) if maxchars else "",
        "fs": fs,
        "pos": to_px(box),
        "pos_bar": ("position: 'absolute', top: %d, left: %d, width: 6, height: %d"
                    % (t + (26 if overlap else max(0, (h - 52) // 2)), l, min(52, h))),
        "jc": ("'flex-start'" if overlap else "'center'"),
        "pt": (20 if overlap else 0),
        "bar_c": bar_color(sid, stype),
        "card": card,
        "font": FONT, "text": THEME["text"], "muted": THEME["muted"],
        "border": THEME["border"], "panel": THEME["panel"],
        "accent1": THEME["accent1"], "fade": _fade(THEME["accent1"]),
        "color": THEME["text"], "weight": "normal",
    }
    if stype in TABLE_TYPES:
        return TPL_TABLE % ctx
    if stype in IMAGE_TYPES:
        return TPL_IMAGE % ctx
    if sid.lower() in ("title", "headline") or stype == "title":
        # 标题：加长竖条（52px）+ 文字右移 22px
        ctx["pos_bar"] = ("position: 'absolute', top: %d, left: %d, width: 6, height: %d"
                          % (t + max(0, (h - 52) // 2), l, min(52, h)))
        ctx["pos_txt"] = ("position: 'absolute', top: %d, left: %d, width: %d, height: %d"
                          % (t, l + 22, w - 22, h))
        return TPL_TITLE % ctx
    if sid.lower() in ("kicker", "subtitle"):
        # 副标题：accent 色粗体，无卡片底（避免封面层叠碰撞）
        ctx["color"] = THEME["accent1"]
        ctx["weight"] = "bold"
        ctx["card"] = ""
        return TPL_TEXT % ctx
    if stype in ("takeaway", "headline", "insight", "conclusion"):
        # 强调槽：accent 淡底（重叠时同样留空）
        if not overlap:
            ctx["card"] = (", background: '%s', borderRadius: 12" % _fade(THEME["accent1"]))
        ctx["weight"] = "bold"
    return TPL_TEXT % ctx


def page_slug(slide):
    """页面文件名的语义后缀。

    preflight 会判 NON_CANONICAL_PAGE_NAME：只有两位序号（01.slide）不算规范名，
    页序稳定性依赖语义后缀。分类名最稳，退化时用 title 的 ASCII 部分。
    """
    import re as _re
    cat = (slide.get("category") or "").strip().lower()
    cat = _re.sub(r"[^a-z0-9]+", "_", cat).strip("_")
    if cat:
        return cat
    t = _re.sub(r"[^A-Za-z0-9]+", "_", slide.get("title") or "").strip("_").lower()
    return t or "page"


def build_slide(slide, layout, total, theme):
    n = slide.get("index")
    title = (slide.get("title") or "").replace("{", "").replace("}", "")
    head = TPL_HEAD % {
        "bg": THEME["bg"], "lid": slide.get("layout_id"), "cat": slide.get("category"),
        "theme": theme, "title": title,
        "evs": ", ".join(slide.get("evidence_ids") or []),
    }
    body = []
    if not layout:
        body.append("  {/* [ERROR] 取不到版面定义，检查 layout_id 与 --rpa-root */}\n")
    else:
        order = layout.get("reading_order") or []
        specs = layout.get("slot_specs") or []
        by_id = {s.get("slot_id"): s for s in specs}
        ordered = [by_id[k] for k in order if k in by_id]
        ordered += [s for s in specs if s.get("slot_id") not in order]
        ordered = [s for s in ordered
                   if not (s.get("collapse_when_empty") and not s.get("required"))]
        flags = slice_flags(ordered)
        for s, ov in zip(ordered, flags):
            body.append(render_slot(s, overlap=ov))
            body.append("\n")
    foot = TPL_FOOT % {
        "border": THEME["border"], "muted": THEME["muted"], "font": FONT,
        "accent1": THEME["accent1"],
        "title": title, "nn": n, "total": total,
    }
    return head + "\n" + "".join(body) + foot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--rpa-root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--theme", default="paper_blue")
    ap.add_argument("--node", default="node")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    load_theme(args.rpa_root, args.theme)  # 主题 token 前置加载

    # ---- 前置门禁：没有合格的 plan 就不许生成 ----
    if not os.path.exists(args.plan):
        sys.stderr.write("[FAIL] 找不到 %s。必须先跑完 RPA 的 normalize-content + plan，"
                         "不要手搓页面。\n" % args.plan)
        return 1
    plan = json.load(open(args.plan, encoding="utf-8"))
    slides = plan.get("slides") or []
    if not slides:
        sys.stderr.write("[FAIL] deck_plan 里没有 slides。\n")
        return 1
    if plan.get("pipeline_status") != "plan_complete":
        sys.stderr.write("[FAIL] deck_plan 的 pipeline_status = %r，不是 plan_complete。"
                         "先把规划跑完整再生成骨架。\n" % plan.get("pipeline_status"))
        return 1

    os.makedirs(args.out, exist_ok=True)
    if not args.force:
        existing = [f for f in os.listdir(args.out) if f.endswith(".slide")]
        if existing:
            sys.stderr.write("[FAIL] %s 已有 %d 个 .slide。加 --force 覆盖，或换 --out。\n"
                             % (args.out, len(existing)))
            return 1

    cache_dir = os.path.join(args.out, ".cache", "layouts")
    manifest = ["# slot 容量清单（填内容时照此写，超了会被预检判 CAPACITY_EXCEEDED）\n"]
    total = len(slides)

    for s in slides:
        lid = s.get("layout_id")
        d = run_get_layout(args.rpa_root, args.node, lid, args.theme, cache_dir)
        layout = (d or {}).get("layout") if d else None
        fname = "%02d_%s.slide" % (s["index"], page_slug(s))
        open(os.path.join(args.out, fname), "w",
             encoding="utf-8").write(build_slide(s, layout, total, args.theme))

        rows = []
        for spec in (layout or {}).get("slot_specs", []):
            cap = spec.get("capacity") or {}
            rows.append("| %s | %s | %s | %s | %spt | %s |" % (
                spec.get("slot_id"), spec.get("slot_type"),
                cap.get("max_chars") or "-", cap.get("max_lines") or "-",
                cap.get("font_pt_hint") or "-", spec.get("label_zh") or ""))
        manifest.append("\n## P%02d · %s · `%s`\n" % (s["index"], s.get("title"), lid))
        manifest.append("| slot | type | max_chars | max_lines | font | 说明 |\n"
                        "| :-- | :-- | :-- | :-- | :-- | :-- |\n")
        manifest.append(("\n".join(rows) + "\n") if rows else "(无 slot 信息)\n")
        print("  P%02d %s -> %s (%d slots)" % (s["index"], lid, fname, len(rows)))

    open(os.path.join(args.out, "_slots.md"), "w", encoding="utf-8").write("\n".join(manifest))
    print("\n生成 %d 页骨架 -> %s/" % (total, args.out))
    print("容量清单 -> %s" % os.path.join(args.out, "_slots.md"))
    print("\n下一步：按 _slots.md 的容量往骨架里填内容，逐页 slidep-validate。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
