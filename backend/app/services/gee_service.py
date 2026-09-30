from __future__ import annotations

import io
import json
import mimetypes
import re
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from app.api.schemas import (
    GeeFetchRequest,
    GeeFetchResponse,
    GeeStatusResponse,
    ResearchDataAssetCreate,
    ResearchGeeFetchRequest,
    ResearchGeeFetchResponse,
)
from app.core.config import get_app_settings
from app.db.models import ChatMessage, ChatSession
from app.services.agent_service import AgentService
from app.services.research_asset_storage import ResearchAssetStorage
from app.services.research_service import ResearchService

_ALLOWED_DOWNLOAD_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".json", ".geojson", ".csv"}
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class GeeService:
    def __init__(self) -> None:
        self.agent_service = AgentService()
        self.research_service = ResearchService()
        self.research_asset_storage = ResearchAssetStorage()
        self._initialized = False

    def status(self) -> GeeStatusResponse:
        settings = get_app_settings()
        has_credentials = bool(settings.gee_service_account_email and settings.gee_service_account_key_json)
        if settings.gee_auth_mode == "adc":
            has_credentials = True
        return GeeStatusResponse(
            enabled=settings.gee_enabled,
            initialized=self._initialized,
            project=settings.gee_project or None,
            auth_mode=settings.gee_auth_mode,
            has_credentials=has_credentials,
            message="GEE 已启用。" if settings.gee_enabled else "GEE 未启用。",
        )

    def fetch_data(
        self,
        db: Session,
        payload: GeeFetchRequest,
        owner_user_id: int,
    ) -> GeeFetchResponse:
        self._validate_request(payload)
        session = self._get_or_create_session(db, payload, owner_user_id)
        artifact = self._fetch_and_save_artifact(payload, owner_user_id, session.id)
        message = ChatMessage(
            session_id=session.id,
            role="assistant",
            content=self._build_message_content(payload, artifact),
            citations_json=[],
            artifacts_json=[artifact],
        )
        db.add(message)
        if not session.title:
            session.title = f"GEE {payload.dataset_id}"[:80]
        db.commit()
        db.refresh(message)
        response_message = self.agent_service.to_message_response(message)
        return GeeFetchResponse(
            session_id=session.id,
            message=response_message,
            artifact=response_message.artifacts[0],
        )

    def fetch_research_asset(
        self,
        db: Session,
        project_id: int,
        payload: ResearchGeeFetchRequest,
        owner_user_id: int,
    ) -> ResearchGeeFetchResponse:
        """Fetch a bounded GEE result directly into the caller's project asset store."""
        self.research_service.get_project(db, project_id, owner_user_id)
        self._validate_request(payload)
        content, suggested_name = self._download_image(payload)
        file_name, normalized_content = self._normalize_research_download(content, payload, suggested_name)
        source_uri, sha256, size = self.research_asset_storage.store_bytes(
            project_id, file_name, normalized_content
        )
        asset = self.research_service.create_data_asset(
            db,
            project_id,
            ResearchDataAssetCreate(
                name=(payload.label or Path(file_name).stem)[:255],
                asset_kind="raster",
                source_type="gee",
                source_uri=source_uri,
                sha256=sha256,
                metadata={
                    "gee_query": {
                        "dataset_id": payload.dataset_id,
                        "bbox": payload.bbox,
                        "bands": payload.bands,
                        "scale": payload.scale,
                        "crs": payload.crs,
                        "composite": payload.composite,
                        "start_date": payload.start_date,
                        "end_date": payload.end_date,
                    },
                    "download_size": size,
                    "download_file_name": file_name,
                    "raw_project_data_sent": False,
                },
            ),
            owner_user_id,
        )
        return ResearchGeeFetchResponse(
            asset=asset,
            notice="GEE 查询已保存为当前项目的私有数据资产；请创建 DataSnapshot 后再用于可复现实验。",
        )

    def _validate_request(self, payload: GeeFetchRequest | ResearchGeeFetchRequest) -> None:
        settings = get_app_settings()
        if not settings.gee_enabled:
            raise ValueError("GEE 未启用，请先配置 IDLRAG_GEE_ENABLED。")
        allowed = {item.strip() for item in settings.gee_allowed_datasets.split(",") if item.strip()}
        if payload.dataset_id not in allowed:
            raise ValueError("GEE 数据集不在允许列表中。")
        if len(payload.bbox) != 4:
            raise ValueError("bbox 必须包含 [min_lon, min_lat, max_lon, max_lat]。")
        min_lon, min_lat, max_lon, max_lat = payload.bbox
        if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
            raise ValueError("bbox 经纬度范围不合法。")
        if max_lon - min_lon > settings.gee_max_bbox_degrees or max_lat - min_lat > settings.gee_max_bbox_degrees:
            raise ValueError("bbox 范围超过当前 GEE 下载限制。")
        if len(payload.bands) > settings.gee_max_bands:
            raise ValueError("选择的 bands 数量超过限制。")
        if payload.scale < settings.gee_min_scale or payload.scale > settings.gee_max_scale:
            raise ValueError("scale 超出允许范围。")
        if payload.start_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", payload.start_date):
            raise ValueError("start_date 格式必须为 YYYY-MM-DD。")
        if payload.end_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", payload.end_date):
            raise ValueError("end_date 格式必须为 YYYY-MM-DD。")

    def _get_or_create_session(self, db: Session, payload: GeeFetchRequest, owner_user_id: int) -> ChatSession:
        if payload.session_id is not None:
            session = db.get(ChatSession, payload.session_id)
            if session is None or session.owner_user_id != owner_user_id:
                raise ValueError("会话不存在。")
            return session
        session = ChatSession(owner_user_id=owner_user_id, title=f"GEE {payload.dataset_id}"[:80])
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    def _fetch_and_save_artifact(
        self,
        payload: GeeFetchRequest,
        owner_user_id: int,
        session_id: int,
    ) -> dict:
        artifact_id = uuid4().hex
        artifact_dir = get_app_settings().chat_artifacts_dir / f"user-{owner_user_id}" / f"session-{session_id}" / "gee" / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        content, suggested_name = self._download_image(payload)
        file_path = self._save_download(content, artifact_dir, self._safe_file_name(payload, suggested_name))
        media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        metadata = {
            "dataset_id": payload.dataset_id,
            "bbox": payload.bbox,
            "bands": payload.bands,
            "scale": payload.scale,
            "crs": payload.crs,
            "composite": payload.composite,
            "start_date": payload.start_date,
            "end_date": payload.end_date,
            "idl_input_path": f"../inputs/{file_path.name}",
        }
        return {
            "id": artifact_id,
            "file_name": file_path.name,
            "media_type": media_type,
            "size": file_path.stat().st_size,
            "storage_path": file_path.resolve().as_posix(),
            "kind": "gee_preview" if media_type.startswith("image/") and file_path.suffix.lower() not in {".tif", ".tiff"} else "gee_data",
            "previewable": media_type.startswith("image/") and file_path.suffix.lower() not in {".tif", ".tiff"},
            "metadata": metadata,
        }

    def _download_image(self, payload: GeeFetchRequest | ResearchGeeFetchRequest) -> tuple[bytes, str | None]:
        ee = self._initialize_ee()
        region = ee.Geometry.Rectangle(payload.bbox)
        if payload.start_date or payload.end_date:
            collection = ee.ImageCollection(payload.dataset_id).filterBounds(region)
            if payload.start_date and payload.end_date:
                collection = collection.filterDate(payload.start_date, payload.end_date)
            if payload.composite == "mean":
                image = collection.mean()
            elif payload.composite == "first":
                image = ee.Image(collection.first())
            else:
                image = collection.median()
        else:
            image = ee.Image(payload.dataset_id)
        if payload.bands:
            image = image.select(payload.bands)
        image = image.clip(region)
        url = image.getDownloadURL(
            {
                "name": self._safe_label(payload),
                "scale": payload.scale,
                "crs": payload.crs,
                "region": region,
                "format": "GEO_TIFF",
            }
        )
        settings = get_app_settings()
        with httpx.Client(timeout=settings.gee_download_timeout_seconds, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            content = response.content
        max_bytes = settings.gee_max_download_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise ValueError("GEE 下载结果超过大小限制。")
        return content, None

    def _initialize_ee(self):
        if self._initialized:
            import ee

            return ee
        settings = get_app_settings()
        try:
            import ee
        except ImportError as exc:
            raise ValueError("未安装 earthengine-api，请先同步后端依赖。") from exc
        if settings.gee_auth_mode == "adc":
            ee.Initialize(project=settings.gee_project or None)
        else:
            if not settings.gee_service_account_email or not settings.gee_service_account_key_json:
                raise ValueError("GEE service account 凭据未配置。")
            credentials = ee.ServiceAccountCredentials(
                settings.gee_service_account_email,
                key_data=settings.gee_service_account_key_json,
            )
            ee.Initialize(credentials=credentials, project=settings.gee_project or None)
        self._initialized = True
        return ee

    def _save_download(self, content: bytes, artifact_dir: Path, file_name: str) -> Path:
        if zipfile.is_zipfile(io.BytesIO(content)):
            return self._extract_zip(content, artifact_dir)
        suffix = Path(file_name).suffix.lower() or ".tif"
        if suffix not in _ALLOWED_DOWNLOAD_SUFFIXES:
            file_name = f"{Path(file_name).stem}.tif"
        file_path = artifact_dir / file_name
        file_path.write_bytes(content)
        return file_path

    def _extract_zip(self, content: bytes, artifact_dir: Path) -> Path:
        output_name, output_content = self._extract_zip_file(content)
        output_path = artifact_dir / output_name
        output_path.write_bytes(output_content)
        return output_path

    def _extract_zip_file(self, content: bytes) -> tuple[str, bytes]:
        max_bytes = get_app_settings().gee_max_download_mb * 1024 * 1024
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            candidates = []
            for info in archive.infolist():
                name = info.filename.replace("\\", "/")
                path = Path(name)
                if info.is_dir() or path.is_absolute() or ".." in path.parts:
                    continue
                if path.suffix.lower() not in _ALLOWED_DOWNLOAD_SUFFIXES:
                    continue
                if info.file_size > max_bytes:
                    continue
                candidates.append(info)
            if not candidates:
                raise ValueError("GEE 下载压缩包中没有允许的输出文件。")
            selected = sorted(candidates, key=lambda item: item.filename)[0]
            output_name = self._sanitize_name(Path(selected.filename).name)
            return output_name, archive.read(selected)

    def _normalize_research_download(
        self,
        content: bytes,
        payload: ResearchGeeFetchRequest,
        suggested_name: str | None,
    ) -> tuple[str, bytes]:
        if zipfile.is_zipfile(io.BytesIO(content)):
            return self._extract_zip_file(content)
        return self._safe_file_name(payload, suggested_name), content

    def _safe_file_name(
        self, payload: GeeFetchRequest | ResearchGeeFetchRequest, suggested_name: str | None
    ) -> str:
        if suggested_name:
            name = Path(suggested_name).name
        else:
            name = f"{self._safe_label(payload)}.tif"
        return self._sanitize_name(name)

    def _safe_label(self, payload: GeeFetchRequest | ResearchGeeFetchRequest) -> str:
        label = payload.label or payload.dataset_id.split("/")[-1] or "gee_data"
        return self._sanitize_name(label).rsplit(".", 1)[0][:80] or "gee_data"

    def _sanitize_name(self, value: str) -> str:
        name = _SAFE_NAME_RE.sub("_", value).strip("._")
        return name[:120] or "gee_data.tif"

    def _build_message_content(self, payload: GeeFetchRequest, artifact: dict) -> str:
        metadata = artifact.get("metadata") or {}
        lines = [
            "已获取 GEE 数据并保存为当前对话附件。",
            f"dataset: {payload.dataset_id}",
            f"bbox: {json.dumps(payload.bbox, ensure_ascii=False)}",
            f"bands: {', '.join(payload.bands) if payload.bands else '默认'}",
            f"scale: {payload.scale}",
            f"crs: {payload.crs}",
            f"idl_input_path: {metadata.get('idl_input_path')}",
        ]
        if payload.start_date or payload.end_date:
            lines.insert(3, f"date: {payload.start_date or '-'} 至 {payload.end_date or '-'}")
        return "\n".join(lines)
