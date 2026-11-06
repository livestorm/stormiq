"""JSON schemas for the models' structured answers.

Every JSON step has a Pydantic model as its schema, registered here with
`@json_schema`. Registering builds the strict JSON schema the providers are
sent (`strict_json_schema`) at import time, so a schema they would refuse,
a recursive one for instance, fails the import instead of a paid call.
`llm_client.generate_json` takes only registered schemas.

The strict schema is a subset of JSON Schema that both providers accept
(Anthropic's `output_config.format`, OpenAI's strict `response_format`).
StormIQ chooses this common subset; each provider accepts a little more.

- every object lists its properties, all of them required, and sets
  `additionalProperties: false`. An optional field is `X | None` without a
  default (an `anyOf` with null); a field with a default is refused, since
  the answer must always carry it;
- references to nested models are written out in place, so the schema has
  no `$ref` or `$defs`. The providers accept references; inlining keeps the
  schema simple and makes a recursive model easy to refuse;
- keywords outside the subset are left out of what is sent: bounds such as
  `minimum`, `maximum`, `multipleOf`, `minLength`, `maxLength`, `pattern`,
  `minItems` and `maxItems`, and `format`, `title` and `examples`. Pydantic
  still checks them on the answer (`model_validate_json`), but the model
  doesn't see them while it writes: a complete answer can break one, and the
  step then fails (`llm_client.LLMInvalidOutput`);
- anything else outside the subset (free-form objects such as `dict[str, X]`,
  tuples, `oneOf`, `Any`) is refused.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Tuple, Type, TypeVar

from pydantic import BaseModel

Model = TypeVar("Model", bound=type)

# Kept in what is sent.
_KEPT_KEYWORDS = frozenset({"type", "properties", "required", "additionalProperties", "items", "anyOf", "enum", "const", "description"})
# Left out of what is sent; Pydantic still enforces them on the answer.
_STRIPPED_KEYWORDS = frozenset({
    "title", "examples", "default",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "format",
    "minItems", "maxItems", "uniqueItems",
})
_JSON_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DEFS_PREFIX = "#/$defs/"

SCHEMAS: Dict[str, Type[BaseModel]] = {}
_STRICT: Dict[Type[BaseModel], Dict[str, Any]] = {}


class SchemaError(TypeError):
    """A Pydantic model that can't be sent as a strict JSON schema."""


def json_schema(model: Model) -> Model:
    """Register `model` as the schema of a JSON step. Builds its strict schema
    now, so an unsupported model fails at import."""
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise SchemaError(f"{model!r} is not a Pydantic model")
    name = model.__name__
    if not _NAME_RE.match(name):
        raise SchemaError(f"{name!r} can't name a schema: use letters, digits, '_' or '-', at most 64")
    if SCHEMAS.get(name, model) is not model:
        raise SchemaError(f"Two schemas are named {name}")
    _STRICT[model] = build_strict_schema(model)
    SCHEMAS[name] = model
    return model


def is_registered(model: Any) -> bool:
    return isinstance(model, type) and SCHEMAS.get(getattr(model, "__name__", "")) is model


def strict_json_schema(model: Type[BaseModel]) -> Dict[str, Any]:
    """The strict schema of a registered model, as sent to the providers (a copy)."""
    if not is_registered(model):
        raise SchemaError(f"{getattr(model, '__name__', model)!r} is not a registered schema (`@json_schema`)")
    return copy.deepcopy(_STRICT[model])


def build_strict_schema(model: Type[BaseModel]) -> Dict[str, Any]:
    """Turn a Pydantic model into the strict schema both providers accept, or raise SchemaError."""
    raw = model.model_json_schema(mode="validation")
    defs = raw.pop("$defs", {}) if isinstance(raw, dict) else {}
    strict = _strict(raw, defs, model.__name__, ())
    if strict.get("type") != "object":
        raise SchemaError(f"{model.__name__}: the answer must be a JSON object")
    problems = lint_strict_schema(strict)
    if problems:  # a bug in the conversion above, not in the model
        raise SchemaError(f"{model.__name__}: " + "; ".join(problems))
    return strict


def _strict(node: Any, defs: Dict[str, Any], path: str, stack: Tuple[str, ...]) -> Dict[str, Any]:
    if not isinstance(node, dict):
        raise SchemaError(f"{path}: expected a schema object")
    if "$ref" in node:
        ref = node["$ref"]
        name = ref[len(_DEFS_PREFIX):] if isinstance(ref, str) and ref.startswith(_DEFS_PREFIX) else ""
        if name not in defs:
            raise SchemaError(f"{path}: unsupported reference {ref!r}")
        if name in stack:
            raise SchemaError(f"{path}: recursive schema ({' -> '.join(stack + (name,))}); structured outputs refuse recursion")
        siblings = {key: value for key, value in node.items() if key != "$ref"}
        return _strict({**defs[name], **siblings}, defs, path, stack + (name,))
    if "allOf" in node:
        parts = node["allOf"]
        if not isinstance(parts, list) or len(parts) != 1:
            raise SchemaError(f"{path}: 'allOf' is not supported")
        siblings = {key: value for key, value in node.items() if key != "allOf"}
        return _strict({**parts[0], **siblings} if isinstance(parts[0], dict) else parts[0], defs, path, stack)

    unknown = sorted(key for key in node if key not in _KEPT_KEYWORDS and key not in _STRIPPED_KEYWORDS)
    if unknown:
        raise SchemaError(f"{path}: {', '.join(repr(key) for key in unknown)} not supported by structured outputs")
    if not any(key in node for key in ("type", "anyOf", "enum", "const")):
        raise SchemaError(f"{path}: every value needs a type (no `Any`)")

    out: Dict[str, Any] = {}
    if "description" in node:
        out["description"] = str(node["description"])
    if "type" in node:
        types = node["type"] if isinstance(node["type"], list) else [node["type"]]
        if not types or any(kind not in _JSON_TYPES for kind in types):
            raise SchemaError(f"{path}: unsupported type {node['type']!r}")
        out["type"] = node["type"]
    for key in ("enum", "const"):
        if key in node:
            values = node[key] if key == "enum" else [node[key]]
            if key == "enum" and (not isinstance(values, list) or not values):
                raise SchemaError(f"{path}: an enum needs values")
            if any(value is not None and not isinstance(value, (str, int, float, bool)) for value in values):
                raise SchemaError(f"{path}: {key} values must be strings, numbers, booleans or null")
            out[key] = copy.deepcopy(node[key])
    if "anyOf" in node:
        options = node["anyOf"]
        if not isinstance(options, list) or not options:
            raise SchemaError(f"{path}: 'anyOf' needs options")
        out["anyOf"] = [_strict(option, defs, f"{path}|{index}", stack) for index, option in enumerate(options)]

    is_object = "object" in (out.get("type") if isinstance(out.get("type"), list) else [out.get("type")])
    if is_object or "properties" in node:
        properties = node.get("properties")
        if not isinstance(properties, dict) or not properties:
            raise SchemaError(f"{path}: a free-form object (such as dict[str, X]) is not supported; give it named fields")
        extra = node.get("additionalProperties", False)
        if extra is not False:
            raise SchemaError(f"{path}: extra properties are not supported (additionalProperties must be false)")
        missing = [name for name in properties if name not in (node.get("required") or [])]
        if missing:
            raise SchemaError(
                f"{path}: every field must be required ({', '.join(missing)} has a default); "
                "make an optional field `X | None` without a default"
            )
        out["type"] = "object"
        out["properties"] = {name: _strict(sub, defs, f"{path}.{name}", stack) for name, sub in properties.items()}
        out["required"] = list(properties)
        out["additionalProperties"] = False
    elif "additionalProperties" in node or "required" in node:
        raise SchemaError(f"{path}: object keywords on a value that is not an object")

    is_array = "array" in (out.get("type") if isinstance(out.get("type"), list) else [out.get("type")])
    if is_array:
        if "items" not in node or not isinstance(node["items"], dict):
            raise SchemaError(f"{path}: an array needs one item schema")
        out["items"] = _strict(node["items"], defs, f"{path}[]", stack)
    elif "items" in node:
        raise SchemaError(f"{path}: 'items' on a value that is not an array")
    return out


def lint_strict_schema(schema: Any, path: str = "$") -> List[str]:
    """What breaks the strict subset in a schema meant for the providers: an
    object that isn't closed or doesn't require every field, a keyword outside
    the subset, a reference. An empty list means it passes."""
    problems: List[str] = []
    if not isinstance(schema, dict):
        return [f"{path}: not a schema object"]
    for key in schema:
        if key not in _KEPT_KEYWORDS:
            problems.append(f"{path}: keyword {key!r} is not sent")
    if "properties" in schema or schema.get("type") == "object":
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        if not properties:
            problems.append(f"{path}: an object without named fields")
        if schema.get("additionalProperties") is not False:
            problems.append(f"{path}: additionalProperties is not false")
        if sorted(schema.get("required") or []) != sorted(properties):
            problems.append(f"{path}: not every field is required")
        for name, sub in properties.items():
            problems.extend(lint_strict_schema(sub, f"{path}.{name}"))
    if "items" in schema:
        problems.extend(lint_strict_schema(schema["items"], f"{path}[]"))
    for index, option in enumerate(schema.get("anyOf") or []):
        problems.extend(lint_strict_schema(option, f"{path}|{index}"))
    return problems
