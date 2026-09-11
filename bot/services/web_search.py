"""Web search and web page extraction service."""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional
import aiohttp
from bs4 import BeautifulSoup
from ddgs import DDGS

from bot.config import config

logger = logging.getLogger(__name__)

URL_REGEX = re.compile(r'https?://[^\s<>"\']+')


class WebSearchService:
    def __init__(self):
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }

    def extract_urls(self, text: str) -> List[str]:
        """Extract valid HTTP/HTTPS URLs from text."""
        if not text:
            return []
        matches = URL_REGEX.findall(text)
        # Clean trailing punctuation from URLs
        clean_urls = []
        for u in matches:
            u = u.rstrip(".,;!?:)")
            if u and u not in clean_urls:
                clean_urls.append(u)
        return clean_urls

    async def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        region: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """
        Execute DuckDuckGo search in a background thread.
        Returns list of dicts with 'title', 'href', 'body'.
        """
        limit = max_results or config.max_search_results
        query = query.strip()
        if not query:
            return []

        loop = asyncio.get_running_loop()

        def _do_search() -> List[Dict[str, str]]:
            try:
                with DDGS() as ddgs:
                    search_kwargs: Dict[str, Any] = {"max_results": limit}
                    if region:
                        search_kwargs["region"] = region
                    res = list(ddgs.text(query, **search_kwargs))
                    if not res and region:
                        # Retry without region
                        res = list(ddgs.text(query, max_results=limit))
                    return res or []
            except Exception as e:
                logger.warning(f"DuckDuckGo search error for '{query}': {e}")
                return []

        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(None, _do_search),
                timeout=12.0
            )
            return results
        except asyncio.TimeoutError:
            logger.warning(f"DuckDuckGo search timed out for query '{query}'")
            return []
        except Exception as e:
            logger.error(f"Unexpected search failure for '{query}': {e}")
            return []

    async def multi_search(self, queries: List[str], max_per_query: int = 3) -> List[Dict[str, str]]:
        """Run multiple search queries in parallel and deduplicate results by URL."""
        if not queries:
            return []

        tasks = [self.search(q, max_results=max_per_query) for q in queries[:3]]
        results_nested = await asyncio.gather(*tasks, return_exceptions=True)

        merged: List[Dict[str, str]] = []
        seen_urls = set()

        for res in results_nested:
            if isinstance(res, list):
                for item in res:
                    url = item.get("href", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        merged.append(item)

        return merged[:config.max_search_results]

    async def fetch_url_content(self, url: str, max_chars: int = 5000) -> Optional[Dict[str, str]]:
        """
        Fetch webpage HTML, extract page title and clean text.
        """
        timeout = aiohttp.ClientTimeout(total=config.web_fetch_timeout)
        try:
            async with aiohttp.ClientSession(headers=self.headers, timeout=timeout) as session:
                async with session.get(url, allow_redirects=True) as resp:
                    if resp.status >= 400:
                        logger.warning(f"Fetch {url} returned status {resp.status}")
                        return None
                    
                    content_type = resp.headers.get("Content-Type", "").lower()
                    if "text/html" not in content_type and "text/plain" not in content_type and "json" not in content_type:
                        logger.info(f"Skipping non-text content-type '{content_type}' for {url}")
                        return None

                    html = await resp.text(errors="replace")

            soup = BeautifulSoup(html, "html.parser")

            # Extract title
            title = soup.title.string.strip() if soup.title and soup.title.string else ""
            if not title and soup.find("h1"):
                title = soup.find("h1").get_text(strip=True)

            # Strip script, style, nav, footer, ads
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "svg", "form"]):
                tag.decompose()

            text = soup.get_text(separator=" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()

            if len(text) > max_chars:
                text = text[:max_chars] + " ... [содержимое сокращено]"

            return {
                "url": url,
                "title": title or url,
                "content": text
            }

        except Exception as e:
            logger.warning(f"Error fetching webpage {url}: {e}")
            return None


web_search_service = WebSearchService()
