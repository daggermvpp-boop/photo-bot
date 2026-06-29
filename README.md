---
title: Anime Sticker Bot
emoji: 🎨
colorFrom: purple
colorTo: pink
sdk: docker
pinned: false
---

# Anime Sticker Bot

Telegram бот для превращения фото в аниме-стикеры.

## Деплой на Hugging Face Spaces (бесплатно)

1. Создай аккаунт на [huggingface.co](https://huggingface.co)
2. Создай новый Space → Docker
3. Загрузи файлы из этой папки
4. В Settings → Variables добавь:
   - `BOT_TOKEN` — токен твоего бота от @BotFather
   - `HF_TOKEN` — токен от huggingface.co/settings/tokens
   - `HF_MODEL` — модель (по умолчанию digiplay/AnimePastelDream)
   - `HF_PROVIDER` — провайдер (по умолчанию hf-inference, можно wavespeed и др.)
5. Space автоматически запустится

## Локальный запуск

```bash
pip install -r requirements.txt
set BOT_TOKEN=...
set HF_TOKEN=...
python bot.py
```

## Команды

- Отправь фото — получишь стикер
- `/prompt <текст>` — свой промпт
- `/default` — сбросить промпт
