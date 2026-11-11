import os
import time
import aiohttp
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = "9160352065:AAEEZDlCJ5qNW1XjheHJlzhXayiAokA_FU8"
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

async def check_dm(update: Update) -> bool:
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_text(
            "❌ <b>ДАСТРАСӢ МАҲДУД АСТ!</b>\n\n"
            "🛡️ Ин бот танҳо дар гурӯҳҳо кор мекунад.\n"
            "🔑 Барои гирифтани Ключ ба <b>@parnetka</b> муроҷиат кунед!",
            parse_mode="HTML"
        )
        return True
    return False

# 1. /like (100 лайк бо тарҳи зебо)
async def like_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_dm(update): return
    if not is_allowed(update.effective_chat): return

    if not context.args or len(context.args) != 1:
        await update.message.reply_text(
            "⚠️ <b>ТАРЗИ ИСТИФОДАБАРӢ:</b>\n\n"
            "👉 <code>/like 14610622738</code>",
            parse_mode="HTML"
        )
        return

    uid = context.args[0]
    user_id = update.effective_user.id
    current_time = time.time()

    if user_id in user_cooldowns:
        elapsed = current_time - user_cooldowns[user_id]
        if elapsed < COOLDOWN_TIME:
            rem_h = int((COOLDOWN_TIME - elapsed) / 3600)
            rem_m = int(((COOLDOWN_TIME - elapsed) % 3600) / 60)
            await update.message.reply_text(
                f"⏳ <b>ЛИНЧИ СОАТӢ!</b>\n\n"
                f"Шумо аллакай лайк гирифтаед.\n"
                f"⏰ Бори дигар пас аз: <b>{rem_h} соат ва {rem_m} дақиқа</b>",
                parse_mode="HTML"
            )
            return

    progress = await update.message.reply_text("🔄 <b>Дар ҳоли пайвастшавӣ ба сервери Free Fire...</b>", parse_mode="HTML")
    api_url = f"{BASE_API_URL}/like?api_key={LIKES_API_KEY}&uid={uid}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as resp:
                raw = await resp.text()
        data = __import__("json").loads(raw)
        
        if not isinstance(data, dict) or data.get("success") is not True:
            await progress.edit_text(f"❌ <b>Хатогӣ дар сервер!</b>\n\n<code>{raw[:3500]}</code>", parse_mode="HTML")
            return

        details = data.get("data", {})
        likes_given = int(details.get('LikesGivenByAPI', 0))

        if likes_given <= 0:
            await progress.edit_text(
                "❌ <b>Лайк илова нашуд!</b>\n\n"
                f"👤 <b>Бозигар:</b> <code>{details.get('PlayerNickname', 'N/A')}</code>\n"
                f"🆔 <b>UID:</b> <code>{details.get('UID', uid)}</code>\n\n"
                "⚠️ <i>Эзоҳ: Аккаунт лимит дорад ё ном хато аст.</i>",
                parse_mode="HTML"
            )
            return

        user_cooldowns[user_id] = time.time()
        msg = (
            f"🎉 <b>ЛАЙКҲО БОМУВАФФАҚИЯТ РАФТАНД!</b> 🚀\n\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Ном:</b> <code>{details.get('PlayerNickname', 'N/A')}</code>\n"
            f"🆔 <b>UID:</b> <code>{details.get('UID', uid)}</code>\n"
            f"📊 <b>Қабл аз лайк:</b> <code>{details.get('LikesbeforeCommand', 'N/A')}</code>\n"
            f"🔥 <b>Илова шуд:</b> <code>+{likes_given}</code>\n"
            f"📈 <b>Ҳоло:</b> <code>{details.get('LikesafterCommand', 'N/A')}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"✨ <i>Бозӣ бо @parnetka лаззатбахш аст!</i>"
        )
        await progress.edit_text(msg, parse_mode="HTML")
    except Exception:
        await progress.edit_text("❌ <b>Хатогии ногаҳонии сервер!</b>", parse_mode="HTML")

# 2. /remain (Баланс)
async def remain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_dm(update): return
    if not is_allowed(update.effective_chat): return

    api_url = f"{BASE_API_URL}/status?api_key={LIKES_API_KEY}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as resp:
                raw = await resp.text()
        await update.message.reply_text(f"📊 <b>Омори панел / Баланс:</b>\n\n<code>{raw}</code>", parse_mode="HTML")
    except Exception:
        await update.message.reply_text("❌ Хатогӣ ҳангоми гирифтани маълумот.")

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    global ptb_application
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN missing!")

    ptb_application = ApplicationBuilder().token(BOT_TOKEN).build()
    ptb_application.add_handler(CommandHandler("like", like_command))
    ptb_application.add_handler(CommandHandler("remain", remain_command))
    ptb_application.add_handler(CommandHandler("status", remain_command))
    ptb_application.add_handler(CommandHandler("me", remain_command))

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




