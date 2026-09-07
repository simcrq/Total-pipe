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
C_BG = "#FFFFFF"
C_TITLE = "#1E4FA8"
C_TEXT = "#1A2230"
C_MUTED = "#4A5568"
C_LINE = "#D6DCE5"

IMAGE_TYPES = {"image", "figure", "visual", "photo", "chart", "diagram"}
TABLE_TYPES = {"table", "matrix", "grid"}
TITLE_IDS = {"title", "headline", "kicker", "subtitle"}

# ---------------- JSX 模板（% 格式化，大括号原样保留） ----------------

TPL_HEAD = """<Slide style={{ width: '1280px', height: '720px', background: '%(bg)s', position: 'relative' }}>
  {/* 自动生成骨架 · layout_id=%(lid)s · category=%(cat)s · theme=%(theme)s */}
  {/* 标题：%(title)s */}
  {/* evidence_ids: %(evs)s */}
"""

TPL_TEXT = """  {/* [%(sid)s] %(label)s%(cap)s · %(fs)dpx */}
  <Box style={{ %(pos)s, flexDirection: 'column', justifyContent: 'center' }}>
    <Text style={{ fontSize: %(fs)d, color: '%(color)s', fontFamily: '%(font)s', fontWeight: '%(weight)s' }}>
      TODO: %(label)s
    </Text>
  </Box>
"""

TPL_IMAGE = """  {/* [%(sid)s] %(label)s%(cap)s */}
  <Image src="assets/TODO.png" style={{ %(pos)s, objectFit: 'contain' }} />
"""

TPL_TABLE = """  {/* [%(sid)s] %(label)s%(cap)s */}
  <Box style={{ %(pos)s }}>
    <Table
      style={{ width: '100%%', height: '100%%' }}
      defaultTextStyle={{ fontSize: 14, color: '%(text)s', fontFamily: '%(font)s', textAlign: 'left' }}
      defaultCellStyle={{ padding: 12, border: { left: { width: 1, color: '%(line)s' }, right: { width: 1, color: '%(line)s' }, top: { width: 1, color: '%(line)s' }, bottom: { width: 1, color: '%(line)s' } } }}
      cells={ [ ['列1', '列2'], ['TODO', 'TODO'] ] }
    />
  </Box>
"""

TPL_FOOT = """  {/* C 页脚区 660-720（母版三区硬约束） */}
  <Box style={{ position: 'absolute', top: 660, left: 40, right: 40, height: 60, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
    <Box style={{ width: '100%%', height: 1, background: '%(line)s', position: 'absolute', top: 0, left: 0, right: 0 }} />
    <Text style={{ fontSize: 14, color: '%(muted)s', fontFamily: '%(font)s', marginTop: 14 }}>%(title)s</Text>
    <Text style={{ fontSize: 16, color: '%(title_c)s', fontFamily: '%(font)s', fontWeight: 'bold', marginTop: 14 }}>%(nn)02d / %(total)d</Text>
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


def render_slot(slot):
    sid = slot.get("slot_id", "slot")
    stype = (slot.get("slot_type") or "text").lower()
    cap = slot.get("capacity") or {}
    fs = font_px(cap)
    maxchars = cap.get("max_chars")
    ctx = {
        "sid": sid,
        "label": slot.get("label_zh") or sid,
        "cap": (" · ≤%d字" % maxchars) if maxchars else "",
        "fs": fs,
        "pos": to_px(slot.get("pptx_in") or slot.get("box")),
        "font": FONT, "text": C_TEXT, "line": C_LINE, "muted": C_MUTED,
        "title_c": C_TITLE, "color": C_TEXT, "weight": "normal",
    }
    if stype in TABLE_TYPES:
        return TPL_TABLE % ctx
    if stype in IMAGE_TYPES:
        return TPL_IMAGE % ctx
    if sid.lower() in TITLE_IDS or stype == "title":
        ctx["color"] = C_TITLE
        ctx["weight"] = "bold"
    elif stype in ("takeaway", "headline"):
        ctx["weight"] = "bold"
    return TPL_TEXT % ctx


def build_slide(slide, layout, total, theme):
    n = slide.get("index")
    title = (slide.get("title") or "").replace("{", "").replace("}", "")
    head = TPL_HEAD % {
        "bg": C_BG, "lid": slide.get("layout_id"), "cat": slide.get("category"),
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
        for s in ordered:
            if s.get("collapse_when_empty") and not s.get("required"):
                continue
            body.append(render_slot(s))
            body.append("\n")
    foot = TPL_FOOT % {
        "line": C_LINE, "muted": C_MUTED, "font": FONT, "title_c": C_TITLE,
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
        open(os.path.join(args.out, "%02d.slide" % s["index"]), "w",
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
        print("  P%02d %s -> %02d.slide (%d slots)" % (s["index"], lid, s["index"], len(rows)))

    open(os.path.join(args.out, "_slots.md"), "w", encoding="utf-8").write("\n".join(manifest))
    print("\n生成 %d 页骨架 -> %s/" % (total, args.out))
    print("容量清单 -> %s" % os.path.join(args.out, "_slots.md"))
    print("\n下一步：按 _slots.md 的容量往骨架里填内容，逐页 slidep-validate。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
