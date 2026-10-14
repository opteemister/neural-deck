"""Experimental OpenAI Speech adapter; runtime/model compatibility is unverified."""

from __future__ import annotations

import argparse
import io
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import soundfile as sf
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field


class SpeechRequest(BaseModel):
    model: str | None = None
    input: str = Field(min_length=1, max_length=20_000)
    # Qwen speaker names are used directly; this is not an OpenAI voice alias.
    voice: str = "Vivian"
    response_format: Literal["wav"] = "wav"
    speed: float = 1.0
    instructions: str | None = Field(default=None, max_length=2_000)
    language: str = "Auto"


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
        from qwen_tts import Qwen3TTSModel

        # Do not let a typo or incomplete MODELS_ROOT path trigger a Hub download.
        app.state.model = Qwen3TTSModel.from_pretrained(
            str(model_path), local_files_only=True, device_map="auto",
            torch_dtype="auto", attn_implementation="sdpa",
        )
        app.state.speakers = {speaker.casefold() for speaker in app.state.model.get_supported_speakers() or []}
        if not app.state.speakers:
            raise RuntimeError("This adapter requires a Qwen3-TTS CustomVoice checkpoint")
        app.state.inference_lock = threading.Lock()
        yield

    app = FastAPI(title="Qwen3-TTS OpenAI Speech adapter", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "model": public_model_name}

    @app.get("/v1/models")
    def models() -> dict[str, object]:
        return {"object": "list", "data": [
            {"object": "model", "id": public_model_name, "owned_by": "local"}
        ]}

    @app.post("/v1/audio/speech")
    def speech(request: SpeechRequest) -> Response:
        if request.model and request.model != public_model_name:
            raise HTTPException(404, "The requested model is not served by this process")
        if request.speed != 1.0:
            raise HTTPException(422, "Qwen3-TTS speed control is not exposed by this adapter")

        model = app.state.model
        if request.voice.casefold() not in app.state.speakers:
            choices = ", ".join(sorted(app.state.speakers))
            raise HTTPException(422, f"voice must be a native Qwen speaker; available: {choices}")

        try:
            with app.state.inference_lock:
                waveforms, sample_rate = model.generate_custom_voice(
                    text=request.input,
                    language=request.language,
                    speaker=request.voice,
                    instruct=request.instructions,
                )
            audio = io.BytesIO()
            sf.write(audio, waveforms[0], sample_rate, format="WAV", subtype="PCM_16")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(500, f"Speech synthesis failed: {exc}") from exc

        return Response(content=audio.getvalue(), media_type="audio/wav")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Existing local CustomVoice model directory")
    parser.add_argument("--model-name", required=True, help="Public model name returned by the API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18101)
    args = parser.parse_args()
    app = create_app(local_model_path(args.model), args.model_name)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
