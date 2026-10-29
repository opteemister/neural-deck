"""Reasoning metadata and request adaptation shared by all model routes."""

from __future__ import annotations

import ast
import json
import re
import shlex
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from .errors import AppError

EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def reasoning_capabilities(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize reported capabilities; never infer levels from a model name."""
    reported = metadata.get("reasoning_capabilities")
    if isinstance(reported, Mapping):
        raw = reported.get("efforts")
        parameter = reported.get("effort_parameter", "reasoning_effort")
        disable = reported.get("can_disable_thinking") is True
        default = reported.get("default_effort")
    else:
        raw = metadata.get("supported_reasoning_efforts")
        parameter = "reasoning_effort"
        disable = metadata.get("supports_enable_thinking") is True
        default = metadata.get("default_reasoning_effort")
    efforts = [item for item in EFFORTS if isinstance(raw, (list, tuple)) and item in raw]
    if not isinstance(parameter, str) or parameter not in {
        "reasoning_effort",
        "chat_template_kwargs",
    }:
        return {}
    if not efforts and not disable:
        template = metadata.get("chat_template")
        return template_capabilities(template) if isinstance(template, str) else {}
    result: dict[str, Any] = {
        "efforts": efforts,
        "effort_parameter": parameter,
        "can_disable_thinking": disable,
    }
    if default in efforts or default == "none" and disable:
        result["default_effort"] = default
    return {"reasoning_capabilities": result}


def template_capabilities(template: str) -> dict[str, Any]:
    # Inspect Jinja expressions, not prose or comments. This is static inspection,
    # not execution of a downloaded template. Unknown templates stay unknown.
    expressions = " ".join(re.findall(r"{%.*?%}|{{.*?}}", template, re.DOTALL))
    variables = {"reasoning_effort"}
    variables.update(re.findall(r"\bset\s+(\w+)\s*=\s*reasoning_effort\b", expressions))
    efforts: list[str] = []
    for variable in variables:
        constraint = re.search(
            rf"\b{re.escape(variable)}\s+not\s+in\s*(\[[^\]]+\]|\([^)]*\))",
            expressions,
        )
        if constraint:
            try:
                values = ast.literal_eval(constraint.group(1))
            except (ValueError, SyntaxError):
                continue
            if isinstance(values, (list, tuple)) and all(value in EFFORTS for value in values):
                efforts = [value for value in EFFORTS if value in values]
                break
    disable = bool(re.search(r"\benable_thinking\b", expressions))
    if not efforts and not disable:
        return {}
    result: dict[str, Any] = {
        "efforts": efforts,
        "effort_parameter": "chat_template_kwargs",
        "can_disable_thinking": disable,
    }
    default = re.search(r"\breasoning_effort\s*\|\s*default\(\s*(['\"])(\w+)\1\s*\)", expressions)
    if default and default.group(2) in efforts:
        result["default_effort"] = default.group(2)
    return {"reasoning_capabilities": result}


@lru_cache(maxsize=64)
def _file_metadata(path: str, _mtime: int, _size: int) -> dict[str, Any]:
    content = Path(path).read_text(encoding="utf-8")
    if Path(path).suffix == ".json":
        payload = json.loads(content)
        content = payload.get("chat_template", "") if isinstance(payload, dict) else ""
        if not isinstance(content, str):
            return {}
    return template_capabilities(content)


def runtime_reasoning_metadata(command: str, working_directory: Path | None) -> dict[str, Any]:
    """Read only the template selected by the recorded running command."""
    try:
        words = shlex.split(command.replace("\\\n", " "))
    except ValueError:
        return {}

    def option(name: str) -> str | None:
        for index, word in enumerate(words):
            if word.startswith(name + "="):
                return word[len(name) + 1 :]
            if word == name and index + 1 < len(words):
                return words[index + 1]
        return None

    template = option("--chat-template")
    if template and ("{%" in template or "{{" in template):
        result = template_capabilities(template)
    else:
        model = option("--tokenizer") or option("--model-path") or option("--model")
        if not model and "serve" in words:
            index = words.index("serve") + 1
            if index < len(words) and not words[index].startswith("-"):
                model = words[index]
        selected = template or (str(Path(model) / "tokenizer_config.json") if model else None)
        if not selected or "$" in selected or "{{" in selected:
            return {}
        path = Path(selected).expanduser()
        if not path.is_absolute():
            if working_directory is None:
                return {}
            path = working_directory / path
        try:
            info = path.stat()
            if info.st_size > 8 * 1024 * 1024 or not path.is_file():
                return {}
            result = _file_metadata(str(path), info.st_mtime_ns, info.st_size)
        except (OSError, UnicodeError, ValueError):
            # Metadata is optional. An unreadable/registered template does not
            # justify inventing supported levels or blocking ordinary requests.
            return {}
    result = {key: dict(value) for key, value in result.items()}
    raw_defaults = option("--default-chat-template-kwargs") or option("--chat-template-kwargs")
    if raw_defaults and result:
        try:
            defaults = json.loads(raw_defaults)
        except ValueError:
            result["reasoning_capabilities"].pop("default_effort", None)
            return result
        if isinstance(defaults, dict):
            capabilities = result["reasoning_capabilities"]
            if defaults.get("enable_thinking") is False and capabilities["can_disable_thinking"]:
                capabilities["default_effort"] = "none"
            elif defaults.get("reasoning_effort") in capabilities["efforts"]:
                capabilities["default_effort"] = defaults["reasoning_effort"]
    return result


def adapt_reasoning_request(
    payload: Mapping[str, Any],
    route: str,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Accept standard Chat/Responses fields and the existing template kwargs."""
    body = dict(payload)
    if route not in {"/v1/chat/completions", "/v1/responses"}:
        return body
    reasoning = body.get("reasoning")
    if reasoning is not None and not isinstance(reasoning, dict):
        raise AppError(
            "invalid_reasoning", "reasoning должен быть объектом", details={"field": "reasoning"}
        )
    kwargs = body.get("chat_template_kwargs")
    if kwargs is not None and not isinstance(kwargs, dict):
        raise AppError(
            "invalid_reasoning",
            "chat_template_kwargs должен быть объектом",
            details={"field": "chat_template_kwargs"},
        )
    kwargs = dict(kwargs or {})
    top_level = body.get("reasoning_effort")
    nested = (reasoning or {}).get("effort")
    if top_level is not None and nested is not None and top_level != nested:
        raise AppError("conflicting_reasoning", "Указаны разные уровни reasoning")
    standard = top_level if top_level is not None else nested
    native = kwargs.get("reasoning_effort")
    if standard is not None and native is not None and standard != native:
        raise AppError("conflicting_reasoning", "Указаны разные уровни reasoning")
    effort = standard if standard is not None else native
    enabled = kwargs.get("enable_thinking")
    if enabled is not None and not isinstance(enabled, bool):
        raise AppError("invalid_reasoning", "enable_thinking должен быть true или false")
    if effort is not None and (not isinstance(effort, str) or not effort):
        raise AppError("invalid_reasoning", "Уровень reasoning должен быть строкой")
    if effort is not None and enabled is not None and enabled != (effort != "none"):
        raise AppError("conflicting_reasoning", "Уровень reasoning противоречит enable_thinking")
    capabilities = reasoning_capabilities(metadata).get("reasoning_capabilities")
    if capabilities is None:
        return body
    if effort is not None and effort not in capabilities["efforts"]:
        if not (effort == "none" and capabilities["can_disable_thinking"]):
            raise AppError(
                "unsupported_reasoning_effort",
                "Модель не поддерживает этот уровень reasoning",
                details={
                    "field": "reasoning_effort"
                    if route.endswith("completions")
                    else "reasoning.effort",
                    "supported": capabilities["efforts"],
                },
            )
    if effort is None:
        return body
    # Remove only effort fields. Preserve summary, max_tokens, and all unrelated kwargs.
    body.pop("reasoning_effort", None)
    if isinstance(reasoning, dict) and "effort" in reasoning:
        remaining = {key: value for key, value in reasoning.items() if key != "effort"}
        if remaining:
            body["reasoning"] = remaining
        else:
            body.pop("reasoning", None)
    kwargs.pop("reasoning_effort", None)
    if effort == "none" and capabilities["can_disable_thinking"]:
        kwargs["enable_thinking"] = False
    elif capabilities["effort_parameter"] == "chat_template_kwargs":
        kwargs["reasoning_effort"] = effort
        if capabilities["can_disable_thinking"]:
            kwargs["enable_thinking"] = True
    elif route == "/v1/responses":
        body["reasoning"] = {**(body.get("reasoning") or {}), "effort": effort}
    else:
        body["reasoning_effort"] = effort
    if kwargs:
        body["chat_template_kwargs"] = kwargs
    else:
        body.pop("chat_template_kwargs", None)
    return body
