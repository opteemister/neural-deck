"""Experimental text embeddings and private text/image endpoint; runtime is unverified."""

from __future__ import annotations

import argparse
import base64
import binascii
import io
import os
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from PIL import Image, UnidentifiedImageError


MAX_BATCH_SIZE = 32
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_DATA_URI = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$")
IMAGE_SUFFIX = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}


class OpenAIEmbeddingRequest(BaseModel):
    model: str | None = None
    input: str | list[str]
    encoding_format: Literal["float"] = "float"
    dimensions: int | None = None


class MediaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = None
    image: str | None = None


class MediaEmbeddingRequest(BaseModel):
    model: str | None = None
    inputs: list[MediaInput] = Field(min_length=1, max_length=MAX_BATCH_SIZE)


def local_model_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise SystemExit(f"--model must be an existing local directory: {path}")
    return path


def create_app(model_path: Path, public_model_name: str) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Honor MODELS_ROOT as the only model source; never fall back to Hub.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from sentence_transformers import SentenceTransformer

        app.state.model = SentenceTransformer(str(model_path), local_files_only=True)
        app.state.inference_lock = threading.Lock()
        yield

    app = FastAPI(title="Local text/image embeddings adapter", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "model": public_model_name}

    @app.get("/v1/models")
    def models() -> dict[str, object]:
        return {"object": "list", "data": [
            {"object": "model", "id": public_model_name, "owned_by": "local"}
        ]}

    @app.post("/v1/embeddings")
    def text_embeddings(request: OpenAIEmbeddingRequest) -> dict[str, object]:
        if request.model and request.model != public_model_name:
            raise HTTPException(404, "The requested model is not served by this process")
        if request.dimensions is not None:
            raise HTTPException(422, "This adapter returns the native embedding dimension only")
        values = [request.input] if isinstance(request.input, str) else request.input
        if not values or len(values) > MAX_BATCH_SIZE:
            raise HTTPException(422, f"input must contain 1 to {MAX_BATCH_SIZE} strings")
        if any(not value for value in values):
            raise HTTPException(422, "embedding input strings must not be empty")
        if any(len(value) > 20_000 for value in values):
            raise HTTPException(413, "an input string exceeds 20,000 characters")

        with app.state.inference_lock:
            embeddings = app.state.model.encode(
                values, convert_to_numpy=True, normalize_embeddings=True
            )
        return {
            "object": "list",
            "model": public_model_name,
            "data": [
                {"object": "embedding", "index": index, "embedding": vector.tolist()}
                for index, vector in enumerate(np.asarray(embeddings))
            ],
        }

    @app.post("/embed", include_in_schema=False)
    @app.post("/v1/embeddings/media")
    def media_embeddings(request: MediaEmbeddingRequest) -> dict[str, object]:
        if request.model and request.model != public_model_name:
            raise HTTPException(404, "The requested model is not served by this process")
        items: list[dict[str, str]] = []
        with TemporaryDirectory(prefix="multimodal-embeddings-") as temporary_directory:
            for index, item in enumerate(request.inputs):
                has_text = item.text is not None
                has_image = item.image is not None
                if has_text == has_image:
                    raise HTTPException(422, "each input must contain exactly one of text or image")
                if has_text:
                    if not item.text:
                        raise HTTPException(422, "text inputs must not be empty")
                    if len(item.text) > 20_000:
                        raise HTTPException(413, "an input string exceeds 20,000 characters")
                    items.append({"text": item.text})
                    continue

                if len(item.image or "") > (MAX_IMAGE_BYTES * 4 // 3 + 64):
                    raise HTTPException(413, "encoded image exceeds 8 MiB")
                match = IMAGE_DATA_URI.fullmatch(item.image or "")
                if match is None:
                    raise HTTPException(422, "image must be a base64 PNG, JPEG, or WebP data URI")
                media_type, encoded = match.groups()
                if len(encoded) > (MAX_IMAGE_BYTES * 4 // 3 + 8):
                    raise HTTPException(413, "encoded image exceeds 8 MiB")
                try:
                    image_bytes = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise HTTPException(422, "image data URI contains invalid base64") from exc
                if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
                    raise HTTPException(413, "decoded image must be between 1 byte and 8 MiB")
                try:
                    with Image.open(io.BytesIO(image_bytes)) as image:
                        if (image.format or "").lower() != media_type:
                            raise HTTPException(422, "image MIME type does not match image contents")
                        image.verify()
                except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
                    raise HTTPException(422, "image is not a valid PNG, JPEG, or WebP file") from exc

                image_path = Path(temporary_directory) / f"image-{index}{IMAGE_SUFFIX[media_type]}"
                image_path.write_bytes(image_bytes)
                items.append({"image": str(image_path)})

            with app.state.inference_lock:
                embeddings = app.state.model.encode(
                    items, convert_to_numpy=True, normalize_embeddings=True
                )

        return {
            "object": "list",
            "model": public_model_name,
            "data": [
                {"object": "embedding", "index": index, "embedding": vector.tolist()}
                for index, vector in enumerate(np.asarray(embeddings))
            ],
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Existing local Sentence Transformers model directory")
    parser.add_argument("--model-name", required=True, help="Public model name returned by the API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18103)
    args = parser.parse_args()
    app = create_app(local_model_path(args.model), args.model_name)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
