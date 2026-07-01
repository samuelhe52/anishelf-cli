from __future__ import annotations

from anishelf_cli.models.output import (
    CacheActiveResult,
    CacheMetadataStatusResult,
    CacheScopeResult,
    CacheStatusResult,
    TMDbSearchMatchResult,
)
from anishelf_cli.models.transport.tmdb import TMDbTitleSearchMatch


def test_cache_status_result_model_dump_preserves_public_json_shape() -> None:
    result = CacheStatusResult(
        initialized=True,
        active=CacheActiveResult(
            initialized=True,
            entries=12,
            has_sync_token=True,
            scope=CacheScopeResult(
                container="container",
                environment="production",
                database="private",
                zone="zone",
                user_record_name="_user",
            ),
            metadata=CacheMetadataStatusResult(
                tracked_entries=12,
                hydrated_entries=10,
                missing_entries=2,
                ready=False,
            ),
        ),
        scopes=(
            CacheScopeResult(
                container="container",
                environment="production",
                database="private",
                zone="zone",
                user_record_name="_user",
            ),
        ),
        cache_path="/tmp/cache.sqlite3",
        lock_path="/tmp/cache.lock",
        cache_files=1,
        lock_files=2,
    )

    assert result.model_dump(mode="json") == {
        "summary": {
            "initialized": True,
            "scope_count": 1,
            "cache_files": 1,
            "lock_files": 2,
        },
        "active": {
            "initialized": True,
            "entries": 12,
            "has_sync_token": True,
            "scope": {
                "container": "container",
                "environment": "production",
                "database": "private",
                "zone": "zone",
                "user_record_name": "_user",
            },
            "metadata": {
                "tracked_entries": 12,
                "hydrated_entries": 10,
                "missing_entries": 2,
                "ready": False,
            },
        },
        "scopes": [
            {
                "container": "container",
                "environment": "production",
                "database": "private",
                "zone": "zone",
                "user_record_name": "_user",
            }
        ],
        "cache": {
            "path": "/tmp/cache.sqlite3",
            "lock_path": "/tmp/cache.lock",
        },
    }


def test_tmdb_search_match_result_from_match_avoids_round_trip_revalidation() -> None:
    match = TMDbTitleSearchMatch(
        entry_type="movie",
        tmdb_id=55,
        title="Alien",
        original_title="Alien",
        release_date="1979-05-25",
        original_language_code="en",
        overview="A space horror film.",
        poster_path="/poster.jpg",
        details_url="https://www.themoviedb.org/movie/55",
    )

    result = TMDbSearchMatchResult.from_match(match)

    assert result.model_dump(mode="json") == {
        "entry_type": "movie",
        "tmdb_id": 55,
        "title": "Alien",
        "original_title": "Alien",
        "release_date": "1979-05-25",
        "original_language_code": "en",
        "overview": "A space horror film.",
        "poster_path": "/poster.jpg",
        "details_url": "https://www.themoviedb.org/movie/55",
    }
