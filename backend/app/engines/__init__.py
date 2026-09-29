"""Engine discovery, adapters and asynchronous installation control."""

from .adapters import ADAPTER_BY_ID, ADAPTERS, Candidate, Verification
from .discovery import DiscoveryCoordinator, DiscoveryResult, installation_id
from .service import EngineService

__all__ = [
    "ADAPTERS",
    "ADAPTER_BY_ID",
    "Candidate",
    "DiscoveryCoordinator",
    "DiscoveryResult",
    "EngineService",
    "Verification",
    "installation_id",
]
