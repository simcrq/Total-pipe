#!/usr/bin/env python3
"""Pairwise element-bbox collision check on artifact-tool layout/v4 sidecars.

Fills a real gap in the RPA visual-quality ruleset (v1.4.0): it checks text
overflow / element_out_of_bounds / contrast / text-on-image (image_count>0 only)
/ decorative_element_relations (decorative only), but has NO pairwise
element-bbox intersection check.  This module adds ELEMENT_COLLISION as a
per-slide check.

Algorithm (per slide):
  1. Skip the slide-bg rect (>=1200x700).
  2. For every pair of remaining elements, compute bbox intersection.
  3. Drop pairs whose intersection is fully contained by one bbox
     (parent-child pattern, e.g. text sitting inside a chip card or a band;
     these are intentional layering, not visual collisions).
  4. Apply thresholds to remaining overlaps:
        warn if overlap_area >= 100 px^2
        fail if overlap_area >= 200 px^2 AND overlap_ratio_of_smaller >= 0.10
     Thresholds chosen so the slide1 chip3-vs-summary bug (overlap ~11000 px^2,
     ratio ~0.13) trips FAIL, while small intentional layering (e.g. accent
     bar overlapping the slide bg by ~700 px^2, ratio <0.05) stays silent.

Usage:
  from pairwise_collision import check_sidecar
  result = check_sidecar("sidecars/slide-01.json")
  # result = {"status": "pass|warning|fail",
  #           "violations": [{"severity":..., "code":"ELEMENT_COLLISION",
  #                           "details": {...}}, ...],
  #           "checked_pairs": int,
  #           "true_collisions": int}
  # or use the CLI:
  python pairwise_collision.py sidecars/slide-01.json
"""
import json
import sys

# Containment tolerance (px): how far outside one bbox the other can be before
# we no longer count it as parent-child containment.  Keep tight to avoid
# masking real bugs.
CONTAIN_TOL_PX = 0.5

# Slide background heuristic: skip elements this large (slide canvas 1280x720).
SLIDE_BG_W, SLIDE_BG_H = 1200, 700

# Thresholds for reporting (post-containment-filter).
WARN_MIN_AREA_PX2 = 100
FAIL_MIN_AREA_PX2 = 200
FAIL_MIN_OVERLAP_RATIO = 0.10


def _intersect(a, b):
    """Return (overlap_area_px2, area_a, area_b) for two bboxes."""
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    ox = max(0, min(ax2, bx2) - max(ax1, bx1))
    oy = max(0, min(ay2, by2) - max(ay1, by1))
    return ox * oy, aw * ah, bw * bh


def _is_contained(a, b, tol=CONTAIN_TOL_PX):
    """True if bbox b is fully inside bbox a (or vice versa) within tol px."""
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    # a contains b
    if ax1 - tol <= bx1 and ay1 - tol <= by1 and ax2 + tol >= bx2 and ay2 + tol >= by2:
        return True
    # b contains a
    if bx1 - tol <= ax1 and by1 - tol <= ay1 and bx2 + tol >= ax2 and by2 + tol >= ay2:
        return True
    return False


def _is_slide_bg(bbox):
    return bbox[2] >= SLIDE_BG_W and bbox[3] >= SLIDE_BG_H


def _label(e):
    """Best short label for an element."""
    if e.get("text"):
        return e["text"][:32]
    if e.get("textPreview"):
        return e["textPreview"][:32]
    return e.get("name", e.get("aid", "?"))


def check_sidecar(path):
    """Run pairwise collision check on a sidecar JSON; return result dict."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    elems = (data.get("renderer_output") or {}).get("elements") or []
    visible = [e for e in elems if not _is_slide_bg(e["bbox"])]

    pairs = 0
    collisions = []
    for i in range(len(visible)):
        for j in range(i + 1, len(visible)):
            a, b = visible[i], visible[j]
            ov, area_a, area_b = _intersect(a["bbox"], b["bbox"])
            pairs += 1
            if ov <= 0:
                continue
            if _is_contained(a["bbox"], b["bbox"]):
                continue  # parent-child layering, intentional
            smaller_area = min(area_a, area_b)
            ratio = ov / smaller_area if smaller_area > 0 else 0
            if ov < WARN_MIN_AREA_PX2:
                continue
            severity = (
                "fail"
                if (ov >= FAIL_MIN_AREA_PX2 and ratio >= FAIL_MIN_OVERLAP_RATIO)
                else "warning"
            )
            collisions.append({
                "severity": severity,
                "code": "ELEMENT_COLLISION",
                "details": {
                    "elements": [a.get("aid", "?"), b.get("aid", "?")],
                    "labels": [_label(a), _label(b)],
                    "bbox_a": list(a["bbox"]),
                    "bbox_b": list(b["bbox"]),
                    "overlap_area_px2": round(ov, 1),
                    "smaller_bbox_area_px2": round(smaller_area, 1),
                    "overlap_ratio_of_smaller": round(ratio, 3),
                },
            })

    if any(c["severity"] == "fail" for c in collisions):
        status = "fail"
    elif collisions:
        status = "warning"
    else:
        status = "pass"

    return {
        "status": status,
        "violations": collisions,
        "checked_pairs": pairs,
        "true_collisions": len(collisions),
    }


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: pairwise_collision.py <sidecar.json> [<sidecar2.json> ...]")
    for path in sys.argv[1:]:
        r = check_sidecar(path)
        print(
            f"{path}: status={r['status']} "
            f"pairs={r['checked_pairs']} collisions={r['true_collisions']}"
        )
        for v in r["violations"]:
            d = v["details"]
            print(
                f"  [{v['severity']}] ELEMENT_COLLISION "
                f"{d['labels'][0]!r} <-> {d['labels'][1]!r} "
                f"area={d['overlap_area_px2']}px2 ratio={d['overlap_ratio_of_smaller']}"
            )


if __name__ == "__main__":
    main()