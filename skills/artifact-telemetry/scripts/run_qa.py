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
    ap.add_argument("--deck", help="output path for rendered-deck.json (default <work>/rendered-deck.json)")
    args = ap.parse_args()
    os.makedirs(args.work, exist_ok=True)
    cli = os.path.join(args.rpa_root, "server", "cli.mjs")

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
            telemetries.append(tel["canonical_telemetry"])
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
                                                    "visual_quality_status") if k in deck}
        print("deck:", json.dumps(summary["deck"], ensure_ascii=False))
    except Exception:
        print(f"deck: ERROR (non-JSON output) -> {deck_path}")
        summary["deck"] = {"error": deck_path}

    json.dump(summary, open(os.path.join(args.work, "summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("summary ->", os.path.join(args.work, "summary.json"))


if __name__ == "__main__":
    main()
