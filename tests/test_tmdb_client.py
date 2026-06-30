from __future__ import annotations

import httpx

from anishelf_cli.tmdb.client import TMDbClient, TMDbSummaryIdentity


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
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tmdb = TMDbClient("tmdb-secret-token", client=client)

    summary = tmdb.fetch_summary(TMDbSummaryIdentity(entry_type="movie", tmdb_id=55))

    assert summary["name"] == "Alien"
    assert summary["link_to_details"] == "https://www.themoviedb.org/movie/55"
    assert len(requests) == 1
    assert requests[0].url.path == "/3/movie/55"
    assert requests[0].url.params["api_key"] == "tmdb-secret-token"


def test_tmdb_client_searches_movie_and_tv_titles() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/3/search/movie":
            return httpx.Response(200, json={"results": [{"id": 55}]})
        return httpx.Response(200, json={"results": [{"id": 22}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tmdb = TMDbClient("tmdb-secret-token", client=client)

    result = tmdb.search_title("Alien")

    assert result.movie_ids == {55}
    assert result.series_ids == {22}
    assert [request.url.path for request in requests] == ["/3/search/movie", "/3/search/tv"]
    assert all(request.url.params["api_key"] == "tmdb-secret-token" for request in requests)
    assert all(request.url.params["query"] == "Alien" for request in requests)
