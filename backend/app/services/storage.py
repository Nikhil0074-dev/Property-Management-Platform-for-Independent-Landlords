import os
import uuid

from ..config import settings


def _path(key: str) -> str:
    if "/" in key or "\\" in key or ".." in key:
        raise ValueError("bad key")
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    return os.path.join(settings.UPLOAD_DIR, key)


def save_bytes(data: bytes, ext: str) -> str:
    key = uuid.uuid4().hex + ext
    with open(_path(key), "wb") as f:
        f.write(data)
    return key


def read_bytes(key: str) -> bytes:
    with open(_path(key), "rb") as f:
        return f.read()
