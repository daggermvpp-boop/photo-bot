import os, io, json, asyncio, logging, base64
from PIL import Image
import telegram, telegram.request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, PreCheckoutQueryHandler
from huggingface_hub import InferenceClient

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
HF_TOKEN = os.environ.get("HF_TOKEN")
STARS_TOKEN = os.environ.get("STARS_PROVIDER_TOKEN")
USDT_WALLET = os.environ.get("USDT_WALLET", "")
PRICE_STARS = 30
PRICE_USDT = 3
PAID_AMOUNT = 100
FREE_LIMIT = 5

client = InferenceClient(token=HF_TOKEN, base_url="https://router.huggingface.co/hf-inference/v1")
DATA_FILE = "/app/users_data.json"

STYLES = {
    "anime": "anime style, japanese anime art, vibrant, clean lineart, cel shading, studio ghibli inspired",
    "3d": "3D render, pixar style, disney style, volumetric lighting, cute, toy-like",
    "cartoon": "cartoon style, western animation, bold outlines, flat colors, comic style",
    "pixel": "pixel art, 8-bit, retro game style, blocky, low resolution, nostalgic",
    "oil": "oil painting, impasto, rich textures, classic painting, artistic, canvas",
    "sketch": "pencil sketch, black and white, hand-drawn, line art, detailed shading",
}

def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_user(user_id):
    data = load_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"requests": FREE_LIMIT, "total": 0, "paid": []}
        save_data(data)
    return data[uid]

def use_request(user_id):
    data = load_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"requests": FREE_LIMIT, "total": 0, "paid": []}
    if data[uid]["requests"] > 0:
        data[uid]["requests"] -= 1
        data[uid]["total"] += 1
        save_data(data)
        return True
    return False

def add_requests(user_id, amount, tx_info=""):
    data = load_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"requests": FREE_LIMIT, "total": 0, "paid": []}
    data[uid]["requests"] += amount
    data[uid]["paid"].append(tx_info)
    save_data(data)

# ---------- IMAGE PROCESSING ----------

def resize_and_crop(img, size):
    w, h = img.size
    mn = min(w, h)
    img = img.crop(((w-mn)//2, (h-mn)//2, (w+mn)//2, (h+mn)//2))
    return img.resize((size, size), Image.LANCZOS)

def image_to_bytes(img, fmt="JPEG", quality=95):
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=quality)
    buf.seek(0)
    return buf

# ---------- HANDLERS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = get_user(uid)
    name = update.effective_user.first_name or "User"
    keyboard = [
        [InlineKeyboardButton("Functions", callback_data="menu_functions"),
         InlineKeyboardButton("Balance", callback_data="menu_balance")],
        [InlineKeyboardButton("Buy requests", callback_data="menu_buy"),
         InlineKeyboardButton("Help", callback_data="menu_help")],
    ]
    await update.message.reply_text(
        f"Hey {name}! I am AI Studio Bot\n\n"
        f"I can: remove background, stylize photos, generate images, write texts\n\n"
        f"Free requests left: {u['requests']}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data

    if data == "menu_functions":
        text = (
            "How to use:\n\n"
            " Send photo + caption 'bg' - remove background\n"
            " Send photo + caption 'anime' - stylize as anime\n"
            " Send photo - I'll show action buttons\n"
            " /generate <prompt> - create image from text\n"
            " Just send text - AI will reply\n\n"
            "Available styles: anime, 3d, cartoon, pixel, oil, sketch"
        )
    elif data == "menu_balance":
        u = get_user(uid)
        text = f"Requests left: {u['requests']}\nTotal used: {u['total']}"
    elif data == "menu_buy":
        lines = [f"Buy {PAID_AMOUNT} requests:"]
        if STARS_TOKEN:
            lines.append(f" Telegram Stars: {PRICE_STARS} Stars /pay")
        if USDT_WALLET:
            lines.append(f" USDT (TRC-20): {PRICE_USDT} USDT to {USDT_WALLET}")
            lines.append(" Send TXID after payment")
        text = "\n".join(lines)
    elif data == "menu_help":
        text = "/start - menu\n/generate <text> - image\n/buy - purchase\n/balance - requests"
    else:
        return

    keyboard = [[InlineKeyboardButton("Back", callback_data="menu_start")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def menu_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    u = get_user(uid)
    keyboard = [
        [InlineKeyboardButton("Functions", callback_data="menu_functions"),
         InlineKeyboardButton("Balance", callback_data="menu_balance")],
        [InlineKeyboardButton("Buy requests", callback_data="menu_buy"),
         InlineKeyboardButton("Help", callback_data="menu_help")],
    ]
    await query.edit_message_text(
        f"AI Studio Bot\n\nFree requests left: {u['requests']}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    if text.startswith("/"):
        return

    # TXID check
    if len(text) == 64 and all(c in "0123456789abcdefABCDEF" for c in text) and USDT_WALLET:
        await handle_txid(update, context)
        return

    u = get_user(uid)
    if u["requests"] <= 0:
        await update.message.reply_text("No requests left. /buy")
        return

    status = await update.message.reply_text("Thinking...")
    try:
        result = await asyncio.wait_for(asyncio.to_thread(
            client.chat.completions.create,
            model="meta-llama/Llama-3.2-3B-Instruct",
            messages=[{"role": "user", "content": text}],
            max_tokens=800,
        ), timeout=60)
        reply = result.choices[0].message.content
        use_request(uid)
        await status.edit_text(reply)
    except Exception as e:
        logger.exception("text error")
        await status.edit_text(f"Error: {str(e)[:200]}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    caption = (update.message.caption or "").strip().lower()
    photo = update.message.photo[-1]

    u = get_user(uid)
    if u["requests"] <= 0:
        await update.message.reply_text("No requests left. /buy")
        return

    # Download photo
    file = await photo.get_file()
    img_bytes = await file.download_as_bytearray()
    context.user_data["last_photo"] = img_bytes

    # If caption matches a style, process directly
    if caption in STYLES:
        await process_style(update, context, caption, uid, img_bytes)
        return
    if caption == "bg":
        await process_bg(update, context, uid, img_bytes)
        return

    # Show action buttons
    keyboard = [
        [InlineKeyboardButton("Remove BG", callback_data="act_bg")],
        [InlineKeyboardButton("Stylize", callback_data="act_styles")],
        [InlineKeyboardButton("Cancel", callback_data="act_cancel")],
    ]
    await update.message.reply_text("Choose action:", reply_markup=InlineKeyboardMarkup(keyboard))

async def photo_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id
    chat_id = query.message.chat_id
    img_bytes = context.user_data.get("last_photo")

    if not img_bytes:
        await query.edit_message_text("Please send photo again")
        return

    u = get_user(uid)
    if u["requests"] <= 0:
        await query.edit_message_text("No requests left. /buy")
        return

    if data == "act_bg":
        await query.edit_message_text("Removing background...")
        try:
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            img = resize_and_crop(img, 512)
            buf = image_to_bytes(img)
            result = await asyncio.wait_for(asyncio.to_thread(
                client.image_segmentation, image=buf, model="briaai/RMBG-1.4",
            ), timeout=120)
            mask = result[0].convert("L")
            img_rgba = img.convert("RGBA")
            img_rgba.putalpha(mask)
            out = io.BytesIO()
            img_rgba.save(out, format="PNG")
            out.seek(0)
            use_request(uid)
            await query.delete_message()
            await context.bot.send_document(chat_id=chat_id, document=out, filename="no_bg.png")
        except Exception as e:
            await query.edit_message_text(f"Error: {str(e)[:200]}")
        return

    if data == "act_styles":
        rows = []
        styles = list(STYLES.keys())
        for i in range(0, len(styles), 2):
            row = [InlineKeyboardButton(s.capitalize(), callback_data=f"style_{s}") for s in styles[i:i+2]]
            rows.append(row)
        rows.append([InlineKeyboardButton("Back", callback_data="act_back")])
        await query.edit_message_text("Choose style:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "act_cancel" or data == "act_back":
        await query.edit_message_text("Cancelled")
        return

    if data.startswith("style_"):
        style = data[6:]
        prompt = STYLES.get(style, "anime style")
        await query.edit_message_text(f"Applying {style}...")
        try:
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            img = resize_and_crop(img, 512)
            buf = image_to_bytes(img)
            result = await asyncio.wait_for(asyncio.to_thread(
                client.image_to_image,
                image=buf, prompt=prompt + ", high quality, detailed",
                model="runwayml/stable-diffusion-v1-5",
                timeout=120,
                parameters={"negative_prompt": "ugly, blurry, low quality, deformed", "strength": 0.8, "guidance_scale": 7.5},
            ), timeout=150)
            if hasattr(result, "read"):
                result_img = Image.open(result)
            elif isinstance(result, bytes):
                result_img = Image.open(io.BytesIO(result))
            else:
                result_img = result
            result_img = result_img.convert("RGB")
            result_img = resize_and_crop(result_img, 512)
            out = image_to_bytes(result_img, "PNG")
            use_request(uid)
            await query.delete_message()
            await context.bot.send_photo(chat_id=chat_id, photo=out)
        except Exception as e:
            await query.edit_message_text(f"Error: {str(e)[:200]}")
        return

async def remove_background(update, context, is_callback=False):
    uid = update.from_user.id if is_callback else update.effective_user.id
    img_bytes = context.user_data.get("last_photo")
    if not img_bytes:
        msg = update if is_callback else update.message
        await msg.reply_text("Send photo again")
        return

    status = await (update.edit_message_text if is_callback else update.message.reply_text)("Removing background...")
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img = resize_and_crop(img, 512)
        buf = image_to_bytes(img)

        result = await asyncio.wait_for(asyncio.to_thread(
            client.image_segmentation, image=buf, model="briaai/RMBG-1.4",
        ), timeout=120)

        # result is list of masks, take first
        mask = result[0]
        mask = mask.convert("L")
        img_rgba = img.convert("RGBA")
        img_rgba.putalpha(mask)

        out = io.BytesIO()
        img_rgba.save(out, format="PNG")
        out.seek(0)

        use_request(uid)
        if is_callback:
            await status.delete()
            await update.message.reply_document(document=out, filename="no_bg.png")
        else:
            await status.delete()
            await update.message.reply_document(document=out, filename="no_bg.png")
    except Exception as e:
        logger.exception("bg error")
        await (status.edit_text if is_callback else status.edit_text)(f"Error: {str(e)[:200]}")

async def apply_style(update, context, style, is_callback=False):
    uid = update.from_user.id if is_callback else update.effective_user.id
    img_bytes = context.user_data.get("last_photo")
    if not img_bytes:
        return

    prompt = STYLES.get(style, "anime style")
    status = await (update.edit_message_text if is_callback else update.message.reply_text)(f"Applying {style} style...")

    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img = resize_and_crop(img, 512)
        buf = image_to_bytes(img)

        result = await asyncio.wait_for(asyncio.to_thread(
            client.image_to_image,
            image=buf,
            prompt=prompt + ", high quality, detailed",
            model="runwayml/stable-diffusion-v1-5",
            timeout=120,
            parameters={
                "negative_prompt": "ugly, blurry, low quality, deformed",
                "strength": 0.8,
                "guidance_scale": 7.5,
            },
        ), timeout=150)

        if hasattr(result, "read"):
            result_img = Image.open(result)
        elif isinstance(result, bytes):
            result_img = Image.open(io.BytesIO(result))
        else:
            result_img = result

        result_img = result_img.convert("RGB")
        result_img = resize_and_crop(result_img, 512)
        out = image_to_bytes(result_img, "PNG")

        use_request(uid)
        if is_callback:
            await status.delete()
            await update.message.reply_photo(photo=out)
        else:
            await status.delete()
            await update.message.reply_photo(photo=out)
    except Exception as e:
        logger.exception(f"style {style} error")
        await (status.edit_text if is_callback else status.edit_text)(f"Error: {str(e)[:200]}")

async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("Use: /generate <description>")
        return

    u = get_user(uid)
    if u["requests"] <= 0:
        await update.message.reply_text("No requests left. /buy")
        return

    status = await update.message.reply_text("Generating image...")
    try:
        result = await asyncio.wait_for(asyncio.to_thread(
            client.text_to_image,
            prompt=prompt,
            model="black-forest-labs/FLUX.1-dev",
            timeout=120,
        ), timeout=150)

        if hasattr(result, "read"):
            img = Image.open(result)
        elif isinstance(result, bytes):
            img = Image.open(io.BytesIO(result))
        else:
            img = result

        out = image_to_bytes(img, "PNG")
        use_request(uid)
        await status.delete()
        await update.message.reply_photo(photo=out)
    except Exception as e:
        logger.exception("generate error")
        await status.edit_text(f"Error: {str(e)[:200]}")

async def process_bg(update, context, uid, img_bytes):
    status = await update.message.reply_text("Removing background...")
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img = resize_and_crop(img, 512)
        buf = image_to_bytes(img)
        result = await asyncio.wait_for(asyncio.to_thread(
            client.image_segmentation, image=buf, model="briaai/RMBG-1.4",
        ), timeout=120)
        mask = result[0].convert("L")
        img_rgba = img.convert("RGBA")
        img_rgba.putalpha(mask)
        out = io.BytesIO()
        img_rgba.save(out, format="PNG")
        out.seek(0)
        use_request(uid)
        await status.delete()
        await update.message.reply_document(document=out, filename="no_bg.png")
    except Exception as e:
        await status.edit_text(f"Error: {str(e)[:200]}")

async def process_style(update, context, style, uid, img_bytes):
    prompt = STYLES.get(style, "anime style")
    status = await update.message.reply_text(f"Applying {style}...")
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img = resize_and_crop(img, 512)
        buf = image_to_bytes(img)
        result = await asyncio.wait_for(asyncio.to_thread(
            client.image_to_image,
            image=buf, prompt=prompt + ", high quality, detailed",
            model="runwayml/stable-diffusion-v1-5",
timeout=120,
                parameters={"negative_prompt": "ugly, blurry, low quality, deformed", "strength": 0.8, "guidance_scale": 7.5},
        ), timeout=150)
        if hasattr(result, "read"):
            result_img = Image.open(result)
        elif isinstance(result, bytes):
            result_img = Image.open(io.BytesIO(result))
        else:
            result_img = result
        result_img = result_img.convert("RGB")
        result_img = resize_and_crop(result_img, 512)
        out = image_to_bytes(result_img, "PNG")
        use_request(uid)
        await status.delete()
        await update.message.reply_photo(photo=out)
    except Exception as e:
        await status.edit_text(f"Error: {str(e)[:200]}")

# ---------- PAYMENT ----------

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [f"Buy {PAID_AMOUNT} requests for ${PRICE_USDT}:"]
    if STARS_TOKEN:
        lines.append(f" /pay - {PRICE_STARS} Stars")
    if USDT_WALLET:
        lines.append(f" Send USDT (TRC-20) to:")
        lines.append(f" `{USDT_WALLET}`")
        lines.append(f" Then send TXID here")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def pay_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not STARS_TOKEN:
        await update.message.reply_text("Stars payment not configured")
        return
    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title="AI Studio - 100 requests",
        description=f"Get {PAID_AMOUNT} AI requests",
        provider_token=STARS_TOKEN,
        currency="XTR",
        prices=[telegram.LabeledPrice("100 requests", PRICE_STARS)],
        payload=f"stars_{update.effective_user.id}",
    )

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    if query.invoice_payload.startswith("stars_"):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Invalid")

async def success_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    payload = update.message.successful_payment.invoice_payload
    if payload.startswith("stars_"):
        add_requests(uid, PAID_AMOUNT, f"stars_{payload}")
        await update.message.reply_text(f"Payment confirmed! +{PAID_AMOUNT} requests")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    await update.message.reply_text(f"Requests: {u['requests']}\nTotal used: {u['total']}")

async def handle_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    txid = update.message.text.strip()
    if not USDT_WALLET:
        return

    status = await update.message.reply_text("Checking transaction...")
    try:
        import urllib.request, json
        url = f"https://api.trongrid.io/v1/transactions/{txid}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())["data"]
        if not data:
            await status.edit_text("Transaction not found")
            return

        contract = data[0].get("raw_data", {}).get("contract", [{}])[0].get("parameter", {}).get("value", {})
        to = contract.get("to_address", "")
        amount = contract.get("amount", 0) / 1_000_000
        target = USDT_WALLET.replace("T", "41")

        if to == target and amount >= PRICE_USDT:
            add_requests(uid, PAID_AMOUNT, txid)
            await status.edit_text(f"Confirmed! +{PAID_AMOUNT} requests")
        else:
            await status.edit_text(f"Wrong wallet or amount ({amount} USDT)")
    except Exception as e:
        await status.edit_text(f"Check error: {str(e)[:200]}")

# ---------- MAIN ----------

def main():
    app = Application.builder().token(BOT_TOKEN).request(
        telegram.request.HTTPXRequest(connect_timeout=30, read_timeout=30)
    ).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("generate", generate))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("pay", pay_stars))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, success_payment))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))

    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu_"))
    app.add_handler(CallbackQueryHandler(menu_start_callback, pattern="^menu_start$"))
    app.add_handler(CallbackQueryHandler(photo_action_callback, pattern="^act_"))
    app.add_handler(CallbackQueryHandler(photo_action_callback, pattern="^style_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
