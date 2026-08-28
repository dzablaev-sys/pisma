"""Телеграм-бот «Письма».

Поток:
1. Пользователь пересылает боту входящее письмо (текст).
2. Бот показывает кнопки объектов — пользователь выбирает один.
3. Бот просит тезисы ответа (или «—», чтобы ответить по смыслу входящего).
4. Модель Anthropic пишет ТОЛЬКО тело ответа.
5. Бот вставляет тело в файл-шаблон объекта и присылает готовый .docx.

Параллельно поднимается лёгкий HTTP-сервер с /health — чтобы Render видел
открытый порт и сервис не считался «упавшим». Пинг этого адреса раз в 5 минут
не даёт бесплатному сервису уснуть.
"""

import logging
import os
import tempfile
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import OBJECTS
from docx_filler import IntroParagraphNotFound, fill_template
from letter_writer import generate_body

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("pisma-bot")

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"

# Состояния диалога.
CHOOSING_OBJECT, WAITING_THESES = range(2)

# Необязательный список разрешённых Telegram-ID (через запятую в ALLOWED_USER_IDS).
# Если пусто — бот отвечает всем.
_allowed_raw = os.environ.get("ALLOWED_USER_IDS", "").strip()
ALLOWED_USER_IDS = {int(x) for x in _allowed_raw.split(",") if x.strip()} if _allowed_raw else set()


def _is_allowed(update: Update) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    user = update.effective_user
    return bool(user and user.id in ALLOWED_USER_IDS)


# ---------- Обработчики диалога ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Привет! Это бот «Письма».\n\n"
        "Перешлите мне входящее письмо (текстом), затем выберите объект и напишите тезисы ответа. "
        "Я верну готовый .docx на бланке нужного объекта."
    )
    return ConversationHandler.END


async def on_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_allowed(update):
        await update.message.reply_text("Извините, у вас нет доступа к этому боту.")
        return ConversationHandler.END

    text = update.message.text or update.message.caption or ""
    if not text.strip():
        await update.message.reply_text("Пришлите текст входящего письма.")
        return ConversationHandler.END

    context.user_data["incoming"] = text

    keyboard = [
        [InlineKeyboardButton(obj["title"], callback_data=key)]
        for key, obj in OBJECTS.items()
    ]
    await update.message.reply_text(
        "Письмо получил. Выберите объект:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSING_OBJECT


async def on_object_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    key = query.data
    obj = OBJECTS.get(key)
    if obj is None:
        await query.edit_message_text("Неизвестный объект. Начните заново, прислав письмо.")
        return ConversationHandler.END

    context.user_data["object_key"] = key
    await query.edit_message_text(
        f"Объект: {obj['title']}.\n\n"
        "Теперь пришлите тезисы ответа. Если хотите, чтобы я ответил по смыслу входящего, "
        "отправьте «—»."
    )
    return WAITING_THESES


async def on_theses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    incoming = context.user_data.get("incoming", "")
    key = context.user_data.get("object_key")
    obj = OBJECTS.get(key)

    if not incoming or obj is None:
        await update.message.reply_text("Что-то потерялось. Начните заново, прислав письмо.")
        return ConversationHandler.END

    theses = update.message.text or ""
    await update.message.reply_text("Готовлю ответ, минутку…")

    try:
        body = generate_body(incoming, theses, obj)
        if not body:
            await update.message.reply_text(
                "Модель вернула пустой ответ. Попробуйте ещё раз или уточните тезисы."
            )
            return ConversationHandler.END

        template_path = TEMPLATES_DIR / obj["filename"]
        if not template_path.exists():
            await update.message.reply_text(
                f"Не найден файл-шаблон «{obj['filename']}» в папке templates/."
            )
            return ConversationHandler.END

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_name = f"Ответ_{obj['title'].replace(' ', '_')}_{stamp}.docx"

        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / out_name
            fill_template(template_path, body, output_path, obj["intro_marker"])
            with open(output_path, "rb") as f:
                await update.message.reply_document(document=f, filename=out_name)

    except IntroParagraphNotFound as e:
        await update.message.reply_text(f"Не удалось обработать бланк: {e}")
    except Exception as e:  # noqa: BLE001 — показываем пользователю понятную ошибку
        logger.exception("Ошибка при формировании письма")
        await update.message.reply_text(f"Произошла ошибка: {e}")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменил. Пришлите новое письмо, когда будете готовы.")
    return ConversationHandler.END


# ---------- Лёгкий сервер /health ----------

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # заглушаем шумные логи веб-сервера
        pass


def _start_health_server():
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    logger.info("Health-сервер слушает порт %s", port)
    server.serve_forever()


# ---------- Точка входа ----------

def main() -> None:
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise SystemExit("Не задан TELEGRAM_TOKEN (в .env или в переменных окружения Render).")

    # Health-сервер — в фоновом потоке, чтобы Render видел открытый порт.
    threading.Thread(target=_start_health_server, daemon=True).start()

    application = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, on_incoming)],
        states={
            CHOOSING_OBJECT: [CallbackQueryHandler(on_object_chosen)],
            WAITING_THESES: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_theses)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv)

    logger.info("Бот запускается (polling)…")
    application.run_polling()


if __name__ == "__main__":
    main()
