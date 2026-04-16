"""Compatibility wrapper: re-export `get_cost_forecast` from cost_explorer.forecast."""

from .cost_explorer.forecast import get_cost_forecast

__all__ = ['get_cost_forecast']
