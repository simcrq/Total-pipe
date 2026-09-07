#!/usr/bin/env python3
"""Build render-evidence sidecars (0.4.7) from artifact-tool layout/v4 documents.

Usage:
  python build_sidecars.py --layouts <dir> --assets <dir> --out <dir> \
      [--deck-id my-deck] [--producer my-render@1.0] [--decor decor.json] \
      [--visuals visuals.json] [--viewing-note ""]

- asset sha256 + pixel dims are computed from the actual files in --assets
- container auto-detection: smallest textless shape whose bbox contains the
  image bbox (override per visual via --visuals JSON: {"<visual_key>":
  {"container": "...", "slot": "...", "panels": 3, "embedded_text": true,
   "whitespace": "minimal|intentional|reserved", "role": "image_only"}})
- element annotations: names found in --decor JSON ({name: quality_role});
  defaults: global-top-bar=background, page-title=content

Compatibility shim (disclosed): artifact-tool reports the asset id at
asset.assetId; the RPA adapter looks it up at element.assetId — the
renderer-reported value is copied verbatim to the element level.
"""
import argparse, glob, hashlib, json, os, struct, sys

sys.stdout.reconfigure(encoding="utf-8")
DEFAULT_CONTRACT = "0.4.7"


def jpeg_size(p):
    with open(p, "rb") as f:
        d = f.read()
    i = 2
    while i < len(d):
        if d[i] != 0xFF:
            i += 1
            continue
        m = d[i + 1]
        if m in (0xC0, 0xC1, 0xC2, 0xC3):
            h, w = struct.unpack(">HH", d[i + 5 : i + 9])
            return w, h
        if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        ln = struct.unpack(">H", d[i + 2 : i + 4])[0]
        i += 2 + ln
    raise ValueError(f"cannot parse jpeg size: {p}")


def sha256(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def box_contains(outer, inner):
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ox <= ix and oy <= iy and ix + iw <= ox + ow and iy + ih <= oy + oh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layouts", required=True)
    ap.add_argument("--assets", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--deck-id", default="deck")
    ap.add_argument("--producer", default="artifact-tool-telemetry@1.0")
    ap.add_argument("--decor", help="JSON file {shape_name: quality_role}")
    ap.add_argument("--visuals", help="JSON file {visual_key: overrides}")
    ap.add_argument("--contract", default=DEFAULT_CONTRACT)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    decor = {**{"global-top-bar": "background", "page-title": "content"},
             **(json.load(open(args.decor, encoding="utf-8")) if args.decor else {})}
    overrides = json.load(open(args.visuals, encoding="utf-8")) if args.visuals else {}
    cache = {}

    def meta(fname):
        if fname not in cache:
            p = os.path.join(args.assets, fname)
            cache[fname] = (sha256(p), *jpeg_size(p))
        return cache[fname]

    files = sorted(glob.glob(os.path.join(args.layouts, "*.json")))
    if not files:
        sys.exit(f"no layout json in {args.layouts}")

    for f in files:
        doc = json.load(open(f, encoding="utf-8"))
        slide_id = doc["slide"]["aid"]
        registry, visuals, containers, annotations = {}, [], [], []
        for el in doc.get("elements", []):
            if el.get("kind") == "image":
                aid = el.get("asset", {}).get("assetId")
                if aid and "assetId" not in el:
                    el["assetId"] = aid  # verbatim shim, see docstring
                alt = el.get("alt", "")
                key = alt[4:].split(" ")[0] if alt.startswith("rpa:") else el.get("name")
                fname = os.path.basename(el["asset"]["uri"].replace("\\", "/"))
                sha, w, h = meta(fname)
                registry[aid] = sha
                ov = overrides.get(key, {})
                # auto-detect container: smallest textless shape containing the image bbox
                best, best_area = None, None
                for cand in doc["elements"]:
                    if cand.get("kind") != "shape" or cand is el:
                        continue
                    if cand.get("text") or cand.get("textLayout"):
                        continue
                    b = cand.get("bbox")
                    if b and box_contains(b, el["bbox"]):
                        area = b[2] * b[3]
                        if best_area is None or area < best_area:
                            best, best_area = cand["name"], area
                container = ov.get("container", best)
                if not container:
                    print(f"[warn] {f}: no container found for visual {key}; visual skipped", file=sys.stderr)
                    continue
                visuals.append({
                    "visual_key": key, "slide_id": slide_id,
                    "description": ov.get("desc", alt.split("|", 1)[-1].strip()),
                    "slot_id": ov.get("slot", key), "container_id": container,
                    "group_id": None, "sibling_index": None,
                    "source_visual_id": key, "region_id": None,
                    "visual_type": ov.get("visual_type", "dense_plot"),
                    "panel_count": ov.get("panels", 1),
                    "has_embedded_text": ov.get("embedded_text", True),
                    "source_width_px": w, "source_height_px": h,
                    "asset_sha256": sha, "crop_mode": "full_figure",
                    "source_region": {"x": 0, "y": 0, "width": 1, "height": 1},
                    "coordinate_space": "source_normalized_0_1",
                })
                containers.append({
                    "container_id": container, "shape_name": container,
                    "role": ov.get("role", "image_only"), "fit_policy": el.get("imageFit", "contain"),
                    "crop_policy": "full_figure",
                    "whitespace_policy": ov.get("whitespace", "intentional"),
                    "mismatch_policy": ov.get("mismatch", "replan"),
                })
            elif el.get("kind") == "shape" and el.get("name") in decor:
                annotations.append({"shape_name": el["name"], "quality_role": decor[el["name"]]})

        sidecar = {
            "renderer_output": doc,
            "renderer_profile": "artifact-tool",
            "asset_registry": registry,
            "visual_manifest": {
                "manifest_schema_version": args.contract,
                "producer_version": args.producer,
                "deck_id": args.deck_id,
                "visuals": visuals,
                "containers": containers,
                "visual_groups": [],
                "element_annotations": annotations,
            },
        }
        out = os.path.join(args.out, os.path.basename(f))
        json.dump(sidecar, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        keys = [v["visual_key"] for v in visuals]
        print(f"sidecar {os.path.basename(out)}: visuals={keys} containers={[c['container_id'] for c in containers]}")


if __name__ == "__main__":
    main()
