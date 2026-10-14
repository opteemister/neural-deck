"""Strict adapter between OpenAI Decisions and Jev System One."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..errors import AppError


def _invalid(message: str) -> AppError:
    return AppError("invalid_request_error", message, status_code=400)


def _unsupported(message: str) -> AppError:
    return AppError("upstream_contract_unsupported", message, status_code=502)


def _text_input(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list) or not value:
        raise _invalid("Для Jev доступны только текстовые input")
    chunks: list[str] = []
    for message in value:
        if not isinstance(message, Mapping) or message.get("role") != "user":
            raise _invalid("Jev принимает только текстовые сообщения user")
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(content)
            continue
        if not isinstance(content, list):
            raise _invalid("Неподдерживаемый формат текстового input")
        for part in content:
            if not isinstance(part, Mapping) or part.get("type") != "input_text":
                raise _invalid("Входные изображения и нетекстовые части Jev не поддерживаются")
            text = part.get("text")
            if not isinstance(text, str):
                raise _invalid("Часть input_text должна содержать строку text")
            chunks.append(text)
    return "\n".join(chunks)


def build_jev_request(payload: Mapping[str, Any]) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Validate the public request and map it to Jev's named-question shape."""

    model = payload.get("model")
    if not isinstance(model, str) or not model:
        raise _invalid("Поле model обязательно")
    state = _text_input(payload.get("input"))
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        raise _invalid("Поле questions должно быть непустым массивом")

    jev_questions: dict[str, dict[str, Any]] = {}
    mappings: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, question in enumerate(questions):
        if not isinstance(question, Mapping):
            raise _invalid("Каждый question должен быть объектом")
        kind = question.get("type")
        if kind not in {"predicate", "choice", "score"}:
            raise _invalid("Поддерживаются только predicate, choice и score")
        supplied_name = question.get("name")
        if supplied_name is not None and not isinstance(supplied_name, str):
            raise _invalid("Поле question.name должно быть строкой или null")
        key = supplied_name if supplied_name else f"question_{index}"
        if key in names:
            raise _invalid("Имена вопросов должны быть уникальными")
        names.add(key)
        instructions = question.get("instructions")
        if not isinstance(instructions, str) or not instructions:
            raise _invalid("Поле question.instructions обязательно")

        mapped: dict[str, Any] = {"type": "noul" if kind == "predicate" else kind,
                                  "instructions": instructions}
        values: list[Any] = []
        labels: list[str] = []
        if kind == "choice":
            choices = question.get("choices")
            if not isinstance(choices, list) or len(choices) < 2 or len(choices) > 255:
                raise _invalid("Choice требует от 2 до 255 вариантов")
            criteria: dict[str, str] = {}
            for choice_index, choice in enumerate(choices):
                if not isinstance(choice, Mapping):
                    raise _invalid("Каждый вариант choices должен быть объектом")
                value = choice.get("value")
                if isinstance(value, bool):
                    value_label = "true" if value else "false"
                elif isinstance(value, str):
                    value_label = value
                else:
                    raise _invalid("choices[].value должен быть строкой или boolean")
                if value in values:
                    raise _invalid("Значения вариантов choices должны быть уникальными")
                values.append(value)
                label = f"option_{choice_index}"
                labels.append(label)
                description = choice.get("description")
                if description is not None and not isinstance(description, str):
                    raise _invalid("choices[].description должен быть строкой")
                criteria[label] = (
                    f"{value_label} — {description}" if description else value_label
                )
            mapped["type"] = "choice"
            mapped["criteria"] = criteria
        elif kind == "score":
            levels = question.get("levels")
            if not isinstance(levels, list) or len(levels) < 2 or len(levels) > 10:
                raise _invalid("Score требует от 2 до 10 уровней")
            criteria_list: list[str] = []
            for level_index, level in enumerate(levels):
                if not isinstance(level, Mapping):
                    raise _invalid("Каждый уровень levels должен быть объектом")
                label = level.get("label")
                description = level.get("description")
                if not isinstance(label, str) or not label:
                    raise _invalid("levels[].label обязателен")
                if description is not None and not isinstance(description, str):
                    raise _invalid("levels[].description должен быть строкой")
                labels.append(label)
                values.append(level_index)
                criteria_list.append(f"{label} — {description}" if description else label)
            mapped["criteria"] = criteria_list
        jev_questions[key] = mapped
        mappings.append(
            {
                "key": key,
                "name": supplied_name,
                "type": kind,
                "labels": labels,
                "values": values,
            }
        )
    return model, {"state": state, "questions": jev_questions}, mappings


def _number(value: Any, *, probability: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _unsupported("Jev вернул ответ без числового значения")
    result = float(value)
    if not math.isfinite(result) or (probability and not 0 <= result <= 1):
        raise _unsupported("Jev вернул значение за допустимыми пределами")
    return result


def _distribution(value: Any, labels: list[str]) -> list[float]:
    if isinstance(value, Mapping):
        if set(value) != set(labels):
            raise _unsupported("Распределение Jev не соответствует вариантам запроса")
        values = [_number(value[label], probability=True) for label in labels]
    elif isinstance(value, list) and len(value) == len(labels):
        values = []
        for index, item in enumerate(value):
            if isinstance(item, Mapping):
                label = item.get("label", item.get("value"))
                if label != labels[index]:
                    raise _unsupported("Порядок распределения Jev не соответствует запросу")
                item = item.get("probability")
            values.append(_number(item, probability=True))
    else:
        raise _unsupported("Jev не вернул полное распределение вероятностей")
    if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-5):
        raise _unsupported("Распределение Jev не нормировано")
    return values


def _optional_confidence(answer: Mapping[str, Any]) -> float | None:
    if "confidence" not in answer or answer["confidence"] is None:
        return None
    return _number(answer["confidence"], probability=True)


def _usage(value: Any) -> dict[str, Any] | None:
    """Pass through complete OpenAI usage only; native Jev may expose less."""

    required = {
        "input_tokens",
        "input_tokens_details",
        "output_tokens",
        "output_tokens_details",
        "total_tokens",
    }
    if not isinstance(value, Mapping) or not required.issubset(value):
        return None
    input_details = value.get("input_tokens_details")
    output_details = value.get("output_tokens_details")
    if not isinstance(input_details, Mapping) or not {
        "cached_tokens", "cache_write_tokens"
    }.issubset(input_details):
        return None
    if not isinstance(output_details, Mapping) or "reasoning_tokens" not in output_details:
        return None
    fields = [
        value["input_tokens"],
        value["output_tokens"],
        value["total_tokens"],
        input_details["cached_tokens"],
        input_details["cache_write_tokens"],
        output_details["reasoning_tokens"],
    ]
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in fields):
        return None
    return {
        "input_tokens": value["input_tokens"],
        "input_tokens_details": {
            "cached_tokens": input_details["cached_tokens"],
            "cache_write_tokens": input_details["cache_write_tokens"],
        },
        "output_tokens": value["output_tokens"],
        "output_tokens_details": {"reasoning_tokens": output_details["reasoning_tokens"]},
        "total_tokens": value["total_tokens"],
    }


def adapt_jev_response(
    payload: Mapping[str, Any], model: str, mappings: list[dict[str, Any]]
) -> dict[str, Any]:
    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        raise _unsupported("Jev не вернул объект answers")
    result: list[dict[str, Any]] = []
    for item in mappings:
        answer = answers.get(item["key"])
        if not isinstance(answer, Mapping):
            raise _unsupported("Jev не вернул ответ на каждый запрошенный вопрос")
        kind = item["type"]
        common = {"type": kind, "name": item["name"]}
        if answer.get("type") == "refusal" or answer.get("refusal") is True:
            result.append({"type": "refusal", "name": item["name"]})
            continue
        if kind == "predicate":
            raw = answer.get("noul", answer.get("probability"))
            result.append({**common, "probability": _number(raw, probability=True)})
            continue
        confidence = _optional_confidence(answer)
        if kind == "choice":
            probabilities = _distribution(answer.get("probabilities"), item["labels"])
            selected_label = answer.get("choice")
            if selected_label not in item["labels"]:
                raise _unsupported("Выбранный Jev вариант отсутствует в запросе")
            choice_answer = {
                **common,
                "choice": item["values"][item["labels"].index(selected_label)],
                "probabilities": [
                    {"value": original, "probability": probability}
                    for original, probability in zip(item["values"], probabilities, strict=True)
                ],
            }
            if confidence is not None:
                choice_answer["confidence"] = confidence
            result.append(choice_answer)
            continue
        # Strands System One indexes score probabilities by level ("0", "1", ...)
        # and returns the display text separately in ``legend``. Preserve the
        # requested labels in the public response instead of expecting upstream
        # probabilities to be keyed by those labels.
        native_score_labels = [str(index) for index in range(len(item["labels"]))]
        probabilities = _distribution(answer.get("probabilities"), native_score_labels)
        expected = sum(index * probability for index, probability in enumerate(probabilities))
        score_answer = {
            **common,
            "score": expected,
            "probabilities": [
                {"label": label, "value": index, "probability": probability}
                for index, (label, probability) in enumerate(
                    zip(item["labels"], probabilities, strict=True)
                )
            ],
        }
        if confidence is not None:
            score_answer["confidence"] = confidence
        result.append(score_answer)
    response = {"model": model, "answers": result}
    usage = _usage(payload.get("usage"))
    if usage is not None:
        response["usage"] = usage
    return response


__all__ = ["adapt_jev_response", "build_jev_request"]
