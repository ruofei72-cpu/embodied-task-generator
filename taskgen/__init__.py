"""Deterministic task variation generation; no model or credentials required."""

__version__ = "0.2.0"

from .engine import TaskgenError, generate_tasks, validate_config, validate_variants

__all__ = ["TaskgenError", "generate_tasks", "validate_config", "validate_variants"]
