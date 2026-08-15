from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wb_insight.storage import RawStore


def test_raw_store_writes_append_only_envelope(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")
    fetched_at = datetime(2026, 8, 15, 9, 30, tzinfo=UTC)
    records = [{"id": "DEU", "name": "Germany"}]

    artifact = store.save_records(
        dataset="countries",
        records=records,
        run_id="run-001",
        request_params={"endpoint": "/country"},
        fetched_at=fetched_at,
    )

    assert artifact.record_count == 1
    assert artifact.path.exists()
    assert artifact.path.as_posix().endswith(
        "countries/load_date=2026-08-15/run_id=run-001/countries.json"
    )

    payload = json.loads(artifact.path.read_text(encoding="utf-8"))
    assert payload["metadata"]["run_id"] == "run-001"
    assert payload["metadata"]["record_count"] == 1
    assert payload["metadata"]["request_params"] == {"endpoint": "/country"}
    assert payload["records"] == records

    with pytest.raises(FileExistsError, match="already exists"):
        store.save_records(
            dataset="countries",
            records=records,
            run_id="run-001",
            request_params={"endpoint": "/country"},
            fetched_at=fetched_at,
        )


def test_raw_store_rejects_unsafe_path_components(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")

    with pytest.raises(ValueError, match="unsupported path characters"):
        store.save_records(
            dataset="../countries",
            records=[],
            run_id="run-001",
            request_params={},
        )
