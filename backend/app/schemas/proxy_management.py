"""Typed management inputs shared by HTTP and external MCP."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictStr, model_validator


class ManagementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionRequest(ManagementInput):
    name: StrictStr = Field(min_length=1, max_length=128, description="Отображаемое имя подключения.")
    base_url: StrictStr = Field(min_length=1, description="HTTP(S) адрес OpenAI API с префиксом, например https://host/v1.")
    api_key: SecretStr | None = Field(default=None, description="Ключ: null сохраняет прежний, пустая строка удаляет; в ответах не возвращается.")


class CandidateTarget(ManagementInput):
    profile_id: StrictStr | None = Field(default=None, min_length=1, description="ID локального профиля; исключает connection_id и model.")
    connection_id: StrictStr | None = Field(default=None, min_length=1, description="ID OpenAI-подключения; задаётся вместе с model.")
    model: StrictStr | None = Field(default=None, min_length=1, description="Точный ID модели из каталога подключения.")

    @model_validator(mode="after")
    def target(self):
        if not ((self.profile_id is not None and self.connection_id is None and self.model is None)
                or (self.profile_id is None and self.connection_id is not None and self.model is not None)):
            raise ValueError("Укажите profile_id либо пару connection_id и model")
        return self


class ComfySourceRequest(ManagementInput):
    name: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", description="Уникальное имя подключения ComfyUI.")
    base_url: StrictStr = Field(min_length=1, description="Полный HTTP(S) адрес ComfyUI с портом, например http://host:8188.")


class MediaWorkflows(ManagementInput):
    generate: StrictStr | None = Field(default=None, description="Путь API workflow генерации из каталога ComfyUI; null отключает операцию.")
    edit: StrictStr | None = Field(default=None, description="Путь API workflow редактирования; null отключает операцию.")
    edit_masked: StrictStr | None = Field(default=None, description="Путь API workflow редактирования изображения по маске; только image.")
    variations: StrictStr | None = Field(default=None, description="Путь API workflow вариаций изображения; только image.")


class MediaAliasRequest(ManagementInput):
    name: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", description="Публичный алиас модели, уникальный среди текстовых и медиа-моделей.")
    type: Literal["image", "video"] = Field(description="Тип алиаса: изображение или видео; при изменении сохраняется прежний тип.")
    source: StrictStr = Field(min_length=1, description="Имя сохранённого подключения ComfyUI, включая local.")
    workflows: MediaWorkflows = Field(description="Полный набор операций: отсутствующие или пустые пути отключают операцию.")
    max_megapixels: float | None = Field(default=None, gt=0, strict=True, allow_inf_nan=False, description="Максимум изображения в МП; обязателен для image. Обычный размер использует половину бюджета.")
    max_resolution: int | None = Field(default=None, gt=0, strict=True, description="Максимальная короткая сторона видео, px; обязательна для video.")
    max_seconds: int | None = Field(default=None, gt=0, strict=True, description="Максимальная длительность видео, секунды; обязательна для video.")

    @model_validator(mode="after")
    def validate_limits(self):
        if self.type == "image" and self.max_megapixels is None:
            raise ValueError("Укажите максимум изображения в МП")
        if self.type == "video" and (self.max_resolution is None or self.max_seconds is None):
            raise ValueError("Укажите максимальное разрешение и длительность видео")
        if self.type == "image" and (self.max_resolution is not None or self.max_seconds is not None):
            raise ValueError("Для изображения используется max_megapixels")
        if self.type == "video" and self.max_megapixels is not None:
            raise ValueError("Для видео используются max_resolution и max_seconds")
        if self.type == "video" and (self.workflows.edit_masked or self.workflows.variations):
            raise ValueError("Для video доступны generate и edit")
        return self
