"""Utilities for searching AnimeUnity's catalog by title.

Unlike the rest of the crawling code, the search endpoint used here
(`/livesearch`) isn't exercised anywhere else in this project, so its exact
request/response shape couldn't be verified against the live site. If
AnimeUnity's search behaves differently than assumed, `search_titles` is the
one place to fix.
"""

from __future__ import annotations

import logging
import re

import requests

from .config import BASE_DOMAIN, prepare_headers

# Best-effort patterns for spotting a season marker in an anime's title, since
# AnimeUnity doesn't expose an explicit season number per episode (each season
# is typically its own separate catalog entry rather than being nested).
_SEASON_PATTERNS = [
    re.compile(r"(\d+)\s*(?:st|nd|rd|th)\s*season", re.IGNORECASE),
    re.compile(r"season\s*(\d+)", re.IGNORECASE),
    re.compile(r"stagione\s*(\d+)", re.IGNORECASE),
    re.compile(r"\bpart\s*(\d+)\b", re.IGNORECASE),
]


def search_titles(query: str, timeout: int = 15) -> list[dict]:
    """Search AnimeUnity's catalog for titles matching `query`.

    Returns the raw list of matching catalog records (each expected to carry
    at least `id`, `slug`, `title`, `type`, and `episodes_count`), or an
    empty list if the request fails or nothing matches.
    """
    url = f"https://{BASE_DOMAIN}/livesearch"
    headers = prepare_headers()
    headers["Content-Type"] = "application/json"

    try:
        response = requests.post(
            url,
            json={"title": query},
            headers=headers,
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
