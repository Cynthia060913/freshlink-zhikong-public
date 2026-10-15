"""Pure A reception projection for C quality; no database, MQTT, or public API."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from app.modules.telemetry.quality import process_batch


class ReceptionLike(Protocol):
    """Trusted fields supplied by A's authenticated reception boundary."""

    raw_text: str
    authenticated_device_id: UUID
    warehouse_id: UUID
    received_at: datetime
    reception_id: UUID

    @property
    def raw_payload(self) -> dict[str, Any]: ...


def _trusted_uuid(value: object, field: str) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError(f"{field} must be a trusted UUID")
    return value


def _raw_matches_uuid(value: object, expected: UUID) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return UUID(value) == expected
    except ValueError:
        return False


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("raw_text has duplicate JSON members")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> Any:
    raise ValueError("raw_text contains a non-JSON constant")


def prepare_receptions(
    receptions: list[ReceptionLike], threshold_c: float | None = None
) -> list[dict[str, Any]]:
    """Preserve raw evidence and classify a separate trusted processing view.

    Identity/JSON rejection belongs to A. The optional threshold is a fixed
    fixture value, not a historical B rule. Duplicate state is batch-local.
    """
    if not isinstance(receptions, list):
        raise ValueError("receptions must be a list")
    evidence: list[dict[str, Any]] = []
    views: list[object] = []
    for reception in receptions:
        device_id = _trusted_uuid(
            reception.authenticated_device_id, "authenticated_device_id"
        )
        warehouse_id = _trusted_uuid(reception.warehouse_id, "warehouse_id")
        reception_id = _trusted_uuid(reception.reception_id, "reception_id")
        received_at = reception.received_at
        if (
            not isinstance(received_at, datetime)
            or received_at.tzinfo is None
            or received_at.utcoffset() is None
        ):
            raise ValueError("received_at must be a trusted aware timestamp")
        if not isinstance(reception.raw_text, str):
            raise ValueError("raw_text must be text")
        try:
            parsed_raw = json.loads(
                reception.raw_text,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
        except ValueError:
            raise ValueError("raw_text must be a valid JSON object") from None
        except RecursionError:
            raise ValueError("raw_text must be a valid JSON object") from None
        if not isinstance(parsed_raw, dict):
            raise ValueError("raw_text must be a JSON object")
        raw_payload = deepcopy(reception.raw_payload)
        if not isinstance(raw_payload, dict):
            raise ValueError("raw_payload must be an object")
        try:
            parsed_canonical = json.dumps(
                parsed_raw, sort_keys=True, ensure_ascii=True, allow_nan=False
            )
            payload_canonical = json.dumps(
                raw_payload, sort_keys=True, ensure_ascii=True, allow_nan=False
            )
        except TypeError:
            raise ValueError("raw_payload must contain JSON values") from None
        except ValueError:
            raise ValueError("raw_payload must contain JSON values") from None
        if parsed_canonical != payload_canonical:
            raise ValueError("raw_text and raw_payload differ")
        if not _raw_matches_uuid(raw_payload.get("device_id"), device_id):
            raise ValueError("raw device_id differs from trusted device")
        if "warehouse_id" in raw_payload and not _raw_matches_uuid(
            raw_payload["warehouse_id"], warehouse_id
        ):
            raise ValueError("raw warehouse_id differs from trusted warehouse")

        trusted_time = received_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        view = deepcopy(raw_payload)
        view["warehouse_id"] = str(warehouse_id)
        view["received_at"] = trusted_time
        views.append(view)
        evidence.append(
            {
                "raw_text": reception.raw_text,
                "raw_payload": raw_payload,
                "trusted": {
                    "authenticated_device_id": str(device_id),
                    "warehouse_id": str(warehouse_id),
                    "received_at": trusted_time,
                    "reception_id": str(reception_id),
                },
            }
        )
    classified = process_batch(views, threshold_c)
    for snapshot, result in zip(evidence, classified, strict=True):
        snapshot.update(
            {
                "processing_view": result["raw"],
                "quality": result["quality"],
                "batch_duplicate": result["duplicate"],
                "normalized": result["normalized"],
                "risk": result["risk"],
            }
        )
    return evidence
