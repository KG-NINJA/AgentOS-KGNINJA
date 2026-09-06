"""Strict validation for the bundled schema vocabulary; no dynamic schema loading."""
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import hashlib
import json
import re
from urllib.parse import urlsplit
from ..sources import digest, instant, json_bytes, strict_json


class ControlError(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def require(condition, code, status=409):
    if not condition:
        raise ControlError(code, status)


def fields(value, required, optional=()):
    require(isinstance(value, dict) and set(required) <= set(value) <= set(required) | set(optional), "INVALID_FIELDS", 400)


def text(value, maximum=2000):
    require(isinstance(value, str) and 0 < len(value) <= maximum and "\x00" not in value, "INVALID_TEXT", 400)
    return value


def integer(value, maximum=2**53-1):
    require(type(value) is int and 0 <= value <= maximum, "INVALID_INTEGER", 400)
    return value


def sha(value):
    require(isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value), "INVALID_HASH", 400)
    return value


def relative(value):
    text(value, 512)
    p = PurePosixPath(value)
    require(not p.is_absolute() and str(p) == value and ".." not in p.parts
            and "\\" not in value and ":" not in value and all(not x.startswith(".") for x in p.parts), "UNSAFE_PATH", 400)
    return value


def validate(value, schema, location="$", depth=0):
    """Fail on unsupported schema keywords; bundled schemas only, no network $ref."""
    require(depth <= 40, "SCHEMA_DEPTH", 400)
    known = {"$schema", "title", "description", "type", "properties", "required", "additionalProperties",
             "items", "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "minimum", "maximum",
             "pattern", "format", "enum", "const", "anyOf"}
    require(not set(schema) - known, "UNSUPPORTED_SCHEMA", 500)
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                validate(value, option, location, depth + 1)
                return
            except ControlError:
                pass
        raise ControlError("SCHEMA_ANY_OF:" + location, 400)
    if "const" in schema:
        require(type(value) is type(schema["const"]) and value == schema["const"], "SCHEMA_CONST:" + location, 400)
    if "enum" in schema:
        require(any(type(value) is type(x) and value == x for x in schema["enum"]), "SCHEMA_ENUM:" + location, 400)
    kind = schema.get("type")
    classes = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool, "null": type(None)}
    if kind:
        require(kind in classes and type(value) is classes[kind], "SCHEMA_TYPE:" + location, 400)
    if isinstance(value, dict):
        require(set(schema.get("required", [])) <= set(value), "SCHEMA_REQUIRED:" + location, 400)
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            require(not set(value) - set(props), "SCHEMA_EXTRA:" + location, 400)
        for key, child in value.items():
            if key in props:
                validate(child, props[key], location + "." + key, depth + 1)
    elif isinstance(value, list):
        require(schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 1000), "SCHEMA_LENGTH:" + location, 400)
        if schema.get("uniqueItems"):
            require(len({json_bytes(x) for x in value}) == len(value), "SCHEMA_DUPLICATE:" + location, 400)
        for child in value:
            validate(child, schema.get("items", {}), location + "[]", depth + 1)
    elif isinstance(value, str):
        require(schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 100000), "SCHEMA_LENGTH:" + location, 400)
        if "pattern" in schema:
            require(re.search(schema["pattern"], value), "SCHEMA_PATTERN:" + location, 400)
        if schema.get("format") == "date-time":
            try:
                instant(value)
            except ValueError as exc:
                raise ControlError("SCHEMA_TIME:" + location, 400) from exc
        if schema.get("format") == "uri":
            require(bool(urlsplit(value).scheme), "SCHEMA_URI:" + location, 400)
    elif type(value) is int:
        require(schema.get("minimum", value) <= value <= schema.get("maximum", value), "SCHEMA_RANGE:" + location, 400)


SCHEMAS = {p.stem.replace(".schema", ""): strict_json(p.read_bytes()) for p in Path(__file__).with_name("schemas").glob("*.json")}


def schema(name, value):
    validate(value, SCHEMAS[name])


@dataclass(frozen=True)
class Principal:
    actor_id: str
    role: str


def role(actor, *roles):
    require(isinstance(actor, Principal) and actor.role in roles, "FORBIDDEN", 403)


def policy_hash(config):
    directory = Path(__file__).parent
    code = {p.name: digest(p.read_bytes()) for p in sorted(directory.glob("*.py"))}
    code.update({"web/" + p.name: digest(p.read_bytes()) for p in sorted(directory.joinpath("web").glob("*")) if p.is_file()})
    return digest(json_bytes({"version": "revenue-controller/0.2", "policy": config,
                              "code": code, "schemas": SCHEMAS}))
