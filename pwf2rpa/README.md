# pwf2rpa — PaperWorkflow v4 → Research PPT Assistant bridge

Converts a PaperWorkflow v4 `workflow.json` into the content-model input that
RPA's `normalize_content` and `create_deck_plan` consume.

Standard library only, Python 3.10+, deterministic output.

```bash
python3 pwf_to_rpa.py workflow.json --briefs briefs.json --out rpa_input.json
node server/cli.mjs normalize-content --file rpa_input.json
node server/cli.mjs plan --file rpa_input.json --presentation-type group_meeting
```

## What it does, and what it deliberately doesn't

RPA already understands PaperWorkflow: given `paperworkflow_v4`, its own
`adaptWorkflow` derives every Source, Citation and Evidence record. Re-deriving
them here would add nothing and drift the moment RPA changes.

So the adapter passes the workflow through **untouched** and concentrates on
the one thing RPA cannot infer — `slide_briefs`:

```json
{ "paperworkflow_v4": { ...verbatim... }, "slide_briefs": [ ... ] }
```

## The four hard constraints

These are enforced because each one has bitten a real run.

| # | Rule | If violated | Severity |
|---|------|-------------|----------|
| 1 | `evidence_ids` must resolve to real `EV####` | `normalize_content` fails: *"does not resolve to Evidence"* | **error** — blocks |
| 2 | `category_hint` must be one of 40 real category ids | RPA silently relaxes to whole-library search, picks a mismatched layout | warning |
| 3 | Text must fit the category's slots | `SLOT_CAPACITY_EXCEEDED` | warning |
| 4 | Output must be reproducible | caches and diffs become meaningless | enforced by design |

**Errors block and write nothing** — RPA would reject the input anyway.
**Warnings are reported but still produce a file**, because RPA *does* produce a
deck in those cases, just a degraded one. Use `--strict` to make warnings fatal
in CI.

## Rule 3 is a simulation, not a guess

`fit.py` replays both of RPA's capacity gates offline against a table extracted
from RPA's own layout library (`capacity.py`, all 320 layouts):

- **retriever gate** — title chars, total text chars, image/table/chart counts,
  process steps, and the minimum display width each `visual_type` needs;
- **slot-binding gate** — text poured into slots in RPA's real order
  (priority desc, then reading order), with the same affinity rules.

This turns *"RPA picked a weird layout"* into an actionable message:

```
[WARNING][CAPACITY_EXCEEDED] briefs[0]: no layout in category 'cover' can hold
this page: goal is 35 chars but the subtitle slot holds 23
```

That warning is real. It reproduces the exact defect in the archived
`fail-graphene-skill-run` deck, whose page 1 fell back to `RM-CASE-03`.
Shortening the goal to ≤29 chars makes RPA select a genuine cover layout with
zero warnings.

**Verified fidelity: 70/72** across a category × length sweep, including exact
boundaries (`figure_text` relaxes at 37 chars, not 36; `single_figure` at 37).

### Deck-level contention

RPA never reuses a layout inside one deck — `createDeckPlan` feeds used ids
back as `exclude_ids`. Six short pages all asking for `figure_text` will push
the last ones off the category even though each fits in isolation. Since this
is a bipartite matching problem, not a counting one, the adapter solves it as
one (`crowded_pages`), which avoids false alarms.

### Known limits of the simulation

- RPA assigns layouts **greedily by score**, so it can strand a page even when
  a perfect matching exists. The adapter under-warns in that case (2/72 above).
  This is the safe direction: RPA still plans and warns itself.
- Slot ids and Chinese labels also contribute to binding affinity; ignoring
  them can only make the simulation *more* conservative, never falsely optimistic.
- The table reflects the `projector` viewing profile, RPA's default.

## Usage

### As a CLI

```bash
python3 pwf_to_rpa.py workflow.json --briefs briefs.json --out rpa_input.json
python3 pwf_to_rpa.py workflow.json --out rpa_input.json   # fallback deck
python3 pwf_to_rpa.py --list-categories                    # the 40 legal ids
python3 pwf_to_rpa.py workflow.json --briefs b.json --strict   # CI mode
```

Exit codes: `0` ok · `2` blocking problem (nothing written) · `3` `--strict` and warnings.

### As a library

```python
from pwf2rpa import Workflow, convert, write_output

workflow = Workflow.from_path("workflow.json")
workflow.validate()
payload, warnings = convert(workflow, specs)
write_output(payload, "rpa_input.json")
```

### Without `--briefs`

Evidence is grouped by `query_ids` (PaperWorkflow's retrieval intents), each
intent mapped to a fitting category, and claims compressed to fit the slots.
Every evidence entry stays cited. The result plans cleanly — it is a starting
point to edit, not a talk.

## Brief spec format

Only `title` is required.

```json
{
  "category_hint": "figure_text",
  "title": "等双轴拉伸使 K1 光学声子快速软化",
  "claims": ["εA=0.205 时 K1 模显著下移", "εA=0.212 时 K1 模变为虚频"],
  "takeaway": "声子与能量扫描给出一致阈值。",
  "evidence_ids": ["EV0014", "EV0021"],
  "visuals": [{"visual_type": "dense_plot", "panel_count": 1,
               "has_embedded_text": true, "caption": "图 1：面内声子"}]
}
```

`text_chars`, `title_chars`, `image_count` and `process_step_count` are derived
from the actual content — a hand-written value that contradicts the content
would only make RPA shop for the wrong layout.

Unknown fields are reported rather than dropped silently, since a misspelled
key means the author's intent was lost.

## Maintenance

When RPA's layout library changes, regenerate the table:

```bash
python3 pwf_to_rpa.py --refresh-capacity /path/to/research-ppt-assistant
python3 -m unittest discover -s tests
```

This asks RPA's own readability module for the numbers rather than
reimplementing its typography maths, so the table cannot drift silently.
Verified to reproduce the committed table exactly.

## Layout

```
pwf_to_rpa.py         CLI entry point
pwf2rpa/
  workflow.py         indexed read-only view of workflow.json
  briefs.py           spec -> SLIDE_BRIEF, enforces the four rules
  fit.py              offline replay of RPA's two capacity gates
  fallback.py         deterministic briefs when none are supplied
  convert.py          payload assembly and deterministic writing
  capacity.py         generated: 320 layouts x capacity envelope
  refresh.py          regenerates capacity.py from an RPA checkout
  errors.py           Problem records with severity
tests/test_adapter.py 55 tests
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Hermetic — they build their own miniature workflow rather than depending on
the archived graphene sample.
