# ruff: noqa: E402
from __future__ import annotations

import hashlib
import json
import math
from urllib.parse import quote, urlparse, urlunparse

import httpx

from app.services.geospatial_runtime import configure_bundled_rasterio_data

configure_bundled_rasterio_data()

import rasterio
from rasterio.io import MemoryFile
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds
from sqlalchemy.orm import Session

from app.api.schemas import (
    ResearchDataAssetCreate,
    ResearchDataAssetResponse,
    ResearchStacCandidate,
    ResearchStacCandidateImport,
    ResearchStacDownloadRequest,
    ResearchStacDownloadResponse,
    ResearchStacSearchRequest,
    ResearchStacSearchResponse,
)
from app.core.config import get_app_settings
from app.db.models import ResearchExternalSearchLog
from app.services.research_asset_storage import ResearchAssetStorage
from app.services.research_service import ResearchService


class ResearchStacService:
    """受控的公开 STAC 查询、远端引用登记和私有 GeoTIFF 导入。"""

    _ENDPOINTS = {
        "planetary_computer": "https://planetarycomputer.microsoft.com/api/stac/v1/search",
        "earth_search": "https://earth-search.aws.element84.com/v1/search",
    }

    def __init__(self) -> None:
        self.research_service = ResearchService()
        self.asset_storage = ResearchAssetStorage()
        self.client_factory = httpx.Client

    def search(
        self,
        db: Session,
        project_id: int,
        owner_user_id: int,
        payload: ResearchStacSearchRequest,
    ) -> ResearchStacSearchResponse:
        self.research_service.get_project(db, project_id, owner_user_id)
        endpoint = self._ENDPOINTS[payload.provider]
        self._validated_host(endpoint)
        self._validate_query(payload)
        body = self._request_body(payload)
        audit = ResearchExternalSearchLog(
            project_id=project_id,
            owner_user_id=owner_user_id,
            provider=f"stac:{payload.provider}",
            query=json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            request_metadata_json={
                "endpoint": endpoint,
                "outbound_fields": ["provider", "collections", "bbox", "datetime", "query"],
                "query": body,
                "raw_project_data_sent": False,
            },
            status="completed",
        )
        try:
            with self.client_factory(timeout=get_app_settings().research_stac_search_timeout_seconds) as client:
                response = client.post(endpoint, json=body)
                response.raise_for_status()
                candidates = self._parse_candidates(payload.provider, response.json())
            audit.result_count = len(candidates)
            audit.request_metadata_json["candidates"] = [candidate.model_dump(mode="json") for candidate in candidates]
            db.add(audit)
            db.commit()
            db.refresh(audit)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            audit.status = "failed"
            audit.error_message = str(exc)[:1000]
            db.add(audit)
            db.commit()
            raise ValueError("公开 STAC 查询失败，未创建数据资产。") from exc
        return ResearchStacSearchResponse(
            audit_id=audit.id,
            provider=payload.provider,
            query=payload,
            candidates=candidates,
            notice="这是公开 STAC 元数据候选；尚未下载像元。请研究者核对时间、范围、云量和许可后再显式导入引用。",
        )

    def import_reference(
        self,
        db: Session,
        project_id: int,
        owner_user_id: int,
        payload: ResearchStacCandidateImport,
    ) -> ResearchDataAssetResponse:
        audit, selected, host, candidate_json = self._resolve_candidate(db, project_id, owner_user_id, payload)
        name = (payload.name or f"{payload.candidate.external_id}-{payload.asset_key}")[:255]
        return self.research_service.create_data_asset(
            db,
            project_id,
            ResearchDataAssetCreate(
                name=name,
                asset_kind="raster",
                source_type="reference",
                source_uri=selected.href,
                metadata={
                    "stac_reference_only": True,
                    "download_required_before_runner": True,
                    "stac_audit_id": audit.id,
                    "stac_provider": payload.candidate.provider,
                    "stac_item_id": payload.candidate.external_id,
                    "stac_collection": payload.candidate.collection,
                    "stac_asset_key": payload.asset_key,
                    "stac_host": host,
                    "candidate": candidate_json,
                    "raw_project_data_sent": False,
                },
            ),
            owner_user_id,
        )

    def download_asset(
        self,
        db: Session,
        project_id: int,
        owner_user_id: int,
        payload: ResearchStacDownloadRequest,
    ) -> ResearchStacDownloadResponse:
        """下载并可选裁剪公开 GeoTIFF，最终只留下私有、可复跑的本地资产。"""
        audit, selected, host, candidate_json = self._resolve_candidate(db, project_id, owner_user_id, payload)
        self._validate_crop_bbox(payload.crop_bbox)
        settings = get_app_settings()
        max_bytes = settings.research_stac_max_download_mb * 1024 * 1024
        authorized_href = self._authorized_download_href(
            selected.href, payload.candidate.provider, payload.candidate.collection
        )
        remote_size = self._probe_content_length(authorized_href)
        if remote_size is not None and remote_size > max_bytes:
            if payload.crop_bbox is None:
                raise ValueError("STAC 对象超过整文件下载上限；请指定 crop_bbox 使用 COG HTTP Range 窗口读取。")
            output_content, raster_metadata = self._prepare_remote_geotiff(
                authorized_href, payload.crop_bbox, payload.target_resolution
            )
            source_sha256 = None
            download_size = remote_size
            raster_metadata["source_mode"] = "cog_http_range"
        else:
            raw_content = self._download_bytes(
                authorized_href,
                payload.candidate.provider,
                payload.candidate.collection,
                authorized=True,
            )
            source_sha256 = hashlib.sha256(raw_content).hexdigest()
            output_content, raster_metadata = self._prepare_geotiff(
                raw_content, payload.crop_bbox, payload.target_resolution
            )
            download_size = len(raw_content)
            raster_metadata["source_mode"] = "full_download"
        if len(output_content) > max_bytes:
            raise ValueError("STAC 裁剪/重采样输出超过大小限制。")
        output_sha256 = hashlib.sha256(output_content).hexdigest()
        file_name = self._safe_download_name(payload.candidate.external_id, payload.asset_key)
        source_uri, sha256, size = self.asset_storage.store_bytes(project_id, file_name, output_content)
        asset = self.research_service.create_data_asset(
            db,
            project_id,
            ResearchDataAssetCreate(
                name=(payload.name or f"{payload.candidate.external_id}-{payload.asset_key}")[:255],
                asset_kind="raster",
                source_type="reference",
                source_uri=source_uri,
                sha256=sha256,
                metadata={
                    "stac_downloaded": True,
                    "stac_reference_only": False,
                    "stac_audit_id": audit.id,
                    "stac_provider": payload.candidate.provider,
                    "stac_item_id": payload.candidate.external_id,
                    "stac_collection": payload.candidate.collection,
                    "stac_asset_key": payload.asset_key,
                    "stac_host": host,
                    "stac_href": selected.href,
                    "source_sha256": source_sha256,
                    "source_hash_scope": "full_source" if source_sha256 else "stored_output_only",
                    "output_sha256": output_sha256,
                    "download_size": download_size,
                    "stored_size": size,
                    "crop_bbox": payload.crop_bbox,
                    "target_resolution": payload.target_resolution,
                    "raster": raster_metadata,
                    "candidate": candidate_json,
                    "raw_project_data_sent": False,
                },
            ),
            owner_user_id,
        )
        return ResearchStacDownloadResponse(
            asset=asset,
            notice="公开 STAC 像元已下载、校验并保存到当前项目私有 research://assets/；可创建 DataSnapshot 后用于 PythonRunner。",
        )

    def _resolve_candidate(
        self,
        db: Session,
        project_id: int,
        owner_user_id: int,
        payload: ResearchStacCandidateImport,
    ) -> tuple[ResearchExternalSearchLog, object, str, dict]:
        self.research_service.get_project(db, project_id, owner_user_id)
        audit = (
            db.query(ResearchExternalSearchLog)
            .filter(
                ResearchExternalSearchLog.id == payload.audit_id,
                ResearchExternalSearchLog.project_id == project_id,
                ResearchExternalSearchLog.owner_user_id == owner_user_id,
                ResearchExternalSearchLog.provider == f"stac:{payload.candidate.provider}",
                ResearchExternalSearchLog.status == "completed",
            )
            .first()
        )
        if audit is None:
            raise ValueError("STAC 候选的审计记录不存在、已失败或不属于当前项目。")
        stored = audit.request_metadata_json.get("candidates", [])
        candidate_json = payload.candidate.model_dump(mode="json")
        if candidate_json not in stored:
            raise ValueError("只能导入当前项目刚刚检索到的 STAC 候选，不能伪造远端引用。")
        selected = payload.candidate.assets.get(payload.asset_key)
        if selected is None:
            raise ValueError("所选 STAC asset 不存在于候选中。")
        host = self._validated_host(selected.href)
        return audit, selected, host, candidate_json

    def _probe_content_length(self, href: str) -> int | None:
        """Probe a remote object's total size with one-byte Range, without buffering it."""
        settings = get_app_settings()
        try:
            with self.client_factory(
                timeout=settings.research_stac_download_timeout_seconds,
                follow_redirects=True,
            ) as client:
                with client.stream("GET", href, headers={"Range": "bytes=0-0"}) as response:
                    response.raise_for_status()
                    self._validated_host(str(getattr(response, "url", href)))
                    content_range = response.headers.get("content-range", "")
                    if "/" in content_range:
                        total_text = content_range.rsplit("/", 1)[-1]
                        if total_text.isdigit():
                            return int(total_text)
                    content_length = response.headers.get("content-length")
                    if content_length and content_length.isdigit():
                        return int(content_length)
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return None
        return None

    def _download_bytes(
        self,
        href: str,
        provider: str,
        collection: str,
        *,
        authorized: bool = False,
    ) -> bytes:
        settings = get_app_settings()
        max_bytes = settings.research_stac_max_download_mb * 1024 * 1024
        download_href = href if authorized else self._authorized_download_href(href, provider, collection)
        try:
            with self.client_factory(
                timeout=settings.research_stac_download_timeout_seconds,
                follow_redirects=True,
            ) as client:
                with client.stream("GET", download_href) as response:
                    response.raise_for_status()
                    self._validated_host(str(getattr(response, "url", download_href)))
                    header_length = response.headers.get("content-length")
                    if header_length and int(header_length) > max_bytes:
                        raise ValueError("STAC 下载文件超过大小限制。")
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes(1024 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError("STAC 下载文件超过大小限制。")
                        chunks.append(chunk)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            if isinstance(exc, ValueError) and "大小限制" in str(exc):
                raise
            raise ValueError("STAC 像元下载失败，未创建数据资产。") from exc
        content = b"".join(chunks)
        if not content:
            raise ValueError("STAC 下载返回空文件。")
        return content

    def _authorized_download_href(self, href: str, provider: str, collection: str) -> str:
        parsed = urlparse(href)
        if provider != "planetary_computer" or not parsed.hostname or not parsed.hostname.lower().endswith(".blob.core.windows.net"):
            return href
        if "sig=" in parsed.query:
            return href
        settings = get_app_settings()
        try:
            with self.client_factory(timeout=settings.research_stac_search_timeout_seconds) as client:
                signed_response = client.get(
                    "https://planetarycomputer.microsoft.com/api/sas/v1/sign",
                    params={"href": href},
                )
                signed_response.raise_for_status()
                signed_document = signed_response.json()
            signed_href = signed_document.get("href") if isinstance(signed_document, dict) else None
            if isinstance(signed_href, str) and signed_href and "sig=" in urlparse(signed_href).query:
                self._validated_host(signed_href)
                return signed_href
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            pass

        token_endpoint = f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{quote(collection, safe='')}"
        try:
            with self.client_factory(timeout=settings.research_stac_search_timeout_seconds) as client:
                response = client.get(token_endpoint)
                response.raise_for_status()
                document = response.json()
            token = document.get("token") if isinstance(document, dict) else None
            if not isinstance(token, str) or not token:
                raise ValueError("Planetary Computer 未返回有效 SAS token。")
            return urlunparse(parsed._replace(query=token))
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise ValueError("Planetary Computer 公开对象需要有效 SAS token，当前无法授权下载。") from exc

    @staticmethod
    def _prepare_geotiff(content: bytes, crop_bbox: list[float] | None, target_resolution: float | None) -> tuple[bytes, dict]:
        try:
            with MemoryFile(content) as source_memory, source_memory.open() as source:
                return ResearchStacService._prepare_geotiff_dataset(source, crop_bbox, target_resolution)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("STAC asset 不是可读取的 GeoTIFF/COG，未创建数据资产。") from exc

    @staticmethod
    def _prepare_remote_geotiff(
        href: str, crop_bbox: list[float], target_resolution: float | None
    ) -> tuple[bytes, dict]:
        settings = get_app_settings()
        try:
            with rasterio.Env(
                GDAL_HTTP_TIMEOUT=str(settings.research_stac_download_timeout_seconds),
                GDAL_HTTP_MAX_RETRY="1",
                GDAL_CACHEMAX=settings.research_stac_range_cache_mb,
                CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF,.tiff,.TIFF",
                VSI_CACHE="TRUE",
                VSI_CACHE_SIZE="1048576",
            ):
                with rasterio.open(href) as source:
                    return ResearchStacService._prepare_geotiff_dataset(source, crop_bbox, target_resolution)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("STAC COG 的 HTTP Range 窗口读取失败，未创建数据资产。") from exc

    @staticmethod
    def _prepare_geotiff_dataset(
        source: rasterio.io.DatasetReader,
        crop_bbox: list[float] | None,
        target_resolution: float | None,
    ) -> tuple[bytes, dict]:
        if source.driver not in {"GTiff", "COG"}:
            raise ValueError("STAC asset 不是受支持的 GeoTIFF/COG。")
        if source.count < 1 or source.width < 1 or source.height < 1:
            raise ValueError("STAC GeoTIFF 没有有效栅格尺寸。")
        window = Window(0, 0, source.width, source.height)
        if crop_bbox is not None:
            if source.crs is None:
                raise ValueError("指定 crop_bbox 时，STAC GeoTIFF 必须包含 CRS。")
            bounds = transform_bounds("EPSG:4326", source.crs, *crop_bbox, densify_pts=21)
            requested = from_bounds(*bounds, transform=source.transform)
            try:
                window = requested.intersection(Window(0, 0, source.width, source.height))
            except Exception as exc:
                raise ValueError("crop_bbox 与 STAC 影像没有交集。") from exc
            window = window.round_offsets().round_lengths()
            if window.width < 1 or window.height < 1:
                raise ValueError("crop_bbox 裁剪后没有有效像元。")

        out_width = int(window.width)
        out_height = int(window.height)
        out_transform = source.window_transform(window)
        if target_resolution is not None:
            # ``target_resolution`` is expressed in metres at the API boundary.
            # Sentinel/WorldCover COGs are often published in EPSG:4326, where
            # the native transform is in degrees.  Treating 10 m as 10 degrees
            # silently collapsed the real WorldCover crop to a 1x1 raster.
            # Convert the requested window extent to metres before calculating
            # the output dimensions; projected datasets use their CRS unit
            # factor (normally metres) instead.
            window_left, window_bottom, window_right, window_top = source.window_bounds(window)
            if source.crs is not None and source.crs.is_geographic:
                center_lat = (window_bottom + window_top) / 2.0
                meters_per_degree_lat = 110_574.0
                meters_per_degree_lon = 111_320.0 * max(math.cos(math.radians(center_lat)), 1e-6)
                extent_width_m = abs(window_right - window_left) * meters_per_degree_lon
                extent_height_m = abs(window_top - window_bottom) * meters_per_degree_lat
            else:
                unit_factor = 1.0
                if source.crs is not None:
                    try:
                        unit_factor = float(source.crs.linear_units_factor)
                    except (AttributeError, TypeError, ValueError):
                        unit_factor = 1.0
                extent_width_m = abs(window_right - window_left) * unit_factor
                extent_height_m = abs(window_top - window_bottom) * unit_factor
            out_width = max(1, int(round(extent_width_m / target_resolution)))
            out_height = max(1, int(round(extent_height_m / target_resolution)))
            out_transform = out_transform * Affine.scale(window.width / out_width, window.height / out_height)
        pixels = out_width * out_height * source.count
        if pixels > get_app_settings().research_stac_max_output_pixels:
            raise ValueError("STAC 裁剪/重采样结果像元数超过限制。")
        data = source.read(
            out_shape=(source.count, out_height, out_width),
            window=window,
            masked=False,
            resampling=Resampling.nearest,
        )
        profile = source.profile.copy()
        profile.update(
            driver="GTiff",
            width=out_width,
            height=out_height,
            transform=out_transform,
            compress="lzw",
            tiled=False,
        )
        with MemoryFile() as output_memory:
            with output_memory.open(**profile) as destination:
                destination.write(data)
                metadata = {
                    "driver": destination.driver,
                    "crs": destination.crs.to_string() if destination.crs else None,
                    "width": destination.width,
                    "height": destination.height,
                    "count": destination.count,
                    "dtype": destination.dtypes[0],
                    "bounds": list(destination.bounds),
                    "crop_applied": crop_bbox is not None,
                    "resample_applied": target_resolution is not None,
                    "target_resolution_m": target_resolution,
                    "target_resolution_interpretation": "metres",
                }
            output = output_memory.read()
        return output, metadata

    @staticmethod
    def _validate_crop_bbox(crop_bbox: list[float] | None) -> None:
        if crop_bbox is None:
            return
        min_lon, min_lat, max_lon, max_lat = crop_bbox
        if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
            raise ValueError("crop_bbox 经纬度范围不合法。")

    @staticmethod
    def _safe_download_name(external_id: str, asset_key: str) -> str:
        safe = "".join(char if char.isalnum() or char in "-_." else "_" for char in f"{external_id}_{asset_key}")
        return f"{safe[:180] or 'stac_asset'}.tif"

    @classmethod
    def _validate_query(cls, payload: ResearchStacSearchRequest) -> None:
        if len(set(payload.collections)) != len(payload.collections):
            raise ValueError("STAC collections 不能重复。")
        min_lon, min_lat, max_lon, max_lat = payload.bbox
        if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
            raise ValueError("bbox 经纬度范围不合法。")
        if payload.datetime_start and payload.datetime_end and payload.datetime_start > payload.datetime_end:
            raise ValueError("datetime_start 不能晚于 datetime_end。")

    @staticmethod
    def _request_body(payload: ResearchStacSearchRequest) -> dict:
        body: dict[str, object] = {
            "collections": payload.collections,
            "bbox": payload.bbox,
            "limit": payload.limit,
        }
        if payload.datetime_start or payload.datetime_end:
            body["datetime"] = f"{payload.datetime_start.isoformat() if payload.datetime_start else '..'}/{payload.datetime_end.isoformat() if payload.datetime_end else '..'}"
        if payload.cloud_cover_max is not None:
            body["query"] = {"eo:cloud_cover": {"lte": payload.cloud_cover_max}}
        return body

    def _parse_candidates(self, provider: str, document: object) -> list[ResearchStacCandidate]:
        if not isinstance(document, dict) or not isinstance(document.get("features"), list):
            raise ValueError("STAC 响应不是合法 FeatureCollection。")
        candidates: list[ResearchStacCandidate] = []
        for feature in document["features"][: get_app_settings().research_stac_search_max_results]:
            if not isinstance(feature, dict) or not feature.get("id") or not isinstance(feature.get("assets"), dict):
                continue
            properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
            collection_value = feature.get("collection")
            if isinstance(collection_value, str) and collection_value:
                collection_name = collection_value
            elif isinstance(collection_value, list) and collection_value:
                collection_name = str(collection_value[0])
            elif isinstance(feature.get("collections"), list) and feature["collections"]:
                collection_name = str(feature["collections"][0])
            else:
                collection_name = "unknown"
            assets = {}
            for key, raw in feature["assets"].items():
                if not isinstance(raw, dict) or not isinstance(raw.get("href"), str):
                    continue
                try:
                    self._validated_host(raw["href"])
                except ValueError:
                    continue
                assets[str(key)] = {
                    "href": raw["href"],
                    "title": raw.get("title"),
                    "media_type": raw.get("type"),
                    "roles": raw.get("roles") if isinstance(raw.get("roles"), list) else [],
                }
            if not assets:
                continue
            candidates.append(
                ResearchStacCandidate(
                    provider=provider,
                    external_id=str(feature["id"]),
                    collection=collection_name,
                    datetime=properties.get("datetime"),
                    cloud_cover=properties.get("eo:cloud_cover"),
                    assets=assets,
                )
            )
        return candidates

    @staticmethod
    def _validated_host(value: str) -> str:
        parsed = urlparse(value)
        allowed = [item.strip().lower() for item in get_app_settings().research_stac_allowed_hosts.split(",") if item.strip()]
        host = parsed.hostname.lower() if parsed.hostname else ""
        exact_allowed = {item for item in allowed if not item.startswith("*.")}
        suffix_allowed = [item[1:] for item in allowed if item.startswith("*.")]
        host_allowed = host in exact_allowed or any(host.endswith(suffix) for suffix in suffix_allowed)
        if parsed.scheme != "https" or not host or not host_allowed:
            raise ValueError("STAC asset 必须来自配置的 HTTPS 公共域名白名单。")
        return host
