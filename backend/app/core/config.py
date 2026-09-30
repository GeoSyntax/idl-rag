import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_AUTH_SECRET = "idl-rag-local-auth-secret"
logger = logging.getLogger(__name__)


class AppSettings(BaseSettings):
    app_name: str = "IDL RAG Panel"
    api_prefix: str = "/api"
    base_dir: str | None = None
    default_provider_name: str = "openai-compatible"
    default_api_base_url: str = "https://api.openai.com/v1"
    default_chat_model: str = "gpt-4.1-mini"
    default_embedding_model: str = "text-embedding-3-small"
    default_system_prompt: str = (
        "你是一个 ENVI/IDL 资料助手。回答必须优先基于检索到的资料，"
        "不确定时要明确说明，并尽量引用来源。"
    )
    embedding_dimensions: int = 1536
    environment: str = "development"
    auth_secret: str = DEFAULT_AUTH_SECRET
    allow_default_auth_secret: bool = False
    previous_auth_secrets: str = ""  # 逗号分隔的旧密钥列表
    auth_token_expire_minutes: int = 10080
    cors_origins: str = ""
    import_roots: str = ""
    allow_arbitrary_import_path: bool = False
    max_upload_files: int = 20
    max_upload_file_mb: int = 50
    max_upload_total_mb: int = 200
    research_max_upload_file_mb: int = 512
    research_literature_search_timeout_seconds: int = 15
    research_literature_search_max_results: int = 20
    semantic_scholar_api_key: str = ""
    research_semantic_scholar_min_interval_seconds: float = Field(default=0.25, ge=0, le=60)
    research_stac_search_timeout_seconds: int = 20
    research_stac_search_max_results: int = 20
    research_stac_allowed_hosts: str = "planetarycomputer.microsoft.com,earth-search.aws.element84.com,*.blob.core.windows.net"
    research_stac_download_timeout_seconds: int = 120
    research_stac_max_download_mb: int = 100
    research_stac_max_output_pixels: int = 25_000_000
    research_stac_range_cache_mb: int = 64
    max_pdf_pages: int = 300
    index_job_max_attempts: int = 3
    index_job_timeout_minutes: int = 30
    index_worker_enabled: bool = True
    index_worker_heartbeat_timeout_seconds: int = Field(default=30, ge=5, le=300)
    research_run_timeout_minutes: int = Field(default=120, ge=1, le=1440)
    # ``idl.exe`` is the command-line interpreter for project .pro wrappers.
    # Workbench launchers (idlde/envi_idl) can exit without executing source
    # and are rejected by the runners; deployments may override this with a
    # licensed ENVI batch/SAV adapter.
    idl_executable: str = "idl"
    idl_run_timeout_seconds: int = 30
    idl_run_max_stdout_chars: int = 20000
    idl_run_max_stderr_chars: int = 20000
    idl_run_max_output_files: int = 12
    idl_run_max_output_file_mb: int = 20
    idl_run_allowed_output_suffixes: str = ".png,.jpg,.jpeg,.tif,.tiff,.bmp,.gif"
    idl_run_allowed_input_suffixes: str = ".tif,.tiff,.dat,.sav,.csv,.json,.geojson"
    idl_run_max_input_files: int = 8
    idl_run_max_input_file_mb: int = 200
    gee_enabled: bool = False
    gee_project: str = ""
    gee_auth_mode: str = "service_account"
    gee_service_account_email: str = ""
    gee_service_account_key_json: str = ""
    gee_allowed_datasets: str = "COPERNICUS/S2_SR_HARMONIZED,LANDSAT/LC08/C02/T1_L2,CGIAR/SRTM90_V4"
    gee_max_download_mb: int = 100
    gee_download_timeout_seconds: int = 120
    gee_max_bbox_degrees: float = 5.0
    gee_max_bands: int = 12
    gee_min_scale: int = 1
    gee_max_scale: int = 10000

    model_config = SettingsConfigDict(env_prefix="IDLRAG_", extra="ignore")

    @property
    def is_default_auth_secret(self) -> bool:
        return self.auth_secret == DEFAULT_AUTH_SECRET

    def validate_auth_secret(self) -> None:
        if not self.is_default_auth_secret:
            return
        if self.environment.lower() in {"prod", "production"} and not self.allow_default_auth_secret:
            raise RuntimeError("生产环境必须设置 IDLRAG_AUTH_SECRET，不能使用默认认证密钥。")
        logger.warning("当前使用默认 IDLRAG_AUTH_SECRET，仅适合本地开发环境。")

    @property
    def previous_auth_secrets_list(self) -> list[str]:
        """解析逗号分隔的旧密钥列表。"""
        if not self.previous_auth_secrets:
            return []
        return [s.strip() for s in self.previous_auth_secrets.split(",") if s.strip()]

    @property
    def import_root_paths(self) -> list[Path]:
        if not self.import_roots:
            return []
        roots = [item.strip() for item in self.import_roots.split(",") if item.strip()]
        return [Path(root).expanduser().resolve() for root in roots]

    @property
    def project_root(self) -> Path:
        if self.base_dir:
            return Path(self.base_dir)
        return Path(__file__).resolve().parents[3]

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def source_dir(self) -> Path:
        return self.data_dir / "sources"

    @property
    def parsed_dir(self) -> Path:
        return self.data_dir / "parsed"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def generated_dir(self) -> Path:
        return self.data_dir / "generated"

    @property
    def chat_artifacts_dir(self) -> Path:
        return self.generated_dir / "chat"

    @property
    def research_dir(self) -> Path:
        return self.data_dir / "research"

    @property
    def research_assets_dir(self) -> Path:
        return self.research_dir / "assets"

    @property
    def research_runs_dir(self) -> Path:
        return self.research_dir / "runs"

    @property
    def lancedb_dir(self) -> Path:
        return self.data_dir / "indexes" / "lancedb"

    @property
    def index_worker_heartbeat_path(self) -> Path:
        return self.data_dir / "worker" / "index-worker-heartbeat.json"

    @property
    def ocr_cache_dir(self) -> Path:
        return self.data_dir / "cache" / "ocr"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"


@lru_cache(maxsize=1)
def get_app_settings() -> AppSettings:
    settings = AppSettings()
    for path in [
        settings.data_dir,
        settings.source_dir,
        settings.parsed_dir,
        settings.logs_dir,
        settings.generated_dir,
        settings.chat_artifacts_dir,
        settings.research_dir,
        settings.research_assets_dir,
        settings.research_runs_dir,
        settings.lancedb_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)
    return settings
