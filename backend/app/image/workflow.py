"""Small API-format workflows used by the managed Qwen Image profile."""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path
from uuid import uuid4

from ..errors import AppError

_MODEL_SUFFIXES = {".safetensors", ".gguf", ".bin", ".pt", ".pth", ".ckpt"}
_CATEGORIES = ("diffusion_models", "text_encoders", "vae")


def _files(directory: Path) -> list[Path]:
    try:
        return sorted(
            item for item in directory.rglob("*") if item.is_file() and item.suffix.lower() in _MODEL_SUFFIXES
        )
    except (OSError, RuntimeError):
        return []


def model_components(model_directory: Path) -> dict[str, str]:
    """Return ComfyUI-relative component names for the first Qwen Image model."""

    missing = [name for name in _CATEGORIES if not _files(model_directory / name)]
    if missing:
        raise AppError(
            "image_model_incomplete",
            "Для ComfyUI Image нужны папки diffusion_models, text_encoders и vae с файлами",
            status_code=422,
            details={"missing_directories": missing, "model": str(model_directory)},
        )
    diffusion = _files(model_directory / "diffusion_models")
    text = _files(model_directory / "text_encoders")
    vae = _files(model_directory / "vae")
    preferred_text = next((item for item in text if "qwen3.5" in item.name.lower()), text[0])
    return {
        # ComfyUI resolves these names relative to each model category. The
        # category itself must not be repeated in the API workflow value.
        "diffusion_model": diffusion[0].relative_to(model_directory / "diffusion_models").as_posix(),
        "text_encoder": preferred_text.relative_to(model_directory / "text_encoders").as_posix(),
        "vae": vae[0].relative_to(model_directory / "vae").as_posix(),
    }


def validate_model_directory(model_directory: Path) -> None:
    if not model_directory.is_absolute() or not model_directory.is_dir():
        raise AppError("image_model_not_found", "Папка image-модели недоступна", status_code=422)
    model_components(model_directory)


def decode_image_bytes(value: str) -> bytes:
    """Validate and decode an image for the ComfyUI upload API."""
    raw = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
    try:
        content = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AppError("invalid_input_image", "Поле image содержит неверный base64", status_code=422) from exc
    if not content or len(content) > 25 * 1024 * 1024:
        raise AppError("invalid_input_image", "Размер входного изображения должен быть от 1 байта до 25 МБ", status_code=422)
    return content


def decode_input_image(value: str, destination: Path) -> str:
    """Decode an image into an explicitly supplied directory."""
    content = decode_image_bytes(value)
    name = f"llm-studio-{uuid4().hex}.png"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / name).write_bytes(content)
    return name


def parse_size(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{2,5})x(\d{2,5})", value.strip().lower())
    if not match:
        raise AppError("invalid_image_size", "Размер должен иметь вид WIDTHxHEIGHT", status_code=422)
    width, height = (int(item) for item in match.groups())
    if width < 64 or height < 64 or width > 4096 or height > 4096:
        raise AppError("invalid_image_size", "Размер изображения должен быть от 64 до 4096 пикселей", status_code=422)
    return width, height


def build_workflow(
    model_directory: Path,
    *,
    prompt: str,
    size: str,
    seed: int,
    steps: int,
    transparent: bool = False,
    input_image: str | None = None,
) -> dict[str, dict[str, object]]:
    components = model_components(model_directory)
    width, height = parse_size(size)
    text = prompt.strip()
    if transparent:
        text = (
            "This is an RGBA format image with transparency. "
            "Preserve transparent background where it is requested. "
            + text
        )
    # TextEncodeQwenImage21 currently takes one resolution value; square
    # presets are exact, while a rectangular request uses the larger edge.
    resolution = max(width, height)
    workflow: dict[str, dict[str, object]] = {
        "3": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": components["diffusion_model"], "weight_dtype": "default"},
        },
        "4": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": components["text_encoder"],
                "type": "qwen_image",
                "device": "default",
            },
        },
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": components["vae"]}},
        "6": {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {
                "clip": ["4", 0],
                "prompt": text,
                "negative_prompt": "",
                "vae": ["5", 0],
                "resolution": resolution,
            },
        },
        "10": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"model": ["3", 0], "shift": 3.1},
        },
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "model": ["10", 0],
                "positive": ["6", 0],
                "negative": ["6", 1],
                "latent_image": ["6", 2],
            },
        },
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["5", 0]}},
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "llm-studio/qwen-image", "images": ["8", 0]},
        },
    }
    if input_image:
        workflow["11"] = {"class_type": "LoadImage", "inputs": {"image": input_image}}
        workflow["6"]["inputs"]["images"] = ["11", 0]
    return workflow


__all__ = [
    "build_workflow",
    "decode_input_image",
    "model_components",
    "parse_size",
    "validate_model_directory",
]
