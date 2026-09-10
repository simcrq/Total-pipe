#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
design_land.py — S8「设计落地层」：阶段 3 之后、阶段 4 渲染之前的那一站。

为什么需要这一站
----------------
版面库契约（research-ppt-assistant/assets/layout-library/layouts.json, v2.0.0）实测：

    每版面槽位数            {4:16, 5:112, 6:114, 7:68, 8:10}  → 单页最多 8 个元素
    1864 个槽 font_pt_hint  全为 None                          → 不规定任何细粒度字号
    minimum_body_font_pt    {20:242, 18:78}                    → 最小合法正文 18pt
    default_body_font_pt    20（全部 320 版面）

骨架生成器忠实执行这份契约 → 每页 ~8 个大框、20pt 正文，观感必然空旷。
这一层不改写骨架，而是在**槽位内部**做排版细化与装饰落地，并用自己的契约
（design_contract.json，默认字号下限 10.5pt）接管字号管辖权。

契约关系（重要，别搞反）
------------------------
    layouts.json 的 18pt 下限  管辖「版面选槽阶段」——S1~S7 必须遵守
    design_contract.json       管辖「槽位内部排版」——S8 及之后遵守

两者不冲突：S5 preflight 已经按 18pt 判过几何兼容并放行；S8 只在已放行的
槽位 bbox 内部再切分，不再选槽。所以 S8 用 10.5pt 图注不违反任何上游判定。

设计原则：不代写设计
--------------------
本脚本只做两件机械的事，构图全部交给 AI：

    brief  —— 汇总几何 / 容量 / 已用内容 / 空白带 / 语汇菜单 → 设计任务书
              （全是软目标，没有模板，没有"必须画什么"）
    check  —— 校验 AI 写好的落地页，只卡**物理不可行**项，
              构图类指标（密度）只 WARN 不 FAIL

子命令
------
    design_land.py contract --out design_contract.json [--min-font 10.5]
    design_land.py brief  --skeleton <骨架目录> --slots <_slots.md> [--content x.md] [--assets <资产目录>...]
    design_land.py check  --slides <落地页目录> [--contract design_contract.json] [--assets <资产目录>...]

退出码：brief/contract 恒 0；check 有 FAIL → 1（--strict 时 WARN 也算 FAIL）。

图片硬约束（v1.1.0 起）
-----------------------
渲染器（slidep）对图片**一律按 cover 裁切到图框比例**，`objectFit` 属性无效——
实测 contain/cover/fill、写在 prop 还是 style，产出逐字节相同。
因此「图片图框的宽高比」是唯一能控制裁切的量：

    图框比例 != 图片比例  →  按较小的一维铺满，另一维被裁掉

契约要求先按图片自身比例算适配矩形，再让图框**等于**该矩形。
brief 会为每张图列出原始尺寸与建议图框；check 对比例不符的图判 FAIL
（IMAGE_ASPECT_MISMATCH）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

CANVAS_W, CANVAS_H = 1280, 720
FOOTER_TOP = 660

DEFAULT_CONTRACT = {
    "design_layer_version": "1.1.0",
    "authority": "S8 之后字号由本契约管辖，不再受 layouts.json minimum_body_font_pt(18) 约束",
    "canvas": {"width": CANVAS_W, "height": CANVAS_H},
    "footer_top_px": FOOTER_TOP,
    "minimum_font_pt": 10.5,
    "font_ladder_pt": {
        "display": [40, 54],
        "headline": [26, 35],
        "body": [16, 20],
        "note": [12, 14],
        "micro": [10.5, 11],
    },
    "density_target_shapes_per_page": [22, 44],
    "line_height_ratio": 1.45,
    # 图片图框的宽高比必须等于图片文件自身的宽高比（相对偏差容差）。
    # 这不是审美要求：渲染器对图片一律按 cover 裁切到图框比例，
    # 图框比例不匹配 = 画面内容被裁掉。见文件中部「图片」一节。
    "image_frame_aspect_tolerance": 0.02,
    "image_fit_mode": (
        "contain —— 先按图片自身比例算出适配矩形，再让图框等于该矩形；"
        "不要先定图框再让图片去适应它（会被 cover 裁切）"
    ),
}

# 语汇菜单：只是「货架」，不是清单。AI 可以全不用，也可以自己发明。
VOCABULARY = [
    ("顶栏标签", "页面顶部一条细标签带：章节名 / 关键词 / 状态徽章，把标题区从 1 个元素变成 3–5 个"),
    ("图注", "图下方 10.5–11pt 的 `Fig. 2e · 说明`，配 1px 上分隔线"),
    ("脚注引文", "页脚左：作者 + 年份 + 期刊；右：页码。中间可插数据来源 / 测试方法"),
    ("指标双列", "把「数值 + 单位」和「对照 / 条件」拆成左右两列，而不是挤进一句话"),
    ("序号徽章", "卡片左上角 01/02/03 的小圆角方块，替代纯文字序号"),
    ("关键词高亮", "一句话里把关键数值用 accent 色单独成段，而不是整句同色"),
    ("来源标注", "数据旁 10.5pt 的灰字：数据来源、测试条件、样本量"),
    ("对比条", "两组数值用横向条 + 数字标注，比纯文字更快读"),
    ("流程箭头", "步骤之间加细箭头 / 连接线，把并列的框串成时序"),
    ("分隔与留白", "用 1px 线 + 12–16px 间距做分组，比加大字号更清晰"),
]

# --------------------------------------------------------------------------- #
# SlideDSL 解析
# --------------------------------------------------------------------------- #

TAG_RE = re.compile(r"<(Box|Image|Text|Slide)\b([^>]*?)(/?)>", re.S)


def _split_top(s: str, sep: str = ","):
    """按顶层分隔符切分，忽略引号与嵌套括号内的分隔符。"""
    parts, buf, quote, depth = [], [], None, 0
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            continue
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def parse_style(attr: str) -> dict:
    m = re.search(r"style=\{\{(.*?)\}\}", attr, re.S)
    if not m:
        return {}
    out = {}
    for part in _split_top(m.group(1)):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def _num(v):
    if v is None:
        return None
    v = v.strip().strip("'\"").replace("px", "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def geom_of(style: dict):
    """返回 (x, y, w, h)；缺失或百分比记为 None。"""
    return (
        _num(style.get("left")),
        _num(style.get("top")),
        _num(style.get("width")),
        _num(style.get("height")),
    )


def strip_text(inner: str) -> str:
    inner = re.sub(r"\{\s*/\*.*?\*/\s*\}", "", inner, flags=re.S)
    inner = re.sub(r"<[^>]+>", "", inner)
    return re.sub(r"\s+", " ", inner).strip()


def elements(src: str) -> list:
    """递归解析 SlideDSL 元素树。"""
    els = []
    for m in TAG_RE.finditer(src):
        tag, attr, selfclose = m.group(1), m.group(2), m.group(3)
        inner, end = "", m.end()
        if not selfclose:
            close = src.find("</%s>" % tag, m.end())
            if close != -1:
                inner = src[m.end():close]
                end = close + len(tag) + 3
        style = parse_style(attr)
        els.append(
            {
                "tag": tag,
                "style": style,
                "geo": geom_of(style),
                "font": _num(style.get("fontSize")),
                "text": strip_text(inner) if tag == "Text" else "",
                "src": (re.search(r'src="([^"]+)"', attr).group(1)
                        if tag == "Image" and 'src="' in attr else None),
                "absolute": style.get("position", "").strip("'\"") == "absolute",
                "children": elements(inner) if tag == "Box" else [],
            }
        )
    return els


def page_title(src: str) -> str:
    m = re.search(r"\{\s*/\*\s*标题：(.+?)\s*\*/\s*\}", src)
    if m:
        return m.group(1).strip()
    for e in elements(src):
        if e["tag"] == "Text" and e["font"] and e["font"] >= 36:
            return e["text"]
    return ""


def page_meta(src: str) -> dict:
    out = {}
    m = re.search(r"layout_id=([A-Z0-9\-]+)", src)
    if m:
        out["layout_id"] = m.group(1)
    m = re.search(r"category=([a-z_]+)", src)
    if m:
        out["category"] = m.group(1)
    m = re.search(r"evidence_ids:\s*([A-Z0-9,\s]+)", src)
    if m:
        out["evidence"] = [x.strip() for x in m.group(1).split(",") if x.strip()]
    return out


# --------------------------------------------------------------------------- #
# 文本度量（CJK 估算，不需要字体文件）
# --------------------------------------------------------------------------- #

def char_w(ch: str, fs: float) -> float:
    return fs if ord(ch) > 0x2E80 else fs * 0.55


def est_lines(text: str, fs: float, avail_w: float) -> int:
    if not text or avail_w <= 0:
        return 1
    lines, cur = 1, 0.0
    for ch in text:
        cw = char_w(ch, fs)
        if cur + cw > avail_w:
            lines += 1
            cur = cw
        else:
            cur += cw
    return lines


def est_text_height(text: str, fs: float, avail_w: float, ratio: float) -> float:
    return est_lines(text, fs, avail_w) * fs * ratio


# --------------------------------------------------------------------------- #
# 图片：读真实像素尺寸 + 算「不变形不裁切」的图框
# --------------------------------------------------------------------------- #
# 为什么必须有这一节
# ------------------
# slidep 的 pptx 写出端对每一张图片**无条件按 cover 裁切到图框比例**：
#   - `objectFit` 写成 prop 也好、写在 style 里也好，填 contain / cover / fill
#     —— 产出**逐字节相同**，属性完全无效；
#   - 唯一影响裁切量的是**图框自身的宽高比**。
# 实测（frame 1107x318 放进 440x209 的图）：
#   srcRect t=19761 b=19761  → 上下各裁 19.76%，恰好 = 1 - (图 AR / 框 AR)
# 所以「把图放进槽位框」这个动作本身就是错的。正确做法是先按图片比例算出
# 适配矩形，再让图框**等于**这个矩形（contain 与 cover 在此时重合，歧义消失）。
#
# 规划器其实已经声明了 allowed_transformations.preserve_visual_aspect = true，
# 只是没人执行它 —— 这一节就是执行者。

def img_size(path: str):
    """读 PNG / JPEG / GIF / BMP / WebP 的像素尺寸。纯标准库，不引 Pillow。"""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
            if head[:2] == b"\xff\xd8":                       # JPEG：扫 SOFn 段
                f.seek(2)
                while True:
                    b = f.read(1)
                    while b and b != b"\xff":
                        b = f.read(1)
                    while b == b"\xff":
                        b = f.read(1)
                    if not b:
                        return None
                    marker = b[0]
                    if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                        continue
                    ln = int.from_bytes(f.read(2), "big")
                    if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                        d = f.read(5)
                        return int.from_bytes(d[3:5], "big"), int.from_bytes(d[1:3], "big")
                    f.seek(ln - 2, 1)
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return int.from_bytes(head[6:8], "little"), int.from_bytes(head[8:10], "little")
            if head[:2] == b"BM":
                return int.from_bytes(head[18:22], "little"), int.from_bytes(head[22:26], "little")
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                if head[12:16] == b"VP8X":
                    w = int.from_bytes(head[24:27], "little") + 1
                    h = int.from_bytes(head[27:30], "little") + 1
                    return w, h
                if head[12:16] == b"VP8 ":
                    return (int.from_bytes(head[26:28], "little") & 0x3FFF,
                            int.from_bytes(head[28:30], "little") & 0x3FFF)
    except Exception:
        return None
    return None


def fit_rect(iw: float, ih: float, bw: float, bh: float):
    """把 iw×ih 等比缩放（contain）进 bw×bh，返回 (w, h)（保留 2 位）。"""
    if not all(x and x > 0 for x in (iw, ih, bw, bh)):
        return None
    s = min(bw / iw, bh / ih)
    return round(iw * s, 2), round(ih * s, 2)


def resolve_asset(src: str, bases: list):
    """按候选基目录找资产文件，返回绝对路径或 None。"""
    if not src:
        return None
    if os.path.isabs(src):
        return src if os.path.isfile(src) else None
    for b in bases:
        if not b:
            continue
        p = os.path.normpath(os.path.join(b, src))
        if os.path.isfile(p):
            return p
    return None


def image_frames(src: str):
    """
    遍历 SlideDSL，返回 [(src_path, frame_w, frame_h, origin)]。
    图框取「图片自身几何」，没有就用最近的、有数值宽高的祖先 Box。
    origin ∈ {'self', 'box', None}
    """
    res = []

    def walk(s: str, inherited):
        i = 0
        while True:
            m = TAG_RE.search(s, i)
            if not m:
                return
            tag, attr, selfclose = m.group(1), m.group(2), m.group(3)
            inner, nxt = "", m.end()
            if not selfclose:
                close = s.find("</%s>" % tag, m.end())
                if close != -1:
                    inner = s[m.end():close]
                    nxt = close + len(tag) + 3
            _, _, w, h = geom_of(parse_style(attr))
            if tag == "Image":
                sm = re.search(r'src="([^"]+)"', attr)
                if w and h:
                    fr = (w, h, "self")
                elif inherited:
                    fr = (inherited[0], inherited[1], "box")
                else:
                    fr = (None, None, None)
                if sm:
                    res.append((sm.group(1), fr[0], fr[1], fr[2]))
            elif inner:
                # Slide / Box / 其它容器：几何能数出来就往下传，供 Image 兜底
                walk(inner, (w, h) if (w and h) else inherited)
            i = nxt

    walk(src, None)
    return res


# --------------------------------------------------------------------------- #
# _slots.md 解析
# --------------------------------------------------------------------------- #

def load_slots(path: str) -> dict:
    """-> {1: [{'slot','type','max_chars','max_lines','font','desc'}], ...}"""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    cur = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^##\s*P(\d+)\b", line)
            if m:
                cur = int(m.group(1))
                out.setdefault(cur, [])
                continue
            m = re.match(r"^\|\s*([a-z0-9_]+)\s*\|\s*([a-z]+)\s*\|\s*([\d\-]+)\s*\|"
                         r"\s*([\d\-]+)\s*\|\s*([\d.]+pt)\s*\|\s*(.*?)\s*\|\s*$", line)
            if m and cur is not None:
                out[cur].append(
                    {
                        "slot": m.group(1),
                        "type": m.group(2),
                        "max_chars": None if m.group(3) == "-" else int(m.group(3)),
                        "max_lines": None if m.group(4) == "-" else int(m.group(4)),
                        "font": m.group(5),
                        "desc": m.group(6),
                    }
                )
    return out


def slot_boxes(src: str) -> list:
    """从骨架里提取 slot 名 → 主框几何（跳过 6px 语义竖条）。"""
    out, seen = [], set()
    for m in re.finditer(r"\{\s*/\*\s*\[([a-z0-9_]+)\].*?\*/\s*\}", src, re.S):
        name = m.group(1)
        if name in seen:
            continue
        for e in elements(src[m.end():]):
            if e["tag"] != "Box" or not e["absolute"]:
                continue
            x, y, w, h = e["geo"]
            if w is None or w == 6 or h is None:
                continue
            if x is None or y is None:
                continue
            txt = " ".join(c["text"] for c in e["children"] if c["tag"] == "Text")
            out.append({"slot": name, "x": x, "y": y, "w": w, "h": h, "text": txt})
            seen.add(name)
            break
    return out


def blank_bands(boxes: list, footer_top: int = FOOTER_TOP, min_h: int = 24):
    """找出未被槽位覆盖的横向空白带（AI 可自由支配的余地）。"""
    spans = sorted(
        [(b["y"], b["y"] + b["h"]) for b in boxes if b["y"] is not None and b["h"]],
    )
    gaps, cursor = [], 0.0
    for y0, y1 in spans:
        if y0 - cursor >= min_h:
            gaps.append((cursor, y0))
        cursor = max(cursor, y1)
    if footer_top - cursor >= min_h:
        gaps.append((cursor, footer_top))
    return gaps


# --------------------------------------------------------------------------- #
# contract
# --------------------------------------------------------------------------- #

def cmd_contract(args):
    c = dict(DEFAULT_CONTRACT)
    if args.min_font:
        c["minimum_font_pt"] = args.min_font
        c["font_ladder_pt"]["micro"] = [args.min_font, args.min_font]
    if args.density:
        lo, hi = args.density
        c["density_target_shapes_per_page"] = [lo, hi]
    if os.path.isfile(args.out):
        try:
            with open(args.out, encoding="utf-8") as f:
                c.update(json.load(f))
        except Exception:
            pass
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)
    print("design_contract → %s" % args.out)
    print("  字号下限 %.1fpt · 密度目标 %s 个/页"
          % (c["minimum_font_pt"], c["density_target_shapes_per_page"]))
    return 0


def load_contract(path: str) -> dict:
    c = dict(DEFAULT_CONTRACT)
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                c.update(json.load(f))
        except Exception as e:
            print("[warn] 契约读取失败，用默认值：%s" % e, file=sys.stderr)
    return c


# --------------------------------------------------------------------------- #
# brief
# --------------------------------------------------------------------------- #

def load_content(path: str) -> dict:
    """可选素材：md 按 `## P07` 分节；json 按 {"7": "..."} / {"P07": "..."}。"""
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    if path.endswith(".json"):
        try:
            d = json.loads(raw)
            out = {}
            for k, v in d.items():
                m = re.search(r"(\d+)", str(k))
                if m:
                    out[int(m.group(1))] = v if isinstance(v, str) else json.dumps(
                        v, ensure_ascii=False, indent=2)
            return out
        except Exception:
            return {}
    out, cur, buf = {}, None, []

    def flush():
        if cur is not None:
            out[cur] = "\n".join(buf).strip()

    for line in raw.splitlines():
        m = re.match(r"^##\s*P(\d+)\b", line)
        if m:
            flush()
            cur, buf = int(m.group(1)), []
            continue
        if cur is not None:
            buf.append(line)
    flush()
    return out


def asset_bases_for(anchor_dir: str, explicit=None):
    """资产查找基目录：显式路径 > anchor/assets > 父级 > anchor 的同级 assets > anchor > cwd。

    SlideDSL 里通常写 `src="assets/xxx.jpg"`，而 anchor 是 `deck/slides`，
    所以必须包含 `deck/`（父级）这一项，否则会拼成 `deck/assets/assets/xxx.jpg`。
    """
    bases = []
    for b in (explicit or []):
        ab = os.path.abspath(b)
        bases.append(ab)
        # 兼容两种写法：给「含 assets/ 的项目根目录」，或直接给「assets/ 目录本身」。
        # SlideDSL 写的是 `src="assets/xxx.jpg"`，所以 base 必须能拼出 <base>/assets/xxx.jpg。
        if os.path.basename(ab) == "assets":
            bases.append(os.path.dirname(ab))
    bases += [
        os.path.abspath(os.path.join(anchor_dir, "assets")),
        os.path.abspath(os.path.join(anchor_dir, os.pardir)),
        os.path.abspath(os.path.join(anchor_dir, os.pardir, "assets")),
        os.path.abspath(anchor_dir),
        os.getcwd(),
    ]
    out, seen = [], set()
    for b in bases:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def cmd_brief(args):
    skel = args.skeleton
    files = sorted(f for f in os.listdir(skel) if f.endswith(".slide") and not f.startswith("_"))
    if not files:
        print("[fail] %s 下没有 .slide" % skel, file=sys.stderr)
        return 1

    slots_by_page = load_slots(args.slots)
    content = load_content(args.content)
    asset_bases = asset_bases_for(skel, args.assets)
    total = len(files)

    L = []
    L.append("# 设计任务书 · S8 设计落地层")
    L.append("")
    L.append("> 这份文档只提供**事实**（几何、容量、已用内容、空白）和**软目标**（密度、字号阶梯）。")
    L.append("> 构图、语汇、增删元素完全由你决定——下面所有建议都可以不采纳，也可以自己发明新的。")
    L.append("> 唯一不能违反的是第 §硬约束 节。")
    L.append("")
    L.append("- 骨架目录：`%s`" % skel)
    L.append("- 槽位清单：`%s`" % (args.slots or "（未给，几何取自骨架实测）"))
    L.append("- 画布：%dx%d px · 页脚保护区 y ≥ %d" % (CANVAS_W, CANVAS_H, FOOTER_TOP))
    L.append("")
    L.append("## §硬约束（违反会被 check 判 FAIL）")
    L.append("")
    L.append("1. 所有元素必须完整落在画布内（x≥0, y≥0, x+w≤%d, y+h≤%d）" % (CANVAS_W, CANVAS_H))
    L.append("2. 字号 ≥ %.1fpt（这是 S8 自己的下限，版面库的 18pt 在这里不适用）" % args.min_font)
    L.append("3. 单个框内的文字不得明显溢出（按 CJK 宽度估算）")
    L.append("4. 页脚区（y %d–%d）保留，页码 `NN / %02d` 正确" % (FOOTER_TOP, CANVAS_H, total))
    L.append("5. **图片图框的宽高比必须等于图片文件自身的宽高比**（容差 %.0f%%）。"
             % (args.tol * 100))
    L.append("   渲染器对图片**一律按 cover 裁切到图框比例**，`objectFit` 属性无效——"
             "把图放进一个比例不同的框里，画面就会被裁掉。")
    L.append("   正确做法：**先按图片比例算出适配矩形，再让图框等于这个矩形**"
             "（每页的「本页图片」小节已给出算好的尺寸，直接用）。")
    L.append("")
    L.append("## §软目标（参考值，超了也只是 WARN）")
    L.append("")
    L.append("- 每页元素数建议 %d–%d 个（骨架现在平均 ~8 个，这是「太空旷」的直接原因）"
             % (args.density[0], args.density[1]))
    L.append("- 字号阶梯：")
    for k, v in DEFAULT_CONTRACT["font_ladder_pt"].items():
        L.append("  - `%s` %s pt" % (k, "–".join(str(x) for x in v)))
    L.append("")
    L.append("## §语汇货架（挑有用的用，不必全用，可自创）")
    L.append("")
    for name, desc in VOCABULARY:
        L.append("- **%s** — %s" % (name, desc))
    L.append("")
    L.append("---")
    L.append("")

    for i, fn in enumerate(files, 1):
        with open(os.path.join(skel, fn), encoding="utf-8") as f:
            src = f.read()
        meta = page_meta(src)
        title = page_title(src)
        boxes = slot_boxes(src)
        declared = slots_by_page.get(i, [])
        cap = {d["slot"]: d for d in declared}

        L.append("## P%02d · %s" % (i, title or fn))
        L.append("")
        L.append("`%s` · layout=%s · category=%s%s"
                 % (fn, meta.get("layout_id", "?"), meta.get("category", "?"),
                    (" · evidence=" + ",".join(meta["evidence"])) if meta.get("evidence") else ""))
        L.append("")

        L.append("### 槽位与已用内容")
        L.append("")
        L.append("| slot | x | y | w | h | 容量 | 骨架字号 | 已用字数 | 当前内容 |")
        L.append("| :-- | --: | --: | --: | --: | :-- | :-- | --: | :-- |")
        for b in boxes:
            d = cap.get(b["slot"], {})
            mc = d.get("max_chars")
            used = len(b["text"])
            L.append("| `%s` | %d | %d | %d | %d | %s | %s | %d%s | %s |"
                     % (b["slot"], b["x"], b["y"], b["w"], b["h"],
                        ("%d字/%d行" % (mc, d["max_lines"])) if mc else "—",
                        d.get("font", "—"), used,
                        (" ⚠超" if mc and used > mc else ""),
                        (b["text"][:34] + "…") if len(b["text"]) > 34 else (b["text"] or "—")))
        L.append("")

        gaps = blank_bands(boxes)
        if gaps:
            L.append("### 可自由支配的空白带")
            L.append("")
            for y0, y1 in gaps:
                L.append("- y %d–%d（高 %d px，全宽可用）" % (y0, y1, y1 - y0))
            L.append("")

        imgs = [e["src"] for e in elements(src) if e["tag"] == "Image" and e["src"]]
        if imgs:
            L.append("### 本页图片（图框宽高比 = 图片宽高比，否则会被裁切）")
            L.append("")
            L.append("| 图片 | 原始像素 | 图片比例 | 当前图框 | 建议图框 | 不改会裁掉 |")
            L.append("| :-- | :-- | --: | :-- | :-- | --: |")
            for srcp, fw, fh, origin in image_frames(src):
                path = resolve_asset(srcp, asset_bases)
                dim = img_size(path) if path else None
                if not dim:
                    L.append("| `%s` | 未找到文件 | — | — | — | — |" % srcp)
                    continue
                iw, ih = dim
                iar = iw / ih
                if fw and fh:
                    far = fw / fh
                    crop = 1 - min(far / iar, iar / far)
                    note = "**%.1f%%**" % (crop * 100) if crop > args.tol else "不会裁"
                    cur = "%dx%d (%.2f)" % (round(fw), round(fh), far)
                    fit = fit_rect(iw, ih, fw, fh)
                    sug = ("%dx%d (%.2f)" % (round(fit[0]), round(fit[1]), fit[0] / fit[1])
                           if fit else "—")
                    if far < iar:      # 框比图"窄长" → 图会被裁左右
                        if crop > args.tol:
                            sug += " · 或把框高改为 %d" % round(fw / iar)
                    elif crop > args.tol:
                        sug += " · 或把框宽改为 %d" % round(fh * iar)
                else:
                    cur, note = "无固定几何", "—"
                    fit = fit_rect(iw, ih, 1100, 400)
                    sug = "由你定（保持 %.2f 的比例）" % iar
                L.append("| `%s` | %dx%d | %.2f | %s | %s | %s |"
                         % (srcp, iw, ih, iar, cur, sug, note))
            L.append("")

        if content.get(i):
            L.append("### 补充素材")
            L.append("")
            L.append(content[i])
            L.append("")

        fill = sum(len(b["text"]) for b in boxes)
        L.append("### 落地提示")
        L.append("")
        L.append("当前 %d 个元素、正文合计 %d 字。想达到软目标密度，主要手段是**把一个大框拆成"
                 "若干子元素**（标签 / 数值 / 单位 / 对照 / 来源 / 图注），而不是把字号调大。"
                 "拆分后单个元素字号可以降到 %.1f–14pt，视觉密度会显著上升。" % (len(boxes), fill, args.min_font))
        L.append("")
        L.append("---")
        L.append("")

    out = args.out or os.path.join(skel, "_design_brief.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("设计任务书 → %s（%d 页）" % (out, total))
    return 0


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #

def cmd_check(args):
    c = load_contract(args.contract)
    min_font = c.get("minimum_font_pt", 10.5)
    dlo, dhi = c.get("density_target_shapes_per_page", [22, 44])
    ratio = c.get("line_height_ratio", 1.45)
    tol = float(c.get("image_frame_aspect_tolerance", 0.02))
    asset_bases = asset_bases_for(args.slides, args.assets)

    files = sorted(f for f in os.listdir(args.slides)
                   if f.endswith(".slide") and not f.startswith("_"))
    if not files:
        print("[fail] %s 下没有 .slide" % args.slides, file=sys.stderr)
        return 1

    total = len(files)
    rows, fails, warns = [], [], []

    for i, fn in enumerate(files, 1):
        with open(os.path.join(args.slides, fn), encoding="utf-8") as f:
            src = f.read()
        els = [e for e in elements(src) if e["tag"] in ("Box", "Image")]
        shapes = [e for e in els if e["absolute"]]
        texts = [t for t in elements(src) if t["tag"] == "Text"]

        # 1) 字号下限
        tiny = [t for t in texts if t["font"] is not None and t["font"] < min_font]
        if tiny:
            fails.append("P%02d FONT_TOO_SMALL: %d 处 < %.1fpt（最小 %s）"
                         % (i, len(tiny), min_font, min(t["font"] for t in tiny)))

        # 2) 画布越界
        oob = []
        for e in shapes:
            x, y, w, h = e["geo"]
            if None in (x, y, w, h):
                continue
            if x < -1 or y < -1 or x + w > CANVAS_W + 1 or y + h > CANVAS_H + 1:
                oob.append((e["tag"], x, y, w, h))
        if oob:
            fails.append("P%02d OUT_OF_CANVAS: %d 个越界，例 %s" % (i, len(oob), oob[0]))

        # 3) 文字溢出估算
        over = []
        for e in shapes:
            x, y, w, h = e["geo"]
            if None in (w, h):
                continue
            pad = (_num(e["style"].get("paddingLeft")) or 0) + \
                  (_num(e["style"].get("paddingRight")) or 0)
            for t in e["children"]:
                if t["tag"] != "Text":
                    continue
                fs = t["font"] or 16
                need = est_text_height(t["text"], fs, w - pad, ratio)
                # 容差：允许半行左右的溢出。单行标题放进略矮的居中框是常态
                # （如 53pt 标题 / 72px 框），按字面判会全页误报。
                slack = 0.35 * fs * ratio
                if need > h + slack:
                    over.append((t["text"][:18], round(need), round(h)))
        if over:
            fails.append("P%02d TEXT_OVERFLOW: %d 处疑似溢出，例「%s」需 %dpx / 框高 %dpx"
                         % (i, len(over), over[0][0], over[0][1], over[0][2]))

        # 4) 页脚
        foot = [e for e in shapes if e["geo"][1] is not None and e["geo"][1] >= FOOTER_TOP - 20]
        pnum_ok = any("%02d / %02d" % (i, total) in e["text"]
                      for e in elements(src) if e["tag"] == "Text")
        if not foot:
            warns.append("P%02d FOOTER_MISSING: y≥%d 没有元素" % (i, FOOTER_TOP - 20))
        if not pnum_ok:
            warns.append("P%02d PAGE_NUMBER: 未找到 %02d / %02d" % (i, i, total))

        # 5) 密度（只 WARN）
        n = len(shapes)
        if n < dlo:
            warns.append("P%02d LOW_DENSITY: %d 个绝对定位元素 < 建议下限 %d" % (i, n, dlo))
        elif n > dhi:
            warns.append("P%02d HIGH_DENSITY: %d 个 > 建议上限 %d（确认不是碎片化）" % (i, n, dhi))

        # 6) 图片图框比例：渲染器按 cover 裁切，比例不符 = 画面被裁
        bad_img, miss_img = [], []
        for srcp, fw, fh, origin in image_frames(src):
            path = resolve_asset(srcp, asset_bases)
            dim = img_size(path) if path else None
            if not dim:
                miss_img.append(srcp)
                continue
            if not (fw and fh):
                continue
            iw, ih = dim
            iar, far = iw / ih, fw / fh
            dev = abs(far - iar) / iar
            if dev > tol:
                crop = 1 - min(far / iar, iar / far)
                fit = fit_rect(iw, ih, fw, fh)
                bad_img.append((srcp, round(fw), round(fh), iw, ih,
                                crop * 100, fit))
        if bad_img:
            s0 = bad_img[0]
            fit = s0[6]
            fix = ("把图框改成 %dx%d 并居中" % (round(fit[0]), round(fit[1]))
                   if fit else "按图片比例重定图框")
            fails.append(
                "P%02d IMAGE_ASPECT_MISMATCH: %d 张图会被裁掉画面，例 `%s` 框 %dx%d vs 图 %dx%d"
                "（裁 %.1f%%）→ %s"
                % (i, len(bad_img), s0[0], s0[1], s0[2], s0[3], s0[4], s0[5], fix))
        if miss_img:
            warns.append("P%02d IMAGE_UNRESOLVED: 找不到文件 %s（无法校验比例）"
                         % (i, ", ".join(miss_img[:3])))

        rows.append((i, fn, page_title(src), n,
                     min([t["font"] for t in texts if t["font"]] or [0]),
                     len(tiny), len(oob), len(over), len(bad_img)))

    W = 96
    print("=" * W)
    print("S8 设计落地 · check   %s" % args.slides)
    print("=" * W)
    print("%-4s %-24s %6s %8s %8s %8s %8s %8s"
          % ("页", "标题", "元素", "最小字号", "小字", "越界", "溢出", "裁图"))
    print("-" * W)
    for i, fn, title, n, mf, nt, no, nov, nb in rows:
        print("%-4d %-24s %6d %8.1f %8d %8d %8d %8d"
              % (i, (title or fn)[:24], n, mf, nt, no, nov, nb))
    print("-" * W)

    if warns:
        print("\n[WARN] %d 条（构图类，不阻断）" % len(warns))
        for w in warns:
            print("  - " + w)
    if fails:
        print("\n[FAIL] %d 条（物理不可行，必须修）" % len(fails))
        for f in fails:
            print("  - " + f)

    print("\n平均元素数 %.1f / 页（软目标 %d–%d）"
          % (sum(r[3] for r in rows) / len(rows), dlo, dhi))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("# S8 设计落地 check 报告\n\n")
            f.write("| 页 | 标题 | 元素 | 最小字号 | 小字 | 越界 | 溢出 | 裁图 |\n")
            f.write("| :-- | :-- | --: | --: | --: | --: | --: | --: |\n")
            for i, fn, title, n, mf, nt, no, nov, nb in rows:
                f.write("| P%02d | %s | %d | %.1f | %d | %d | %d | %d |\n"
                        % (i, title or fn, n, mf, nt, no, nov, nb))
            f.write("\n## WARN\n\n" + ("\n".join("- " + w for w in warns) or "无"))
            f.write("\n\n## FAIL\n\n" + ("\n".join("- " + f for f in fails) or "无"))
        print("报告 → %s" % args.out)

    if fails:
        return 1
    if args.strict and warns:
        return 1
    return 0


# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(
        prog="design_land.py",
        description="S8 设计落地层：brief 出任务书 / check 只卡物理不可行项")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("contract", help="生成 design_contract.json")
    p.add_argument("--out", required=True)
    p.add_argument("--min-font", type=float, default=DEFAULT_CONTRACT["minimum_font_pt"])
    p.add_argument("--density", nargs=2, type=int, default=None,
                   metavar=("LO", "HI"))
    p.set_defaults(func=cmd_contract)

    p = sub.add_parser("brief", help="生成设计任务书")
    p.add_argument("--skeleton", required=True, help="骨架 .slide 目录")
    p.add_argument("--slots", default=None, help="_slots.md 路径")
    p.add_argument("--content", default=None, help="可选补充素材 md/json")
    p.add_argument("--assets", nargs="*", default=None, help="图片资产根目录（可多个）")
    p.add_argument("--tol", type=float, default=DEFAULT_CONTRACT["image_frame_aspect_tolerance"],
                   help="图片图框宽高比容差，默认 0.02")
    p.add_argument("--out", default=None)
    p.add_argument("--min-font", type=float, default=DEFAULT_CONTRACT["minimum_font_pt"])
    p.add_argument("--density", nargs=2, type=int,
                   default=DEFAULT_CONTRACT["density_target_shapes_per_page"])
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("check", help="校验落地页")
    p.add_argument("--slides", required=True, help="落地页 .slide 目录")
    p.add_argument("--contract", default=None, help="design_contract.json 路径")
    p.add_argument("--assets", nargs="*", default=None, help="图片资产根目录（可多个）")
    p.add_argument("--out", default=None, help="报告 md 路径")
    p.add_argument("--strict", action="store_true", help="WARN 也算失败")
    p.set_defaults(func=cmd_check)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
