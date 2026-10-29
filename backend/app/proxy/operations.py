"""Supported operations and effective upstream URL semantics."""

from urllib.parse import urlsplit, urlunsplit

from ..errors import AppError
from ..schemas.instances import Instance

OPERATIONS = {
    "/v1/chat/completions": ("text", {"openai"}),
    "/v1/responses": ("text", {"openai"}),
    "/v1/embeddings": ("embeddings", {"openai"}),
    "/v1/embeddings/media": ("embeddings", {"openai"}),
    "/v1/audio/speech": ("tts", {"openai"}),
    "/v1/audio/transcriptions": ("asr", {"openai"}),
    "/v1/audio/translations": ("asr", {"openai"}),
    "/v1/ocr": ("ocr", {"openai"}),
    "/v1/images/generations": ("image", {"openai", "comfyui"}),
    "/v1/images/edits": ("image", {"openai"}),
    "/v1/images/variations": ("image", {"openai"}),
    "/v1/decisions": ("decision", {"jev"}),
    "/v1/systemone": ("decision", {"jev"}),
    "/v2/embed": ("embeddings", {"openai"}),
}


def supports(instance: Instance, route: str) -> bool:
    operation = OPERATIONS.get(route)
    return bool(operation and operation[0] in instance.types
                and instance.api_protocol in operation[1])


def public_types(instance: Instance) -> list[str]:
    return sorted({kind for route, (kind, _) in OPERATIONS.items() if supports(instance, route)})


def upstream_url(instance: Instance, route: str) -> str:
    base = instance.api_base_url
    if not base:
        raise AppError("upstream_unavailable", "У экземпляра отсутствует API URL",
                       status_code=503)
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AppError("upstream_unavailable", "Некорректный API URL экземпляра",
                       status_code=503)
    path = parsed.path.rstrip("/")
    if instance.api_protocol == "openai":
        # Cohere-compatible providers may store the endpoint itself rather
        # than a server root.  Only normalize this known endpoint; arbitrary
        # path suffixes must continue to receive the operation route.
        if route == "/v2/embed" and path.endswith(route):
            suffix = ""
        elif path.endswith("/v1"):
            root = path[:-3]
            suffix = route
            path = root
        else:
            suffix = route
    else:
        suffix = "/v1/systemone" if route == "/v1/decisions" else route
    return urlunsplit((parsed.scheme, parsed.netloc, path + suffix, "", ""))


__all__ = ["OPERATIONS", "public_types", "supports", "upstream_url"]
