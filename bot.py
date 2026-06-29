import os, io, json, asyncio, logging, time, hashlib, urllib.request, urllib.error
from PIL import Image
import telegram, telegram.request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from huggingface_hub import InferenceClient
import base64

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
HF_TOKEN = os.environ.get("HF_TOKEN")
USDT_WALLET = os.environ.get("USDT_WALLET", "TTqNPFMYe4jsYTQZmNmB3ZPPKFW2GQm9Re")

FREE_LIMIT = 3
PAID_AMOUNT = 50
USDT_PRICE = 5
USDT_WALLET = USDT_WALLET

client = InferenceClient(token=HF_TOKEN, base_url="https://router.huggingface.co/hf-inference/v1")

DATA_FILE = "/app/users_data.json"

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
        data[uid] = {"requests": FREE_LIMIT, "total": 0, "paid_tx": []}
        save_data(data)
    return data[uid]

def use_request(user_id):
    data = load_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"requests": FREE_LIMIT, "total": 0, "paid_tx": []}
    if data[uid]["requests"] > 0:
        data[uid]["requests"] -= 1
        data[uid]["total"] += 1
        save_data(data)
        return True
    return False

# ---------- HANDLERS ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = get_user(uid)
    await update.message.reply_text(
        f"AI Assistant Bot\n\n"
        f"Что я умею:\n"
        f"- Отправь текст — я отвечу\n"
        f"- Отправь фото — опишу его\n"
        f"- /generate <prompt> — создам изображение\n\n"
        f"У тебя осталось: {u['requests']} запросов\n"
        f"/buy — купить ещё (50 запросов за {USDT_PRICE} USDT)"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    if text.startswith("/"):
        return

    # Check if it's a TXID (64 hex chars)
    if len(text) == 64 and all(c in "0123456789abcdefABCDEF" for c in text):
        await handle_txid(update, context)
        return

    u = get_user(uid)
    if u["requests"] <= 0:
        await update.message.reply_text(f"Лимит закончен. Купи ещё: /buy")
        return

    status = await update.message.reply_text("Думаю...")
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                client.chat.completions.create,
                model="meta-llama/Llama-3.2-3B-Instruct",
                messages=[{"role": "user", "content": text}],
                max_tokens=800,
            ),
            timeout=60,
        )
        reply = result.choices[0].message.content
        use_request(uid)
        await status.edit_text(reply)
    except Exception as e:
        logger.exception("text error")
        await status.edit_text(f"Ошибка: {str(e)[:200]}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = get_user(uid)
    if u["requests"] <= 0:
        await update.message.reply_text(f"Лимит закончен. Купи ещё: /buy")
        return

    status = await update.message.reply_text("Анализирую фото...")
    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        img_bytes = await file.download_as_bytearray()

        img_b64 = base64_encode(img_bytes)
        result = await asyncio.wait_for(
            asyncio.to_thread(
                client.chat.completions.create,
                model="meta-llama/Llama-3.2-11B-Vision-Instruct",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Опиши это изображение подробно на русском"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                    ]
                }],
                max_tokens=500,
            ),
            timeout=90,
        )
        reply = result.choices[0].message.content
        use_request(uid)
        await status.edit_text(reply)
    except Exception as e:
        logger.exception("photo error")
        await status.edit_text(f"Ошибка: {str(e)[:200]}")

async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("Напиши промпт: /generate <описание>")
        return

    u = get_user(uid)
    if u["requests"] <= 0:
        await update.message.reply_text(f"Лимит закончен. Купи ещё: /buy")
        return

    status = await update.message.reply_text("Генерирую изображение...")
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                client.text_to_image,
                prompt=prompt,
                model="black-forest-labs/FLUX.1-dev",
                provider="hf-inference",
                timeout=120,
            ),
            timeout=150,
        )
        if isinstance(result, bytes):
            img = Image.open(io.BytesIO(result))
        elif hasattr(result, "read"):
            img = Image.open(result)
        else:
            img = result

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        use_request(uid)
        await status.delete()
        await update.message.reply_photo(photo=buf)
    except Exception as e:
        logger.exception("generate error")
        await status.edit_text(f"Ошибка: {str(e)[:200]}")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Купить 50 запросов за {USDT_PRICE} USDT (TRC-20):\n\n"
        f"1. Переведи {USDT_PRICE} USDT на кошелёк:\n"
        f"`{USDT_WALLET}`\n"
        f"2. После оплаты отправь TXID (хэш транзакции) сюда\n"
        f"3. Бот проверит и начислит запросы\n\n"
        f"Команда /balance — проверить баланс",
        parse_mode="Markdown"
    )

async def handle_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    txid = update.message.text.strip()

    if len(txid) < 30 or len(txid) > 100:
        return

    status = await update.message.reply_text("Проверяю транзакцию...")
    try:
        # Check transaction on TronGrid
        url = f"https://api.trongrid.io/v1/transactions/{txid}"
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())

        # Verify it's a transfer to our wallet with correct amount
        tx_data = data.get("data", [])
        if not tx_data:
            await status.edit_text("Транзакция не найдена. Проверь TXID.")
            return

        tx = tx_data[0]
        # Check raw_data.contract[0].parameter.value
        contract = tx.get("raw_data", {}).get("contract", [])
        if not contract:
            await status.edit_text("Не удалось проверить транзакцию.")
            return

        val = contract[0].get("parameter", {}).get("value", {})
        to_addr = val.get("to_address")
        amount = val.get("amount", 0)

        # Convert to USDT (divisible by 1e6)
        usdt_amount = amount / 1_000_000 if amount > 1_000_000 else 0

        # Check destination
        if to_addr and to_addr == USDT_WALLET.replace("T", "41") and usdt_amount >= USDT_PRICE:
            data = load_data()
            uid_str = str(uid)
            if uid_str not in data:
                data[uid_str] = {"requests": FREE_LIMIT, "total": 0, "paid_tx": []}
            data[uid_str]["requests"] += PAID_AMOUNT
            data[uid_str]["paid_tx"].append(txid)
            save_data(data)
            await status.edit_text(f"Оплата подтверждена! +{PAID_AMOUNT} запросов.")
        else:
            await status.edit_text(f"Транзакция не подходит (не тот кошелёк или сумма). Сумма: {usdt_amount} USDT")
    except Exception as e:
        logger.exception("tx check error")
        await status.edit_text(f"Ошибка проверки: {str(e)[:200]}")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    await update.message.reply_text(f"Осталось запросов: {u['requests']}")

# ---------- UTILS ----------

def base64_encode(data):
    return base64.b64encode(data).decode()

# ---------- MAIN ----------

def main():
    app = Application.builder().token(BOT_TOKEN).request(
        telegram.request.HTTPXRequest(connect_timeout=30, read_timeout=30)
    ).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("generate", generate))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("balance", balance))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
