import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import InputError


def read_json(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputError(f"JSON 文件不存在：{source}", code="JSON_NOT_FOUND") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"无法读取 JSON：{source}: {exc}", code="INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise InputError(f"JSON 顶层必须是 object：{source}", code="INVALID_JSON")
    return value


def write_json(path: str | Path, value: Any) -> Path:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = None
    temporary_path = None
    try:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary_path = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(temporary_path, destination)
    except OSError as exc:
        if handle and not handle.closed:
            handle.close()
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()
        raise InputError(f"无法写入 JSON：{destination}: {exc}", code="JSON_WRITE_FAILED") from exc
    return destination
