# ruff: noqa: E402

from pathlib import Path

from fastapi.testclient import TestClient
import numpy as np
from app.services.geospatial_runtime import configure_bundled_rasterio_data

configure_bundled_rasterio_data()

import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin


def _prepare_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("IDLRAG_IMPORT_ROOTS", str(tmp_path))
    from app.core.config import get_app_settings
    from app.db.database import get_engine, get_index_engine, get_index_session_factory, get_session_factory

    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_index_engine.cache_clear()
    get_session_factory.cache_clear()
    get_index_session_factory.cache_clear()
    from app.api.routes.auth import _clear_register_rate_limits

    _clear_register_rate_limits()


def _register(client: TestClient, username: str) -> dict:
    response = client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    assert response.status_code == 201, response.text
    return response.json()


def _headers(auth: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth['access_token']}"}


class _Response:
    def raise_for_status(self) -> None:
        return None

    @staticmethod
    def json() -> dict:
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "id": "S2A_TEST_001",
                    "collection": "sentinel-2-l2a",
                    "properties": {"datetime": "2024-06-01T10:20:30Z", "eo:cloud_cover": 3.2},
                    "assets": {
                        "visual": {
                            "href": "https://planetarycomputer.microsoft.com/api/stac/v1/assets/S2A_TEST_001/visual.tif",
                            "type": "image/tiff; application=geotiff",
                            "roles": ["data", "visual"],
                        }
                    },
                }
            ],
        }


class _Client:
    last_body: dict | None = None

    def __init__(self, **_: object) -> None:
        pass

    def __enter__(self) -> "_Client":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def post(self, _: str, *, json: dict) -> _Response:
        _Client.last_body = json
        return _Response()


def _geotiff_bytes() -> bytes:
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            width=30,
            height=30,
            count=1,
            dtype="uint16",
            crs="EPSG:4326",
            transform=from_origin(115.9, 39.2, 0.01, 0.01),
        ) as dataset:
            dataset.write(np.arange(900, dtype=np.uint16).reshape(1, 30, 30))
        return memory.read()


class _StreamResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.headers = {"content-length": str(len(content))}

    def __enter__(self) -> "_StreamResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self, _: int):
        yield self.content[:100]
        yield self.content[100:]


class _DownloadClient(_Client):
    content = _geotiff_bytes()

    def stream(self, _: str, __: str) -> _StreamResponse:
        return _StreamResponse(self.content)


class _InvalidDownloadClient(_DownloadClient):
    content = b"not a raster"


class _TokenResponse:
    def raise_for_status(self) -> None:
        return None

    @staticmethod
    def json() -> dict:
        return {"token": "st=2026-09-29T00%3A00%3A00Z&se=2026-09-30T00%3A00%3A00Z&sp=rl&sig=test"}


class _TokenClient(_Client):
    def get(self, _: str, *, params: dict | None = None) -> _TokenResponse:
        return _TokenResponse()


def test_public_stac_search_is_audited_and_import_requires_exact_candidate(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    from app.api.routes.research import stac_service
    from app.main import create_app

    monkeypatch.setattr(stac_service, "client_factory", _Client)
    with TestClient(create_app()) as client:
        owner = _register(client, "stac-owner")
        outsider = _register(client, "stac-outsider")
        owner_headers = _headers(owner)
        project = client.post(
            "/api/research/projects", json={"name": "公开数据审计", "entry_mode": "open"}, headers=owner_headers
        ).json()
        project_id = project["id"]
        response = client.post(
            f"/api/research/projects/{project_id}/stac-search",
            json={
                "provider": "planetary_computer",
                "collections": ["sentinel-2-l2a"],
                "bbox": [116.0, 39.0, 116.1, 39.1],
                "datetime_start": "2024-06-01",
                "datetime_end": "2024-06-30",
                "cloud_cover_max": 10,
                "limit": 5,
            },
            headers=owner_headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["audit_id"] > 0
        assert payload["candidates"][0]["external_id"] == "S2A_TEST_001"
        assert _Client.last_body == {
            "collections": ["sentinel-2-l2a"],
            "bbox": [116.0, 39.0, 116.1, 39.1],
            "limit": 5,
            "datetime": "2024-06-01/2024-06-30",
            "query": {"eo:cloud_cover": {"lte": 10.0}},
        }
        assert "private" not in str(_Client.last_body).lower()

        candidate = payload["candidates"][0]
        imported = client.post(
            f"/api/research/projects/{project_id}/stac-search/import",
            json={"audit_id": payload["audit_id"], "candidate": candidate, "asset_key": "visual"},
            headers=owner_headers,
        )
        assert imported.status_code == 201, imported.text
        asset = imported.json()
        assert asset["source_type"] == "reference"
        assert asset["metadata"]["stac_reference_only"] is True
        assert asset["metadata"]["download_required_before_runner"] is True
        assert asset["sha256"] is None

        spoofed = {**candidate, "external_id": "forged"}
        rejected = client.post(
            f"/api/research/projects/{project_id}/stac-search/import",
            json={"audit_id": payload["audit_id"], "candidate": spoofed, "asset_key": "visual"},
            headers=owner_headers,
        )
        assert rejected.status_code == 400

        forbidden = client.post(
            f"/api/research/projects/{project_id}/stac-search",
            json={"collections": ["sentinel-2-l2a"], "bbox": [116, 39, 116.1, 39.1]},
            headers=_headers(outsider),
        )
        assert forbidden.status_code == 404


def test_public_stac_download_crops_and_registers_private_geotiff(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    from app.api.routes.research import stac_service
    from app.main import create_app

    monkeypatch.setattr(stac_service, "client_factory", _Client)
    with TestClient(create_app()) as client:
        owner = _register(client, "stac-download-owner")
        headers = _headers(owner)
        project = client.post(
            "/api/research/projects", json={"name": "STAC 下载验收", "entry_mode": "open"}, headers=headers
        ).json()
        project_id = project["id"]
        search = client.post(
            f"/api/research/projects/{project_id}/stac-search",
            json={"collections": ["sentinel-2-l2a"], "bbox": [116, 39, 116.1, 39.1]},
            headers=headers,
        )
        assert search.status_code == 200, search.text
        payload = search.json()
        candidate = payload["candidates"][0]

        monkeypatch.setattr(stac_service, "client_factory", _DownloadClient)
        downloaded = client.post(
            f"/api/research/projects/{project_id}/stac-search/download",
            json={
                "audit_id": payload["audit_id"],
                "candidate": candidate,
                "asset_key": "visual",
                "crop_bbox": [116.0, 39.0, 116.05, 39.05],
            },
            headers=headers,
        )
        assert downloaded.status_code == 201, downloaded.text
        asset = downloaded.json()["asset"]
        assert asset["source_uri"].startswith("research://assets/")
        assert len(asset["sha256"]) == 64
        assert asset["metadata"]["stac_downloaded"] is True
        assert asset["metadata"]["stac_reference_only"] is False
        assert asset["metadata"]["raster"]["crop_applied"] is True
        assert asset["metadata"]["raster"]["width"] == 5
        assert asset["metadata"]["raster"]["height"] == 5

        from app.services.research_asset_storage import ResearchAssetStorage

        local_path = ResearchAssetStorage().resolve_asset_uri(asset["source_uri"])
        with rasterio.open(local_path) as dataset:
            assert dataset.width == 5
            assert dataset.height == 5
            assert dataset.crs.to_string() == "EPSG:4326"
        snapshot = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={"name": "STAC 下载冻结快照", "asset_ids": [asset["id"]]},
            headers=headers,
        )
        assert snapshot.status_code == 201, snapshot.text
        assert snapshot.json()["asset_ids"] == [asset["id"]]


def test_geographic_target_resolution_is_interpreted_as_metres(monkeypatch, tmp_path: Path) -> None:
    """A 10 m request must not be interpreted as ten degrees in EPSG:4326."""
    _prepare_state(monkeypatch, tmp_path)
    from app.services.research_stac_service import ResearchStacService

    output, metadata = ResearchStacService._prepare_geotiff(
        _geotiff_bytes(),
        [115.9, 39.0, 115.95, 39.05],
        1_000,
    )
    assert metadata["target_resolution_m"] == 1_000
    assert metadata["target_resolution_interpretation"] == "metres"
    # 0.05 degrees at 39N is roughly 4.3 km east-west and 5.5 km north-south.
    # The dimensions therefore remain multi-pixel, unlike the old 1x1 result.
    with MemoryFile(output) as memory:
        with memory.open() as dataset:
            assert 3 <= dataset.width <= 6
            assert 4 <= dataset.height <= 7


def test_public_stac_download_rejects_invalid_raster_without_creating_asset(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    from app.api.routes.research import stac_service
    from app.main import create_app

    monkeypatch.setattr(stac_service, "client_factory", _Client)
    with TestClient(create_app()) as client:
        owner = _register(client, "stac-invalid-owner")
        headers = _headers(owner)
        project = client.post(
            "/api/research/projects", json={"name": "STAC 格式拒绝", "entry_mode": "open"}, headers=headers
        ).json()
        project_id = project["id"]
        search = client.post(
            f"/api/research/projects/{project_id}/stac-search",
            json={"collections": ["sentinel-2-l2a"], "bbox": [116, 39, 116.1, 39.1]},
            headers=headers,
        )
        payload = search.json()
        monkeypatch.setattr(stac_service, "client_factory", _InvalidDownloadClient)
        rejected = client.post(
            f"/api/research/projects/{project_id}/stac-search/download",
            json={"audit_id": payload["audit_id"], "candidate": payload["candidates"][0], "asset_key": "visual"},
            headers=headers,
        )
        assert rejected.status_code == 400
        assert client.get(f"/api/research/projects/{project_id}/data-assets", headers=headers).json() == []


def test_planetary_computer_blob_href_uses_sas_without_storing_token(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    from app.services.research_stac_service import ResearchStacService

    service = ResearchStacService()
    monkeypatch.setattr(service, "client_factory", _TokenClient)
    href = "https://sentinel2l2a01.blob.core.windows.net/sentinel2l2/example.tif"
    signed = service._authorized_download_href(href, "planetary_computer", "sentinel-2-l2a")
    assert "sig=test" in signed
    assert "sentinel2l2a01.blob.core.windows.net" in signed


def test_large_cog_requires_crop_and_uses_range_output_path(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    from app.api.routes.research import stac_service
    from app.core.config import get_app_settings
    from app.main import create_app

    monkeypatch.setattr(stac_service, "client_factory", _Client)
    with TestClient(create_app()) as client:
        owner = _register(client, "stac-range-branch-owner")
        headers = _headers(owner)
        project_id = client.post(
            "/api/research/projects", json={"name": "大 COG Range 分支", "entry_mode": "open"}, headers=headers
        ).json()["id"]
        search = client.post(
            f"/api/research/projects/{project_id}/stac-search",
            json={"collections": ["sentinel-2-l2a"], "bbox": [116, 39, 116.1, 39.1]},
            headers=headers,
        )
        payload = search.json()
        candidate = payload["candidates"][0]
        too_large = get_app_settings().research_stac_max_download_mb * 1024 * 1024 + 1
        monkeypatch.setattr(stac_service, "_probe_content_length", lambda _: too_large)
        monkeypatch.setattr(stac_service, "_authorized_download_href", lambda href, provider, collection: href)
        monkeypatch.setattr(
            stac_service,
            "_prepare_remote_geotiff",
            lambda href, crop_bbox, target_resolution: (
                _geotiff_bytes(),
                {"source_mode": "cog_http_range", "width": 30, "height": 30},
            ),
        )

        no_crop = client.post(
            f"/api/research/projects/{project_id}/stac-search/download",
            json={"audit_id": payload["audit_id"], "candidate": candidate, "asset_key": "visual"},
            headers=headers,
        )
        assert no_crop.status_code == 400
        with_crop = client.post(
            f"/api/research/projects/{project_id}/stac-search/download",
            json={
                "audit_id": payload["audit_id"],
                "candidate": candidate,
                "asset_key": "visual",
                "crop_bbox": [116.0, 39.0, 116.05, 39.05],
            },
            headers=headers,
        )
        assert with_crop.status_code == 201, with_crop.text
        asset = with_crop.json()["asset"]
        assert asset["metadata"]["raster"]["source_mode"] == "cog_http_range"
        assert asset["metadata"]["source_sha256"] is None
        assert asset["metadata"]["source_hash_scope"] == "stored_output_only"


def test_remote_cog_range_uses_integer_gdal_cache_budget(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    from app.services import research_stac_service as stac_module
    from app.services.research_stac_service import ResearchStacService

    captured: dict[str, object] = {}

    class _Env:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def __enter__(self) -> "_Env":
            return self

        def __exit__(self, *_: object) -> None:
            return None

    class _Source:
        def __enter__(self) -> "_Source":
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(stac_module.rasterio, "Env", _Env)
    monkeypatch.setattr(stac_module.rasterio, "open", lambda _: _Source())
    monkeypatch.setattr(
        ResearchStacService,
        "_prepare_geotiff_dataset",
        lambda *_args: (b"stacked", {"width": 1, "height": 1}),
    )

    result, metadata = ResearchStacService._prepare_remote_geotiff(
        "https://example.blob.core.windows.net/scene.tif?sig=test",
        [116.0, 39.0, 116.01, 39.01],
        None,
    )
    assert result == b"stacked"
    assert metadata["width"] == 1
    assert isinstance(captured["GDAL_CACHEMAX"], int)
