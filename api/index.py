import os
import time
import json
import logging
import aiohttp
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "9160352065:AAEEZDlCJ5qNW1XjheHJlzhXayiAokA_FU8")
LIKES_API_KEY = os.getenv("API_KEY", "Parnetka_43455514")
BASE_API_URL = "https://fflikes.ucbot.net"
FIREBASE_URL = "https://api-key-mgmt-default-rtdb.firebaseio.com/telegram_bot_cooldowns"

ALLOWED_GROUPS = [-1004354930364]
COOLDOWN_TIME = 86400  # 24 соат ба сония

ptb_application = None

def is_allowed(chat) -> bool:
    if chat.type == "private":
        return True
    return chat.id in ALLOWED_GROUPS

async def get_user_cooldown(user_id: int) -> float:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{FIREBASE_URL}/{user_id}.json", timeout=5) as res:
                if res.status == 200:
                    data = await res.json()
                    if data:
                        return float(data)
    except Exception:
        pass
    return 0.0

async def set_user_cooldown(user_id: int, timestamp: float):
    try:
        async with aiohttp.ClientSession() as session:
            await session.put(f"{FIREBASE_URL}/{user_id}.json", json=timestamp, timeout=5)
    except Exception:
        pass

async def like_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if not is_allowed(chat):
        return

    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "⚠️ **Истифодабарӣ:** `/like {uid}`\n"
            "📌 **Намуна:** `/like 14610622738`",
            parse_mode="Markdown"
        )
        return

    uid = context.args[0].strip()
    user_id = user.id
    current_time = time.time()

    # Санҷиши лимити 24 соат аз Firebase
    last_time = await get_user_cooldown(user_id)
    if last_time > 0:
        elapsed_time = current_time - last_time
        if elapsed_time < COOLDOWN_TIME:
            remaining_hours = int((COOLDOWN_TIME - elapsed_time) / 3600)
            remaining_mins = int(((COOLDOWN_TIME - elapsed_time) % 3600) / 60)
            await update.message.reply_text(
                f"⏳ **Шумо аллакай лайк гирифтаед!**\n"
                f"Лутфан пас аз **{remaining_hours} соат ва {remaining_mins} дақиқа** бори дигар кӯшиш кунед.",
                parse_mode="Markdown"
            )
            return

    progress = await update.message.reply_text("📤 Фиристодани лайкҳо...")

    # Суроғаи аслии API бо GET
    api_url = f"{BASE_API_URL}/like?api_key={LIKES_API_KEY}&uid={uid}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=15) as resp:
                raw_response = await resp.text()

        try:
            data = json.loads(raw_response)
        except ValueError:
            data = None

        if not isinstance(data, dict) or data.get("success") is not True:
            error_message = (
                "❌ **Хатогӣ дар API**\n\n"
                f"**Ҷавоби сервер:**\n`{raw_response[:3500]}`"
            )
            await progress.edit_text(error_message, parse_mode="Markdown")
            return

        details = data.get("data", {})
        likes_given = details.get("LikesGivenByAPI", 0)
        try:
            likes_given_int = int(likes_given)
        except (ValueError, TypeError):
            likes_given_int = 0

        if likes_given_int <= 0:
            message = (
                f"❌ **Лайк нарафт! (Хатогӣ)**\n\n"
                f"**👤 Ном:** `{details.get('PlayerNickname', 'N/A')}`\n"
                f"**💬 UID:** `{details.get('UID', uid)}`\n"
                f"**📊 Пеш аз лайк:** `{details.get('LikesbeforeCommand', 'N/A')}`\n"
                f"**👍 Лайкҳои иловашуда:** `0`\n"
                f"**📈 Баъд аз лайк:** `{details.get('LikesafterCommand', 'N/A')}`\n\n"
                f"⚠️ *Эзоҳ: Мумкин ном символҳои махсус (монанди &) дорад ё аккаунт лимит дорад!*"
            )
            await progress.edit_text(message, parse_mode="Markdown")
            return

        # Сабти вақт дар Firebase пас аз муваффақият
        await set_user_cooldown(user_id, time.time())

        message = (
            f"✅ **Лайкҳо бомуваффақият фиристода шуданд!**\n\n"
            f"**👤 Ном:** `{details.get('PlayerNickname', 'N/A')}`\n"
            f"**💬 UID:** `{details.get('UID', uid)}`\n"
            f"**📊 Пеш аз лайк:** `{details.get('LikesbeforeCommand', 'N/A')}`\n"
            f"**👍 Лайкҳои иловашуда:** `{likes_given}`\n"
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
            async with session.get(api_url, timeout=10) as resp:
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
    if ptb_application:
        req = await request.json()
        update = Update.de_json(req, ptb_application.bot)
        await ptb_application.process_update(update)
    return {"status": "ok"}

