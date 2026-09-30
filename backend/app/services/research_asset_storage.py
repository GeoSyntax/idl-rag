from __future__ import annotations

import hashlib
import re
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import get_app_settings

_ALLOWED_SUFFIXES = {".tif", ".tiff", ".geojson", ".json", ".csv"}
_IDL_SCRIPT_SUFFIX = ".pro"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class ResearchAssetStorage:
    """私有研究数据资产的受控文件存储，不接受或暴露任意本机路径。"""

    def store_upload(self, project_id: int, upload: UploadFile) -> tuple[str, str, int]:
        source_name = Path(upload.filename or "asset").name
        suffix = Path(source_name).suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise ValueError(f"研究数据暂不支持该文件格式：{suffix or '无扩展名'}")

        safe_name = self._safe_file_name(source_name)
        root = get_app_settings().research_assets_dir.resolve()
        target_dir = root / f"project-{project_id}" / f"asset-{uuid4().hex}"
        target_dir.mkdir(parents=True, exist_ok=False)
        target = target_dir / safe_name

        max_bytes = get_app_settings().research_max_upload_file_mb * 1024 * 1024
        digest = hashlib.sha256()
        size = 0
        try:
            with target.open("wb") as destination:
                while chunk := upload.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("研究数据文件超过大小限制。")
                    digest.update(chunk)
                    destination.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            target_dir.rmdir()
            raise

        relative = target.resolve().relative_to(root).as_posix()
        return f"research://assets/{relative}", digest.hexdigest(), size

    def store_idl_script_upload(self, project_id: int, upload: UploadFile) -> tuple[str, str, int]:
        """Store one local ``.pro`` source under the same private asset boundary.

        IDL sources are intentionally kept out of the generic raster upload
        allow-list.  A caller must use the dedicated research endpoint, and a
        later experiment must reference the resulting asset explicitly.
        """
        source_name = Path(upload.filename or "script.pro").name
        if Path(source_name).suffix.lower() != _IDL_SCRIPT_SUFFIX:
            raise ValueError("项目级 IDLRunner 只接受 .pro 脚本。")
        safe_name = self._safe_file_name(source_name)
        root = get_app_settings().research_assets_dir.resolve()
        target_dir = root / f"project-{project_id}" / f"asset-{uuid4().hex}"
        target_dir.mkdir(parents=True, exist_ok=False)
        target = target_dir / safe_name

        max_bytes = get_app_settings().research_max_upload_file_mb * 1024 * 1024
        digest = hashlib.sha256()
        size = 0
        try:
            with target.open("wb") as destination:
                while chunk := upload.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("IDL 脚本超过研究资产大小限制。")
                    digest.update(chunk)
                    destination.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            target_dir.rmdir()
            raise

        relative = target.resolve().relative_to(root).as_posix()
        return f"research://assets/{relative}", digest.hexdigest(), size

    def store_bytes(self, project_id: int, source_name: str, content: bytes) -> tuple[str, str, int]:
        """Store trusted service output in the same private asset boundary as uploads."""
        safe_name = self._validated_safe_file_name(source_name)
        max_bytes = get_app_settings().research_max_upload_file_mb * 1024 * 1024
        if not content:
            raise ValueError("研究数据服务未返回可保存内容。")
        if len(content) > max_bytes:
            raise ValueError("研究数据文件超过大小限制。")
        root = get_app_settings().research_assets_dir.resolve()
        target_dir = root / f"project-{project_id}" / f"asset-{uuid4().hex}"
        target_dir.mkdir(parents=True, exist_ok=False)
        target = target_dir / safe_name
        try:
            target.write_bytes(content)
        except Exception:
            target.unlink(missing_ok=True)
            target_dir.rmdir()
            raise
        relative = target.resolve().relative_to(root).as_posix()
        return f"research://assets/{relative}", hashlib.sha256(content).hexdigest(), len(content)

    def resolve_asset_uri(self, source_uri: str) -> Path:
        prefix = "research://assets/"
        if not source_uri.startswith(prefix):
            raise ValueError("PythonRunner 目前只接受平台托管的 research://assets/ 数据资产。")
        relative = source_uri.removeprefix(prefix)
        if not relative or Path(relative).is_absolute():
            raise ValueError("研究数据资产 URI 不合法。")
        root = get_app_settings().research_assets_dir.resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
            raise ValueError("研究数据资产不存在或路径不合法。")
        return path

    @staticmethod
    def _safe_file_name(value: str) -> str:
        normalized = _SAFE_NAME_RE.sub("_", Path(value).name).strip("._")
        suffix = Path(value).suffix.lower()
        stem = Path(normalized).stem[:120].strip("._") or "asset"
        return f"{stem}{suffix}"

    @classmethod
    def _validated_safe_file_name(cls, value: str) -> str:
        source_name = Path(value or "asset").name
        suffix = Path(source_name).suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise ValueError(f"研究数据暂不支持该文件格式：{suffix or '无扩展名'}")
        return cls._safe_file_name(source_name)
