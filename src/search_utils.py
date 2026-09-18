"""Utilities for searching AnimeUnity's catalog by title.

Unlike the rest of the crawling code, the search endpoint used here
(`/livesearch`) isn't exercised anywhere else in this project, so its exact
request/response shape couldn't be verified against the live site. If
AnimeUnity's search behaves differently than assumed, `search_titles` is the
one place to fix.

`/livesearch` is a POST endpoint on a Laravel app, which rejects a POST
without a valid CSRF token for that session (HTTP 419 "Page Expired"). So a
plain stateless POST fails; `_get_csrf_token` first visits the homepage with
a `requests.Session` to pick up a session cookie and a CSRF token (either the
`XSRF-TOKEN` cookie Laravel/axios-based frontends use, or a `<meta
name="csrf-token">` tag), then `search_titles` reuses that same session so
the cookie and token travel together on the POST.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

from .config import BASE_DOMAIN, BASE_HEADERS, DEFAULT_HEADERS, prepare_headers

# Best-effort patterns for spotting a season marker in an anime's title, since
# AnimeUnity doesn't expose an explicit season number per episode (each season
# is typically its own separate catalog entry rather than being nested).
_SEASON_PATTERNS = [
    re.compile(r"(\d+)\s*(?:st|nd|rd|th)\s*season", re.IGNORECASE),
    re.compile(r"season\s*(\d+)", re.IGNORECASE),
    re.compile(r"stagione\s*(\d+)", re.IGNORECASE),
    re.compile(r"\bpart\s*(\d+)\b", re.IGNORECASE),
]


def _get_csrf_token(
    session: requests.Session,
    headers: dict,
    timeout: int,
) -> tuple[str, str] | None:
    """Visit the homepage to establish a session and grab a CSRF token.

    Returns the (header name, token value) to send on the follow-up POST, or
    None if no token could be found. The header name depends on where the
    token came from: a `XSRF-TOKEN` cookie is echoed back as `X-XSRF-TOKEN`
    (Laravel decrypts it), a `<meta name="csrf-token">` tag as `X-CSRF-TOKEN`
    (compared as plaintext) - sending either under the wrong header fails.
    """
    try:
        response = session.get(
            f"https://{BASE_DOMAIN}/",
            headers=headers,
            timeout=timeout,
            verify=False,  # noqa: S501
        )
        response.raise_for_status()

    except requests.RequestException:
        logging.exception("Failed to load the AnimeUnity homepage for a CSRF token")
        return None

    xsrf_cookie = session.cookies.get("XSRF-TOKEN")
    if xsrf_cookie:
        return ("X-XSRF-TOKEN", unquote(xsrf_cookie))

    soup = BeautifulSoup(response.text, "html.parser")
    meta_tag = soup.find("meta", attrs={"name": "csrf-token"})
    if meta_tag and meta_tag.get("content"):
        return ("X-CSRF-TOKEN", meta_tag["content"])

    return None


def search_titles(query: str, timeout: int = 15) -> list[dict]:
    """Search AnimeUnity's catalog for titles matching `query`.

    Returns the raw list of matching catalog records (each expected to carry
    at least `id`, `slug`, `title`, `type`, and `episodes_count`), or an
    empty list if the request fails or nothing matches.
    """
    session = requests.Session()
    page_headers = prepare_headers()
    page_headers.update(DEFAULT_HEADERS)
    csrf = _get_csrf_token(session, page_headers, timeout)

    search_headers = prepare_headers()
    search_headers.update(BASE_HEADERS)
    search_headers["Content-Type"] = "application/json"
    search_headers["X-Requested-With"] = "XMLHttpRequest"
    search_headers["Referer"] = f"https://{BASE_DOMAIN}/"
    if csrf:
        header_name, token_value = csrf
        search_headers[header_name] = token_value

    try:
        response = session.post(
            f"https://{BASE_DOMAIN}/livesearch",
            json={"title": query},
            headers=search_headers,
            timeout=timeout,
            verify=False,  # noqa: S501
        )
        response.raise_for_status()
        payload = response.json()

    except requests.RequestException:
        logging.exception("AnimeUnity search request failed for %r", query)
        return []

    return payload.get("records", [])


def build_anime_url(record: dict) -> str:
    """Build an AnimeUnity anime-page URL from a search result record."""
    anime_id = record["id"]
    slug = record.get("slug") or record.get("title", "")
    return f"https://{BASE_DOMAIN}/anime/{anime_id}-{slug}"


def guess_season_number(title: str) -> int:
    """Best-effort guess of a season number from an anime title.

    Looks for common "season" markers (e.g. "2nd Season", "Stagione 2",
    "Part 2") and falls back to 1 when none match.
    """
    for pattern in _SEASON_PATTERNS:
        match = pattern.search(title)
        if match:
            return int(match.group(1))

    return 1
