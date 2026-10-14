"""Experimental OpenAI transcription adapter; runtime/model compatibility is unverified."""

from __future__ import annotations

import argparse
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse


MAX_UPLOAD_BYTES = 100 * 1024 * 1024
SUPPORTED_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}


def local_model_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise SystemExit(f"--model must be an existing local directory: {path}")
    return path


def create_app(model_path: Path, public_model_name: str) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from transformers import AutoModelForRNNT, AutoProcessor
        from transformers.audio_utils import load_audio

        app.state.load_audio = load_audio
        # Both loads are local-only so serving never fetches weights implicitly.
        app.state.processor = AutoProcessor.from_pretrained(
            str(model_path), local_files_only=True
        )
        app.state.model = AutoModelForRNNT.from_pretrained(
            str(model_path), device_map="auto", local_files_only=True
        )
        app.state.model.eval()
        app.state.inference_lock = threading.Lock()
        yield

    app = FastAPI(title="Nemotron 3.5 ASR OpenAI adapter", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "model": public_model_name}

    @app.get("/v1/models")
    def models() -> dict[str, object]:
        return {"object": "list", "data": [
            {"object": "model", "id": public_model_name, "owned_by": "local"}
        ]}

    @app.post("/v1/audio/transcriptions", response_model=None)
    def transcribe(
        file: UploadFile = File(...),
        model: str = Form(...),
        language: str | None = Form(default=None),
        response_format: Literal["json", "text"] = Form(default="json"),
    ) -> dict[str, str] | PlainTextResponse:
        if model != public_model_name:
            raise HTTPException(404, "The requested model is not served by this process")
        suffix = Path(file.filename or "audio.wav").suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise HTTPException(415, "Supported audio extensions: wav, mp3, m4a, flac, ogg")

        processor = app.state.processor
        try:
            with TemporaryDirectory(prefix="nemotron-asr-") as temporary_directory:
                audio_path = Path(temporary_directory) / f"upload{suffix}"
                size = 0
                with audio_path.open("wb") as output:
                    while chunk := file.file.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_UPLOAD_BYTES:
                            raise HTTPException(413, "Audio upload exceeds 100 MiB")
                        output.write(chunk)
                if size == 0:
                    raise HTTPException(400, "Audio upload is empty")

                audio = app.state.load_audio(
                    str(audio_path),
                    sampling_rate=processor.feature_extractor.sampling_rate,
                )
                inputs = processor(
                    audio,
                    sampling_rate=processor.feature_extractor.sampling_rate,
                    language=language or "auto",
                    return_tensors="pt",
                )
                inputs = inputs.to(app.state.model.device, dtype=app.state.model.dtype)
                with app.state.inference_lock:
                    output = app.state.model.generate(**inputs, return_dict_in_generate=True)
                transcript = processor.decode(output.sequences[0], skip_special_tokens=True)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, f"Audio transcription failed: {exc}") from exc
        finally:
            file.file.close()

        if response_format == "text":
            return PlainTextResponse(transcript)
        return {"text": transcript}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Existing local Nemotron checkpoint directory")
    parser.add_argument("--model-name", required=True, help="Public model name returned by /health")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18102)
    args = parser.parse_args()
    app = create_app(local_model_path(args.model), args.model_name)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
