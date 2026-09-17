"""Main module of the project.

This module provides functionality to read URLs from a file, process
them for downloading Anime content, and write results back to the file.

Usage:
    To use this module, ensure that 'URLs.txt' is present in the same
    directory as this script. Execute the script to read URLs, download
    content, and clear the URL list upon completion.
"""

from __future__ import annotations

import asyncio
import logging

from anime_downloader import (
    check_anime_download,
    parse_arguments,
    process_anime_download,
)
from src.config import DOWNLOAD_WORKERS, URLS_FILE
from src.file_utils import read_file, write_file
from src.general_utils import clear_terminal


async def process_urls(
    urls: list[str],
    custom_path: str | None = None,
    *,
    check: bool = False,
    workers: int = DOWNLOAD_WORKERS,
) -> list[str]:
    """Validate and download items for a list of URLs.

    A URL that fails doesn't abort the rest of the batch. Returns the URLs
    that failed, so the caller can keep them queued instead of silently
    losing track of them.
    """
    failed_urls = []
    for url in urls:
        try:
            if check:
                await check_anime_download(url)
            else:
                await process_anime_download(
                    url,
                    custom_path=custom_path,
                    workers=workers,
                )

        except Exception:  # pylint: disable=broad-exception-caught
            # One bad URL must never abort the rest of the batch.
            logging.exception("Failed to process %s", url)
            failed_urls.append(url)

    return failed_urls


async def main() -> None:
    """Run the script."""
    # Clear terminal and parse arguments
    clear_terminal()
    args = parse_arguments(common_only=True)

    # Read and process URLs, ignoring empty lines
    urls = [url.strip() for url in read_file(URLS_FILE) if url.strip()]
    failed_urls = await process_urls(
        urls,
        custom_path=args.custom_path,
        check=args.check,
        workers=args.parallel_downloads,
    )

    # Clear URLs file, unless this was just a preview; keep any URL that
    # failed so it isn't silently dropped from the queue.
    if not args.check:
        write_file(URLS_FILE, "\n".join(failed_urls))


if __name__ == "__main__":
    asyncio.run(main())
