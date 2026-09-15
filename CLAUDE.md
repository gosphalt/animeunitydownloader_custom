# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A Python CLI tool that downloads anime episodes from AnimeUnity (`animeunity.so`). It scrapes the site's info API to enumerate episodes, resolves each episode's embed page to a direct video URL, and downloads episodes concurrently with a Rich-based progress display.

## Commands

Set up a virtual environment and install dependencies (`.venv` is already gitignored):
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```
Then either prefix commands with `.venv/bin/python`/`.venv/bin/pip`, or `source .venv/bin/activate` first and use `python`/`pip` directly.

Download a single anime (optionally a range or specific episode list):
```bash
python3 anime_downloader.py <anime_url> [--start N] [--end N] [--episodes 1,3,7]
python3 anime_downloader.py <anime_url> --custom-path /path/to/dir
```

Batch download from `URLs.txt` (one URL per line in the repo root; the file is cleared after a run):
```bash
python3 main.py [--custom-path /path/to/dir]
```

Preview a URL without downloading (`--check`, valid on both entry points): validates the URL and prints the anime name, total episode count, and which episodes match `--start`/`--end`/`--episodes`. In `main.py`, `--check` also skips clearing `URLs.txt`.
```bash
python3 anime_downloader.py <anime_url> --check [--start N] [--end N] [--episodes 1,3,7]
python3 main.py --check
```

Control download concurrency (`--parallel-downloads N`, valid on both entry points; default 2, from `src/config.DOWNLOAD_WORKERS`):
```bash
python3 anime_downloader.py <anime_url> --parallel-downloads 5
python3 main.py --parallel-downloads 5
```

Lint (CI runs Pylint over all tracked `.py` files, see `.github/workflows/pylint.yml`; requires `pip install pylint` in the venv, it's not in `requirements.txt`):
```bash
.venv/bin/python -m pylint $(git ls-files '*.py')
```
CONTRIBUTING.md also documents a Ruff-based style (`ruff check .`, `select = ["ALL"]`, line-length 88), but no `ruff.toml` is currently checked in — CI enforcement is Pylint only.

There is no test suite in this repository.

## Architecture

Entry points:
- `anime_downloader.py` — downloads a single anime given a URL; also exposes `parse_arguments`, `process_anime_download`, `check_anime_download`, and `download_anime`, which `main.py` reuses. `EpisodeFilters` (a `NamedTuple` of `start_episode`/`end_episode`/`episodes`) bundles the episode-selection filters shared by `process_anime_download` and `check_anime_download`, keeping both signatures short.
- `main.py` — batch mode; reads URLs from `URLs.txt` and calls `process_anime_download` (or `check_anime_download` under `--check`) for each one, then clears the file (skipped under `--check`).

Pipeline for a single anime (`process_anime_download` in `anime_downloader.py`):
1. `src/general_utils.fetch_page_httpx` fetches the anime's landing page.
2. `src/crawler/crawler.py::Crawler` is constructed from the URL: it derives the AnimeUnity `info_api` URL, fetches the total episode count, and extracts the anime name (`Crawler.extract_anime_name`, with several fallback strategies: title tag → `<title>` → `og:title` meta → URL slug).
3. `src/file_utils.create_download_directory` creates `Downloads/<sanitized anime name>/` (or `<custom_path>/Downloads/...`).
4. `Crawler.collect_video_urls` (async): fetches episode IDs from the info API in batches of `BATCH_SIZE` (120, to avoid request failures on long series), filters them by `--episodes` or `--start`/`--end` range via the shared `Crawler._get_matching_episodes` helper, builds `embed-url/<id>` URLs, and concurrently resolves each to a real video URL (bounded by `CRAWLER_WORKERS` via an `asyncio.Semaphore`). `Crawler.get_matching_episode_numbers` reuses the same filtering helper but stops before resolving embed URLs, which is what powers `--check` (see `anime_downloader.check_anime_download`).
5. `download_anime` runs `src/download_utils.run_in_parallel` in a `ThreadPoolExecutor` (bounded by the `workers` argument, which flows from `--parallel-downloads` down through `process_anime_download` → `download_anime` → `run_in_parallel`; defaults to `DOWNLOAD_WORKERS`) inside a `rich.live.Live` context to download each episode with a per-episode and overall progress bar (`src/progress_utils.py`).
6. Each episode download (`download_episode` → `process_video_url` → `extract_download_link`) fetches the embed page, regex-extracts `window.downloadUrl` from inline scripts (`src/config.DOWNLOAD_LINK_PATTERN`), then streams the file to disk with a chunk size chosen by file size (`src/download_utils.get_chunk_size`, thresholds in `src/config.THRESHOLDS`).

Networking / anti-bot handling (`src/general_utils.py`, `src/crawler/crawler_utils.py`):
- Requests use rotating fake user agents (`src/config.prepare_headers`, via `fake_useragent`).
- Page fetches try `httpx`/`requests` first; on a 403 (Cloudflare) or failure they fall back to `cloudscraper` (`fetch_page_cloudflare`, `fetch_with_cloudscraper`), which mimics a Firefox/macOS browser.
- `MockResponse` in `crawler_utils.py` wraps a `requests.Response` so the cloudscraper fallback has the same interface (`.json()`, `.raise_for_status()`) as an `httpx` response.
- API/embed fetches (`fetch_with_retries`) retry with exponential backoff and fall back to cloudscraper on repeated failure.
- SSL verification is disabled throughout (`verify=False`) to work around the target site's certificate/Cloudflare setup — this is intentional, not an oversight.

Configuration lives entirely in `src/config.py`: paths (`DOWNLOAD_FOLDER`, `URLS_FILE`), regex patterns for the download link and anime name, worker/batch-size tuning (`CRAWLER_WORKERS`, `DOWNLOAD_WORKERS`, `BATCH_SIZE`), file-size chunking thresholds, and the shared argparse setup (`setup_parser`/`parse_arguments`, used by both entry points — `main.py` calls it with `common_only=True` since batch mode takes no URL/range args).

`src/version.py` defines the semantic version (`VersionInfo` NamedTuple) surfaced via `--version` on both CLIs.
