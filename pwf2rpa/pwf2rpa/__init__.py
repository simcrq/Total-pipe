"""PaperWorkflow v4 -> Research PPT Assistant content-model bridge.

Typical use::

    from pwf2rpa import Workflow, convert, write_output

    workflow = Workflow.from_path("workflow.json")
    workflow.validate()
    write_output(convert(workflow, specs), "rpa_input.json")

Or from the shell::

    python3 pwf_to_rpa.py workflow.json --briefs briefs.json --out rpa_input.json

The package is standard-library only and deterministic: identical inputs
produce byte-identical output.
"""

from .briefs import build_briefs
from .capacity import CATEGORIES, LAYOUT_LIBRARY_VERSION
from .convert import convert, load_specs, write_output
from .errors import AdapterError, BriefError, Problem, WorkflowError
from .fallback import fallback_specs
from .fit import PageShape, category_ids, evaluate, is_known_category
from .workflow import Evidence, Workflow

__version__ = "1.0.0"

__all__ = [
    "AdapterError",
    "BriefError",
    "CATEGORIES",
    "Evidence",
    "LAYOUT_LIBRARY_VERSION",
    "PageShape",
    "Problem",
    "Workflow",
    "WorkflowError",
    "__version__",
    "build_briefs",
    "category_ids",
    "convert",
    "evaluate",
    "fallback_specs",
    "is_known_category",
    "load_specs",
    "write_output",
]
