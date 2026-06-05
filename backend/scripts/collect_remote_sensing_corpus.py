from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

QUERIES = [
    "remote sensing algorithm NDVI classification atmospheric correction",
    "ENVI IDL remote sensing algorithm",
    "remote sensing image classification algorithm",
    "hyperspectral image classification remote sensing",
    "land surface temperature remote sensing algorithm",
    "vegetation index remote sensing NDVI EVI SAVI",
    "atmospheric correction remote sensing algorithm FLAASH QUAC",
    "change detection remote sensing algorithm",
    "image registration remote sensing resampling",
    "remote sensing object based image analysis algorithm",
    "SAR remote sensing classification algorithm",
    "LiDAR remote sensing algorithm",
    "thermal infrared remote sensing algorithm",
    "water index NDWI remote sensing algorithm",
    "soil adjusted vegetation index remote sensing algorithm",
    "random forest remote sensing classification",
    "support vector machine remote sensing classification",
    "deep learning remote sensing image classification",
    "semantic segmentation remote sensing imagery",
    "unmixing hyperspectral remote sensing algorithm",
    "radiometric calibration remote sensing algorithm",
    "topographic correction remote sensing algorithm",
    "cloud detection remote sensing algorithm",
    "drought monitoring remote sensing vegetation index",
    "crop classification remote sensing algorithm",
]

SOURCE_NOTE = (
    "This corpus entry was collected from the OpenAlex Works API. "
    "The title, authors, year, DOI, URL, source venue, concepts, and abstract are copied from OpenAlex metadata."
)


def reconstruct_abstract(value: dict[str, list[int]] | None) -> str:
    if not value:
        return ""
    positions: list[tuple[int, str]] = []
    for word, indexes in value.items():
        for index in indexes:
            positions.append((index, word))
    return " ".join(word for _, word in sorted(positions))


def sanitize_filename(text: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9一-鿿._-]+", "_", text).strip("._-")
    text = re.sub(r"_+", "_", text)
    return (text[:96] or fallback).strip("._-")


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def extract_record(work: dict[str, Any], query: str) -> dict[str, Any] | None:
    openalex_id = work.get("id")
    title = clean_text(work.get("display_name"))
    if not openalex_id or not title:
        return None

    doi = work.get("doi") or ""
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    landing_page_url = primary_location.get("landing_page_url") or ""
    open_access = work.get("open_access") or {}
    best_oa = work.get("best_oa_location") or {}
    url = doi or landing_page_url or openalex_id
    if not url:
        return None

    authorships = work.get("authorships") or []
    authors = [
        clean_text((authorship.get("author") or {}).get("display_name"))
        for authorship in authorships[:12]
    ]
    authors = [author for author in authors if author]

    concepts = [
        clean_text(concept.get("display_name"))
        for concept in (work.get("concepts") or [])[:10]
        if concept.get("display_name")
    ]
    keywords = [
        clean_text(keyword.get("display_name"))
        for keyword in (work.get("keywords") or [])[:10]
        if keyword.get("display_name")
    ]
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))

    return {
        "openalex_id": openalex_id,
        "doi": doi,
        "url": url,
        "landing_page_url": landing_page_url,
        "oa_pdf_url": best_oa.get("pdf_url") or "",
        "title": title,
        "year": work.get("publication_year"),
        "publication_date": work.get("publication_date") or "",
        "type": work.get("type") or "",
        "language": work.get("language") or "",
        "cited_by_count": work.get("cited_by_count") or 0,
        "source_name": source.get("display_name") or "",
        "source_type": source.get("type") or "",
        "authors": authors,
        "concepts": concepts,
        "keywords": keywords,
        "abstract": abstract,
        "query": query,
        "is_open_access": bool(open_access.get("is_oa")),
    }


def record_to_markdown(record: dict[str, Any], index: int) -> str:
    authors = "; ".join(record["authors"]) or "Unknown"
    concepts = ", ".join(record["concepts"]) or "Unspecified"
    keywords = ", ".join(record["keywords"]) or "Unspecified"
    abstract = record["abstract"] or "OpenAlex did not provide an abstract for this record."
    lines = [
        "---",
        f"corpus_id: openalex-{index:04d}",
        f"openalex_id: {record['openalex_id']}",
        f"doi: {record['doi']}",
        f"url: {record['url']}",
        f"year: {record['year']}",
        f"type: {record['type']}",
        f"source_name: {record['source_name']}",
        "collected_from: OpenAlex Works API",
        "---",
        "",
        f"# {record['title']}",
        "",
        "## Source verification",
        "",
        f"- OpenAlex ID: {record['openalex_id']}",
        f"- DOI: {record['doi'] or 'Not provided'}",
        f"- URL: {record['url']}",
        f"- Landing page: {record['landing_page_url'] or 'Not provided'}",
        f"- OA PDF: {record['oa_pdf_url'] or 'Not provided'}",
        f"- Source venue: {record['source_name'] or 'Not provided'}",
        f"- Publication year: {record['year'] or 'Not provided'}",
        f"- Record type: {record['type'] or 'Not provided'}",
        f"- Cited by count: {record['cited_by_count']}",
        "",
        "## Metadata",
        "",
        f"- Authors: {authors}",
        f"- Concepts: {concepts}",
        f"- Keywords: {keywords}",
        f"- Collection query: {record['query']}",
        f"- Open access: {record['is_open_access']}",
        "",
        "## Abstract",
        "",
        abstract,
        "",
        "## Relevance for IDL/ENVI remote-sensing RAG",
        "",
        "This record is useful for retrieval around remote-sensing algorithms, ENVI/IDL workflows, image processing, classification, indices, calibration, correction, or geospatial analysis when those topics appear in its title, abstract, concepts, or source metadata.",
        "",
        "## Provenance note",
        "",
        SOURCE_NOTE,
        "",
    ]
    return "\n".join(lines)


def collect_records(target_count: int, per_page: int, mailto: str, pause: float) -> list[dict[str, Any]]:
    client = httpx.Client(timeout=30.0, follow_redirects=True)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_by_query = {query: 1 for query in QUERIES}

    while len(records) < target_count:
        made_progress = False
        for query in QUERIES:
            if len(records) >= target_count:
                break
            page = page_by_query[query]
            response = client.get(
                "https://api.openalex.org/works",
                params={
                    "search": query,
                    "filter": "type:article|book-chapter|book|report|dataset",
                    "per-page": per_page,
                    "page": page,
                    "mailto": mailto,
                },
            )
            response.raise_for_status()
            payload = response.json()
            page_by_query[query] = page + 1
            results = payload.get("results") or []
            if not results:
                continue
            made_progress = True
            for work in results:
                record = extract_record(work, query)
                if record is None:
                    continue
                if record["openalex_id"] in seen:
                    continue
                seen.add(record["openalex_id"])
                records.append(record)
                if len(records) >= target_count:
                    break
            if pause:
                time.sleep(pause)
        if not made_progress:
            break
    client.close()
    return records


def write_outputs(records: list[dict[str, Any]], output_dir: Path, manifest_name: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / manifest_name
    with manifest_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    markdown_dir = output_dir / "openalex_remote_sensing_articles"
    markdown_dir.mkdir(parents=True, exist_ok=True)
    for stale in markdown_dir.glob("*.md"):
        stale.unlink()

    for index, record in enumerate(records, start=1):
        title_slug = sanitize_filename(record["title"], f"record_{index:04d}")
        file_path = markdown_dir / f"openalex_{index:04d}_{title_slug}.md"
        file_path.write_text(record_to_markdown(record, index), encoding="utf-8")

    return {
        "records": len(records),
        "manifest": manifest_path.as_posix(),
        "markdown_dir": markdown_dir.as_posix(),
        "markdown_files": len(list(markdown_dir.glob("*.md"))),
        "with_doi": sum(1 for record in records if record["doi"]),
        "with_abstract": sum(1 for record in records if record["abstract"]),
        "with_landing_page": sum(1 for record in records if record["landing_page_url"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect verified remote-sensing/IDL corpus metadata from OpenAlex.")
    parser.add_argument("--target-count", type=int, default=1000)
    parser.add_argument("--per-page", type=int, default=50)
    parser.add_argument("--mailto", default="idl-rag@example.com")
    parser.add_argument("--output-dir", type=Path, default=Path("data/sources/collected"))
    parser.add_argument("--manifest-name", default="openalex_remote_sensing_manifest.jsonl")
    parser.add_argument("--pause", type=float, default=0.05)
    args = parser.parse_args()

    records = collect_records(args.target_count, args.per_page, args.mailto, args.pause)
    if len(records) < args.target_count:
        raise RuntimeError(f"Only collected {len(records)} records; target was {args.target_count}.")
    summary = write_outputs(records, args.output_dir, args.manifest_name)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
