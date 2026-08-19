"""Runtime data types for MAIKA."""

from __future__ import annotations

from dataclasses import dataclass

from .api import MaikaApiClient
from .coordinator import MaikaDataUpdateCoordinator


@dataclass(slots=True)
class MaikaRuntimeData:
    """Objects attached to a MAIKA config entry while loaded."""

    client: MaikaApiClient
    coordinator: MaikaDataUpdateCoordinator
