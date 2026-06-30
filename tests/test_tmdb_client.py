from __future__ import annotations

import httpx
import pytest

from anishelf_cli.tmdb.client import (
    TMDbClient,
    TMDbRequestError,
    TMDbSummaryIdentity,
    TMDbTitleSearchQuery,
)


def test_tmdb_client_uses_per_request_api_key_and_summary_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
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
                "status": "Released",
                "genres": [
                    {"id": 878, "name": "Science Fiction"},
                    {"id": 27, "name": "Horror"},
                ],
                "runtime": 117,
                "vote_average": 8.2,
                "vote_count": 15432,
                "popularity": 44.5,
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tmdb = TMDbClient("tmdb-secret-token", client=client)

    summary = tmdb.fetch_summary(TMDbSummaryIdentity(entry_type="movie", tmdb_id=55))

    assert summary["name"] == "Alien"
    assert summary["link_to_details"] == "https://www.themoviedb.org/movie/55"
    assert summary["status"] == "Released"
    assert summary["genres"] == [
        {"id": 878, "name": "Science Fiction"},
        {"id": 27, "name": "Horror"},
    ]
    assert summary["runtime_minutes"] == 117
    assert summary["season_count"] is None
    assert summary["episode_count"] is None
    assert summary["vote_average"] == 8.2
    assert summary["vote_count"] == 15432
    assert summary["popularity"] == 44.5
    assert summary["source_version"] == "tmdb.http.summary.v2"
    assert len(requests) == 1
    assert requests[0].url.path == "/3/movie/55"
    assert requests[0].url.params["api_key"] == "tmdb-secret-token"


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
            return httpx.Response(200, json={"results": [{"id": 55}, {"id": 55}]})
        return httpx.Response(200, json={"results": [{"id": 22}, {"id": 99}]})

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
