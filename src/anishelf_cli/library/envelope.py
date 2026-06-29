from __future__ import annotations

from typing import Any

from anishelf_cli.library.identity import (
    LibraryIdentity,
    LibraryIdentityError,
    parse_library_identity,
)
from anishelf_cli.library.records import LibraryRecordDecodeError, decode_library_entry_record


def library_get_envelope(
    identities: list[str],
    lookup_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    parsed_identities: dict[str, LibraryIdentity] = {}
    items: list[dict[str, Any] | None] = []

    for raw_identity in identities:
        try:
            parsed = parse_library_identity(raw_identity)
        except LibraryIdentityError as exc:
            items.append(
                _error_item(raw_identity, "invalid_identity", str(exc)),
            )
        else:
            parsed_identities[raw_identity] = parsed
            items.append(None)

    lookup_results = _lookup_results_by_record_name(lookup_payload or {})
    for index, raw_identity in enumerate(identities):
        if items[index] is not None:
            continue
        if raw_identity not in parsed_identities:
            continue

        result = lookup_results.get(raw_identity)
        if result is None:
            items[index] = _error_item(raw_identity, "not_found", "Library entry not found.")
            continue
        if code := _optional_string(result.get("serverErrorCode")):
            items[index] = _error_item(
                raw_identity,
                _item_error_code(code),
                _cloudkit_item_error_message(result),
            )
            continue
        try:
            entry = decode_library_entry_record(result)
        except LibraryRecordDecodeError as exc:
            items[index] = _error_item(raw_identity, "decode_error", str(exc))
            continue

        items[index] = {"identity": raw_identity, "status": "found", "entry": entry}

    completed_items = [item for item in items if item is not None]
    return {
        "items": completed_items,
        "summary": {
            "requested": len(identities),
            "found": sum(1 for item in completed_items if item["status"] == "found"),
            "errors": sum(1 for item in completed_items if item["status"] == "error"),
        },
    }


def valid_lookup_record_names(identities: list[str]) -> list[str]:
    valid: list[str] = []
    for identity in identities:
        try:
            parse_library_identity(identity)
        except LibraryIdentityError:
            continue
        valid.append(identity)
    return valid


def has_any_found_item(envelope: dict[str, Any]) -> bool:
    items = envelope.get("items")
    return isinstance(items, list) and any(
        isinstance(item, dict) and item.get("status") == "found" for item in items
    )


def _lookup_results_by_record_name(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = payload.get("records")
    if not isinstance(records, list):
        return {}

    results: dict[str, dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        record_name = _record_name_or_none(item)
        if record_name:
            results[record_name] = item
    return results


def _error_item(identity: str, code: str, message: str) -> dict[str, Any]:
    return {
        "identity": identity,
        "status": "error",
        "error": {
            "code": code,
            "message": message,
        },
    }


def _item_error_code(server_error_code: str) -> str:
    normalized = server_error_code.lower().replace("-", "_")
    if normalized in {"not_found", "unknown_item"}:
        return "not_found"
    return normalized


def _cloudkit_item_error_message(result: dict[str, Any]) -> str:
    if reason := _optional_string(result.get("reason")):
        return reason
    if code := _optional_string(result.get("serverErrorCode")):
        return code
    return "CloudKit returned an item-level error."


def _record_name_or_none(record: dict[str, Any]) -> str | None:
    if record_name := _optional_string(record.get("recordName")):
        return record_name
    record_id = record.get("recordID")
    if isinstance(record_id, dict):
        return _optional_string(record_id.get("recordName"))
    return None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
