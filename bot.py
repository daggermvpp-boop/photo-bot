import os
import io
import asyncio
import logging
from PIL import Image
import requests
import telegram
import telegram.request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
HF_TOKEN = os.environ.get("HF_TOKEN")
HF_MODEL = os.environ.get("HF_MODEL", "runwayml/stable-diffusion-v1-5")

DEFAULT_PROMPT = "anime style, sticker, vibrant colors, cute, clean lineart, flat shading"

HF_API_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL}"
HF_HEADERS = {"Authorization": f"Bearer {HF_TOKEN}"}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Anime Sticker Bot\n\n"
        "Otprav mne foto - ya prevrashu ego v anime-stiker!\n\n"
        "/prompt <text> - svoi prompt\n"
        "/default - sbrosit na standartny"
    )

async def set_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace("/prompt", "", 1).strip()
    if text:
        context.user_data["prompt"] = text
        await update.message.reply_text("Prompt ustanovlen: " + text)
    else:
        await update.message.reply_text("Napishe prompt posle /prompt")

async def reset_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("prompt", None)
    await update.message.reply_text("Prompt sbroshen")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    status_msg = await update.message.reply_text("Generiruyu stiker...")

    try:
        file = await photo.get_file()
        image_bytes = await file.download_as_bytearray()

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img = resize_and_crop(img, 512)
        img = add_white_bg(img)

        img_buf = io.BytesIO()
        img.save(img_buf, format="JPEG", quality=95)
        img_buf.seek(0)

        prompt = context.user_data.get("prompt", DEFAULT_PROMPT)
        full_prompt = prompt + ", anime style, sticker, kawaii"
        neg_prompt = "ugly, blurry, deformed, low quality, bad anatomy, realistic, photo"

        result_bytes = await asyncio.to_thread(
            query_hf_api, img_buf.getvalue(), full_prompt, neg_prompt
        )

        if result_bytes is None:
            await status_msg.edit_text("Model loading, try again in 30s...")
            return

        result = Image.open(io.BytesIO(result_bytes)).convert("RGBA")
        result = resize_and_crop(result, 512)

        sticker_buf = io.BytesIO()
        result.save(sticker_buf, format="WEBP", quality=90)
        sticker_buf.seek(0)

        await status_msg.delete()
        await update.message.reply_sticker(sticker_buf)

    except Exception as e:
        logger.exception("Error handling photo")
        await status_msg.edit_text("Oshibka: " + str(e)[:200])

def query_hf_api(image_bytes, prompt, neg_prompt, retries=3):
    for attempt in range(retries):
        resp = requests.post(
            HF_API_URL,
            headers=HF_HEADERS,
            data=image_bytes,
            params={
                "prompt": prompt,
                "negative_prompt": neg_prompt,
                "strength": 0.75,
                "guidance_scale": 7.5,
            },
            timeout=120,
        )

        if resp.status_code == 200:
            return resp.content

        if resp.status_code == 503:
            import time
            wait = resp.json().get("estimated_time", 30)
            logger.info(f"Model loading, waiting {wait}s (attempt {attempt+1})")
            time.sleep(min(wait, 60))
            continue

        logger.error(f"API error {resp.status_code}: {resp.text[:200]}")
        resp.raise_for_status()

    return None

def add_white_bg(img):
    bg = Image.new("RGB", img.size, (255, 255, 255))
    bg.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
    return bg

def resize_and_crop(img, size):
    w, h = img.size
    min_side = min(w, h)
    left = (w - min_side) // 2
    top = (h - min_side) // 2
    img = img.crop((left, top, left + min_side, top + min_side))
    img = img.resize((size, size), Image.LANCZOS)
    return img

def main():
    request = telegram.request.HTTPXRequest(
        connect_timeout=30,
        read_timeout=30,
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("prompt", set_prompt))
    app.add_handler(CommandHandler("default", reset_prompt))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
