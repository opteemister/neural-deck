"""Safe shell placeholder validation and conservative script formatting."""

from __future__ import annotations

import re
import shlex

from ..errors import AppError

PLACEHOLDERS = ("{{engine}}", "{{model}}")
_SEPARATORS = frozenset(" \t\r\n|;&<>\n()")
_UNSAFE_FORMAT_CHARS = frozenset(r"'\"`$;|&<>()[\]*!?#")


class PlaceholderError(ValueError):
    """Raised when a profile placeholder is not a complete shell word."""


def _word_start(command: str, index: int) -> bool:
    return index == 0 or command[index - 1] in _SEPARATORS


def _word_end(command: str, index: int) -> bool:
    return index == len(command) or command[index] in _SEPARATORS


def validate_placeholders(command: str) -> None:
    """Validate the two supported placeholders without executing shell code.

    The scanner intentionally rejects anything ambiguous: quotes, escaped
    characters, assignments, comments and command substitutions.  A command
    without a placeholder remains free to use normal shell syntax.
    """

    if "`" in command and any(item in command for item in PLACEHOLDERS):
        raise PlaceholderError("{{engine}} и {{model}} нельзя использовать в command substitution")
    quote: str | None = None
    escaped = False
    command_substitution = 0
    comment = False
    index = 0
    while index < len(command):
        char = command[index]
        if comment:
            if any(command.startswith(item, index) for item in PLACEHOLDERS):
                raise PlaceholderError("заполнитель нельзя использовать в комментарии")
            index += 1
            continue
        if escaped:
            escaped = False
            index += 1
            continue
        if quote == "'":
            if any(command.startswith(item, index) for item in PLACEHOLDERS):
                raise PlaceholderError("заполнитель нельзя использовать внутри кавычек")
            if char == "'":
                quote = None
            index += 1
            continue
        if quote == '"':
            if any(command.startswith(item, index) for item in PLACEHOLDERS):
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
            if any(command.startswith(item, index + 1) for item in PLACEHOLDERS):
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
        matched = next((item for item in PLACEHOLDERS if command.startswith(item, index)), None)
        if matched is not None:
            end = index + len(matched)
            if (
                quote is not None
                or escaped
                or command_substitution
                or not _word_start(command, index)
                or not _word_end(command, end)
            ):
                raise PlaceholderError(
                    f"{matched} должен быть самостоятельным shell-словом вне кавычек"
                )
            # A word of the form NAME={{engine}} is an assignment even though
            # the placeholder itself starts at a token boundary after '='.
            left = index - 1
            while left >= 0 and command[left] not in _SEPARATORS:
                left -= 1
            token = command[left + 1 : end]
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token):
                raise PlaceholderError(f"{matched} нельзя использовать в assignment")
            index = end
            continue
        index += 1
    if quote is not None or escaped or command_substitution:
        raise PlaceholderError("незавершённая shell-конструкция в команде")


def substitute_placeholders(command: str, *, engine: str, model: str) -> str:
    """Substitute validated words with shell-quoted paths."""

    validate_placeholders(command)
    values = {"{{engine}}": shlex.quote(engine), "{{model}}": shlex.quote(model)}
    for placeholder, replacement in values.items():
        command = command.replace(placeholder, replacement)
    return command


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
    formatted = " \\\n    ".join(words)
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
    "format_script",
    "substitute_placeholders",
    "validate_command_or_error",
    "validate_placeholders",
]
