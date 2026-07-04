from __future__ import annotations

import httpx
import pytest

from anishelf_cli.models import MetadataDepth
from anishelf_cli.models.tmdb import TMDbTitleSearchQuery
from anishelf_cli.models.transport.tmdb import (
    TMDbMovieSummaryResponse,
    TMDbSearchResponse,
    TMDbSeasonSummaryResponse,
    TMDbSeriesSummaryResponse,
)
from anishelf_cli.tmdb.client import TMDbClient, TMDbRequestError, TMDbSummaryIdentity


def test_tmdb_transport_keeps_only_likely_reused_nested_structures_typed() -> None:
    movie = TMDbMovieSummaryResponse.model_validate(
        {
            "id": 55,
            "title": "Alien",
            "belongs_to_collection": {"id": 10, "name": "Alien Collection"},
            "production_companies": [{"id": 1, "name": "Brandywine"}],
            "spoken_languages": [{"english_name": "English", "iso_639_1": "en"}],
        }
    )
    series = TMDbSeriesSummaryResponse.model_validate(
        {
            "id": 22,
            "name": "Alien Nation",
            "last_episode_to_air": {"id": 7, "name": "Finale"},
            "networks": [{"id": 2, "name": "FOX"}],
            "seasons": [{"id": 33, "season_number": 1}],
        }
    )
    season = TMDbSeasonSummaryResponse.model_validate(
        {
            "id": 33,
            "name": "Season 1",
            "episodes": [{"id": 1, "episode_number": 1}],
        }
    )

    assert movie.belongs_to_collection == {"id": 10, "name": "Alien Collection"}
    assert movie.production_companies == ({"id": 1, "name": "Brandywine"},)
    assert movie.spoken_languages == ({"english_name": "English", "iso_639_1": "en"},)

    assert series.last_episode_to_air == {"id": 7, "name": "Finale"}
    assert series.networks == ({"id": 2, "name": "FOX"},)
    assert series.seasons[0].season_number == 1

    assert season.episodes[0].episode_number == 1


def test_tmdb_transport_ignores_additive_tmdb_fields() -> None:
    search = TMDbSearchResponse.model_validate(
        {
            "results": [
                {
                    "id": 55,
                    "title": "Alien",
                    "softcore": False,
                }
            ]
        }
    )
    movie = TMDbMovieSummaryResponse.model_validate(
        {
            "id": 55,
            "title": "Alien",
            "softcore": False,
        }
    )

    assert search.results[0].id == 55
    assert movie.id == 55


def test_tmdb_client_uses_per_request_api_key_and_summary_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/translations"):
            return httpx.Response(
                200,
                json={
                    "translations": [
                        {
                            "iso_639_1": "ja",
                            "iso_3166_1": "JP",
                            "data": {
                                "title": "エイリアン",
                                "overview": "宇宙ホラー映画。",
                            },
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "id": 55,
                "title": "Alien",
                "original_title": "Alien",
                "overview": "A space horror film.",
                "release_date": "1979-05-25",
                "poster_path": "/poster.jpg",
                "backdrop_path": "/backdrop.jpg",
                "original_language": "en",
                "homepage": "https://example.com/alien",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tmdb = TMDbClient("tmdb-secret-token", client=client)

    summary = tmdb.fetch_summary(TMDbSummaryIdentity(entry_type="movie", tmdb_id=55))

    assert summary.name == "Alien"
    assert summary.language == "en-US"
    assert summary.name_translation_map == {}
    assert summary.overview_translation_map == {}
    assert summary.link_to_details == "https://example.com/alien"
    assert summary.source_version == "tmdb.http.metadata.v1"
    assert len(requests) == 1
    assert requests[0].url.path == "/3/movie/55"
    assert requests[0].url.params["api_key"] == "tmdb-secret-token"
    assert requests[0].url.params["language"] == "en-US"


def test_tmdb_client_fetches_details_translations_for_search_cache() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/translations"):
            return httpx.Response(
                200,
                json={
                    "translations": [
                        {
                            "iso_639_1": "ja",
                            "iso_3166_1": "JP",
                            "data": {
                                "title": "エイリアン",
                                "overview": "宇宙ホラー映画。",
                            },
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "id": 55,
                "title": "Alien",
                "original_title": "Alien",
                "overview": "A space horror film.",
                "release_date": "1979-05-25",
                "poster_path": "/poster.jpg",
                "backdrop_path": "/backdrop.jpg",
                "original_language": "en",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tmdb = TMDbClient("tmdb-secret-token", client=client)

    metadata = tmdb.fetch_metadata(
        TMDbSummaryIdentity(entry_type="movie", tmdb_id=55),
        MetadataDepth.DETAILS,
    )

    assert metadata.name_translation_map == {"ja-JP": "エイリアン"}
    assert metadata.overview_translation_map == {"ja-JP": "宇宙ホラー映画。"}
    assert [request.url.path for request in requests] == [
        "/3/movie/55",
        "/3/movie/55/translations",
    ]


def test_tmdb_client_fetches_series_and_season_summary_counts() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/translations"):
            return httpx.Response(200, json={"translations": []})
        if request.url.path == "/3/tv/22":
            return httpx.Response(
                200,
                json={
                    "id": 22,
                    "name": "Alien Nation",
                    "original_name": "Alien Nation",
                    "overview": "A sci-fi police series.",
                    "first_air_date": "1989-09-18",
                    "poster_path": "/series.jpg",
                    "backdrop_path": "/series-backdrop.jpg",
                    "original_language": "en",
                    "homepage": "https://example.com/alien-nation",
                },
            )
        return httpx.Response(
            200,
            json={
                "id": 33,
                "name": "Season 1",
                "overview": "The first season.",
                "air_date": "1989-09-18",
                "poster_path": "/season.jpg",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tmdb = TMDbClient("tmdb-secret-token", client=client)

    series_summary = tmdb.fetch_summary(TMDbSummaryIdentity(entry_type="series", tmdb_id=22))
    season_summary = tmdb.fetch_summary(
        TMDbSummaryIdentity(entry_type="season", tmdb_id=33, parent_series_id=22, season_number=1)
    )

    assert series_summary.language == "en-US"
    assert series_summary.link_to_details == "https://example.com/alien-nation"
    assert season_summary.original_language_code == "en"
    assert season_summary.link_to_details == "https://example.com/alien-nation"
    assert [request.url.path for request in requests] == [
        "/3/tv/22",
        "/3/tv/22",
        "/3/tv/22/season/1",
    ]


def test_tmdb_client_full_metadata_fetches_translations_and_series_episode_summaries() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/translations"):
            return httpx.Response(
                200,
                json={
                    "translations": [
                        {
                            "iso_639_1": "ja",
                            "iso_3166_1": "JP",
                            "data": {"name": "カウボーイビバップ", "overview": "宇宙の賞金稼ぎ。"},
                        }
                    ]
                },
            )
        if request.url.path == "/3/tv/22":
            return httpx.Response(
                200,
                json={
                    "id": 22,
                    "name": "Cowboy Bebop",
                    "overview": "Bounty hunters in space.",
                    "first_air_date": "1998-04-03",
                    "number_of_seasons": 1,
                    "number_of_episodes": 26,
                    "seasons": [
                        {
                            "name": "Season 1",
                            "season_number": 1,
                            "episode_count": 26,
                            "air_date": "1998-04-03",
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "id": 100,
                "name": "Season 1",
                "episodes": [
                    {
                        "episode_number": 1,
                        "season_number": 1,
                        "name": "Asteroid Blues",
                        "air_date": "1998-04-03",
                        "still_path": "/still.jpg",
                    }
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tmdb = TMDbClient("tmdb-secret-token", client=client)

    metadata = tmdb.fetch_metadata(
        TMDbSummaryIdentity(entry_type="series", tmdb_id=22),
        MetadataDepth.FULL,
    )

    assert metadata.name_translation_map == {"ja-JP": "カウボーイビバップ"}
    assert len(metadata.season_summaries) == 1
    assert metadata.season_summaries[0].episode_count == 26
    assert len(metadata.episode_summaries) == 1
    assert metadata.episode_summaries[0].name == "Asteroid Blues"
    assert [request.url.path for request in requests] == [
        "/3/tv/22",
        "/3/tv/22/season/1",
        "/3/tv/22/translations",
    ]


def test_tmdb_client_searches_movie_and_tv_titles() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/3/search/movie":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 55,
                            "title": "Alien",
                            "original_title": "Alien",
                            "release_date": "1979-05-25",
                            "original_language": "en",
                            "overview": "A space horror film.",
                            "poster_path": "/poster.jpg",
                            "genre_ids": [16, 878],
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 22,
                        "name": "Alien Nation",
                        "original_name": "Alien Nation",
                        "first_air_date": "1989-09-18",
                        "original_language": "en",
                        "overview": "A sci-fi police series.",
                        "poster_path": "/series.jpg",
                        "genre_ids": [16, 18],
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tmdb = TMDbClient("tmdb-secret-token", client=client)

    result = tmdb.search_titles(TMDbTitleSearchQuery(title="Alien", year=1979))

    assert len(result.movies) == 1
    assert result.movies[0].entry_type == "movie"
    assert result.movies[0].tmdb_id == 55
    assert result.movies[0].title == "Alien"
    assert result.movies[0].release_date == "1979-05-25"
    assert result.movies[0].details_url == "https://www.themoviedb.org/movie/55"
    assert len(result.series) == 1
    assert result.series[0].entry_type == "series"
    assert result.series[0].tmdb_id == 22
    assert result.series[0].title == "Alien Nation"
    assert result.series[0].release_date == "1989-09-18"
    assert result.series[0].details_url == "https://www.themoviedb.org/tv/22"
    assert [request.url.path for request in requests] == ["/3/search/movie", "/3/search/tv"]
    assert all(request.url.params["api_key"] == "tmdb-secret-token" for request in requests)
    assert all(request.url.params["query"] == "Alien" for request in requests)
    assert requests[0].url.params["primary_release_year"] == "1979"
    assert requests[1].url.params["first_air_date_year"] == "1979"


def test_tmdb_client_search_title_preserves_legacy_id_sets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/movie":
            return httpx.Response(
                200,
                json={"results": [{"id": 55, "genre_ids": [16]}, {"id": 55, "genre_ids": [16]}]},
            )
        return httpx.Response(
            200,
            json={"results": [{"id": 22, "genre_ids": [16]}, {"id": 99, "genre_ids": [16]}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tmdb = TMDbClient("tmdb-secret-token", client=client)

    result = tmdb.search_title("Alien")

    assert result.movie_ids == {55}
    assert result.series_ids == {22, 99}
    assert [match.tmdb_id for match in result.movies] == [55, 55]
    assert [match.tmdb_id for match in result.series] == [22, 99]


def test_tmdb_client_discovers_without_title_and_respects_entry_type_filter() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 55,
                        "title": "Alien",
                        "original_title": "Alien",
                        "release_date": "1979-05-25",
                        "original_language": "en",
                        "overview": "A space horror film.",
                        "poster_path": "/poster.jpg",
                        "genre_ids": [16, 878],
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tmdb = TMDbClient("tmdb-secret-token", client=client)

    result = tmdb.search_titles(TMDbTitleSearchQuery(year=1979, entry_type="movie"))

    assert len(result.movies) == 1
    assert result.movies[0].tmdb_id == 55
    assert result.series == ()
    assert [request.url.path for request in requests] == ["/3/discover/movie"]
    assert requests[0].url.params["api_key"] == "tmdb-secret-token"
    assert requests[0].url.params["primary_release_year"] == "1979"
    assert requests[0].url.params["sort_by"] == "popularity.desc"
    assert requests[0].url.params["with_genres"] == "16"


def test_tmdb_client_filters_non_animation_title_search_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/movie":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": 55, "title": "Alien", "genre_ids": [878]},
                        {"id": 66, "title": "Spirited Away", "genre_ids": [16, 14]},
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {"id": 22, "name": "Alien Nation", "genre_ids": [18]},
                    {"id": 44, "name": "Cowboy Bebop", "genre_ids": [16, 10765]},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tmdb = TMDbClient("tmdb-secret-token", client=client)

    result = tmdb.search_titles(TMDbTitleSearchQuery(title="Anime"))

    assert [match.tmdb_id for match in result.movies] == [66]
    assert [match.tmdb_id for match in result.series] == [44]


def test_tmdb_client_fails_whole_search_when_one_all_type_endpoint_fails() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/3/search/movie":
            return httpx.Response(200, json={"results": [{"id": 55}]})
        return httpx.Response(500, json={"status_message": "server error"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tmdb = TMDbClient("tmdb-secret-token", client=client, max_attempts=1)

    with pytest.raises(TMDbRequestError, match=r"TMDb title search failed\."):
        tmdb.search_titles(TMDbTitleSearchQuery(title="Alien", entry_type="all"))

    assert [request.url.path for request in requests] == ["/3/search/movie", "/3/search/tv"]


def test_tmdb_client_preserves_validation_error_details_for_search_responses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"id": "bad"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tmdb = TMDbClient("tmdb-secret-token", client=client)

    with pytest.raises(
        TMDbRequestError,
        match=r"TMDb response had an unexpected shape: results\.0\.id:",
    ):
        tmdb.search_titles(TMDbTitleSearchQuery(title="Alien"))
