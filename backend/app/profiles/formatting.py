"""Safe shell placeholder validation and conservative script formatting."""

from __future__ import annotations

import re
import shlex

from ..errors import AppError

PLACEHOLDERS = (
    "{{engine}}",
    "{{model}}",
    "{{model_draft}}",
    "{{model_name}}",
    "{{base_directory}}",
    "{{port}}",
    "{{target}}",
    "{{models_root}}",
    "{{extra_model_paths_config}}",
)
_SEPARATORS = frozenset(" \t\r\n|;&<>\n()")
_UNSAFE_FORMAT_CHARS = frozenset(r"'\"`$;|&<>()[\]*!?#")

METRICS_FLAGS = {
    "llama.cpp": (("--metrics",),),
    "vllm": (("--disable-access-log-for-endpoints", "/metrics"), ("--enable-prompt-tokens-details",)),
    "sglang": (("--enable-metrics",), ("--log-level-http", "warning"), ("--enable-cache-report",)),
    "freetoken": (("--enable-cache-report",),),
}


def enable_command_metrics(command: str, engine_id: str, enabled: bool) -> str:
    """Enable exporters, cache usage details and quiet monitoring access logs."""
    if not enabled:
        return command
    for arguments in METRICS_FLAGS.get(engine_id, ()):
        command = _append_missing_option(command, arguments)
    return command


def _append_missing_option(command: str, arguments: tuple[str, ...]) -> str:
    flag = arguments[0]
    logical = command.replace("\\\n", "").strip()
    lexer = shlex.shlex(logical, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    words = list(lexer)
    if any(word == flag or word.startswith(flag + "=") for word in words):
        return command
    addition = shlex.join(arguments)
    if not words:
        raise AppError(
            "metrics_command_requires_manual_flag",
            "Укажите команду движка для включения метрик",
            status_code=422,
        )
    quote: str | None = None
    escaped = False
    insertion = len(command.rstrip())
    for index, char in enumerate(command[:insertion]):
        if escaped:
            escaped = False
            continue
        if quote == "'":
            if char == "'":
                quote = None
            continue
        if char == "\\":
            escaped = True
            continue
        if char == quote:
            quote = None
            continue
        if quote is None and char in "'\"":
            quote = char
            continue
        if quote is None and char == "#" and (index == 0 or command[index - 1].isspace()):
            # A trailing comment is safe; scripts continuing after it are not.
            if command[index:].count("\n") and command[index:].split("\n", 1)[1].strip():
                break
            insertion = index
            return command[:insertion].rstrip() + " " + addition + " " + command[insertion:]
        if char in "`$" or (quote is None and char in ";|&<>()\n\r"):
            break
    else:
        return command[:insertion] + " " + addition + command[insertion:]
    raise AppError(
        "metrics_command_requires_manual_flag",
        f"Для составного shell-скрипта добавьте {addition} к команде движка вручную "
        "или снимите «Включить метрики»",
        status_code=422,
    )


class PlaceholderError(ValueError):
    """Raised when a profile placeholder is not a complete shell word."""


def _word_start(command: str, index: int) -> bool:
    return index == 0 or command[index - 1] in _SEPARATORS


def _word_end(command: str, index: int) -> bool:
    return index == len(command) or command[index] in _SEPARATORS


def validate_placeholders(
    command: str, *, extra_placeholders: tuple[str, ...] = ()
) -> None:
    """Validate supported placeholders without executing shell code.

    The scanner rejects ambiguous placeholder placement: quotes, escaped
    characters, partial assignments, comments and command substitutions. A
    placeholder may be the complete value of a simple unquoted NAME= token.
    A command without a placeholder remains free to use normal shell syntax.
    """

    placeholders = (*PLACEHOLDERS, *extra_placeholders)
    if "`" in command and any(item in command for item in placeholders):
        raise PlaceholderError("заполнители нельзя использовать в command substitution")
    quote: str | None = None
    escaped = False
    command_substitution = 0
    comment = False
    index = 0
    while index < len(command):
        char = command[index]
        if comment:
            if any(command.startswith(item, index) for item in placeholders):
                raise PlaceholderError("заполнитель нельзя использовать в комментарии")
            index += 1
            continue
        if escaped:
            escaped = False
            index += 1
            continue
        if quote == "'":
            if any(command.startswith(item, index) for item in placeholders):
                raise PlaceholderError("заполнитель нельзя использовать внутри кавычек")
            if char == "'":
                quote = None
            index += 1
            continue
        if quote == '"':
            if any(command.startswith(item, index) for item in placeholders):
                raise PlaceholderError("заполнитель нельзя использовать внутри кавычек")
            if char == '"':
                quote = None
            elif char == "\\":
                escaped = True
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        if char == "\\":
            if any(command.startswith(item, index + 1) for item in placeholders):
                raise PlaceholderError("заполнитель нельзя экранировать")
            escaped = True
            index += 1
            continue
        if char == "#" and (index == 0 or command[index - 1].isspace()):
            comment = True
            index += 1
            continue
        if command.startswith("$(", index):
            command_substitution += 1
            index += 2
            continue
        if char == "(" and command_substitution:
            command_substitution += 1
            index += 1
            continue
        if char == ")" and command_substitution:
            command_substitution -= 1
            index += 1
            continue
        matched = next((item for item in placeholders if command.startswith(item, index)), None)
        if matched is not None:
            end = index + len(matched)
            left = index - 1
            while left >= 0 and command[left] not in _SEPARATORS:
                left -= 1
            assignment_prefix = command[left + 1 : index]
            whole_assignment_value = re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*=", assignment_prefix
            ) is not None
            if (
                quote is not None
                or escaped
                or command_substitution
                or (not _word_start(command, index) and not whole_assignment_value)
                or not _word_end(command, end)
            ):
                raise PlaceholderError(
                    f"{matched} должен быть самостоятельным shell-словом вне кавычек"
                )
            index = end
            continue
        index += 1
    if quote is not None or escaped or command_substitution:
        raise PlaceholderError("незавершённая shell-конструкция в команде")


def substitute_placeholders(
    command: str,
    *,
    engine: str,
    model: str | None,
    model_draft: str | None = None,
    model_name: str | None = None,
    base_directory: str | None = None,
    port: int | None = None,
    target: str | None = None,
    models_root: str | None = None,
    extra_model_paths_config: str | None = None,
) -> str:
    """Substitute validated words with shell-quoted profile values."""

    validate_placeholders(command)
    if "{{model_draft}}" in command and not model_draft:
        raise AppError(
            "draft_model_missing", "Выберите draft-модель для {{model_draft}}", status_code=422
        )
    if "{{model_name}}" in command and not model_name:
        raise AppError(
            "model_name_missing", "Укажите публичное имя модели для {{model_name}}", status_code=422
        )
    if "{{base_directory}}" in command and not base_directory:
        raise AppError(
            "base_directory_missing", "Для {{base_directory}} не задан runtime-каталог", status_code=422
        )
    if "{{port}}" in command and port is None:
        raise AppError("port_missing", "Для {{port}} не задан порт профиля", status_code=422)
    if "{{model}}" in command and not model:
        raise AppError("model_missing", "Для {{model}} не выбрана модель", status_code=422)
    if "{{target}}" in command and not target:
        raise AppError(
            "installation_target_missing",
            "Для {{target}} не определена папка установки",
            status_code=422,
        )
    if "{{models_root}}" in command and not models_root:
        raise AppError(
            "models_root_missing",
            "Для {{models_root}} не задан общий каталог моделей",
            status_code=422,
        )
    if "{{extra_model_paths_config}}" in command and not extra_model_paths_config:
        raise AppError(
            "extra_model_paths_config_missing",
            "Для {{extra_model_paths_config}} не задан runtime-файл путей моделей",
            status_code=422,
        )
    values = {
        "{{engine}}": shlex.quote(engine),
        "{{base_directory}}": shlex.quote(base_directory or ""),
        "{{port}}": str(port) if port is not None else "",
    }
    if model is not None:
        values["{{model}}"] = shlex.quote(model)
    if model_draft is not None:
        values["{{model_draft}}"] = shlex.quote(model_draft)
    if model_name is not None:
        values["{{model_name}}"] = shlex.quote(model_name)
    if target is not None:
        values["{{target}}"] = shlex.quote(target)
    if models_root is not None:
        values["{{models_root}}"] = shlex.quote(models_root)
    if extra_model_paths_config is not None:
        values["{{extra_model_paths_config}}"] = shlex.quote(extra_model_paths_config)
    # Replace only the original placeholders, never placeholder-like text in values.
    return re.sub(
        r"\{\{(?:engine|model|model_draft|model_name|base_directory|port|target|models_root|extra_model_paths_config)\}\}",
        lambda match: values[match.group()],
        command,
    )


def format_script(script: str) -> tuple[str, bool, str | None]:
    """Format a simple one-line command while preserving shell words.

    Formatting is deliberately conservative.  Shell syntax containing quotes,
    redirects, expansions, operators or escapes is returned unchanged because
    tokenising and re-serialising it could alter behaviour.
    """

    if "\n" in script or "\r" in script:
        return script, False, "already_multiline"
    if not script.strip():
        return script, False, "empty_script"
    if any(char in _UNSAFE_FORMAT_CHARS for char in script):
        return script, False, "shell_syntax_requires_manual_formatting"
    try:
        words = shlex.split(script, posix=True)
    except ValueError:
        return script, False, "invalid_shell_syntax"
    if not words:
        return script, False, "empty_script"
    # shlex may remove whitespace. Only accept a strict shell-word alphabet so
    # the resulting continuation is demonstrably equivalent.
    if any(not re.fullmatch(r"[A-Za-z0-9_./:@%+={}~=-]+", word) for word in words):
        return script, False, "shell_syntax_requires_manual_formatting"
    # Keep the executable and positional subcommand together, then place each
    # option and its value(s) on one continuation line. This stays readable
    # without expanding every individual shell word into its own line.
    groups: list[list[str]] = [[]]
    for word in words:
        if word.startswith("-") and word != "-" and groups[-1]:
            groups.append([])
        groups[-1].append(word)
    formatted = " \\\n    ".join(" ".join(group) for group in groups)
    return formatted, formatted != script, None


def validate_command_or_error(command: str) -> None:
    try:
        validate_placeholders(command)
    except PlaceholderError as exc:
        raise AppError(
            "invalid_profile_command",
            str(exc),
            status_code=422,
        ) from exc


__all__ = [
    "PLACEHOLDERS",
    "PlaceholderError",
    "enable_command_metrics",
    "format_script",
    "substitute_placeholders",
    "validate_command_or_error",
    "validate_placeholders",
]
