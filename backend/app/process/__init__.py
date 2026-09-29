"""Managed and externally observed process support added in later waves."""

from .discovery import ExternalObservation, discover_external
from .manager import ProcessManager
from .registry import ModelRegistry

__all__ = ["ExternalObservation", "ModelRegistry", "ProcessManager", "discover_external"]
