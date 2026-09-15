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
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

import requests
from rich.console import Console
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
from src.file_utils import create_download_directory
from src.general_utils import clear_terminal, fetch_page, fetch_page_httpx
from src.progress_utils import create_progress_bar, create_progress_table


class EpisodeFilters(NamedTuple):
    """Episode selection filters shared by the download and check flows."""

    start_episode: int | None = None
    end_episode: int | None = None
    episodes: list[int] | None = None


def download_episode(
    download_link: str,
    download_path: str,
    task_info: tuple,
    retries: int = 4,
) -> None:
    """Download an episode from the download link and provides progress updates."""
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
            break


def resolve_download_link(video_url: str) -> str | None:
    """Resolve a video URL's direct download link, without downloading it."""
    soup = fetch_page(video_url)
    script_items = soup.find_all("script")
    return extract_download_link(script_items, video_url)


def process_video_url(video_url: str, download_path: str, task_info: tuple) -> None:
    """Process an embed URL to extract episode download links."""
    download_link = resolve_download_link(video_url)
    download_episode(download_link, download_path, task_info)


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
        run_in_parallel(
            process_video_url,
            video_urls,
            job_progress,
            download_path,
            workers=workers,
        )


def print_check_report(
    anime_name: str,
    num_episodes: int,
    episode_links: list[tuple[str, str | None]],
) -> None:
    """Print a summary of an anime URL, listing every resolved download link."""
    console = Console()
    console.print(f"[b]{anime_name}[/b]")
    console.print(f"Total episodes available: {num_episodes}")
    console.print(f"Episodes matching filters: {len(episode_links)}")
    for number, link in episode_links:
        shown_link = link or "[red]could not resolve link[/red]"
        console.print(f"Episode {number}: {shown_link}")


def _resolve_episode_link(episode_video_url: tuple[str, str]) -> tuple[str, str | None]:
    """Resolve a single (episode number, video URL) pair to its download link."""
    number, video_url = episode_video_url
    return number, resolve_download_link(video_url)


async def check_anime_download(
    url: str,
    filters: EpisodeFilters = EpisodeFilters(),
) -> None:
    """Validate a URL and report the anime name and every resolved download link.

    Unlike `process_anime_download`, this never downloads any file, so it's
    safe to use as a preview, but it does resolve each matching episode's
    embed and video pages to find its direct download link.
    """
    soup = fetch_page_httpx(url)
    crawler = Crawler(
        url=url,
        start_episode=filters.start_episode,
        end_episode=filters.end_episode,
        episodes=filters.episodes,
    )
    anime_name = crawler.extract_anime_name(soup, url)
    episode_video_urls = await crawler.collect_episode_video_urls()

    with ThreadPoolExecutor(max_workers=CRAWLER_WORKERS) as executor:
        episode_links = list(executor.map(_resolve_episode_link, episode_video_urls))

    print_check_report(anime_name, crawler.num_episodes, episode_links)


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
