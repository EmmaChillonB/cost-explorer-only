"""Compatibility wrapper: re-export `get_cost_and_usage` from cost_explorer.usage."""

from .cost_explorer.usage import get_cost_and_usage

__all__ = ['get_cost_and_usage']
