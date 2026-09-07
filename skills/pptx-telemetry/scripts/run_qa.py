#!/usr/bin/env python3
"""Run the RPA post-render QA chain on artifact-tool sidecars.

Usage:
  python run_qa.py --sidecars <dir> --work <dir> [--rpa-root <research-ppt-assistant>] \
      [--node <node.exe>] [--viewing-mode projector] [--deck <rendered-deck.json>]

Chain per slide:  assemble-render-telemetry -> (canonical telemetry)
Then:             visual-quality (per slide) + validate-rendered-deck (all slides)

Outputs in --work:
  telemetry-XX.json   assemble result (status / canonical_telemetry / issues)
  vq-XX.json          visual-quality report per slide
  rendered-deck.json  deck-level validation
  summary.json        machine-readable rollup

Exit code 0 iff every assemble status is pass|manual_review_required AND
deck pipeline_status is plan_complete-equivalent ("qa_complete") with no
invalid slides. manual_review_required is NOT counted as pass — it is
reported as such (raster embedded text is not machine-measurable here).
"""
import argparse, glob, json, os, subprocess, sys

sys.stdout.reconfigure(encoding="utf-8")
DEFAULT_RPA = "C:/Users/Beibei/plugins/research-ppt-assistant"
DEFAULT_NODE = "node"

# Local import — pairwise element-bbox collision check lives next to this script
# and adds a new code ELEMENT_COLLISION that the RPA visual-quality rule set
# does not cover. See pairwise_collision.py for thresholds/algorithm.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pairwise_collision  # noqa: E402

_STATUS_RANK = {"pass": 0, "warning": 1, "fail": 2, "error": 3, "manual_review_required": 2}


def _max_status(*statuses):
    """Return the highest-severity status among inputs."""
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 0)) if statuses else "pass"


def run(node, cmd, out_path):
    with open(out_path, "w", encoding="utf-8") as fh:
        r = subprocess.run([node] + cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=cmd_cwd(cmd))
    return r.returncode


def cmd_cwd(cmd):
    return None


def declared_content_from_plan(plan_slide):
    """Extract declared body text from a plan slide's slot_assignments.

    P3 text-depth QA: the brief's `body` (and claims/takeaway) flow into the
    plan's text slots; here we rejoin them so validate-rendered-deck can check
    whether that substantive text actually landed on the rendered slide.
    Excludes title (structural) and guidance/image slots (placeholders/captions).
    """
    if not isinstance(plan_slide, dict):
        return ""
    parts = []
    for key, a in (plan_slide.get("slot_assignments") or {}).items():
        if not isinstance(a, dict):
            continue
        text = (a.get("text") or "").strip()
        if not text:
            continue
        if key == "title" or a.get("type") in ("guidance", "image"):
            continue
        parts.append(text)
    return " ".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sidecars", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--rpa-root", default=DEFAULT_RPA)
    ap.add_argument("--node", default=DEFAULT_NODE)
    ap.add_argument("--viewing-mode", default="projector")
    ap.add_argument("--deck", help="output path for rendered-deck.json (default <work>/rendered-deck.json)")
    ap.add_argument("--plan", help="deck_plan.json for declared-body injection (P3 text-depth QA)")
    args = ap.parse_args()
    os.makedirs(args.work, exist_ok=True)
    cli = os.path.join(args.rpa_root, "server", "cli.mjs")

    sides = sorted(glob.glob(os.path.join(args.sidecars, "*.json")))
    if not sides:
        sys.exit(f"no sidecars in {args.sidecars}")

    plan_slides = {}
    if args.plan and os.path.exists(args.plan):
        plan = json.load(open(args.plan, encoding="utf-8"))
        for s in plan.get("slides", []):
            plan_slides[s.get("index")] = s

    telemetries = []
    summary = {"slides": [], "deck": None}
    for sc in sides:
        tag = os.path.splitext(os.path.basename(sc))[0].split("-")[-1]
        tel_path = os.path.join(args.work, f"telemetry-{tag}.json")
        run(args.node, [cli, "assemble-render-telemetry", "--file", sc], tel_path)
        try:
            tel = json.load(open(tel_path, encoding="utf-8"))
        except Exception:
            print(f"[{tag}] assemble: ERROR (non-JSON output) -> {tel_path}")
            summary["slides"].append({"slide": tag, "assemble": "error"})
            continue
        status = tel.get("status")
        blocks = [i["code"] for i in (tel.get("missing_facts") or [])]
        fails = [i["code"] for i in (tel.get("failures") or [])]
        manuals = [i["code"] for i in (tel.get("manual_review") or [])]
        print(f"[{tag}] assemble: {status} blocks={blocks} fails={fails} manual={manuals}")
        row = {"slide": tag, "assemble": status, "blocks": blocks, "fails": fails, "manual": manuals}
        if tel.get("canonical_telemetry"):
            ct = tel["canonical_telemetry"]
            # Feed the canonical telemetry as `telemetry` (not a bare slide object),
            # so validate-rendered-deck uses the real elements instead of the legacy
            # adapter (which only reads `text_elements` and would silently drop them).
            entry = {"telemetry": ct, "quality_profile_id": "artifact-tool"}
            slide = ct.get("slide") or {}
            if slide.get("category"):
                entry["category"] = slide["category"]
            if slide.get("layout_id"):
                entry["layout_id"] = slide["layout_id"]
            if plan_slides:
                try:
                    idx = int(tag)
                except (TypeError, ValueError):
                    idx = None
                body = declared_content_from_plan(plan_slides.get(idx))
                if body:
                    entry["declared_body"] = body
            telemetries.append(entry)
            vq_in = os.path.join(args.work, f"vq-input-{tag}.json")
            json.dump({"telemetry": tel["canonical_telemetry"], "profile_id": "artifact-tool"},
                      open(vq_in, "w", encoding="utf-8"), ensure_ascii=False)
            vq_path = os.path.join(args.work, f"vq-{tag}.json")
            run(args.node, [cli, "visual-quality", "--file", vq_in], vq_path)
            try:
                vq = json.load(open(vq_path, encoding="utf-8"))
                vs = [(v.get("severity"), v.get("code")) for v in (vq.get("violations") or [])]
                row["visual_quality"] = vq.get("status")
                row["violations"] = vs
                print(f"[{tag}] visual-quality: {vq.get('status')} {vs}")
            except Exception:
                row["visual_quality"] = "error"

            # Pairwise element-bbox collision check (skill-local rule, not in
            # RPA CLI). Runs against the same sidecar on disk; appends any
            # ELEMENT_COLLISION violations and elevates visual_quality status
            # to at least warning when collisions trip thresholds.
            try:
                pc = pairwise_collision.check_sidecar(sc)
                pc_pairs = [(v["severity"], v["code"]) for v in pc["violations"]]
                if pc_pairs:
                    row.setdefault("violations", []).extend(pc_pairs)
                    row["collision_details"] = pc["violations"]
                    print(f"[{tag}] pairwise-collision: {pc['status']} {pc_pairs}")
                row["visual_quality"] = _max_status(
                    row.get("visual_quality", "pass"), pc["status"]
                )
                row["collision_check"] = {
                    "status": pc["status"],
                    "checked_pairs": pc["checked_pairs"],
                    "true_collisions": pc["true_collisions"],
                }
            except Exception as exc:
                row["collision_check"] = {"error": str(exc)}
                print(f"[{tag}] pairwise-collision: ERROR {exc}")

        summary["slides"].append(row)

    deck_path = args.deck or os.path.join(args.work, "rendered-deck.json")
    deck_in = os.path.join(args.work, "deck-input.json")
    json.dump({"slides": telemetries, "viewing_mode": args.viewing_mode},
              open(deck_in, "w", encoding="utf-8"), ensure_ascii=False)
    run(args.node, [cli, "validate-rendered-deck", "--file", deck_in], deck_path)
    try:
        deck = json.load(open(deck_path, encoding="utf-8"))
        summary["deck"] = {k: deck.get(k) for k in ("pipeline_status", "status", "invalid_slides",
                                                    "warning_slides", "readability_status",
                                                    "visual_quality_status", "text_sparsity_invalid_slides",
                                                    "text_sparsity_warning_slides") if k in deck}
        print("deck:", json.dumps(summary["deck"], ensure_ascii=False))
    except Exception:
        print(f"deck: ERROR (non-JSON output) -> {deck_path}")
        summary["deck"] = {"error": deck_path}

    # Augment deck rollup with collision-based counts so the merged summary
    # reflects ELEMENT_COLLISION too. We add separate merged lists instead of
    # overwriting the Node-side lists.
    def _slide_idx(slide_tag):
        try: return int(slide_tag)
        except (TypeError, ValueError): return None

    collision_fails = []
    collision_warns = []
    for slide_row in summary["slides"]:
        idx = _slide_idx(slide_row.get("slide"))
        if idx is None:
            continue
        cc = slide_row.get("collision_check") or {}
        if cc.get("status") == "fail":
            collision_fails.append(idx)
        elif cc.get("status") == "warning":
            collision_warns.append(idx)
    summary["deck"]["collision_invalid_slides"] = sorted(set(collision_fails))
    summary["deck"]["collision_warning_slides"] = sorted(set(collision_warns))
    print("collision rolls:", json.dumps(
        {"invalid": summary["deck"]["collision_invalid_slides"],
         "warning": summary["deck"]["collision_warning_slides"]},
        ensure_ascii=False,
    ))

    json.dump(summary, open(os.path.join(args.work, "summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("summary ->", os.path.join(args.work, "summary.json"))


if __name__ == "__main__":
    main()
