"""Grounding service for formatting web search results into LLM context."""

from typing import Dict, List, Tuple


class GroundingService:
    @staticmethod
    def build_grounded_context(
        search_results: List[Dict[str, str]],
        page_contents: List[Dict[str, str]]
    ) -> Tuple[str, List[Dict[str, str]]]:
        """
        Combine search snippets and webpage extracts into a structured context block.
        Returns (context_text, list_of_sources).
        """
        context_parts: List[str] = []
        sources: List[Dict[str, str]] = []
        seen_urls = set()

        # 1. Scraped full web pages (highest detail)
        if page_contents:
            context_parts.append("### 🌐 Загруженные веб-страницы:")
            for p in page_contents:
                url = p.get("url", "")
                title = p.get("title") or url
                content = p.get("content", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    sources.append({"title": title, "url": url})
                context_parts.append(
                    f"**Страница:** {title}\n"
                    f"**URL:** {url}\n"
                    f"**Содержимое:**\n{content}\n"
                )

        # 2. Search engine snippets
        if search_results:
            context_parts.append("### 🔍 Результаты поиска в сети (DuckDuckGo):")
            for idx, r in enumerate(search_results, 1):
                url = r.get("href", "")
                title = r.get("title") or url
                snippet = r.get("body", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    sources.append({"title": title, "url": url})
                context_parts.append(
                    f"[{idx}] **{title}**\n"
                    f"URL: {url}\n"
                    f"Описание: {snippet}\n"
                )

        full_context = "\n---\n".join(context_parts)
        return full_context, sources

    @staticmethod
    def build_system_prompt(base_system_prompt: str, grounded_context: str) -> str:
        """Inject grounding instructions and retrieved data into the system prompt."""
        if not grounded_context.strip():
            return base_system_prompt

        instructions = (
            f"{base_system_prompt}\n\n"
            "=====================================================\n"
            "🌐 АКТУАЛЬНЫЕ ДАННЫЕ ИЗ ИНТЕРНЕТА (GROUNDED SEARCH CONTEXT):\n"
            "Ниже приведены свежие результаты поиска и выжимки веб-страниц, "
            "полученные в реальном времени специально для этого запроса:\n\n"
            f"{grounded_context}\n\n"
            "ИНСТРУКЦИИ ПО ИСПОЛЬЗОВАНИЮ ИНТЕРНЕТ-ДАННЫХ:\n"
            "1. Опирайся на предоставленные свежие факты, даты, числа, версии и детали из поиска.\n"
            "2. Отвечай прямо, точно и структурированно, форматируя текст в Telegram Markdown.\n"
            "3. В конце ответа добавь аккуратный блок с кликабельными ссылками на использованные источники (например: `🔗 **Источники:**\\n• [Название статьи/сайта](url)`).\n"
            "====================================================="
        )
        return instructions


grounding_service = GroundingService()
