"""Local append-only storage for records returned by external APIs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RawArtifact:
    """Description of one persisted raw dataset."""

    dataset: str
    path: Path
    record_count: int
    run_id: str
    fetched_at: datetime


class RawStore:
    """Persist API records locally without overwriting previous runs.

    Each dataset is stored as one JSON envelope containing lineage metadata and the
    records returned by the API client. The directory layout is intentionally close
    to an object-storage prefix so the implementation can later be replaced by an
    S3-compatible Yandex Object Storage backend.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def save_records(
        self,
        *,
        dataset: str,
        records: list[dict[str, Any]],
        run_id: str,
        request_params: dict[str, Any],
        fetched_at: datetime | None = None,
    ) -> RawArtifact:
        """Save records and lineage metadata using an append-only path."""

        normalized_dataset = self._normalize_component(dataset, field_name="dataset")
        normalized_run_id = self._normalize_component(run_id, field_name="run_id")
        timestamp = fetched_at or datetime.now(UTC)
        if timestamp.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")

        output_dir = (
            self._root
            / normalized_dataset
            / f"load_date={timestamp.date().isoformat()}"
            / f"run_id={normalized_run_id}"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{normalized_dataset}.json"

        payload = {
            "metadata": {
                "dataset": normalized_dataset,
                "run_id": normalized_run_id,
                "fetched_at": timestamp.astimezone(UTC).isoformat(),
                "record_count": len(records),
                "request_params": request_params,
            },
            "records": records,
        }

        try:
            with output_path.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
        except FileExistsError as exc:
            raise FileExistsError(f"raw artifact already exists: {output_path}") from exc

        return RawArtifact(
            dataset=normalized_dataset,
            path=output_path,
            record_count=len(records),
            run_id=normalized_run_id,
            fetched_at=timestamp,
        )

    @staticmethod
    def _normalize_component(value: str, *, field_name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} cannot be blank")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
        if any(character not in allowed for character in normalized):
            raise ValueError(f"{field_name} contains unsupported path characters: {normalized!r}")
        return normalized
