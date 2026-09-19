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

Exit code 1 for failed or missing required QA, invalid decks, or unconfirmed
required phrases. Warnings/manual visual review return 0 with release_status
review_required, not pass (raster embedded text may need human verification).
"""
import argparse, glob, json, os, re, subprocess, sys

sys.stdout.reconfigure(encoding="utf-8")
DEFAULT_RPA = "C:/Users/Beibei/plugins/research-ppt-assistant"
DEFAULT_NODE = "node"


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def run(node, cmd, out_path):
    with open(out_path, "w", encoding="utf-8") as fh:
        r = subprocess.run([node] + cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=cmd_cwd(cmd))
    return r.returncode


def cmd_cwd(cmd):
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sidecars", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--rpa-root", default=DEFAULT_RPA)
    ap.add_argument("--node", default=DEFAULT_NODE)
    ap.add_argument("--viewing-mode", default="projector")
    ap.add_argument("--plan", help="deck_plan.json; match slide index to optional presentation intent")
    ap.add_argument("--deck", help="output path for rendered-deck.json (default <work>/rendered-deck.json)")
    args = ap.parse_args()
    os.makedirs(args.work, exist_ok=True)
    cli = os.path.join(args.rpa_root, "server", "cli.mjs")
    plan_slides = {}
    if args.plan:
        with open(args.plan, encoding="utf-8") as fh:
            slides = json.load(fh).get("slides", [])
            offset = 1 if any(s.get("index") == 0 for s in slides) else 0
            plan_slides = {str(int(s["index"]) + offset): s for s in slides}

    sides = sorted(glob.glob(os.path.join(args.sidecars, "*.json")))
    if not sides:
        sys.exit(f"no sidecars in {args.sidecars}")

    telemetries = []
    summary = {"slides": [], "deck": None}
    for sc in sides:
        tag = os.path.splitext(os.path.basename(sc))[0].split("-")[-1]
        tel_path = os.path.join(args.work, f"telemetry-{tag}.json")
        run(args.node, [cli, "assemble-render-telemetry", "--file", sc], tel_path)
        try:
            tel = read_json(tel_path)
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
            entry = {"telemetry": tel["canonical_telemetry"],
                     "enforce_typography": True, "viewing_mode": args.viewing_mode}
            page_match = re.match(r"slide-(\d+)(?:-|\.)", os.path.basename(sc))
            page_index = str(int(page_match.group(1))) if page_match else tag
            if args.plan and page_index not in plan_slides:
                raise ValueError(f"No plan slide index matched sidecar: {sc}")
            planned = plan_slides.get(page_index, {})
            intent = (planned.get("design_ir") or {}).get("presentation_intent")
            if intent is not None:
                entry["presentation_intent"] = intent
            telemetries.append(entry)
            vq_in = os.path.join(args.work, f"vq-input-{tag}.json")
            write_json(vq_in, {"telemetry": tel["canonical_telemetry"], "profile_id": "artifact-tool",
                       **({"presentation_intent": intent} if intent is not None else {}),
                       "context": {"viewing_mode": args.viewing_mode, "enforce_typography": True}})
            vq_path = os.path.join(args.work, f"vq-{tag}.json")
            run(args.node, [cli, "visual-quality", "--file", vq_in], vq_path)
            try:
                vq = read_json(vq_path)
                vs = [(v.get("severity"), v.get("code")) for v in (vq.get("violations") or [])]
                row["visual_quality"] = vq.get("status")
                row["typography"] = vq.get("checks", {}).get("projector_typography", {}).get("status", "not_evaluable")
                if intent and intent.get("required_on_screen"):
                    row["presentation_intent"] = vq.get("checks", {}).get("presentation_intent", {}).get("status", "not_evaluable")
                row["violations"] = vs
                print(f"[{tag}] visual-quality: {vq.get('status')} {vs}")
            except Exception:
                row["visual_quality"] = "error"
        summary["slides"].append(row)

    deck_path = args.deck or os.path.join(args.work, "rendered-deck.json")
    deck_in = os.path.join(args.work, "deck-input.json")
    write_json(deck_in, {"slides": telemetries, "viewing_mode": args.viewing_mode})
    run(args.node, [cli, "validate-rendered-deck", "--file", deck_in], deck_path)
    try:
        deck = read_json(deck_path)
        summary["deck"] = {k: deck.get(k) for k in ("pipeline_status", "status", "invalid_slides",
                                                    "warning_slides", "readability_status",
                                                    "visual_quality_status") if k in deck}
        print("deck:", json.dumps(summary["deck"], ensure_ascii=False))
    except Exception:
        print(f"deck: ERROR (non-JSON output) -> {deck_path}")
        summary["deck"] = {"error": deck_path}

    failed = any(row.get("assemble") not in ("pass", "manual_review_required")
                 or row.get("visual_quality") not in ("pass", "warning")
                 or row.get("typography") not in ("pass", "warning")
                 or row.get("presentation_intent", "pass") != "pass"
                 for row in summary["slides"])
    failed = failed or summary["deck"].get("status") not in ("valid", "warning")
    summary["release_status"] = "blocked" if failed else "review_required" if any(
        row.get("assemble") == "manual_review_required" or row.get("visual_quality") == "warning"
        for row in summary["slides"]) else "pass"
    write_json(os.path.join(args.work, "summary.json"), summary)
    print("summary ->", os.path.join(args.work, "summary.json"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
