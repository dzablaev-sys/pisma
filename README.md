# Бот «Письма»

Телеграм-бот: принимает входящее письмо, вы выбираете объект и пишете тезисы,
бот через Anthropic API (модель `claude-sonnet-5`) формирует тело ответа и
вставляет его в готовый бланк-шаблон `.docx`, затем присылает файл.

## Структура

- `bot.py` — сам бот и `/health`-сервер.
- `letter_writer.py` — обращение к Anthropic API (пишет только тело ответа).
- `docx_filler.py` — вставка тела в шаблон после вводного абзаца.
- `config.py` — справочник объектов и их шаблонов.
- `templates/` — 5 файлов-бланков `.docx` (по одному на объект).

## Переменные окружения

- `TELEGRAM_TOKEN` — токен бота от @BotFather.
- `ANTHROPIC_API_KEY` — ключ Anthropic.
- `PORT` — порт для `/health` (Render задаёт автоматически).
- `ALLOWED_USER_IDS` — (необязательно) разрешённые Telegram-ID через запятую.

Локально значения кладутся в `.env` (см. `.env.example`). На Render — в разделе
Environment.

## Запуск на Render (Web Service)

- Build command: `pip install -r requirements.txt`
- Start command: `python bot.py`
