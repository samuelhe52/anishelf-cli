from __future__ import annotations

from anishelf_cli.core.coercion import nonempty_string_or_none
from anishelf_cli.library.records import LibraryRecordDecodeError, decode_library_entry_record
from anishelf_cli.models.domain import LibraryEntryModel
from anishelf_cli.models.identity import (
    LibraryIdentity,
    LibraryIdentityError,
    parse_library_identity,
)
from anishelf_cli.models.output import (
    LibraryGetEnvelope,
    LibraryGetItemError,
    LibraryGetItemErrorResult,
    LibraryGetItemFound,
    LibraryGetSummary,
)
from anishelf_cli.models.transport.cloudkit import CloudKitLookupResponse, CloudKitRecord


def library_get_envelope(
    identities: list[str],
    lookup_payload: CloudKitLookupResponse | None,
) -> LibraryGetEnvelope:
    parsed_identities: dict[str, LibraryIdentity] = {}
    items: list[LibraryGetItemErrorResult | LibraryGetItemFound | None] = []

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

    lookup_results = lookup_payload.results_by_record_name() if lookup_payload is not None else {}
    for index, raw_identity in enumerate(identities):
        if items[index] is not None:
            continue
        if raw_identity not in parsed_identities:
            continue

        result = lookup_results.get(raw_identity)
        if result is None:
            items[index] = _error_item(raw_identity, "not_found", "Library entry not found.")
            continue
        if code := nonempty_string_or_none(result.server_error_code):
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

        items[index] = LibraryGetItemFound(identity=raw_identity, entry=entry)

    completed_items = [item for item in items if item is not None]
    return LibraryGetEnvelope(
        items=tuple(completed_items),
        summary=_summary(identities, completed_items),
    )


def library_get_cache_envelope(
    identities: list[str],
    cached_entries: dict[str, LibraryEntryModel],
) -> LibraryGetEnvelope:
    items: list[LibraryGetItemErrorResult | LibraryGetItemFound] = []
    for raw_identity in identities:
        try:
            parse_library_identity(raw_identity)
        except LibraryIdentityError as exc:
            items.append(_error_item(raw_identity, "invalid_identity", str(exc)))
            continue

        entry = cached_entries.get(raw_identity)
        if entry is None:
            items.append(_error_item(raw_identity, "not_found", "Library entry not found."))
        else:
            items.append(LibraryGetItemFound(identity=raw_identity, entry=entry))

    return LibraryGetEnvelope(items=tuple(items), summary=_summary(identities, items))


def valid_lookup_record_names(identities: list[str]) -> list[str]:
    valid: list[str] = []
    for identity in identities:
        try:
            parse_library_identity(identity)
        except LibraryIdentityError:
            continue
        valid.append(identity)
    return valid


def has_any_found_item(envelope: LibraryGetEnvelope) -> bool:
    return any(item.status == "found" for item in envelope.items)


def _error_item(identity: str, code: str, message: str) -> LibraryGetItemErrorResult:
    return LibraryGetItemErrorResult(
        identity=identity,
        error=LibraryGetItemError(code=code, message=message),
    )


def _summary(
    identities: list[str],
    items: list[LibraryGetItemErrorResult | LibraryGetItemFound],
) -> LibraryGetSummary:
    return LibraryGetSummary(
        requested=len(identities),
        found=sum(1 for item in items if item.status == "found"),
        errors=sum(1 for item in items if item.status == "error"),
    )


def _item_error_code(server_error_code: str) -> str:
    normalized = server_error_code.lower().replace("-", "_")
    if normalized in {"not_found", "unknown_item"}:
        return "not_found"
    return normalized


def _cloudkit_item_error_message(result: CloudKitRecord) -> str:
    if reason := nonempty_string_or_none(result.reason):
        return reason
    if code := nonempty_string_or_none(result.server_error_code):
        return code
    return "CloudKit returned an item-level error."
