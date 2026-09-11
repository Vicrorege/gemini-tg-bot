"""Intent analyzer for routing messages to web search or direct LLM."""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from bot.config import config
from bot.db.models import UserSetting
from bot.llm.client import llm
from bot.services.web_search import web_search_service

logger = logging.getLogger(__name__)

# Fast heuristic triggers for instant web search
EXPLICIT_SEARCH_PATTERNS = [
    re.compile(r"\b(поищи|найди в интернете|найди в сети|погугли|прогугли|загугли|поищи в гугле|search for|google for)\b", re.IGNORECASE),
    re.compile(r"\b(свежие новости|последние новости|новости за сегодня|новости сегодня|курс биткоина|курс доллара|курс евро|погода в|погода на)\b", re.IGNORECASE),
    re.compile(r"\b(когда выйдет|когда вышел|дата выхода|последний релиз|актуальная версия|свежая версия|что нового в|что изменилось в)\b", re.IGNORECASE),
    re.compile(r"\b(о чём (трек|песня|клип|фильм|сериал|книга|игра)|кто (исполнитель|автор|написал|поет|поёт|сыграл)|текст песни|слова песни|смысл песни|смысл трека)\b", re.IGNORECASE),
    re.compile(r"\b(что за трек|что за песня|кто такой|кто такая|что такое|что за сервис|что за проект)\b", re.IGNORECASE),
]


@dataclass
class SearchDecision:
    need_search: bool
    queries: List[str] = field(default_factory=list)
    target_urls: List[str] = field(default_factory=list)
    reason: str = ""


class SearchRouter:
    async def analyze(
        self,
        text: str,
        user_settings: Optional[UserSetting] = None,
        force_search: bool = False
    ) -> SearchDecision:
        """
        Analyze whether a message should trigger web search or webpage fetching.
        """
        if not text or not text.strip():
            return SearchDecision(need_search=False, reason="Empty text")

        clean_text = text.strip()
        extracted_urls = web_search_service.extract_urls(clean_text)

        # 1. Force search flag (e.g. from /search command)
        if force_search:
            queries = [clean_text]
            return SearchDecision(
                need_search=True,
                queries=queries,
                target_urls=extracted_urls,
                reason="Forced search"
            )

        # 2. Check global setting
        if not config.web_search_enabled:
            if extracted_urls:
                return SearchDecision(need_search=False, target_urls=extracted_urls, reason="URL extraction only")
            return SearchDecision(need_search=False, reason="Web search globally disabled")

        # 3. User setting mode ('off', 'on', 'auto')
        user_mode = user_settings.web_search_mode if user_settings else config.default_web_mode

        if user_mode == "off":
            if extracted_urls:
                return SearchDecision(need_search=False, target_urls=extracted_urls, reason="URL extraction only")
            return SearchDecision(need_search=False, reason="Web search disabled by user")

        # If user explicitly provided URLs in text, always fetch them!
        if extracted_urls:
            return SearchDecision(
                need_search=True,
                queries=[clean_text],
                target_urls=extracted_urls,
                reason="User provided URLs"
            )

        # 4. Check explicit regex triggers
        for pat in EXPLICIT_SEARCH_PATTERNS:
            if pat.search(clean_text):
                # Clean prompt into query
                query = re.sub(r"\b(поищи|найди в интернете|найди в сети|погугли|загугли|пожалуйста)\b", "", clean_text, flags=re.IGNORECASE).strip()
                query = query or clean_text
                return SearchDecision(
                    need_search=True,
                    queries=[query],
                    reason="Explicit keyword/question trigger"
                )

        if user_mode == "on":
            return SearchDecision(
                need_search=True,
                queries=[clean_text],
                reason="Web search mode 'on'"
            )

        # 5. Smart LLM-based intent routing ('auto' mode)
        # Avoid routing ultra-short greetings or simple conversational phrases
        lower = clean_text.lower()
        if len(clean_text) < 15 and lower in {"привет", "хай", "hello", "hi", "спасибо", "ок", "ясно", "понял", "пока", "супер", "отлично"}:
            return SearchDecision(need_search=False, reason="Short greeting")

        try:
            return await self._llm_classify(clean_text)
        except Exception as e:
            logger.warning(f"LLM search classification failed: {e}")
            # Fallback: if message is asking something, search by default!
            if "?" in clean_text or len(clean_text) > 10:
                return SearchDecision(need_search=True, queries=[clean_text], reason=f"Fallback search ({e})")
            return SearchDecision(need_search=False, reason=f"Classification fallback ({e})")

    async def _llm_classify(self, text: str) -> SearchDecision:
        """Call fast model to decide if web search is beneficial and formulate search queries."""
        prompt = (
            "You are a search query router for an AI assistant. Analyze the user message and determine "
            "if answering it accurately, factually, and without hallucinations requires web search.\n\n"
            "Search MUST be triggered for:\n"
            "- Specific tracks/songs, music artists, albums, lyrics, movies, books, games, pop-culture\n"
            "- Abbreviations, slang, acronyms, or specific names/titles\n"
            "- Fresh news, recent events, facts, prices, weather, people, companies\n"
            "- Specific software libraries, APIs, tools, frameworks, versions, release notes, documentation\n"
            "- Any questions where external factual knowledge is needed\n\n"
            "NO_SEARCH is only for pure math/logic, code syntax examples (e.g. 'write a quicksort in python'), creative storytelling without specific subjects, greetings, or chit-chat.\n\n"
            "Format your output strictly as:\n"
            "QUERIES:\n"
            "<query 1>\n"
            "<query 2> (optional)\n\n"
            "or:\n"
            "NO_SEARCH\n\n"
            f"User message: {text[:600]}"
        )

        resp = await llm.client.chat.completions.create(
            model=config.router_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=60,
        )

        content = (resp.choices[0].message.content or "").strip() if (resp.choices and resp.choices[0].message.content) else "NO_SEARCH"
        if "NO_SEARCH" in content or not content:
            return SearchDecision(need_search=False, reason="LLM classified NO_SEARCH")

        queries: List[str] = []
        lines = content.split("\n")
        for line in lines:
            line = line.strip().lstrip("-*•1234567890. ")
            if line and not line.upper().startswith("QUERIES"):
                queries.append(line)

        if not queries:
            queries = [text[:100]]

        return SearchDecision(
            need_search=True,
            queries=queries[:2],
            reason="LLM determined search required"
        )


search_router = SearchRouter()
