"""Module to download anime episodes from a given AnimeUnity URL.

It extracts the anime ID, formats the anime name, retrieves episode URLs, and
downloads episodes concurrently.

Usage:
    - Run the script with the URL of the anime page as a command-line argument.
    - It will create a directory structure in the 'Downloads' folder based on
      the anime name where each episode will be downloaded.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

import requests
from rich.live import Live

from src.config import (
    CRAWLER_WORKERS,
    DOWNLOAD_WORKERS,
    parse_arguments,
    prepare_headers,
)
from src.crawler.crawler import Crawler
from src.crawler.crawler_utils import extract_download_link
from src.download_utils import (
    get_episode_filename,
    run_in_parallel,
    save_file_with_progress,
)
from src.file_utils import (
    create_download_directory,
    create_search_output_directory,
    sanitize_directory_name,
)
from src.general_utils import clear_terminal, fetch_page, fetch_page_httpx
from src.progress_utils import create_progress_bar, create_progress_table
from src.search_utils import build_anime_url, guess_season_number, search_titles


class EpisodeFilters(NamedTuple):
    """Episode selection filters shared by the download and check flows."""

    start_episode: int | None = None
    end_episode: int | None = None
    episodes: list[int] | None = None


def download_episode(
    download_link: str | None,
    download_path: str,
    task_info: tuple,
    retries: int = 4,
) -> bool:
    """Download an episode from the download link and provides progress updates.

    Returns whether the download succeeded. If `download_link` couldn't be
    resolved, this returns immediately without retrying, since retrying an
    unresolvable link can only ever fail again.
    """
    if download_link is None:
        return False

    for attempt in range(retries):
        try:
            headers = prepare_headers()
            response = requests.get(
                download_link,
                stream=True,
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()

        except requests.RequestException:
            if attempt < retries - 1:
                delay = 10 * (attempt + 1) + random.uniform(1, 2)  # noqa: S311
                time.sleep(delay)

        else:
            filename = get_episode_filename(download_link)
            final_path = Path(download_path) / filename
            save_file_with_progress(response, final_path, task_info)
            return True

    return False


def resolve_download_link(video_url: str | None) -> str | None:
    """Resolve a video URL's direct download link, without downloading it."""
    if video_url is None:
        return None

    soup = fetch_page(video_url)
    if soup is None:
        return None

    script_items = soup.find_all("script")
    return extract_download_link(script_items, video_url)


def process_video_url(video_url: str, download_path: str, task_info: tuple) -> bool:
    """Process an embed URL to extract episode download links."""
    download_link = resolve_download_link(video_url)
    return download_episode(download_link, download_path, task_info)


def download_anime(
    anime_name: str,
    video_urls: list[str],
    download_path: str,
    workers: int = DOWNLOAD_WORKERS,
) -> None:
    """Download episodes of a specified anime from provided video URLs."""
    job_progress = create_progress_bar()
    progress_table = create_progress_table(anime_name, job_progress)

    with Live(progress_table, refresh_per_second=10):
        failures = run_in_parallel(
            process_video_url,
            video_urls,
            job_progress,
            download_path,
            workers=workers,
        )

    if failures:
        Console().print(f"[red]{failures} episode(s) failed to download.[/red]")


def print_check_report(episode_links: list[tuple[str, str | None]]) -> None:
    """Print every resolved download link, one per line."""
    for _, link in episode_links:
        print(link or "")


def _resolve_episode_link(
    episode_video_url: tuple[str, str | None],
) -> tuple[str, str | None]:
    """Resolve a single (episode number, video URL) pair to its download link."""
    number, video_url = episode_video_url
    return number, resolve_download_link(video_url)


async def check_anime_download(
    url: str,
    filters: EpisodeFilters = EpisodeFilters(),
) -> None:
    """Validate a URL and print every resolved download link, one per line.

    Unlike `process_anime_download`, this never downloads any file, so it's
    safe to use as a preview, but it does resolve each matching episode's
    embed and video pages to find its direct download link.
    """
    fetch_page_httpx(url)
    crawler = Crawler(
        url=url,
        start_episode=filters.start_episode,
        end_episode=filters.end_episode,
        episodes=filters.episodes,
    )
    episode_video_urls = await crawler.collect_episode_video_urls()

    with ThreadPoolExecutor(max_workers=CRAWLER_WORKERS) as executor:
        episode_links = list(executor.map(_resolve_episode_link, episode_video_urls))

    print_check_report(episode_links)


def _is_movie(record: dict, num_episodes: int) -> bool:
    """Best-effort movie/series detection from a search record and episode count."""
    record_type = (record.get("type") or "").strip().lower()
    if record_type:
        return record_type == "movie"

    return num_episodes == 1


def write_movie_file(title: str, output_dir: str) -> Path:
    """Write a file containing only the movie's title."""
    final_path = Path(output_dir) / f"{sanitize_directory_name(title)}.txt"
    final_path.write_text(title, encoding="utf-8")
    return final_path


def write_series_file(
    title: str,
    season: int,
    episode_records: list[tuple[str, str, str | None]],
    output_dir: str,
) -> Path:
    """Write a CSV file listing every episode: season, episode, title, link."""
    final_path = Path(output_dir) / f"{sanitize_directory_name(title)}.csv"

    with final_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(
            ["numero stagione", "numero episodio", "titolo episodio", "link file"],
        )
        for number, episode_title, link in episode_records:
            writer.writerow([season, number, episode_title, link or ""])

    return final_path


def _resolve_episode_record(
    episode: tuple[str, str, str | None],
) -> tuple[str, str, str | None]:
    """Resolve a single (number, title, video URL) triple to its download link."""
    number, episode_title, video_url = episode
    return number, episode_title, resolve_download_link(video_url)


async def export_search_result(record: dict, output_dir: str) -> None:
    """Resolve one search result's episodes/links and write them to a file."""
    title = record.get("title") or record.get("title_eng") or str(record.get("id"))
    url = build_anime_url(record)
    crawler = Crawler(url=url, start_episode=None, end_episode=None, episodes=None)

    if _is_movie(record, crawler.num_episodes):
        write_movie_file(title, output_dir)
        return

    episode_records = await crawler.collect_episode_records()

    with ThreadPoolExecutor(max_workers=CRAWLER_WORKERS) as executor:
        resolved_records = list(executor.map(_resolve_episode_record, episode_records))

    season = guess_season_number(title)
    write_series_file(title, season, resolved_records, output_dir)


async def search_and_export(query: str, custom_path: str | None = None) -> None:
    """Search AnimeUnity for `query` and export every match's links to a file.

    Each match becomes its own file in the output directory: just the title
    for a movie, or a CSV of every episode's season/number/title/link for a
    series. One bad result doesn't stop the rest from being exported.
    """
    console = Console()
    results = search_titles(query)

    if not results:
        console.print(f"[yellow]No results found for '{query}'.[/yellow]")
        return

    output_dir = create_search_output_directory(custom_path=custom_path)
    exported = 0
    for record in results:
        try:
            await export_search_result(record, output_dir)
            exported += 1

        except Exception:  # pylint: disable=broad-exception-caught
            # One bad result must never abort exporting the rest.
            logging.exception("Failed to export search result: %r", record)

    console.print(
        f"[green]Exported {exported}/{len(results)} result(s) to {output_dir}[/green]",
    )


async def process_anime_download(
    url: str,
    filters: EpisodeFilters = EpisodeFilters(),
    *,
    custom_path: str | None = None,
    workers: int = DOWNLOAD_WORKERS,
) -> None:
    """Process the download of an anime from the specified URL."""
    soup = fetch_page_httpx(url)
    crawler = Crawler(
        url=url,
        start_episode=filters.start_episode,
        end_episode=filters.end_episode,
        episodes=filters.episodes,
    )
    anime_name = crawler.extract_anime_name(soup, url)
    download_path = create_download_directory(anime_name, custom_path=custom_path)
    video_urls = await crawler.collect_video_urls()
    download_anime(anime_name, video_urls, download_path, workers=workers)


def parse_episodes_list(episodes_raw: list[str] | None) -> list[int] | None:
    """Parse episode tokens into a sorted list of ints.

    Accepts tokens like ['1,3,7,12'] or ['1,', '3,', '7', '12'] or ['1', '3', '7'].
    """
    if episodes_raw is None:
        return None

    merged = ",".join(episodes_raw)
    return sorted(
        int(episode.strip()) for episode in merged.split(",") if episode.strip()
    )


async def main() -> None:
    """Execute the script to download anime episodes from a given AnimeUnity URL."""
    clear_terminal()
    args = parse_arguments()

    if args.search:
        await search_and_export(args.search, custom_path=args.custom_path)
        return

    episodes = parse_episodes_list(args.episodes)
    filters = EpisodeFilters(args.start, args.end, episodes)

    if args.check:
        await check_anime_download(args.url, filters)
        return

    await process_anime_download(
        args.url,
        filters,
        custom_path=args.custom_path,
        workers=args.parallel_downloads,
    )


if __name__ == "__main__":
    asyncio.run(main())
