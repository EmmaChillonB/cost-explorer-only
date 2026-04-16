"""Compatibility wrapper: re-export functions from cost_explorer.comparison.

This module preserves the original public API so older imports continue
to work while the package was refactored under `cost_explorer/`.
"""

from .cost_explorer.comparison import (
    get_cost_and_usage_comparisons,
    get_cost_comparison_drivers,
)

__all__ = [
    'get_cost_and_usage_comparisons',
    'get_cost_comparison_drivers',
]
