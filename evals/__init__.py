"""Nervure evaluation integration.

The package is intentionally optional: importing normal Nervure runtime modules
does not import Harbor.  Harbor is loaded only by the eval adapter/CLI.
"""

from evals.cases import CASES, SUITES, EvalCase
from evals.metrics import NervureTraceAnalyzer
from evals.trace_atif import NervureTraceToATIFConverter

__all__ = [
    "CASES",
    "SUITES",
    "EvalCase",
    "NervureTraceAnalyzer",
    "NervureTraceToATIFConverter",
]
