"""domain-guard: pluggable domain guard for LLM agents."""

from .context import GuardContext, GuardResult
from .core import DomainGuard

__all__ = ["DomainGuard", "GuardContext", "GuardResult"]
__version__ = "0.1.0"
