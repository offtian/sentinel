"""
Evaluators for the Sentinel eval framework.

Pattern-based (deterministic) evaluators for offline/CI use.
"""

from .keyword_coverage import KeywordCoverage as KeywordCoverage
from .structural import StructuralCheck as StructuralCheck


__all__ = ["KeywordCoverage", "StructuralCheck"]
