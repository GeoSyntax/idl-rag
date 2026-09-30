from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.services.research_evidence_package import ResearchEvidencePackageService


def _digest_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _build_fixture(path: Path, *, output_content: bytes = b"preview") -> str:
    run_manifest = {"schema_version": 1, "run_token": "fixture-run", "outputs": []}
    output_digest = _digest_bytes(output_content)
    files = {
        "README.md": b"# Evidence\n",
        "package_manifest.json": json.dumps(
            {
                "schema_version": 1,
                "privacy": {"raw_data_included": False},
                "runner_manifest_digest": ResearchEvidencePackageService._sha256_json(run_manifest),
                "generated_outputs": [
                    {"kind": "preview", "file_name": "preview.png", "sha256": output_digest}
                ],
            },
            sort_keys=True,
        ).encode("utf-8"),
        "run_manifest.json": json.dumps(run_manifest, sort_keys=True).encode("utf-8"),
        "outputs/preview.png": output_content,
    }
    checksums = "".join(
        f"{_digest_bytes(content)}  {name}\n" for name, content in sorted(files.items())
    ).encode("utf-8")
    files["checksums.sha256"] = checksums
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, content)
    return _digest_bytes(path.read_bytes())


def test_evidence_package_verifier_accepts_valid_package_and_rejects_tampering(tmp_path: Path) -> None:
    package_path = tmp_path / "research_evidence_package.zip"
    package_sha256 = _build_fixture(package_path)
    service = ResearchEvidencePackageService()
    descriptors = [{"kind": "research_evidence_package", "file_name": package_path.name, "sha256": package_sha256}]

    verified = service.verify(
        package_path=package_path,
        package_sha256=package_sha256,
        output_descriptors=descriptors,
    )
    assert verified["verified"] is True
    assert verified["status"] == "verified"
    assert verified["checked_file_count"] == 4
    assert verified["output_count"] == 1

    original_members = {}
    with ZipFile(package_path, "r") as archive:
        original_members = {name: archive.read(name) for name in archive.namelist()}
    original_members["outputs/preview.png"] = b"tampered"
    with ZipFile(package_path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in sorted(original_members.items()):
            archive.writestr(name, content)
    rejected = service.verify(
        package_path=package_path,
        package_sha256=package_sha256,
        output_descriptors=descriptors,
    )
    assert rejected["verified"] is False
    assert rejected["status"] == "failed"
    assert any("成员摘要不一致" in issue or "登记产物摘要不一致" in issue for issue in rejected["issues"])
