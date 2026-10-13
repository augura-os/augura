"""Creative Lab 输出 schema 校验器（无 jsonschema 依赖的轻量实现）。

单一事实源是 lab_prompts.DIAGNOSIS_SCHEMA（JSON-Schema dict）——诊断输出
先过本校验再落库，词表外自由发挥（taxonomy 漂移）在这里被拦下。

只实现 DIAGNOSIS_SCHEMA 用到的子集：required / type（object、array、
string、number、boolean，含 ["string","null"] 联合与 null）/ enum
（允许值含 None）/ properties / items，递归下钻，数组元素路径带下标。
返回错误路径列表（如 ``classification.hook_mechanism: enum violation``），
空列表 = 通过。
"""

from __future__ import annotations

from typing import Any

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    # JSON 没有 int/float 之分；bool 是 int 子类，必须排除
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _type_matches(value: Any, expected: str | list[str]) -> bool:
    names = [expected] if isinstance(expected, str) else list(expected)
    return any(_TYPE_CHECKS[name](value) for name in names if name in _TYPE_CHECKS)


def _type_label(expected: str | list[str]) -> str:
    return expected if isinstance(expected, str) else "|".join(expected)


def _validate(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    expected = schema.get("type")
    if expected is not None and not _type_matches(value, expected):
        errors.append(f"{path}: type violation (expected {_type_label(expected)})")
        return  # 类型不符时不继续下钻（下钻只会报一串误导性次生错误）
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: enum violation")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: required missing")
        for key, subschema in schema.get("properties", {}).items():
            if key in value:
                _validate(value[key], subschema, f"{path}.{key}", errors)
    elif isinstance(value, list):
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                _validate(item, item_schema, f"{path}[{index}]", errors)


def validate(data: Any, schema: dict[str, Any]) -> list[str]:
    """按 JSON-Schema dict 校验 data，返回错误路径列表（空 = 通过）。"""
    errors: list[str] = []
    _validate(data, schema, "$", errors)
    return [error.replace("$.", "", 1).replace("$", "<root>", 1) for error in errors]
