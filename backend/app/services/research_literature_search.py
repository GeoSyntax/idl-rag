from __future__ import annotations

import html
import hashlib
import re
from collections.abc import Callable
from threading import Lock
from time import monotonic, sleep
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.api.schemas import (
    EvidenceCardCreate,
    EvidenceCardResponse,
    ResearchLiteratureCandidate,
    ResearchLiteratureCandidateImport,
    ResearchLiteratureNetworkRequest,
    ResearchLiteratureNetworkResponse,
    ResearchLiteratureRagImport,
    ResearchLiteratureRagImportResponse,
    ResearchLiteratureSearchResponse,
)
from app.core.config import get_app_settings
from app.db.models import KnowledgeBase, ResearchExternalSearchLog, ResearchKnowledgeSource, ResearchProject, ResearchProjectMember
from app.services.ingest_service import IngestService
from app.services.research_service import ResearchService

_CROSSREF_WORKS_URL = "https://api.crossref.org/works"
_OPENALEX_WORKS_URL = "https://api.openalex.org/works"
_SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_SEMANTIC_SCHOLAR_PAPER_URL = "https://api.semanticscholar.org/graph/v1/paper"
_LITERATURE_PROVIDERS = {"crossref", "openalex", "semantic_scholar"}
_TAG_RE = re.compile(r"<[^>]+>")


class LiteratureProviderUnavailableError(ValueError):
    """Raised when a public literature provider cannot be reached safely."""


class ResearchLiteratureSearchService:
    """Search public paper metadata without transmitting project assets or prompts.

    Crossref and OpenAlex are used only for bibliographic discovery. Search
    results remain candidates until the user explicitly saves one as a
    candidate EvidenceCard; they are never silently ingested into the local
    RAG corpus or treated as a verified method source.
    """

    _semantic_rate_lock = Lock()
    _semantic_last_request_at = 0.0

    def __init__(self, client_factory: Callable[..., httpx.Client] = httpx.Client) -> None:
        self.client_factory = client_factory
        self.research_service = ResearchService()
        self.ingest_service = IngestService()

    def search(
        self,
        db: Session,
        project_id: int,
        owner_user_id: int,
        query: str,
        rows: int,
        provider: str = "crossref",
    ) -> ResearchLiteratureSearchResponse:
        project = self._get_owned_project(db, project_id, owner_user_id)
        normalized_query = self._validate_query(query)
        normalized_provider = provider.strip().lower()
        if normalized_provider not in _LITERATURE_PROVIDERS:
            raise ValueError("外部文献 provider 只能是 crossref、openalex 或 semantic_scholar。")
        settings = get_app_settings()
        safe_rows = min(rows, settings.research_literature_search_max_results)
        audit = ResearchExternalSearchLog(
            project_id=project.id,
            owner_user_id=owner_user_id,
            provider=normalized_provider,
            query=normalized_query,
            status="running",
            request_metadata_json={
                "rows": safe_rows,
                "provider": normalized_provider,
                "outbound_fields": self._outbound_fields(normalized_provider),
                "raw_project_data_sent": False,
            },
        )
        db.add(audit)
        db.commit()
        db.refresh(audit)
        try:
            if normalized_provider == "semantic_scholar":
                self._throttle_semantic_scholar(settings.research_semantic_scholar_min_interval_seconds)
            with self.client_factory(
                timeout=settings.research_literature_search_timeout_seconds,
                headers=self._request_headers(normalized_provider, settings.semantic_scholar_api_key),
            ) as client:
                if normalized_provider == "crossref":
                    response = client.get(
                        _CROSSREF_WORKS_URL,
                        params={
                            "query.bibliographic": normalized_query,
                            "rows": safe_rows,
                            "select": self._select_fields(),
                        },
                    )
                elif normalized_provider == "openalex":
                    response = client.get(
                        _OPENALEX_WORKS_URL,
                        params={
                            "search": normalized_query,
                            "per-page": safe_rows,
                            "select": self._openalex_select_fields(),
                        },
                    )
                else:
                    response = client.get(
                        _SEMANTIC_SCHOLAR_SEARCH_URL,
                        params={
                            "query": normalized_query,
                            "limit": safe_rows,
                            "fields": self._semantic_scholar_fields(),
                        },
                    )
                response.raise_for_status()
                payload = response.json()
            candidates = (
                self._parse_candidates(payload)
                if normalized_provider == "crossref"
                else (
                    self._parse_openalex_candidates(payload)
                    if normalized_provider == "openalex"
                    else self._parse_semantic_scholar_candidates(payload)
                )
            )
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            audit.status = "failed"
            audit.error_message = str(exc)[:1000]
            db.commit()
            raise LiteratureProviderUnavailableError(
                "外部文献元数据服务暂时不可用或触发了限流；请稍后重试，不会影响本地研究资产。"
            ) from exc

        audit.status = "completed"
        audit.result_count = len(candidates)
        audit.error_message = None
        audit.request_metadata_json = {
            **(audit.request_metadata_json or {}),
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        }
        db.commit()
        return ResearchLiteratureSearchResponse(
            audit_id=audit.id,
            query=normalized_query,
            candidates=candidates,
            notice=(
                f"结果来自 {normalized_provider} 的公开元数据检索，仅为候选来源；尚未进入本地 RAG，"
                "也不能作为已核验方法依据。保存后仍需核对原文、适用条件与许可。"
            ),
            provider=normalized_provider,
        )

    def search_public(
        self,
        query: str,
        rows: int,
        provider: str = "crossref",
    ) -> ResearchLiteratureSearchResponse:
        """Search public metadata without requiring a bound research project.

        This path is intentionally discovery-only: it does not create a
        project audit, evidence card, or RAG document. The Agent can use it in
        a normal chat after the user explicitly enables external search; any
        project-specific import still has to go through the audited research
        page flow.
        """
        normalized_query = self._validate_query(query)
        normalized_provider = provider.strip().lower()
        if normalized_provider not in _LITERATURE_PROVIDERS:
            raise ValueError("外部文献 provider 只能是 crossref、openalex 或 semantic_scholar。")
        settings = get_app_settings()
        safe_rows = min(rows, settings.research_literature_search_max_results)
        try:
            if normalized_provider == "semantic_scholar":
                self._throttle_semantic_scholar(settings.research_semantic_scholar_min_interval_seconds)
            with self.client_factory(
                timeout=settings.research_literature_search_timeout_seconds,
                headers=self._request_headers(normalized_provider, settings.semantic_scholar_api_key),
            ) as client:
                if normalized_provider == "crossref":
                    response = client.get(
                        _CROSSREF_WORKS_URL,
                        params={
                            "query.bibliographic": normalized_query,
                            "rows": safe_rows,
                            "select": self._select_fields(),
                        },
                    )
                elif normalized_provider == "openalex":
                    response = client.get(
                        _OPENALEX_WORKS_URL,
                        params={
                            "search": normalized_query,
                            "per-page": safe_rows,
                            "select": self._openalex_select_fields(),
                        },
                    )
                else:
                    response = client.get(
                        _SEMANTIC_SCHOLAR_SEARCH_URL,
                        params={
                            "query": normalized_query,
                            "limit": safe_rows,
                            "fields": self._semantic_scholar_fields(),
                        },
                    )
                response.raise_for_status()
                payload = response.json()
            candidates = (
                self._parse_candidates(payload)
                if normalized_provider == "crossref"
                else (
                    self._parse_openalex_candidates(payload)
                    if normalized_provider == "openalex"
                    else self._parse_semantic_scholar_candidates(payload)
                )
            )
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise LiteratureProviderUnavailableError(
                "外部文献元数据服务暂时不可用或触发了限流；请稍后重试，不会影响本地研究资产。"
            ) from exc

        return ResearchLiteratureSearchResponse(
            audit_id=0,
            query=normalized_query,
            candidates=candidates,
            notice=(
                f"结果来自 {normalized_provider} 的公开元数据检索，仅为候选来源；本次未绑定研究项目，"
                "未创建审计、证据卡或 RAG 文档。请在研究页核对原文、适用条件与许可。"
            ),
            provider=normalized_provider,
        )

    def import_candidate_as_evidence_card(
        self,
        db: Session,
        project_id: int,
        owner_user_id: int,
        payload: ResearchLiteratureCandidateImport,
    ) -> EvidenceCardResponse:
        audit, candidate = self._validated_candidate(db, project_id, owner_user_id, payload.audit_id, payload.candidate)
        metadata = {
            "discovery_provider": candidate.provider,
            "external_id": candidate.external_id,
            "external_search_audit_id": audit.id,
            "authors": candidate.authors,
            "container_title": candidate.container_title,
            "published_year": candidate.published_year,
            "item_type": candidate.item_type,
            "abstract": candidate.abstract,
            "candidate_only": True,
        }
        return self.research_service.create_evidence_card(
            db,
            project_id,
            EvidenceCardCreate(
                title=candidate.title,
                status="candidate",
                source_type="paper",
                source_url=candidate.source_url,
                doi=candidate.doi,
                applicability="外部元数据检索候选；需在阅读原文后补充适用范围。",
                limitations="尚未人工核验全文、方法细节、数据集适配性或许可。",
                metadata=metadata,
            ),
            owner_user_id,
        )

    def expand_semantic_scholar_network(
        self,
        db: Session,
        project_id: int,
        owner_user_id: int,
        payload: ResearchLiteratureNetworkRequest,
    ) -> ResearchLiteratureNetworkResponse:
        """Fetch a bounded citation/reference page for an audited S2 candidate.

        The submitted candidate and source audit are revalidated before the
        paper ID is placed in a URL path. Network results become a new audited
        candidate set; they are never silently imported into RAG or promoted
        to verified evidence.
        """
        source_audit, source_candidate = self._validated_candidate(
            db, project_id, owner_user_id, payload.audit_id, payload.candidate
        )
        if source_candidate.provider != "semantic_scholar":
            raise ValueError("只有 Semantic Scholar 候选支持引用/参考文献扩展。")
        paper_id = self._validate_semantic_paper_id(source_candidate.external_id)
        settings = get_app_settings()
        network_audit = ResearchExternalSearchLog(
            project_id=project_id,
            owner_user_id=owner_user_id,
            provider="semantic_scholar",
            query=f"{payload.relation}:{paper_id}"[:500],
            status="running",
            request_metadata_json={
                "rows": payload.limit,
                "offset": payload.offset,
                "provider": "semantic_scholar",
                "relation": payload.relation,
                "source_audit_id": source_audit.id,
                "source_external_id": paper_id,
                "outbound_fields": ["paper_id", "relation", "offset", "limit", "fields"],
                "raw_project_data_sent": False,
            },
        )
        db.add(network_audit)
        db.commit()
        db.refresh(network_audit)
        try:
            self._throttle_semantic_scholar(settings.research_semantic_scholar_min_interval_seconds)
            with self.client_factory(
                timeout=settings.research_literature_search_timeout_seconds,
                headers=self._request_headers("semantic_scholar", settings.semantic_scholar_api_key),
            ) as client:
                response = client.get(
                    f"{_SEMANTIC_SCHOLAR_PAPER_URL}/{quote(paper_id, safe='')}/{payload.relation}",
                    params={
                        "offset": payload.offset,
                        "limit": payload.limit,
                        "fields": self._semantic_scholar_network_fields(payload.relation),
                    },
                )
                response.raise_for_status()
                response_payload = response.json()
            candidates = self._parse_semantic_scholar_network(response_payload, payload.relation)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            network_audit.status = "failed"
            network_audit.error_message = str(exc)[:1000]
            db.commit()
            raise LiteratureProviderUnavailableError(
                "Semantic Scholar 引用关系服务暂时不可用或触发了限流；请稍后重试。"
            ) from exc

        next_offset = response_payload.get("next") if isinstance(response_payload, dict) else None
        if not isinstance(next_offset, int):
            next_offset = None
        network_audit.status = "completed"
        network_audit.result_count = len(candidates)
        network_audit.request_metadata_json = {
            **(network_audit.request_metadata_json or {}),
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            "next_offset": next_offset,
        }
        db.commit()
        return ResearchLiteratureNetworkResponse(
            audit_id=network_audit.id,
            source_audit_id=source_audit.id,
            source_candidate=source_candidate,
            relation=payload.relation,
            offset=payload.offset,
            next_offset=next_offset,
            candidates=candidates,
            notice=(
                f"已获取该候选的 {payload.relation} 公开元数据；结果仍是 candidate，"
                "不会自动进入 RAG 或升级为已核验证据。请人工阅读、去重并核对来源。"
            ),
        )

    def import_candidate_to_rag(
        self,
        db: Session,
        project_id: int,
        owner_user_id: int,
        payload: ResearchLiteratureRagImport,
    ) -> ResearchLiteratureRagImportResponse:
        """Import only the public bibliographic record/abstract into Method RAG.

        A search result is never fetched as an arbitrary remote document here.
        The user must first bind an owned Method knowledge base to the project,
        and the resulting Markdown record is explicitly labeled as an
        ``abstract_only`` candidate. Full text remains a normal user upload so
        publisher access and licensing are not guessed by the platform.
        """
        self._get_owned_project(db, project_id, owner_user_id)
        audit, candidate = self._validated_candidate(db, project_id, owner_user_id, payload.audit_id, payload.candidate)
        if not candidate.abstract:
            raise ValueError("该候选没有公开摘要；请先核对许可并手动上传论文全文到 Method 知识库。")
        knowledge_base = (
            db.query(KnowledgeBase)
            .filter(KnowledgeBase.id == payload.knowledge_base_id, KnowledgeBase.owner_user_id == owner_user_id)
            .first()
        )
        if knowledge_base is None:
            raise ValueError("只能导入到当前用户自己拥有的知识库。")
        binding = (
            db.query(ResearchKnowledgeSource)
            .filter(
                ResearchKnowledgeSource.project_id == project_id,
                ResearchKnowledgeSource.knowledge_base_id == knowledge_base.id,
                ResearchKnowledgeSource.category == "method",
            )
            .first()
        )
        if binding is None:
            raise ValueError("请先把目标知识库以 method 类别显式绑定到当前研究项目。")

        document_content = self._candidate_markdown(candidate, audit.id)
        document_name = f"literature-{candidate.provider}-{hashlib.sha256(candidate.external_id.encode()).hexdigest()[:20]}.md"
        document = self.ingest_service.queue_text_document(
            db,
            knowledge_base.id,
            document_name,
            document_content,
            owner_user_id,
        )
        return ResearchLiteratureRagImportResponse(
            document=document,
            knowledge_base_id=knowledge_base.id,
            category="method",
            notice=(
                "已将公开文献元数据和摘要排队写入当前项目的 Method RAG；记录明确标为 abstract_only，"
                "不会自动下载或宣称已核验论文全文。请在阅读原文并核对许可后，再手动上传全文或更新 EvidenceCard。"
            ),
        )

    @staticmethod
    def _candidate_markdown(candidate: ResearchLiteratureCandidate, audit_id: int) -> str:
        authors = ", ".join(candidate.authors) or "未提供"
        abstract = candidate.abstract or "（公开元数据未提供摘要）"
        return "\n".join(
            [
                f"# {candidate.title}",
                "",
                "> import_scope: abstract_only; evidence_status: candidate",
                f"> discovery_provider: {candidate.provider}",
                f"> external_search_audit_id: {audit_id}",
                f"> external_id: {candidate.external_id}",
                f"> DOI: {candidate.doi or '未提供'}",
                f"> source_url: {candidate.source_url or '未提供'}",
                f"> authors: {authors}",
                f"> container: {candidate.container_title or '未提供'}",
                f"> published_year: {candidate.published_year or '未提供'}",
                "",
                "## Abstract",
                "",
                abstract,
                "",
                "## Review boundary",
                "",
                "This record came from public bibliographic metadata. It is not a verified method source and does not contain the publisher's full text, figures, supplementary material, or license terms.",
                "",
            ]
        )

    @staticmethod
    def _validated_candidate(
        db: Session,
        project_id: int,
        owner_user_id: int,
        audit_id: int,
        candidate: ResearchLiteratureCandidate,
    ) -> tuple[ResearchExternalSearchLog, ResearchLiteratureCandidate]:
        ResearchLiteratureSearchService._get_owned_project(db, project_id, owner_user_id)
        audit = (
            db.query(ResearchExternalSearchLog)
            .filter(
                ResearchExternalSearchLog.id == audit_id,
                ResearchExternalSearchLog.project_id == project_id,
                ResearchExternalSearchLog.owner_user_id == owner_user_id,
                ResearchExternalSearchLog.provider == candidate.provider,
                ResearchExternalSearchLog.status == "completed",
            )
            .first()
        )
        if audit is None:
            raise ValueError("只能从当前项目中已完成的外部检索记录保存文献。")
        stored = (audit.request_metadata_json or {}).get("candidates", [])
        candidate_json = candidate.model_dump(mode="json")
        if candidate_json not in stored:
            raise ValueError("只能导入当前项目刚刚检索到的候选，不能伪造或跨项目复用文献记录。")
        if not candidate.title.strip() or not candidate.external_id.strip():
            raise ValueError("候选文献缺少标题或外部 ID。")
        return audit, candidate

    @staticmethod
    def _select_fields() -> str:
        return "DOI,title,author,container-title,published-print,published-online,published,type,abstract,URL"

    @staticmethod
    def _openalex_select_fields() -> str:
        return "id,doi,title,authorships,primary_location,publication_year,type,abstract_inverted_index"

    @staticmethod
    def _semantic_scholar_fields() -> str:
        return "paperId,externalIds,title,authors,venue,year,publicationTypes,abstract,url,openAccessPdf"

    @staticmethod
    def _semantic_scholar_network_fields(relation: str) -> str:
        nested_prefix = "citingPaper" if relation == "citations" else "citedPaper"
        return ",".join(
            [
                "contexts",
                "intents",
                "isInfluential",
                f"{nested_prefix}.externalIds",
                f"{nested_prefix}.title",
                f"{nested_prefix}.authors",
                f"{nested_prefix}.venue",
                f"{nested_prefix}.year",
                f"{nested_prefix}.publicationTypes",
                f"{nested_prefix}.abstract",
                f"{nested_prefix}.url",
            ]
        )

    @staticmethod
    def _request_headers(provider: str, api_key: str) -> dict[str, str]:
        headers = {"User-Agent": "IDL-RAG-Research-Workflow/0.1 (metadata discovery)"}
        if provider == "semantic_scholar" and api_key.strip():
            headers["x-api-key"] = api_key.strip()
        return headers

    @classmethod
    def _throttle_semantic_scholar(cls, minimum_interval_seconds: float) -> None:
        minimum_interval = max(0.0, float(minimum_interval_seconds))
        with cls._semantic_rate_lock:
            now = monotonic()
            wait_seconds = minimum_interval - (now - cls._semantic_last_request_at)
            if wait_seconds > 0:
                sleep(wait_seconds)
                now = monotonic()
            cls._semantic_last_request_at = now

    @staticmethod
    def _outbound_fields(provider: str) -> list[str]:
        if provider == "crossref":
            return ["query", "rows", "provider"]
        if provider == "openalex":
            return ["query", "rows", "provider"]
        return ["query", "limit", "fields", "provider"]

    @staticmethod
    def _validate_query(query: str) -> str:
        normalized = " ".join(query.split())
        if len(normalized) < 3:
            raise ValueError("文献检索关键词至少需要 3 个字符。")
        if len(normalized) > 500:
            raise ValueError("文献检索关键词不能超过 500 个字符。")
        return normalized

    @staticmethod
    def _parse_candidates(payload: Any) -> list[ResearchLiteratureCandidate]:
        if not isinstance(payload, dict):
            raise ValueError("Crossref 返回格式无效。")
        message = payload.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("items"), list):
            raise ValueError("Crossref 返回中缺少文献条目。")
        candidates: list[ResearchLiteratureCandidate] = []
        for item in message["items"]:
            if not isinstance(item, dict):
                continue
            titles = item.get("title")
            title = titles[0].strip() if isinstance(titles, list) and titles and isinstance(titles[0], str) else ""
            if not title:
                continue
            doi = item.get("DOI") if isinstance(item.get("DOI"), str) else None
            url = item.get("URL") if isinstance(item.get("URL"), str) else None
            external_id = doi or url or title
            candidates.append(
                ResearchLiteratureCandidate(
                    provider="crossref",
                    external_id=external_id[:1000],
                    title=html.unescape(_TAG_RE.sub("", title)),
                    authors=ResearchLiteratureSearchService._authors(item.get("author")),
                    container_title=ResearchLiteratureSearchService._first_text(item.get("container-title")),
                    published_year=ResearchLiteratureSearchService._published_year(item),
                    doi=doi,
                    source_url=url or (f"https://doi.org/{doi}" if doi else None),
                    item_type=item.get("type") if isinstance(item.get("type"), str) else None,
                    abstract=ResearchLiteratureSearchService._clean_abstract(item.get("abstract")),
                )
            )
        return candidates

    @staticmethod
    def _parse_openalex_candidates(payload: Any) -> list[ResearchLiteratureCandidate]:
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("OpenAlex 返回中缺少文献条目。")
        candidates: list[ResearchLiteratureCandidate] = []
        for item in payload["results"]:
            if not isinstance(item, dict):
                continue
            title = item.get("title") if isinstance(item.get("title"), str) else ""
            external_id = item.get("id") if isinstance(item.get("id"), str) else ""
            if not title or not external_id:
                continue
            doi_value = item.get("doi") if isinstance(item.get("doi"), str) else None
            doi = doi_value.removeprefix("https://doi.org/").removeprefix("http://doi.org/") if doi_value else None
            primary_location = item.get("primary_location")
            source = primary_location.get("source") if isinstance(primary_location, dict) else None
            container_title = source.get("display_name") if isinstance(source, dict) and isinstance(source.get("display_name"), str) else None
            candidates.append(
                ResearchLiteratureCandidate(
                    provider="openalex",
                    external_id=external_id[:1000],
                    title=title.strip(),
                    authors=ResearchLiteratureSearchService._openalex_authors(item.get("authorships")),
                    container_title=container_title.strip() if container_title else None,
                    published_year=item.get("publication_year") if isinstance(item.get("publication_year"), int) else None,
                    doi=doi,
                    source_url=f"https://doi.org/{doi}" if doi else external_id,
                    item_type=item.get("type") if isinstance(item.get("type"), str) else None,
                    abstract=ResearchLiteratureSearchService._openalex_abstract(item.get("abstract_inverted_index")),
                )
            )
        return candidates

    @staticmethod
    def _parse_semantic_scholar_candidates(payload: Any) -> list[ResearchLiteratureCandidate]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("Semantic Scholar 返回中缺少文献条目。")
        candidates: list[ResearchLiteratureCandidate] = []
        for item in payload["data"]:
            if not isinstance(item, dict):
                continue
            paper_id = item.get("paperId") if isinstance(item.get("paperId"), str) else ""
            title = item.get("title") if isinstance(item.get("title"), str) else ""
            if not paper_id.strip() or not title.strip():
                continue
            external_ids = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
            doi = external_ids.get("DOI") if isinstance(external_ids.get("DOI"), str) else None
            venue = item.get("venue") if isinstance(item.get("venue"), str) else None
            publication_types = item.get("publicationTypes")
            item_type = publication_types[0] if isinstance(publication_types, list) and publication_types and isinstance(publication_types[0], str) else None
            source_url = item.get("url") if isinstance(item.get("url"), str) else f"https://www.semanticscholar.org/paper/{paper_id}"
            candidates.append(
                ResearchLiteratureCandidate(
                    provider="semantic_scholar",
                    external_id=paper_id[:1000],
                    title=title.strip(),
                    authors=ResearchLiteratureSearchService._semantic_scholar_authors(item.get("authors")),
                    container_title=venue.strip() if venue and venue.strip() else None,
                    published_year=item.get("year") if isinstance(item.get("year"), int) else None,
                    doi=doi.strip() if doi and doi.strip() else None,
                    source_url=source_url,
                    item_type=item_type,
                    abstract=ResearchLiteratureSearchService._clean_abstract(item.get("abstract")),
                )
            )
        return candidates

    @staticmethod
    def _parse_semantic_scholar_network(payload: Any, relation: str) -> list[ResearchLiteratureCandidate]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("Semantic Scholar 引用关系返回格式无效。")
        paper_key = "citingPaper" if relation == "citations" else "citedPaper"
        candidates: list[ResearchLiteratureCandidate] = []
        for item in payload["data"]:
            if not isinstance(item, dict):
                continue
            paper = item.get(paper_key)
            if not isinstance(paper, dict):
                continue
            candidate_payload = {
                **paper,
                "paperId": paper.get("paperId"),
            }
            candidates.extend(ResearchLiteratureSearchService._parse_semantic_scholar_candidates({"data": [candidate_payload]}))
        return candidates

    @staticmethod
    def _validate_semantic_paper_id(value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200 or any(char.isspace() for char in normalized):
            raise ValueError("Semantic Scholar paper ID 无效。")
        if any(char in normalized for char in "/?#\\"):
            raise ValueError("Semantic Scholar paper ID 不能包含路径或查询分隔符。")
        return normalized

    @staticmethod
    def _semantic_scholar_authors(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        authors: list[str] = []
        for author in value[:20]:
            if not isinstance(author, dict):
                continue
            name = author.get("name")
            if isinstance(name, str) and name.strip():
                authors.append(name.strip())
        return authors

    @staticmethod
    def _openalex_authors(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        authors: list[str] = []
        for authorship in value[:20]:
            if not isinstance(authorship, dict):
                continue
            author = authorship.get("author")
            name = author.get("display_name") if isinstance(author, dict) else None
            if isinstance(name, str) and name.strip():
                authors.append(name.strip())
        return authors

    @staticmethod
    def _openalex_abstract(value: Any) -> str | None:
        if not isinstance(value, dict):
            return None
        words: list[tuple[int, str]] = []
        for token, positions in value.items():
            if not isinstance(token, str) or not isinstance(positions, list):
                continue
            for position in positions:
                if isinstance(position, int) and position >= 0:
                    words.append((position, token))
        abstract = " ".join(token for _, token in sorted(words, key=lambda item: item[0]))
        return abstract[:4000] or None

    @staticmethod
    def _authors(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        authors: list[str] = []
        for person in value[:20]:
            if not isinstance(person, dict):
                continue
            name = " ".join(str(person.get(field, "")).strip() for field in ("given", "family")).strip()
            if name:
                authors.append(name)
        return authors

    @staticmethod
    def _first_text(value: Any) -> str | None:
        return value[0].strip() if isinstance(value, list) and value and isinstance(value[0], str) else None

    @staticmethod
    def _published_year(item: dict[str, Any]) -> int | None:
        for key in ("published-print", "published-online", "published"):
            value = item.get(key)
            date_parts = value.get("date-parts") if isinstance(value, dict) else None
            if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list) and date_parts[0]:
                year = date_parts[0][0]
                if isinstance(year, int) and 1800 <= year <= 2100:
                    return year
        return None

    @staticmethod
    def _clean_abstract(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = " ".join(html.unescape(_TAG_RE.sub("", value)).split())
        return cleaned[:4000] or None

    @staticmethod
    def _get_owned_project(db: Session, project_id: int, owner_user_id: int) -> ResearchProject:
        project = db.query(ResearchProject).filter(ResearchProject.id == project_id).first()
        if project is None:
            raise LookupError("研究项目不存在。")
        if project.owner_user_id == owner_user_id:
            return project
        member = (
            db.query(ResearchProjectMember)
            .filter(ResearchProjectMember.project_id == project_id, ResearchProjectMember.user_id == owner_user_id)
            .first()
        )
        if member is None:
            raise LookupError("研究项目不存在。")
        return project
