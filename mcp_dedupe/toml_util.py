"""Minimal TOML emitter (stdlib-only: tomllib can parse but not write).

Covers the value shapes that appear in MCP client configs: strings, bools,
ints, floats, lists of scalars, nested tables, and arrays of tables. Anything
else raises a clear error so we never silently corrupt a config file.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        escaped = escaped.replace("\n", "\\n").replace("\t", "\\t")
        return f'"{escaped}"'
    raise ValueError(f"unsupported TOML value: {type(value).__name__} ({value!r})")


def _is_table(value: Any) -> bool:
    return isinstance(value, dict)


def _is_table_array(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def _is_scalar_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(v, (str, bool, int, float)) for v in value
    )


def dumps(doc: Dict[str, Any]) -> str:
    lines: List[str] = []

    def emit_table(path: List[str], table: Dict[str, Any]) -> None:
        # Scalar / scalar-list keys first, then subtables, then table arrays.
        for key in sorted(table):
            value = table[key]
            if _is_table(value) or _is_table_array(value):
                continue
            if isinstance(value, list) and not _is_scalar_list(value):
                raise ValueError(f"unsupported TOML value for key {key!r}: {value!r}")
            lines.append(f"{key} = {_scalar(value) if not isinstance(value, list) else '[' + ', '.join(_scalar(v) for v in value) + ']'}")
        for key in sorted(table):
            value = table[key]
            if _is_table(value):
                lines.append("")
                lines.append(f"[{'.'.join(path + [key])}]")
                emit_table(path + [key], value)
        for key in sorted(table):
            value = table[key]
            if _is_table_array(value):
                for item in value:
                    lines.append("")
                    lines.append(f"[[{'.'.join(path + [key])}]]")
                    emit_table(path + [key], item)

    top_scalars = {k: v for k, v in doc.items() if not _is_table(v) and not _is_table_array(v)}
    for key in sorted(top_scalars):
        value = top_scalars[key]
        if isinstance(value, list) and not _is_scalar_list(value):
            raise ValueError(f"unsupported TOML value for key {key!r}")
        lines.append(f"{key} = {_scalar(value) if not isinstance(value, list) else '[' + ', '.join(_scalar(v) for v in value) + ']'}")
    for key in sorted(doc):
        value = doc[key]
        if _is_table(value):
            lines.append("")
            lines.append(f"[{key}]")
            emit_table([key], value)
    for key in sorted(doc):
        value = doc[key]
        if _is_table_array(value):
            for item in value:
                lines.append("")
                lines.append(f"[[{key}]]")
                emit_table([key], item)
    return "\n".join(lines).strip() + "\n"
