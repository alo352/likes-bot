import os
import time
import aiohttp
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = "9160352065:AAHRleNu3Kuz6kJDZJxcaOAUlG7Xv_20q6Y"
ALLOWED_GROUPS = [-5494553901]

# Калиди API ва суроға
LIKES_API_KEY = "Parnetka_43455514"
BASE_API_URL = "https://fflikes.ucbot.net"

# Луғат барои нигоҳ доштани лимити 24-соатаи корбарон
user_cooldowns = {}
COOLDOWN_TIME = 86400  # 24 соат ба сония

import logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

ptb_application = None

def is_allowed(chat) -> bool:
    return chat.type in ["group", "supergroup"]


async def like_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if not is_allowed(chat):
        return

    # Танҳо UID талаб карда мешавад
    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "⚠️ **Истифодабарӣ:** `/like {uid}`\n"
            "📌 **Намуна:** `/like 14610622738`",
            parse_mode="Markdown"
        )
        return

    user_id = user.id
    current_time = time.time()

    # Санҷиши лимити 24 соат
    if user_id in user_cooldowns:
        elapsed_time = current_time - user_cooldowns[user_id]
        if elapsed_time < COOLDOWN_TIME:
            remaining_hours = int((COOLDOWN_TIME - elapsed_time) / 3600)
            remaining_mins = int(((COOLDOWN_TIME - elapsed_time) % 3600) / 60)
            await update.message.reply_text(
                f"⏳ **Шумо аллакай лайк гирифтаед!**\n"
                f"Лутфан пас аз **{remaining_hours} соат ва {remaining_mins} дақиқа** бори дигар кӯшиш кунед.",
                parse_mode="Markdown"
            )
            return

    uid = context.args[0]

    progress = await update.message.reply_text("📤 Фиристодани лайкҳо...")

    # Дархост ба API фақат бо калид ва UID
    api_url = f"{BASE_API_URL}/like?api_key={LIKES_API_KEY}&uid={uid}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as resp:
                raw_response = await resp.text()

        try:
            data = __import__("json").loads(raw_response)
        except ValueError:
            data = None

        if not isinstance(data, dict) or data.get("success") is not True:
            error_message = (
                "❌ **Хатогӣ дар API**\n\n"
                f"**Ҷавоби сервер:**\n`{raw_response[:3500]}`"
            )
            await progress.edit_text(error_message, parse_mode="Markdown")
            return

        # Дархост бомуваффақият гузашт, вақтро барои лимити 24 соат сабт мекунем
        user_cooldowns[user_id] = time.time()

        details = data.get("data", {})

        message = (
            f"✅ **Лайкҳо бомуваффақият фиристода шуданд!**\n\n"
            f"**👤 Ном:** `{details.get('PlayerNickname', 'N/A')}`\n"
            f"**💬 UID:** `{details.get('UID', uid)}`\n"
            f"**📊 Пеш аз лайк:** `{details.get('LikesbeforeCommand', 'N/A')}`\n"
            f"**👍 Лайкҳои иловашуда:** `{details.get('LikesGivenByAPI', 'N/A')}`\n"
            f"**📈 Баъд аз лайк:** `{details.get('LikesafterCommand', 'N/A')}`"
        )

        await progress.edit_text(message, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"/like error: {e}")
        await progress.edit_text("❌ Хатогии сервер. Лутфан баъдтар кӯшиш кунед.")


async def remain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    if not is_allowed(chat):
        return

    api_url = f"{BASE_API_URL}/status?api_key={LIKES_API_KEY}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as resp:
                raw = await resp.text()

        await update.message.reply_text(f"📊 **Ҳолати баланс/панел:**\n`{raw}`", parse_mode="Markdown")

    except Exception as e:
        logging.error(f"/remain error: {e}")
        await update.message.reply_text("❌ Хатогӣ ҳангоми гирифтани маълумот.")

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    global ptb_application

    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN env variable is missing!")

    ptb_application = ApplicationBuilder().token(BOT_TOKEN).build()
    ptb_application.add_handler(CommandHandler("like", like_command))
    ptb_application.add_handler(CommandHandler("remain", remain_command))

    await ptb_application.initialize()
    await ptb_application.start()

@app.post("/api/webhook")
async def telegram_webhook(request: Request):
    req = await request.json()
    update = Update.de_json(req, ptb_application.bot)
    await ptb_application.process_update(update)
    return {"status": "ok"}

@app.get("/")
async def health_check():
    return {"status": "Bot is running"}

