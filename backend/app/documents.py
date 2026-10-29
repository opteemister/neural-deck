"""PDF page rendering and the documented Unlimited-OCR-2 input adapter."""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .errors import AppError

MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_RENDERED_BYTES = 64 * 1024 * 1024


class PdfRenderer:
    def __init__(self, temp_root: Path | None = None) -> None:
        self.temp_root = temp_root
        self._slots = asyncio.Semaphore(2)

    async def _run(self, *args: str) -> bytes:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "LC_ALL": "C"},
        )
        try:
            async with asyncio.timeout(60):
                output, _ = await process.communicate()
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            raise AppError(
                "invalid_pdf", "Не удалось прочитать PDF. Проверьте файл и защиту паролем."
            )
        return output

    async def render(self, content: bytes) -> list[bytes]:
        if len(content) > MAX_PDF_BYTES:
            raise AppError("pdf_too_large", "Максимальный размер PDF — 25 МБ")
        if b"%PDF-" not in content[:1024]:
            raise AppError("invalid_pdf", "Файл не является PDF")
        if not shutil.which("pdfinfo") or not shutil.which("pdftoppm"):
            raise AppError(
                "pdf_renderer_unavailable",
                "Для обработки PDF требуется poppler-utils на машине Neural Deck",
                status_code=503,
            )
        async with self._slots:
            with tempfile.TemporaryDirectory(
                prefix="neural-deck-pdf-", dir=self.temp_root
            ) as folder:
                root = Path(folder)
                source = root / "document.pdf"
                await asyncio.to_thread(source.write_bytes, content)
                try:
                    info = (await self._run("pdfinfo", str(source))).decode(errors="replace")
                    match = re.search(r"^Pages:\s+(\d+)\s*$", info, re.MULTILINE)
                    if match is None:
                        raise AppError(
                            "invalid_pdf", "Не удалось определить количество страниц PDF"
                        )
                    pages = int(match.group(1))
                    if not 1 <= pages <= MAX_PDF_PAGES:
                        raise AppError(
                            "pdf_page_limit",
                            f"Поддерживается от 1 до {MAX_PDF_PAGES} страниц PDF за сообщение",
                        )
                    images = []
                    total = 0
                    for page in range(1, pages + 1):
                        prefix = root / "page"
                        # A bounded longest edge prevents oversized page images while
                        # retaining readable document text. Page order is explicit.
                        await self._run(
                            "pdftoppm",
                            "-f",
                            str(page),
                            "-l",
                            str(page),
                            "-singlefile",
                            "-r",
                            "180",
                            "-scale-to",
                            "2560",
                            "-png",
                            str(source),
                            str(prefix),
                        )
                        image = prefix.with_suffix(".png")
                        size = image.stat().st_size
                        total += size
                        if total > MAX_RENDERED_BYTES or size > 8 * 1024 * 1024:
                            raise AppError(
                                "pdf_render_too_large",
                                "Изображения PDF слишком большие; разделите документ",
                            )
                        images.append(await asyncio.to_thread(image.read_bytes))
                        image.unlink()
                    return images
                except TimeoutError as exc:
                    raise AppError(
                        "pdf_render_timeout", "Превышено время обработки страницы PDF"
                    ) from exc


def document_capabilities(metadata: dict[str, Any]) -> dict[str, Any]:
    capabilities = metadata.get("capabilities")
    if isinstance(capabilities, dict) and capabilities.get("document_adapter") == "unlimited_ocr_2":
        return {"capabilities": {"document_adapter": "unlimited_ocr_2"}}
    return {}


def runtime_document_metadata(command: str) -> dict[str, Any]:
    try:
        words = shlex.split(command.replace("\\\n", " "))
    except ValueError:
        return {}
    model_paths = [word.split("=", 1)[1] for word in words if word.startswith("--model-path=")]
    model_paths.extend(
        words[index + 1] for index, word in enumerate(words[:-1]) if word == "--model-path"
    )
    # The same template name is also used by Unlimited-OCR v1. Only identify v2
    # from the configured model path, never from a public alias containing "ocr".
    if not any(Path(path.rstrip("/")).name.lower() == "unlimited-ocr-2" for path in model_paths):
        return {}
    if any(
        word == "--chat-template=unlimited-ocr"
        or word == "--chat-template"
        and index + 1 < len(words)
        and words[index + 1] == "unlimited-ocr"
        for index, word in enumerate(words)
    ):
        return {"capabilities": {"document_adapter": "unlimited_ocr_2"}}
    return {}


def image_part(content: bytes) -> dict[str, Any]:
    return {
        "type": "image_url",
        "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(content).decode("ascii"),
        },
    }


def unlimited_ocr_request(model: str, content: str | list[dict[str, Any]]) -> dict[str, Any]:
    images = (
        [part for part in content if part.get("type") == "image_url"]
        if isinstance(content, list)
        else []
    )
    if not images:
        raise AppError("document_required", "Прикрепите PDF или изображение для распознавания")
    # This is the model card's multi-page contract, not a general-purpose LLM
    # conversation. Do not send the agent system prompt or function tools.
    prompt = "<PAGE><image>" * len(images) + "Multi page parsing."
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    *images,
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 8000 * len(images),
        "skip_special_tokens": False,
        "images_config": {"crop_mode": len(images) == 1},
        "stream": True,
    }
