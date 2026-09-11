"""Document parser for handling text, code, and PDF files."""

import io
import logging
from typing import Optional
import pypdf

logger = logging.getLogger(__name__)

MAX_FILE_CHARS = 50000


def parse_document_content(file_bytes: bytes, filename: str) -> Optional[str]:
    """Extract text content from uploaded files (PDF, Code, Text)."""
    filename_lower = filename.lower()

    # 1. PDF files
    if filename_lower.endswith(".pdf"):
        try:
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            text_parts = []
            for page_num, page in enumerate(reader.pages):
                extracted = page.extract_text()
                if extracted:
                    text_parts.append(f"--- Страница {page_num + 1} ---\n{extracted}")
            full_text = "\n\n".join(text_parts).strip()
            if not full_text:
                return f"[PDF файл {filename} не содержит извлекаемого текста (возможно, отсканированное изображение)]"
            if len(full_text) > MAX_FILE_CHARS:
                full_text = full_text[:MAX_FILE_CHARS] + f"\n\n[...файл обрезан до {MAX_FILE_CHARS} символов...]"
            return f"📄 Содержимое файла {filename}:\n\n{full_text}"
        except Exception as e:
            logger.error(f"Error parsing PDF {filename}: {e}")
            return f"[Ошибка чтения PDF файла {filename}: {e}]"

    # 2. Text / Code / Data files
    try:
        # Try UTF-8 first
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            # Fallback to cp1251
            text = file_bytes.decode("cp1251")
        except Exception:
            try:
                # Fallback to latin-1
                text = file_bytes.decode("latin-1", errors="replace")
            except Exception as e:
                logger.warning(f"Could not decode file {filename}: {e}")
                return None

    if len(text) > MAX_FILE_CHARS:
        text = text[:MAX_FILE_CHARS] + f"\n\n[...файл обрезан до {MAX_FILE_CHARS} символов...]"

    return f"📄 Содержимое файла {filename}:\n\n```\n{text}\n```"
