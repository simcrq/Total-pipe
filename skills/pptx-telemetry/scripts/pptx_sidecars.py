#!/usr/bin/env python3
"""Build render-evidence sidecars (0.4.7) from a REAL pptx (OOXML), no twin.

Evidence provenance (disclosed per field):
  measured_ooxml : geometry (EMU->px exact), fills, run colors, font
                   family/size/bold, wrap, insets, lnSpc, image crop
                   (srcRect), image sha256 + source dims, z-order
  measured_meta  : slide background parsed from slidep JSX descr (if present),
                   else default white
  derived_metrics: text line_count + overflow — real font metrics (Microsoft
                   YaHei via Pillow) applied to the real box widths; NOT the
                   PowerPoint engine's own line breaking
The layout doc is synthesized in artifact-tool layout/v4 shape so the existing
RPA artifact-tool adapter consumes it unchanged.

Usage:
  python pptx_sidecars.py --pptx deck.pptx --out <sidecars_dir> [--deck-id id]
      [--plan deck_plan.json]  # enriches layout_id/category from the RPA plan
"""
import argparse, hashlib, json, os, re, struct, sys, zipfile
import xml.etree.ElementTree as ET
from PIL import ImageFont

sys.stdout.reconfigure(encoding="utf-8")
NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
EMU_PER_PX = 9525  # 12192000x6858000 EMU (13.333x7.5in) -> 1280x720 px
DEFAULT_INSETS = {"lIns": 91440, "rIns": 91440, "tIns": 45720, "bIns": 45720}
FONT_CACHE = {}


def emu(v):
    return round(int(v) / EMU_PER_PX, 2)


def jpeg_or_png_size(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            m = data[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3):
                h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                return int(w), int(h)
            if m == 0xD8 or m == 0xD9 or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            ln = struct.unpack(">H", data[i + 2 : i + 4])[0]
            i += 2 + ln
    raise ValueError("cannot parse image dims")


def load_font(px, bold):
    key = (px, bold)
    if key not in FONT_CACHE:
        names = ["msyhbd.ttc", "msyh.ttc"] if bold else ["msyh.ttc", "msyhbd.ttc"]
        for n in names:
            p = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", n)
            if os.path.exists(p):
                FONT_CACHE[key] = ImageFont.truetype(p, max(int(px), 1))
                break
        else:
            FONT_CACHE[key] = ImageFont.load_default()
    return FONT_CACHE[key]


def wrap_lines(text, font, max_w):
    """Greedy wrap: CJK breaks anywhere, latin words atomic. Returns line count."""
    if not text or max_w <= 0:
        return 1 if text else 0
    lines, cur = 1, 0.0
    tokens = re.findall(r"[A-Za-z0-9@#$%^&*()_+=\-{}\[\];:'\",.<>/?\\|`~ ]+|.", text)
    for tok in tokens:
        w = font.getlength(tok)
        if cur > 0 and cur + w > max_w and cur + font.getlength(tok.strip() or tok[0]) > max_w:
            lines += 1
            cur = w if not tok.isspace() else 0.0
        else:
            cur += w
    return lines


def rel_lum(hex6):
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(int(hex6[i:i + 2], 16)) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    l1, l2 = sorted((rel_lum(fg.lstrip("#")), rel_lum(bg.lstrip("#"))), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def resolve_background(el, elements, slide_bg):
    """Deterministic z-order resolution: smallest opaque filled shape drawn
    before the text whose bbox contains it; else slide background.

    Gradient underlays: a gradient panel has no single colour, so picking stop 0
    would be arbitrary and produced real false positives (white title on a
    #0E3F8C->#1E4FA8 panel was measured against the slide background and
    reported as 1.05:1). We pick the stop with the WORST contrast against this
    text colour -- conservative by construction, never flattering.
    """
    bx = el["bbox"]
    cands = []
    for u in elements:
        if u["order"] >= el["order"] or not u.get("fillColor"):
            continue
        ux, uy, uw, uh = u["bbox"]
        if ux <= bx[0] and uy <= bx[1] and ux + uw >= bx[0] + bx[2] and uy + uh >= bx[1] + bx[3]:
            cands.append(u)
    if not cands:
        return slide_bg
    under = min(cands, key=lambda u: u["bbox"][2] * u["bbox"][3])
    stops = under.get("gradientStops") or []
    if len(stops) > 1:
        fg = (el.get("resolvedTextStyle") or {}).get("color")
        if fg:
            worst = min(stops, key=lambda s: contrast(fg, s))
            el["fill_provenance"] = "gradient_worst_case_stop_of_containing_panel"
            el["gradient_stops_considered"] = list(stops)
            return worst
        el["fill_provenance"] = "gradient_stop_0_no_text_color"
        return stops[0]
    return under["fillColor"]


def solid_hex(el):
    """First srgbClr hex inside the element, or None."""
    if el is None:
        return None
    c = el.find(".//a:solidFill/a:srgbClr", NS)
    return c.get("val") if c is not None else None


def gradient_hexes(el):
    """srgbClr stops of a gradFill, in document order, or []."""
    if el is None:
        return []
    grad = el.find("a:gradFill", NS)
    if grad is None:
        return []
    return [c.get("val") for c in grad.findall(".//a:gs/a:srgbClr", NS) if c.get("val")]


def parse_slide(z, n, rels_map, slide_bg):
    root = ET.fromstring(z.read(f"ppt/slides/slide{n}.xml"))
    elements, registry = [], []
    order = 0
    spTree = root.find(".//p:cSld/p:spTree", NS)
    for sp in spTree:
        tag = sp.tag.split("}")[-1]
        if tag not in ("sp", "pic"):
            continue
        order += 1
        xfrm = sp.find(".//a:xfrm", NS)
        off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
        x, y, w, h = emu(off.get("x")), emu(off.get("y")), emu(ext.get("cx")), emu(ext.get("cy"))
        cnv = sp.find(".//p:cNvPr", NS)
        el = {
            "order": order, "kind": "shape" if tag == "sp" else "image",
            "scope": "slide", "aid": f"sh/pptx-{n}-{order}", "id": str(order),
            "name": f"el-{n}-{order}", "bbox": [x, y, w, h], "geometry": "rect",
        }
        if tag == "pic":
            blip = sp.find(".//a:blip", NS)
            rid = blip.get(f"{{{NS['r']}}}embed")
            target = rels_map.get(rid)
            data = z.read(target) if target else None
            if data is None:
                continue
            sha = hashlib.sha256(data).hexdigest()
            sw, sh = jpeg_or_png_size(data)
            key = f"s{n:02d}-img{sum(1 for e in elements if e.get('kind') == 'image') + 1}"
            src = sp.find(".//a:srcRect", NS)
            region = [0.0, 0.0, 1.0, 1.0]
            cropped = False
            if src is not None:
                l = int(src.get("l", 0)) / 100000.0
                t = int(src.get("t", 0)) / 100000.0
                rr = int(src.get("r", 0)) / 100000.0
                b = int(src.get("b", 0)) / 100000.0
                region = [l, t, 1 - l - rr, 1 - t - b]
                cropped = l or t or rr or b
            if cropped:
                # honest derived asset: crop the real bytes, hash the result
                import io
                from PIL import Image
                img = Image.open(io.BytesIO(data))
                px = img.convert("RGB").crop((
                    round(l * img.width), round(t * img.height),
                    round((l + region[2]) * img.width), round((t + region[3]) * img.height)))
                buf = io.BytesIO()
                px.save(buf, format="PNG")
                ddata = buf.getvalue()
                dsha = hashlib.sha256(ddata).hexdigest()
                dw, dh = px.size
            else:
                dsha, dw, dh, ddata = sha, sw, sh, data
            el["asset"] = {"assetId": f"asset/{dsha[:16]}", "uri": target,
                           "width": dw, "height": dh, "sha256": dsha}
            el["visual_key"] = key
            el["alt"] = f"rpa:{key} | embedded paper figure"
            el["parent_asset"] = {"sha256": sha, "width": sw, "height": sh}
            el["source_region"] = [round(c, 5) for c in region]
            el["crop_mode"] = "preprocessed_fixed_region" if cropped else "full_figure"
            el["imageFit"] = "contain"
            # NOTE: slidep stretches the cropped region into the frame; the box
            # aspect matches the srcRect-cropped aspect up to per-mille srcRect
            # quantization (sub-pixel). We declare fit "contain" and let the
            # RPA geometry profile derive the display box deterministically.
            el["assetSha256"] = dsha
            registry.append(el)
        else:
            spPr = sp.find("p:spPr", NS)
            fill = solid_hex(spPr)
            if fill:
                el["fillColor"] = f"#{fill.upper()}"
            else:
                grads = gradient_hexes(spPr)
                if grads:
                    # no single fill: keep the shape eligible as a text underlay,
                    # but disclose that its colour is a gradient, not a solid.
                    el["fillColor"] = f"#{grads[0].upper()}"
                    el["gradientStops"] = [f"#{g.upper()}" for g in grads]
                    el["fill_provenance"] = "gradient_fill_stops"
            tx = sp.find("p:txBody", NS)
            if tx is None:
                elements.append(el)
                continue
            body = tx.find("a:bodyPr", NS)
            ins = dict(DEFAULT_INSETS)
            wrap_none = False
            anchor = None
            ln_spc_pct = 100000.0
            if body is not None:
                wrap_none = body.get("wrap") == "none"
                anchor = body.get("anchor")
                for k in ins:
                    if body.get(k) is not None:
                        ins[k] = int(body.get(k))
                ls = body.find(".//a:lnSpc/a:spcPct", NS)
                if ls is not None:
                    ln_spc_pct = float(ls.get("val"))
            paras = []
            max_sz = 0
            bold_any = False
            color = None
            face = None
            for p in tx.findall("a:p", NS):
                runs = p.findall("a:r", NS)
                ptxt = ""
                for rn in runs:
                    rPr = rn.find("a:rPr", NS)
                    sz = int(rPr.get("sz", "1800")) if rPr is not None else 1800
                    max_sz = max(max_sz, sz)
                    if rPr is not None and rPr.get("b") == "1":
                        bold_any = True
                    c = solid_hex(rPr) if rPr is not None else None
                    color = color or (f"#{c.upper()}" if c else None)
                    if rPr is not None:
                        f = rPr.find("a:latin", NS)
                        face = face or (f.get("typeface") if f is not None else None)
                    t = rn.find("a:t", NS)
                    ptxt += t.text or "" if t is not None else ""
                paras.append(ptxt)
            text = "\n".join(paras).strip("\n")
            if not text or max_sz == 0:
                elements.append(el)
                continue
            font_px = (max_sz / 100.0) * (4.0 / 3.0)  # pt -> px at 1280px/960pt
            fnt = load_font(font_px, bold_any)
            inner_w = w - (emu(ins["lIns"]) + emu(ins["rIns"]))
            inner_h = h - (emu(ins["tIns"]) + emu(ins["bIns"]))
            if wrap_none:
                line_count = max(1, len(paras))
            else:
                line_count = sum(wrap_lines(t, fnt, inner_w) for t in paras)
            # PowerPoint single-spacing line model (~1.2em) x real lnSpc pct;
            # wrapping itself uses real font advance widths (greedy).
            line_h = 1.2 * font_px * (ln_spc_pct / 100000.0)
            total_h = line_h * line_count
            if wrap_none:
                overflow = None  # generator sizes the box to the single line
            else:
                overflow = bool(line_h > 0 and total_h > inner_h + 0.5)
            el["text"] = text
            el["textPreview"] = text[:60]
            el["resolvedFontSize"] = round(font_px, 1)
            el["resolvedTextStyle"] = {
                "fontSize": round(font_px, 1), "typeface": face or "Microsoft YaHei",
                "color": color, "bold": bold_any,
                "alignment": None, "wrap": "none" if wrap_none else "square",
                **({"verticalAlignment": {"ctr": "middle", "t": "top", "b": "bottom"}.get(anchor)} if anchor else {}),
            }
            tl = {"lineCount": line_count,
                  "derivation": "ppt_line_model_1.2em+real_font_metrics_wrap"}
            if overflow is not None:
                tl["overflow"] = overflow
            el["textLayout"] = tl
            el["provenance"] = "ooxml_measured+font_metrics_derived"
        elements.append(el)
    # z-order-resolved text backgrounds (post-pass: needs the full element list)
    for el in elements:
        st = el.get("resolvedTextStyle")
        if not st or not st.get("color"):
            continue
        bg = resolve_background(el, elements, slide_bg)
        el["resolved_background_color"] = bg
        el["localContrastRatio"] = round(contrast(st["color"], bg), 3)
        if not el.get("fillColor"):
            # adapter reads text background strictly from fillColor; carry the
            # z-order-resolved EFFECTIVE background (real shape has no fill —
            # disclosed, not fabricated)
            if bg != slide_bg:
                el["fillColor"] = bg
                el["fill_provenance"] = "effective_background_z_order_resolved_not_shape_fill"
    return elements, registry


def slide_background(z, n):
    """slidep JSX descr carries the slide background; else default white."""
    x = z.read(f"ppt/slides/slide{n}.xml").decode("utf-8")
    m = re.search(r"background:\s*&#39;|background:\s*'(#[0-9A-Fa-f]{6})'", x)
    if m:
        return m.group(1).upper()
    return "#FFFFFF"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pptx", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--deck-id", default=None)
    ap.add_argument("--producer", default="pptx-ooxml-extract@1.0")
    ap.add_argument("--contract", default="0.4.7")
    ap.add_argument("--plan", help="deck_plan.json for layout_id/category enrichment")
    ap.add_argument("--embedded-text", default="true", help="paper figures carry raster text")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    z = zipfile.ZipFile(args.pptx)
    deck_id = args.deck_id or os.path.splitext(os.path.basename(args.pptx))[0]

    plan_slides = {}
    if args.plan and os.path.exists(args.plan):
        plan = json.load(open(args.plan, encoding="utf-8"))
        for s in plan.get("slides", []):
            plan_slides[s.get("index")] = s

    slide_files = sorted(
        (int(m.group(1)) for m in
         (re.match(r"ppt/slides/slide(\d+)\.xml$", n) for n in z.namelist()) if m),
    )
    for n in slide_files:
        rels = {}
        try:
            rr = ET.fromstring(z.read(f"ppt/slides/_rels/slide{n}.xml.rels"))
            for rel in rr:
                if "image" in rel.get("Type", ""):
                    t = rel.get("Target")
                    # slide rels targets are relative to ppt/slides/
                    rels[rel.get("Id")] = ("ppt/" + t.replace("../", "")) if t.startswith("../") else "ppt/slides/" + t.lstrip("/")
        except KeyError:
            pass
        bg = slide_background(z, n)
        elements, pics = parse_slide(z, n, rels, bg)
        meta = plan_slides.get(n, {})
        doc = {
            "schema": "openai.presentation.layout/v4", "unit": "px",
            "provenance": {
                "geometry": "measured_ooxml_emu_exact", "colors": "measured_ooxml",
                "fonts": "measured_ooxml", "line_count": "derived_font_metrics",
                "slide_background": "measured_slidep_jsx_meta",
            },
            "rpa_layout_id": meta.get("layout_id"), "category": meta.get("category"),
            "slide": {"aid": f"sl/pptx-{n:02d}", "width": 1280, "height": 720,
                      "backgroundColor": bg, "layout_id": meta.get("layout_id"),
                      "category": meta.get("category")},
            "elements": elements,
        }
        visuals, containers, registry = [], [], {}
        for el in pics:
            aid = el["asset"]["assetId"]
            registry[aid] = el["assetSha256"]
            key = el["visual_key"]
            entry = {
                "visual_key": key, "slide_id": f"sl/pptx-{n:02d}",
                "description": f"embedded paper figure {key}",
                "slot_id": key, "container_id": el["name"], "group_id": None,
                "sibling_index": None, "source_visual_id": key, "region_id": None,
                "visual_type": "dense_plot", "panel_count": 1,
                "has_embedded_text": args.embedded_text.lower() == "true",
                "source_width_px": el["asset"]["width"],
                "source_height_px": el["asset"]["height"],
                "asset_sha256": el["assetSha256"],
                "crop_mode": el["crop_mode"], "source_region": el["source_region"],
                "coordinate_space": "source_normalized_0_1",
            }
            if el["crop_mode"] == "preprocessed_fixed_region":
                entry.update({
                    "parent_asset_sha256": el["parent_asset"]["sha256"],
                    "parent_source_width_px": el["parent_asset"]["width"],
                    "parent_source_height_px": el["parent_asset"]["height"],
                    "parent_source_region": el["source_region"],
                    "parent_coordinate_space": "source_normalized_0_1",
                    "derived_asset_sha256": el["assetSha256"],
                    "derived_asset_format": "png",
                    "derivation": "rect_crop_of_parent_bytes_via_pillow_deterministic",
                })
            visuals.append(entry)
            containers.append({
                "container_id": el["name"], "shape_name": el["name"],
                "role": "image_only", "fit_policy": "contain",
                "crop_policy": "fixed_region" if el["crop_mode"] == "preprocessed_fixed_region" else "full_figure",
                "whitespace_policy": "intentional", "mismatch_policy": "replan",
                "note": "self_container_picture_has_no_frame_shape_in_ooxml",
            })
        sidecar = {
            "renderer_output": doc, "renderer_profile": "artifact-tool",
            "asset_registry": registry,
            "visual_manifest": {
                "manifest_schema_version": args.contract,
                "producer_version": args.producer, "deck_id": deck_id,
                "visuals": visuals, "containers": containers,
                "visual_groups": [], "element_annotations": [],
            },
        }
        out = os.path.join(args.out, f"slide-{n:02d}.json")
        json.dump(sidecar, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"sidecar {os.path.basename(out)}: elements={len(elements)} pics={len(pics)} bg={bg} "
              f"layout_id={meta.get('layout_id')}")


if __name__ == "__main__":
    main()
