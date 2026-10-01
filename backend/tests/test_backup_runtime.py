from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

from scripts.backup_runtime import create_backup, verify_backup


def test_runtime_backup_snapshots_database_and_verifies_hashes(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with sqlite3.connect(data_dir / "app.db") as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('ok')")
        connection.commit()
    (data_dir / "sources").mkdir()
    (data_dir / "sources" / "manifest.txt").write_text("source", encoding="utf-8")
    (data_dir / "cache").mkdir()
    (data_dir / "cache" / "ignored.bin").write_bytes(b"ignored")

    archive = tmp_path / "backup.zip"
    result = create_backup(data_dir, archive)
    assert result["file_count"] == 2
    assert verify_backup(archive)["status"] == "pass"

    with zipfile.ZipFile(archive, "a") as handle:
        handle.writestr("sources/manifest.txt", "tampered")
    assert verify_backup(archive)["status"] == "fail"
