"""Извлечение текста входящего письма из PDF.

Логика:
1. Пробуем достать текстовый слой напрямую (pdfplumber) — быстро и бесплатно.
2. Если текста почти нет (скан) — распознаём через модель Claude: она читает
   PDF как изображения (в т.ч. русский рукописный/печатный текст). Это заменяет
   локальный OCR и не требует системных пакетов на Render.
"""

import base64
import io

import anthropic
import pdfplumber

# Если текстовый слой короче — считаем, что это скан, и идём в OCR.
MIN_TEXT_CHARS = 40

OCR_MODEL = "claude-sonnet-5"


def extract_text_layer(pdf_bytes: bytes) -> str:
    """Достать текстовый слой из PDF (если он есть)."""
    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


def ocr_with_claude(pdf_bytes: bytes) -> str:
    """Распознать текст скан-PDF через модель Claude (vision по страницам PDF)."""
    b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=OCR_MODEL,
        max_tokens=8000,
        thinking={"type": "disabled"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Это отсканированное входящее письмо на русском языке. "
                            "Извлеки и верни ВЕСЬ его текст дословно, сохраняя абзацы. "
                            "Не добавляй ничего от себя, не комментируй, верни только текст письма."
                        ),
                    },
                ],
            }
        ],
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()


def read_pdf_text(pdf_bytes: bytes) -> tuple[str, str]:
    """Вернуть (текст письма, способ), где способ = 'layer' или 'ocr'."""
    text = extract_text_layer(pdf_bytes)
    if len(text) >= MIN_TEXT_CHARS:
        return text, "layer"
    return ocr_with_claude(pdf_bytes), "ocr"
