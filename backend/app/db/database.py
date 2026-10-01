from collections.abc import Generator
from functools import lru_cache, wraps
import time

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_app_settings
from app.db.models import Base

_KNOWLEDGE_BASE_ALTER_STATEMENTS = {
    "default_retrieval_strategy": "ALTER TABLE knowledge_bases ADD COLUMN default_retrieval_strategy VARCHAR(100) NOT NULL DEFAULT 'hybrid_rrf_no_rerank'",
    "default_top_k": "ALTER TABLE knowledge_bases ADD COLUMN default_top_k INTEGER NOT NULL DEFAULT 6",
    "default_rerank_enabled": "ALTER TABLE knowledge_bases ADD COLUMN default_rerank_enabled BOOLEAN NOT NULL DEFAULT 0",
}
_DOCUMENT_ALTER_STATEMENTS = {
    "parser_version": "ALTER TABLE documents ADD COLUMN parser_version VARCHAR(32)",
    "chunker_version": "ALTER TABLE documents ADD COLUMN chunker_version VARCHAR(32)",
    "embedding_model": "ALTER TABLE documents ADD COLUMN embedding_model VARCHAR(255)",
    "embedding_dimensions": "ALTER TABLE documents ADD COLUMN embedding_dimensions INTEGER",
    "embedding_is_fallback": "ALTER TABLE documents ADD COLUMN embedding_is_fallback BOOLEAN NOT NULL DEFAULT 0",
    "index_table": "ALTER TABLE documents ADD COLUMN index_table VARCHAR(255)",
    "retry_count": "ALTER TABLE documents ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
    "last_indexed_at": "ALTER TABLE documents ADD COLUMN last_indexed_at DATETIME",
}
_CHAT_SESSION_ALTER_STATEMENTS = {
    "owner_user_id": "ALTER TABLE chat_sessions ADD COLUMN owner_user_id INTEGER",
    "research_project_id": "ALTER TABLE chat_sessions ADD COLUMN research_project_id INTEGER",
}
_CHAT_MESSAGE_ALTER_STATEMENTS = {
    "artifacts_json": "ALTER TABLE chat_messages ADD COLUMN artifacts_json JSON NOT NULL DEFAULT '[]'",
    "agent_trace_json": "ALTER TABLE chat_messages ADD COLUMN agent_trace_json JSON NOT NULL DEFAULT '{}'",
}
_CHAT_REQUEST_LOG_ALTER_STATEMENTS = {
    "stream_id": "ALTER TABLE chat_request_logs ADD COLUMN stream_id VARCHAR(32)",
    "terminal_status": "ALTER TABLE chat_request_logs ADD COLUMN terminal_status VARCHAR(20)",
    "error_message": "ALTER TABLE chat_request_logs ADD COLUMN error_message VARCHAR(500)",
    "agent_step_count": "ALTER TABLE chat_request_logs ADD COLUMN agent_step_count INTEGER NOT NULL DEFAULT 0",
}
_RESEARCH_EXPERIMENT_ALTER_STATEMENTS = {
    "project_protocol_revision_id": "ALTER TABLE research_experiments ADD COLUMN project_protocol_revision_id INTEGER",
    "project_protocol_json": "ALTER TABLE research_experiments ADD COLUMN project_protocol_json JSON NOT NULL DEFAULT '{}'",
    "project_protocol_hash": "ALTER TABLE research_experiments ADD COLUMN project_protocol_hash VARCHAR(64) NOT NULL DEFAULT ''",
}


@lru_cache(maxsize=1)
def get_engine():
    settings = get_app_settings()
    return create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        future=True,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


@lru_cache(maxsize=1)
def get_index_engine():
    """Index worker 专用引擎，独立连接池，避免与 chat 请求争抢连接。"""
    settings = get_app_settings()
    return create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        future=True,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=3,
    )


@lru_cache(maxsize=1)
def get_session_factory():
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)


@lru_cache(maxsize=1)
def get_index_session_factory():
    """Index worker 专用 session factory。"""
    return sessionmaker(bind=get_index_engine(), autoflush=False, autocommit=False, future=True)


def init_database() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        # WAL 模式：允许读写并发，大幅减少 "database is locked" 错误
        connection.execute(text("PRAGMA journal_mode=WAL"))
        # 写冲突时等待 10 秒再报错，而不是立即失败
        connection.execute(text("PRAGMA busy_timeout=10000"))
        _ensure_knowledge_base_schema(connection)
        _ensure_chat_session_columns(connection)
        _ensure_chat_message_columns(connection)
        _ensure_chat_request_log_columns(connection)
        _ensure_document_columns(connection)
        _ensure_research_experiment_columns(connection)
        connection.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                    chunk_id UNINDEXED,
                    content,
                    title,
                    section,
                    symbol_name,
                    document_id UNINDEXED,
                    knowledge_base_id UNINDEXED
                )
                """
            )
        )


def _ensure_document_columns(connection: Connection) -> None:
    existing_columns = _table_columns(connection, "documents")
    for column_name, statement in _DOCUMENT_ALTER_STATEMENTS.items():
        if column_name not in existing_columns:
            connection.exec_driver_sql(statement)


def _ensure_chat_message_columns(connection: Connection) -> None:
    existing_columns = _table_columns(connection, "chat_messages")
    for column_name, statement in _CHAT_MESSAGE_ALTER_STATEMENTS.items():
        if column_name not in existing_columns:
            connection.exec_driver_sql(statement)


def _ensure_chat_request_log_columns(connection: Connection) -> None:
    existing_columns = _table_columns(connection, "chat_request_logs")
    for column_name, statement in _CHAT_REQUEST_LOG_ALTER_STATEMENTS.items():
        if column_name not in existing_columns:
            connection.exec_driver_sql(statement)


def _ensure_chat_session_columns(connection: Connection) -> None:
    existing_columns = _table_columns(connection, "chat_sessions")
    for column_name, statement in _CHAT_SESSION_ALTER_STATEMENTS.items():
        if column_name not in existing_columns:
            connection.exec_driver_sql(statement)


def _ensure_research_experiment_columns(connection: Connection) -> None:
    existing_columns = _table_columns(connection, "research_experiments")
    for column_name, statement in _RESEARCH_EXPERIMENT_ALTER_STATEMENTS.items():
        if column_name not in existing_columns:
            connection.exec_driver_sql(statement)


def _ensure_knowledge_base_schema(connection: Connection) -> None:
    existing_columns = _table_columns(connection, "knowledge_bases")
    if not existing_columns:
        return
    if not _knowledge_base_schema_is_current(connection, existing_columns):
        owner_select = "owner_user_id" if "owner_user_id" in existing_columns else "NULL"
        strategy_select = "default_retrieval_strategy" if "default_retrieval_strategy" in existing_columns else "'hybrid_rrf_no_rerank'"
        top_k_select = "default_top_k" if "default_top_k" in existing_columns else "6"
        rerank_select = "default_rerank_enabled" if "default_rerank_enabled" in existing_columns else "0"
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            connection.exec_driver_sql("DROP TABLE IF EXISTS knowledge_bases__new")
            connection.exec_driver_sql(
                """
                CREATE TABLE knowledge_bases__new (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER REFERENCES users (id),
                    name VARCHAR(200) NOT NULL,
                    description TEXT,
                    default_retrieval_strategy VARCHAR(100) NOT NULL DEFAULT 'hybrid_rrf_no_rerank',
                    default_top_k INTEGER NOT NULL DEFAULT 6,
                    default_rerank_enabled BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_knowledge_base_owner_name UNIQUE (owner_user_id, name)
                )
                """
            )
            connection.exec_driver_sql(
                f"""
                INSERT INTO knowledge_bases__new (
                    id, owner_user_id, name, description, default_retrieval_strategy,
                    default_top_k, default_rerank_enabled, created_at, updated_at
                )
                SELECT id, {owner_select}, name, description, {strategy_select},
                    {top_k_select}, {rerank_select}, created_at, updated_at
                FROM knowledge_bases
                """
            )
            connection.exec_driver_sql("DROP TABLE knowledge_bases")
            connection.exec_driver_sql("ALTER TABLE knowledge_bases__new RENAME TO knowledge_bases")
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")

    existing_columns = _table_columns(connection, "knowledge_bases")
    for column_name, statement in _KNOWLEDGE_BASE_ALTER_STATEMENTS.items():
        if column_name not in existing_columns:
            connection.exec_driver_sql(statement)


def _knowledge_base_schema_is_current(connection: Connection, existing_columns: set[str]) -> bool:
    if "owner_user_id" not in existing_columns:
        return False

    unique_indexes = _unique_index_columns(connection, "knowledge_bases")
    has_owner_name_unique = any(columns == ["owner_user_id", "name"] for columns in unique_indexes)
    has_name_only_unique = any(columns == ["name"] for columns in unique_indexes)
    return has_owner_name_unique and not has_name_only_unique


def _table_columns(connection: Connection, table_name: str) -> set[str]:
    return {
        row["name"]
        for row in connection.exec_driver_sql(f"PRAGMA table_info({table_name})").mappings().all()
    }


def _unique_index_columns(connection: Connection, table_name: str) -> list[list[str]]:
    indexes = connection.exec_driver_sql(f"PRAGMA index_list({table_name})").mappings().all()
    columns_list: list[list[str]] = []
    for index in indexes:
        if not index["unique"]:
            continue
        columns = [
            row["name"]
            for row in connection.exec_driver_sql(f"PRAGMA index_info({index['name']})").mappings().all()
        ]
        columns_list.append(columns)
    return columns_list


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def sqlite_retry(max_attempts: int = 3, base_delay: float = 0.1):
    """装饰器：SQLite 写入冲突时指数退避重试。"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if "database is locked" in str(exc) and attempt < max_attempts - 1:
                        time.sleep(base_delay * (2 ** attempt))
                        continue
                    raise
        return wrapper
    return decorator
