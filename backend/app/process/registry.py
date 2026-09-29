"""In-memory seam between observed instances and the OpenAI proxy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ..schemas.instances import Instance


@dataclass(slots=True)
class RegisteredModel:
    id: str
    upstream_model: str | None
    instance_id: str
    engine_id: str
    profile_name: str | None
    observed_at: datetime


class ModelRegistry:
    """A small volatile registry populated from observed live processes.

    SQLite is intentionally not used here: the source of truth is the current
    process table and the engine API.  The proxy can depend on this class
    without depending on profile-file internals.
    """

    def __init__(self) -> None:
        self._models: dict[str, RegisteredModel] = {}

    def clear_instance(self, instance_id: str) -> None:
        for name, model in list(self._models.items()):
            if model.instance_id == instance_id:
                del self._models[name]

    def register_instance(
        self, instance: Instance, *, upstream_models: list[str] | None = None
    ) -> list[RegisteredModel]:
        self.clear_instance(instance.id)
        models = upstream_models or ([instance.model_name] if instance.model_name else [])
        result: list[RegisteredModel] = []
        for upstream in models:
            public_name = instance.model_name if instance.source == "managed" else upstream
            if not public_name:
                continue
            if public_name in self._models and self._models[public_name].instance_id != instance.id:
                public_name = f"{instance.id}/{upstream}"
            item = RegisteredModel(
                id=public_name,
                upstream_model=upstream,
                instance_id=instance.id,
                engine_id=instance.engine_id,
                profile_name=instance.profile_name,
                observed_at=datetime.now(UTC),
            )
            self._models[public_name] = item
            result.append(item)
        return result

    def remove_exited(self, active_ids: set[str]) -> None:
        for name, model in list(self._models.items()):
            if model.instance_id not in active_ids:
                del self._models[name]

    def list(self) -> list[RegisteredModel]:
        return list(self._models.values())

    def get(self, model_name: str) -> RegisteredModel | None:
        return self._models.get(model_name)


__all__ = ["ModelRegistry", "RegisteredModel"]
