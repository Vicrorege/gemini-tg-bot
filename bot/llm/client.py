"""Async OpenAI-compatible Antigravity LLM client wrapper."""

import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional
from openai import AsyncOpenAI, APIError, RateLimitError, APIConnectionError

from bot.config import config

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self):
        self._client: Optional[AsyncOpenAI] = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=config.openai_base_url,
                api_key=config.openai_api_key,
                timeout=120.0,
                max_retries=2,
            )
        return self._client

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """Stream chat completion response chunk by chunk."""
        target_model = model or config.default_model
        yielded_any_content = False
        detected_tool_calls = False

        try:
            stream = await self.client.chat.completions.create(
                model=target_model,
                messages=messages,
                temperature=temperature,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta:
                        if delta.content:
                            yielded_any_content = True
                            yield delta.content
                        if delta.tool_calls:
                            detected_tool_calls = True

            # If the stream completed without returning any text (e.g. model invoked an internal tool),
            # execute a fallback completion with explicit instruction to answer directly in Telegram text.
            if not yielded_any_content:
                logger.info(f"Stream returned empty content for {target_model} (tool_calls={detected_tool_calls}). Running fallback...")
                fallback_messages: List[Any] = list(messages)
                fallback_messages.append({
                    "role": "system",
                    "content": (
                        "Note: You are communicating directly in a Telegram chat. "
                        "Do not call external workspace or filesystem tools. "
                        "Provide your full, helpful answer directly to the user as formatted text."
                    )
                })
                resp = await self.client.chat.completions.create(
                    model=target_model,
                    messages=fallback_messages,
                    temperature=temperature,
                    stream=False,
                )
                if resp.choices and resp.choices[0].message and resp.choices[0].message.content:
                    yield resp.choices[0].message.content

        except RateLimitError as e:
            logger.error(f"Rate limit exceeded on {target_model}: {e}")
            yield "\n\n⚠️ *Превышен лимит запросов (Rate Limit). Пожалуйста, подождите минуту или переключите модель.*"
        except APIConnectionError as e:
            logger.error(f"Connection error to Antigravity API at {config.openai_base_url}: {e}")
            yield f"\n\n⚠️ *Ошибка соединения с сервером LLM ({config.openai_base_url}). Проверьте доступность API.*"
        except APIError as e:
            logger.error(f"API Error from LLM: {e}")
            yield f"\n\n⚠️ *Ошибка API модели: {e.message}*"
        except Exception as e:
            logger.exception(f"Unexpected error in stream_chat: {e}")
            yield f"\n\n⚠️ *Произошла внутренняя ошибка: {str(e)}*"

    async def generate_title(self, text: str, model: Optional[str] = None) -> str:
        """Generate a short 3-5 word Russian title for a conversation."""
        target_model = model or config.default_model
        prompt = (
            "Сгенерируй короткое, емкое название (3-5 слов) на русском языке для диалога, "
            "который начинается со следующего сообщения пользователя. "
            "Ответь ТОЛЬКО названием, без кавычек, знаков препинания в конце и лишних слов.\n\n"
            f"Сообщение: {text[:500]}"
        )
        try:
            resp = await self.client.chat.completions.create(
                model=target_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=25,
            )
            if resp.choices and resp.choices[0].message.content:
                title = resp.choices[0].message.content.strip().strip('"\'«»')
                if title:
                    return title[:50]
        except Exception as e:
            logger.warning(f"Failed to generate session title: {e}")

        # Fallback to truncated text
        clean = " ".join(text.split()[:5])
        return clean[:40] if clean else "Новый диалог"

    async def summarize_context(
        self,
        existing_summary: Optional[str],
        messages_to_compress: List[Dict[str, str]],
        model: Optional[str] = None,
    ) -> str:
        """Summarize older messages into a compact context block."""
        target_model = model or config.default_model
        formatted_messages = []
        for m in messages_to_compress:
            role = "Пользователь" if m["role"] == "user" else "Ассистент"
            content = m["content"]
            if isinstance(content, list):
                # Multimodal content extraction
                text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                content = " ".join(text_parts) or "[Медиа/Изображение]"
            formatted_messages.append(f"{role}: {content}")

        dialogue_text = "\n".join(formatted_messages)

        prompt = (
            "Ты — модуль сжатия контекста диалога. Твоя задача — составить краткую, структурированную "
            "и емкую выжимку ключевых фактов, деталей, требований и результатов из предыдущей части разговора. "
            "Сохрани все важные детали, имена, переменные, решения и текущий статус, чтобы ассистент мог "
            "бесшовно продолжить разговор.\n\n"
        )
        if existing_summary:
            prompt += f"Предыдущее резюме контекста:\n{existing_summary}\n\n"

        prompt += f"Новые сообщения для сжатия:\n{dialogue_text}\n\nОбновленное структурированное резюме:"

        try:
            resp = await self.client.chat.completions.create(
                model=target_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=800,
            )
            if resp.choices and resp.choices[0].message.content:
                return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Failed to summarize context: {e}")

        # Fallback: simple text truncation if LLM call fails
        fallback = (existing_summary + "\n" if existing_summary else "") + dialogue_text
        return fallback[-1500:]


llm = LLMClient()
