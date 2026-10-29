"""ND.v1 graph boundary. No model names, canvas positions or numeric node IDs."""

import copy
import math
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..errors import AppError


INPUTS = {
    "prompt": ("value",), "width": ("value",), "height": ("value",),
    "seconds": ("value",), "n": ("value",),
    "seed": ("noise_seed", "seed", "value"),
    "image": ("image",), "mask": ("image",), "background": ("value",),
}


def invalid(message: str) -> AppError:
    return AppError("comfyui_contract_invalid", f"ND.v1: {message}", status_code=503)


def parameter(field: str, message: str) -> AppError:
    return AppError("comfyui_parameter_invalid", message, status_code=400, details={"field": field, "param": field})


def maximum(model: Mapping[str, Any], field: str) -> int | float:
    value = model.get(field)
    if type(value) not in {int, float} or (type(value) is float and not math.isfinite(value)) or value <= 0 or (field != "max_megapixels" and type(value) is not int):
        raise invalid(f"укажите {field} в настройках алиаса")
    return value


def check_size(model: Mapping[str, Any], width: int, height: int):
    if model["type"] == "image":
        if width * height > maximum(model, "max_megapixels") * 1024 * 1024:
            raise parameter("size", "Разрешение превышает максимум алиаса в МП")
    elif min(width, height) > maximum(model, "max_resolution"):
        raise parameter("size", "Разрешение превышает максимум видео-алиаса")


def size_pair(value: Any) -> tuple[int, int]:
    try:
        if not isinstance(value, str):
            raise ValueError
        parts = value.lower().split("x")
        if len(parts) != 2 or not all(part.isdecimal() for part in parts):
            raise ValueError
        w, h = map(int, parts)
        if w < 1 or h < 1:
            raise ValueError
        return w, h
    except ValueError as exc:
        raise parameter("size", "Размер должен иметь вид WIDTHxHEIGHT с положительными сторонами") from exc


@dataclass(frozen=True)
class WorkflowContract:
    bindings: dict[str, tuple[str, str]]
    output: str
    model: Mapping[str, Any]
    media_type: str
    operation: str

    @classmethod
    def parse(cls, graph: Mapping[str, Any], model: Mapping[str, Any], operation: str):
        bindings: dict[str, tuple[str, str]] = {}
        marked: dict[str, str] = {}
        for node_id, node in graph.items():
            meta = node.get("_meta", {})
            title = meta.get("title", "") if isinstance(meta, Mapping) else ""
            if not isinstance(title, str) or not title.startswith("ND."):
                continue
            name = title.removeprefix("ND.v1.")
            if not title.startswith("ND.v1.") or name not in {*INPUTS, "output"}:
                continue
            if name in marked:
                raise invalid(f"повторная метка {title}")
            marked[name] = str(node_id)
            if name == "output":
                continue
            inputs = node["inputs"]
            fields = [field for field in INPUTS[name] if field in inputs]
            if len(fields) != 1:
                raise invalid(f"{title}: не найден однозначный вход контракта")
            field = fields[0]
            bindings[name] = (str(node_id), field)
        required = {"width", "height", "seed", "output"}
        if operation != "variations":
            required.add("prompt")
        if operation in {"edit", "edit_masked", "variations"}:
            required.add("image")
        if operation == "edit_masked":
            required.add("mask")
        if model["type"] == "video":
            if operation not in {"generate", "edit"}:
                raise invalid("video поддерживает только generate и edit")
            required.add("seconds")
        if required - marked.keys():
            raise invalid(f"отсутствуют метки: {', '.join(sorted(required - marked.keys()))}")
        output = marked["output"]
        return cls(bindings, output, model, model["type"], operation)

    def value(self, graph, name, default=None):
        binding = self.bindings.get(name)
        return graph[binding[0]]["inputs"][binding[1]] if binding else default

    def patch(self, graph, params, fields, uploaded):
        result = copy.deepcopy(dict(graph))
        if params.get("reference") is not None and params.get("input_reference") is not None:
            raise parameter("input_reference", "Укажите один reference")
        needed = {"image"} if self.operation in {"edit", "edit_masked", "variations"} else set()
        if self.operation == "edit_masked":
            needed.add("mask")
        values = {}
        for field in fields:
            if field in {"model", "response_format", "seed"} or params.get(field) is None:
                continue
            value = params[field]
            if field == "quality":
                if not isinstance(value, str) or value not in {"auto", "low", "medium", "high"}:
                    raise parameter(field, "quality: auto, low, medium или high")
                continue
            if field == "size":
                if value != "auto":
                    w, h = size_pair(value)
                    check_size(self.model, w, h)
                    values.update(width=w, height=h)
                continue
            canonical = "image" if field in {"reference", "input_reference"} else field
            if canonical in {"image", "mask"}:
                if canonical not in needed:
                    raise parameter(field, "Файл не поддерживается этой операцией")
                key = "reference" if field == "input_reference" else field
                value = uploaded.get(key)
                if not value:
                    raise parameter(field, "Требуется одно загруженное изображение")
            if canonical in {"n", "seconds"}:
                if type(value) is not int or value < 1:
                    raise parameter(field, f"{field}: требуется положительное целое число")
                if canonical == "seconds" and value > maximum(self.model, "max_seconds"):
                    raise parameter(field, "Длительность превышает максимум алиаса")
                if canonical == "n" and value == 1 and "n" not in self.bindings:
                    continue
            if canonical == "background":
                if not isinstance(value, str) or value not in {"auto", "opaque", "transparent"}:
                    raise parameter(field, "Неизвестный режим фона")
                if canonical not in self.bindings and value == "auto":
                    continue
                if canonical not in self.bindings and value == "opaque":
                    continue
            if canonical not in self.bindings:
                raise parameter(field, f"Workflow не поддерживает параметр {field}")
            if canonical == "prompt" and not isinstance(value, str):
                raise parameter(field, "prompt должен быть строкой")
            values[canonical] = value
        if needed - values.keys():
            raise parameter(next(iter(sorted(needed - values.keys()))), "Для операции требуется входной файл")
        seed = params.get("seed")
        if seed is None:
            seed = secrets.randbits(63)
        if type(seed) is not int or not 0 <= seed <= 2**64 - 1:
            raise parameter("seed", "seed должен быть целым числом от 0 до 2^64−1")
        values["seed"] = seed
        for field, value in values.items():
            node, target = self.bindings[field]
            result[node]["inputs"][target] = value
        return result
