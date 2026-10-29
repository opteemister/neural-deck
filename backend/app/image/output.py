"""Image encoding shared by public image endpoints and the built-in chat."""

from __future__ import annotations

import io
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, Mapping

from ..errors import AppError
from ..proxy.comfy_provider import ComfyOutput


@dataclass(frozen=True, slots=True)
class ImageOutputOptions:
    format: str = "png"
    compression: int = 100

    @classmethod
    def from_request(cls, params: Mapping[str, Any]) -> ImageOutputOptions:
        image_format = params.get("output_format", "png")
        if image_format not in {"png", "jpeg", "webp"}:
            raise AppError(
                "invalid_parameter", "output_format должен быть png, jpeg или webp",
                status_code=400, details={"field": "output_format"},
            )
        compression = params.get("output_compression", 100)
        if not isinstance(compression, int) or isinstance(compression, bool) or not 0 <= compression <= 100:
            raise AppError(
                "invalid_parameter", "output_compression должен быть целым числом от 0 до 100",
                status_code=400, details={"field": "output_compression"},
            )
        if "output_compression" in params and image_format == "png":
            raise AppError(
                "invalid_parameter", "output_compression поддерживается только для JPEG и WebP",
                status_code=400, details={"field": "output_compression"},
            )
        if image_format == "jpeg" and params.get("background") == "transparent":
            raise AppError(
                "invalid_parameter", "JPEG не поддерживает прозрачный фон; выберите PNG или WebP",
                status_code=400, details={"field": "background"},
            )
        return cls(image_format, compression)

    def encode(self, output: ComfyOutput) -> ComfyOutput:
        media_type = f"image/{self.format}"
        filename = str(PurePosixPath(output.filename).with_suffix(f".{self.format}"))
        # The workflow owns the native PNG contract. Do not inspect/decode the
        # default result; load a codec only when the client requests conversion.
        if self.format == "png":
            return replace(output, filename=filename, media_type=media_type)
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(io.BytesIO(output.content)) as image:
                image.load()
                if self.format == "jpeg":
                    image = image.convert("RGB")
                elif self.format == "webp":
                    alpha = "A" in image.getbands() or "transparency" in image.info
                    image = image.convert("RGBA" if alpha else "RGB")
                buffer = io.BytesIO()
                options: dict[str, Any] = {}
                if self.format in {"jpeg", "webp"}:
                    options["quality"] = self.compression
                if self.format == "jpeg":
                    options["subsampling"] = 0
                if self.format == "webp":
                    options["exact"] = True
                image.save(buffer, format=self.format.upper(), **options)
                return replace(
                    output, filename=filename, content=buffer.getvalue(), media_type=media_type,
                )
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise AppError(
                "invalid_image_output", "ComfyUI вернул некорректное изображение", status_code=502,
            ) from exc
