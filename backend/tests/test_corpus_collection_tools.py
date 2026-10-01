from pathlib import Path

import httpx
from pypdf import PdfReader, PdfWriter

from scripts import collect_remote_sensing_corpus as collector
from scripts.split_pdf_for_indexing import split_pdf


def test_openalex_checkpoint_round_trip(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    records = [{"openalex_id": "https://openalex.org/W1", "title": "A"}]
    pages = {query: 2 for query in collector.QUERIES}

    collector._write_checkpoint(checkpoint, records, pages)
    loaded_records, loaded_pages = collector._load_checkpoint(checkpoint)

    assert loaded_records == records
    assert loaded_pages[collector.QUERIES[0]] == 2


def test_openalex_request_retries_rate_limit(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, status_code: int, payload: dict) -> None:
            self.status_code = status_code
            self.headers = {}
            self._payload = payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                request = httpx.Request("GET", "https://api.openalex.org/works")
                raise httpx.HTTPStatusError("rate limited", request=request, response=self)

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return FakeResponse(429, {})
            return FakeResponse(200, {"results": []})

    client = FakeClient()
    monkeypatch.setattr(collector.time, "sleep", lambda _seconds: None)
    payload = collector._request_openalex_page(
        client, params={"page": 1}, retries=2, retry_delay=0
    )

    assert payload == {"results": []}
    assert client.calls == 2


def test_split_pdf_keeps_page_ranges_and_hashes(tmp_path: Path) -> None:
    source = tmp_path / "large.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    writer.write(source.open("wb"))

    result = split_pdf(source, tmp_path / "parts", max_pages=1)

    assert result["source_page_count"] == 2
    assert len(result["parts"]) == 2
    assert [part["page_start"] for part in result["parts"]] == [1, 2]
    assert all(PdfReader(tmp_path / "parts" / part["path"]).pages for part in result["parts"])
    assert (tmp_path / "parts" / "split_manifest.json").exists()
