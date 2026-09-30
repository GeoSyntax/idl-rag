from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from app.db.models import EvidenceCard, FormulaSpec, ResearchDataAsset, ResearchDataSnapshot, ResearchExperiment, ResearchProject


class ResearchEvidencePackageService:
    """Build a portable, privacy-preserving package for a completed formal run.

    The package deliberately copies only runner-generated products.  It does not
    contain uploaded source imagery, original source URIs, credentials, or any
    arbitrary local paths.  Raw data remain in the protected asset store and are
    represented by IDs, hashes, and an access note.
    """

    _SCHEMA_VERSION = 1

    def build(
        self,
        *,
        output_dir: Path,
        run_token: str,
        project: ResearchProject,
        snapshot: ResearchDataSnapshot,
        assets: list[ResearchDataAsset],
        formula_spec: FormulaSpec,
        evidence_cards: list[EvidenceCard],
        experiment: ResearchExperiment,
        run_manifest: dict[str, Any],
        outputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        output_dir = output_dir.resolve()
        package_path = output_dir / "research_evidence_package.zip"
        if package_path.exists():
            raise ValueError("研究证据包已存在，拒绝覆盖既有运行产物。")

        package_manifest = self._package_manifest(
            project=project,
            snapshot=snapshot,
            assets=assets,
            formula_spec=formula_spec,
            evidence_cards=evidence_cards,
            experiment=experiment,
            run_manifest=run_manifest,
            outputs=outputs,
        )
        files: dict[str, bytes] = {
            "README.md": self._readme().encode("utf-8"),
            "package_manifest.json": self._json_bytes(package_manifest),
            "project_protocol.json": self._json_bytes(experiment.project_protocol_json),
            "data_snapshot.json": self._json_bytes(package_manifest["data_snapshot"]),
            "formula_spec.json": self._json_bytes(package_manifest["formula_spec"]),
            "evidence_cards.json": self._json_bytes(package_manifest["evidence_cards"]),
            "experiment.json": self._json_bytes(package_manifest["experiment"]),
            "run_manifest.json": self._json_bytes(run_manifest),
            "data_access.md": self._data_access_note().encode("utf-8"),
        }

        for output in outputs:
            file_name = str(output.get("file_name") or "")
            source = self._safe_output_path(output_dir, file_name)
            files[f"outputs/{file_name}"] = source.read_bytes()

        checksums = "".join(
            f"{hashlib.sha256(content).hexdigest()}  {name}\n" for name, content in sorted(files.items())
        )
        files["checksums.sha256"] = checksums.encode("utf-8")
        with ZipFile(package_path, "x", compression=ZIP_DEFLATED) as archive:
            for name, content in sorted(files.items()):
                archive.writestr(name, content)

        return {
            "kind": "research_evidence_package",
            "file_name": package_path.name,
            "uri": f"research://runs/{run_token}/{package_path.name}",
            "sha256": self._sha256(package_path),
            "size": package_path.stat().st_size,
            "metadata": {
                "schema_version": self._SCHEMA_VERSION,
                "raw_data_included": False,
                "verification_file": "checksums.sha256",
                "entrypoint": "README.md",
            },
        }

    def verify(
        self,
        *,
        package_path: Path,
        package_sha256: str | None,
        output_descriptors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Verify a persisted evidence package without reading protected inputs.

        Verification is deliberately limited to the package boundary: the ZIP's
        outer digest, its internal checksums, the runner-manifest digest, and the
        generated output digests. It does not claim that the underlying science
        is correct or that the referenced private assets are still available.
        """
        issues: list[str] = []
        checked_file_count = 0
        output_count = 0
        package_path = package_path.resolve()
        if package_path.is_symlink() or not package_path.is_file():
            return {
                "verified": False,
                "status": "failed",
                "package_file_name": package_path.name,
                "package_sha256": None,
                "checked_file_count": 0,
                "output_count": 0,
                "issues": ["研究证据包文件不存在或路径不安全。"],
                "notice": "研究证据包完整性校验失败。",
            }

        actual_package_sha256 = self._sha256(package_path)
        if package_sha256 and actual_package_sha256 != package_sha256:
            issues.append("证据包外层 SHA-256 与运行记录不一致。")

        try:
            with ZipFile(package_path, "r") as archive:
                bad_member = archive.testzip()
                if bad_member:
                    issues.append(f"证据包 ZIP 成员损坏：{bad_member}。")
                names = archive.namelist()
                if len(names) != len(set(names)):
                    issues.append("证据包包含重复的 ZIP 成员。")
                if "checksums.sha256" not in names:
                    issues.append("证据包缺少 checksums.sha256。")
                checksum_entries: dict[str, str] = {}
                if "checksums.sha256" in names:
                    for line_number, raw_line in enumerate(
                        archive.read("checksums.sha256").decode("utf-8").splitlines(), start=1
                    ):
                        if not raw_line.strip():
                            continue
                        try:
                            digest, member_name = raw_line.split("  ", 1)
                        except ValueError:
                            issues.append(f"checksums.sha256 第 {line_number} 行格式无效。")
                            continue
                        member_name = member_name.strip()
                        if len(digest) != 64 or any(character not in "0123456789abcdefABCDEF" for character in digest):
                            issues.append(f"checksums.sha256 第 {line_number} 行摘要无效。")
                            continue
                        member_parts = PurePosixPath(member_name).parts
                        safe_member_name = (
                            bool(member_name)
                            and not PurePosixPath(member_name).is_absolute()
                            and ".." not in member_parts
                            and (len(member_parts) == 1 or (len(member_parts) == 2 and member_parts[0] == "outputs"))
                        )
                        if not safe_member_name:
                            issues.append(f"checksums.sha256 第 {line_number} 行成员路径不安全。")
                            continue
                        if member_name in checksum_entries:
                            issues.append(f"checksums.sha256 重复记录成员：{member_name}。")
                        checksum_entries[member_name] = digest.lower()

                    actual_members = set(names) - {"checksums.sha256"}
                    if set(checksum_entries) != actual_members:
                        missing = sorted(actual_members - set(checksum_entries))
                        extra = sorted(set(checksum_entries) - actual_members)
                        if missing:
                            issues.append(f"checksums.sha256 缺少成员：{', '.join(missing[:5])}。")
                        if extra:
                            issues.append(f"checksums.sha256 包含不存在成员：{', '.join(extra[:5])}。")
                    for member_name, expected_digest in checksum_entries.items():
                        if member_name not in names:
                            continue
                        checked_file_count += 1
                        actual_digest = hashlib.sha256(archive.read(member_name)).hexdigest()
                        if actual_digest != expected_digest:
                            issues.append(f"成员摘要不一致：{member_name}。")

                package_manifest: dict[str, Any] = {}
                run_manifest: dict[str, Any] = {}
                for manifest_name, target in (
                    ("package_manifest.json", "package"),
                    ("run_manifest.json", "run"),
                ):
                    if manifest_name not in names:
                        issues.append(f"证据包缺少 {manifest_name}。")
                        continue
                    try:
                        parsed = json.loads(archive.read(manifest_name).decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        issues.append(f"{manifest_name} 不是有效 JSON。")
                        continue
                    if not isinstance(parsed, dict):
                        issues.append(f"{manifest_name} 顶层必须是 JSON 对象。")
                        continue
                    if target == "package":
                        package_manifest = parsed
                    else:
                        run_manifest = parsed

                expected_runner_digest = package_manifest.get("runner_manifest_digest")
                if expected_runner_digest and expected_runner_digest != self._sha256_json(run_manifest):
                    issues.append("package_manifest.json 中的 runner_manifest_digest 不匹配。")
                generated_outputs = package_manifest.get("generated_outputs", [])
                if not isinstance(generated_outputs, list):
                    issues.append("package_manifest.json 的 generated_outputs 不是数组。")
                    generated_outputs = []
                for output in generated_outputs:
                    if not isinstance(output, dict):
                        issues.append("package_manifest.json 存在非法生成产物记录。")
                        continue
                    file_name = str(output.get("file_name") or "")
                    member_name = f"outputs/{file_name}" if file_name else ""
                    expected_digest = output.get("sha256")
                    if not file_name or member_name not in names:
                        issues.append(f"证据包缺少登记产物：{file_name or '<empty>'}。")
                        continue
                    output_count += 1
                    if isinstance(expected_digest, str):
                        actual_digest = hashlib.sha256(archive.read(member_name)).hexdigest()
                        if actual_digest != expected_digest:
                            issues.append(f"登记产物摘要不一致：{file_name}。")

                if package_manifest.get("privacy", {}).get("raw_data_included") is not False:
                    issues.append("证据包隐私声明不是 raw_data_included=false。")
        except (OSError, ValueError, KeyError) as exc:
            issues.append(f"证据包读取失败：{str(exc)[:300]}。")

        verified = not issues
        return {
            "verified": verified,
            "status": "verified" if verified else "failed",
            "package_file_name": package_path.name,
            "package_sha256": actual_package_sha256,
            "checked_file_count": checked_file_count,
            "output_count": output_count,
            "issues": issues,
            "notice": (
                "证据包外层摘要、内部 checksums、运行清单和生成产物摘要均已通过校验。"
                if verified
                else "证据包完整性校验失败；请勿把该包作为可复核证据使用。"
            ),
        }

    def _package_manifest(
        self,
        *,
        project: ResearchProject,
        snapshot: ResearchDataSnapshot,
        assets: list[ResearchDataAsset],
        formula_spec: FormulaSpec,
        evidence_cards: list[EvidenceCard],
        experiment: ResearchExperiment,
        run_manifest: dict[str, Any],
        outputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": self._SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "scope": "completed_formal_research_run",
            "privacy": {
                "raw_data_included": False,
                "source_uris_included": False,
                "credentials_included": False,
                "access_instruction": "See data_access.md; access requires the original protected project store.",
            },
            "project": {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "entry_mode": project.entry_mode,
                "visibility": project.visibility,
                "egress_policy": project.egress_policy,
                "status": project.status,
            },
            "project_protocol": {
                "revision_id": experiment.project_protocol_revision_id,
                "sha256": experiment.project_protocol_hash,
                "snapshot": experiment.project_protocol_json,
            },
            "data_snapshot": {
                "id": snapshot.id,
                "name": snapshot.name,
                "description": snapshot.description,
                "snapshot_hash": snapshot.snapshot_hash,
                "is_frozen": snapshot.is_frozen,
                "asset_ids": snapshot.asset_ids_json,
                "asset_references": [self._asset_reference(asset) for asset in assets],
            },
            "formula_spec": {
                "id": formula_spec.id,
                "name": formula_spec.name,
                "version": formula_spec.version,
                "status": formula_spec.status,
                "spec": formula_spec.spec_json,
                "evidence_card_ids": formula_spec.evidence_card_ids_json,
            },
            "evidence_cards": [self._evidence_card(card) for card in evidence_cards],
            "experiment": {
                "id": experiment.id,
                "name": experiment.name,
                "runner_type": experiment.runner_type,
                "execution_mode": experiment.execution_mode,
                "parameters": experiment.parameters_json,
                "validation_plan": experiment.validation_plan_json,
                "visualization_contract": experiment.visualization_contract_json,
            },
            "runner_manifest_digest": self._sha256_json(run_manifest),
            "generated_outputs": [
                {
                    "kind": output.get("kind"),
                    "file_name": output.get("file_name"),
                    "sha256": output.get("sha256"),
                    "size": output.get("size"),
                    "metadata": output.get("metadata", {}),
                }
                for output in outputs
                # ``run_manifest.json`` is the package's context file.  Its
                # descriptor is inherently self-referential (the manifest
                # contains the descriptor), so listing its pre-write digest
                # as a generated product would make an otherwise valid IDL
                # or Python package fail its own output check.
                if output.get("kind") != "run_manifest"
            ],
            "conclusion_boundary": (
                "This package records one frozen formal execution and its generated products. "
                "It does not by itself establish scientific superiority, causal interpretation, or "
                "generalization beyond the registered validation plan."
            ),
        }

    @staticmethod
    def _asset_reference(asset: ResearchDataAsset) -> dict[str, Any]:
        return {
            "id": asset.id,
            "name": asset.name,
            "asset_kind": asset.asset_kind,
            "source_type": asset.source_type,
            "sha256": asset.sha256,
            "metadata": asset.metadata_json,
            "access_policy": asset.access_policy,
            "source_uri": "protected://project-asset/" + str(asset.id),
        }

    @staticmethod
    def _evidence_card(card: EvidenceCard) -> dict[str, Any]:
        return {
            "id": card.id,
            "title": card.title,
            "status": card.status,
            "source_type": card.source_type,
            "source_url": card.source_url,
            "doi": card.doi,
            "license_note": card.license_note,
            "applicability": card.applicability,
            "limitations": card.limitations,
            "metadata": card.metadata_json,
            "retrieved_at": card.retrieved_at,
        }

    @staticmethod
    def _safe_output_path(output_dir: Path, file_name: str) -> Path:
        safe_name = Path(file_name).name
        if not safe_name or safe_name != file_name:
            raise ValueError("研究证据包只能收录已登记的安全运行产物。")
        path = (output_dir / safe_name).resolve()
        if not path.is_relative_to(output_dir) or path.is_symlink() or not path.is_file():
            raise ValueError("研究证据包找不到已登记的运行产物。")
        return path

    @staticmethod
    def _json_bytes(value: Any) -> bytes:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str).encode("utf-8")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _sha256_json(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _readme() -> str:
        return """# Research Evidence Package\n\nThis ZIP is an auditable record for one completed **formal** research run.\n\n- `package_manifest.json` describes the project, frozen snapshot, formula, evidence cards, experiment, privacy boundary, and output digests.\n- `project_protocol.json`, `formula_spec.json`, `experiment.json`, and `run_manifest.json` preserve the registered execution context.\n- `outputs/` contains only runner-generated visual and analysis products, never uploaded raw input data.\n- `checksums.sha256` verifies every other file in this package.\n\nUse the original protected project store to access the referenced input data. Scientific conclusions must remain within the registered validation plan and conclusion boundary.\n"""

    @staticmethod
    def _data_access_note() -> str:
        return """# Protected data access\n\nThis evidence package intentionally excludes raw uploaded imagery, vectors, sample tables, local file paths, external credentials, and original source URIs. Each input is represented by a protected asset reference and, where available, a SHA-256 digest.\n\nTo reproduce this run with raw data, an authorized project member must use the original project asset store and confirm the frozen snapshot hash recorded in `data_snapshot.json`. Do not treat the presence of an output raster or PNG as authorization to redistribute its source data.\n"""
