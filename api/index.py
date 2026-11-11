import os
import time
import httpx
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN", "9160352065:AAEEZDlCJ5qNW1XjheHJlzhXayiAokA_FU8")
API_KEY = os.getenv("API_KEY", "Parnetka_43455514")
API_URL = "https://fflikes.ucbot.net"
FIREBASE_URL = "https://api-key-mgmt-default-rtdb.firebaseio.com/telegram_bot_cooldowns"

ALLOWED_GROUPS = [-1004354930364]
COOLDOWN_SECONDS = 86400  # 24 соат

async def get_user_cooldown(user_id: int) -> float:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{FIREBASE_URL}/{user_id}.json")
            if res.status_code == 200 and res.json():
                return float(res.json())
    except Exception:
        pass
    return 0.0

async def set_user_cooldown(user_id: int, timestamp: float):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.put(f"{FIREBASE_URL}/{user_id}.json", json=timestamp)
    except Exception:
        pass

def format_remaining_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours > 0:
        return f"<b>{hours}</b> соат ва <b>{minutes}</b> дақиқа"
    return f"<b>{minutes}</b> дақиқа"

async def like_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if chat_id not in ALLOWED_GROUPS:
        return

    if not context.args:
        await update.message.reply_html(
            "⚠️ <b>ТАРЗИ ИСТИФОДАБАРӢ:</b>\n\n"
            "👉 <code>/like 14610622738</code>"
        )
        return

    uid = context.args[0].strip()
    if not uid.isdigit() or not (8 <= len(uid) <= 12):
        await update.message.reply_html(
            "❌ <b>ID-И ХАТО!</b>\n\n"
            "Free Fire UID бояд танҳо аз рақамҳо (аз 8 то 12 рақам) иборат бошад."
        )
        return

    last_time = await get_user_cooldown(user_id)
    now = time.time()
    elapsed = now - last_time

    if elapsed < COOLDOWN_SECONDS:
        rem = COOLDOWN_SECONDS - elapsed
        time_str = format_remaining_time(rem)
        await update.message.reply_html(
            f"⏳ <b>ЛИМИТИ 24-СОАТӢ!</b>\n\n"
            f"Шумо аллакай лайк гирифтаед.\n"
            f"⏰ Бори дигар пас аз: {time_str}"
        )
        return

    progress = await update.message.reply_html("⏳ <i>Дархост коркард шуда истодааст, лутфан интизор шавед...</i>")

    try:
        payload = {"key": API_KEY, "uid": uid}
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(API_URL, json=payload)
            
            try:
                data = res.json()
            except Exception:
                # Ҷавоби аслии API-ро нишон медиҳем, то сабабаш аниқ шавад:
                short_body = res.text[:150] if res.text else "Ҷавоби холӣ"
                await progress.edit_text(
                    f"❌ <b>Хатогӣ дар сайти API!</b>\n"
                    f"Коди статус: <code>{res.status_code}</code>\n"
                    f"Ҷавоби сайт: <code>{short_body}</code>",
                    parse_mode="HTML"
                )
                return

        if res.status_code != 200 or not data.get("status"):
            msg = data.get("message") or data.get("error") or "Хатогии номаълум"
            await progress.edit_text(f"❌ <b>Хатогӣ дар API:</b> {msg}", parse_mode="HTML")
            return

        details = data.get("details", {})
        likes_given = details.get("LikesGivenByAPI", 0)

        if likes_given <= 0:
            await progress.edit_text(
                "❌ <b>Лайк илова нашуд!</b>\n\n"
                f"👤 <b>Бозигар:</b> <code>{details.get('PlayerNickname', 'N/A')}</code>\n"
                f"🆔 <b>UID:</b> <code>{details.get('UID', uid)}</code>\n\n"
                "⚠️ <i>Аккаунт дар худи бозӣ лимити 24-соата дорад.</i>",
                parse_mode="HTML"
            )
            return

        await set_user_cooldown(user_id, now)

        response_text = (
            "🎉 <b>ЛАЙКҲО БОМУВАФФАҚИЯТ РАФТАНД!</b> 🚀\n"
            "___________________________________\n\n"
            f"👤 <b>Ном:</b> <code>{details.get('PlayerNickname', 'N/A')}</code>\n"
            f"🆔 <b>UID:</b> <code>{details.get('UID', uid)}</code>\n"
            f"📊 <b>Қабл аз лайк:</b> {details.get('LikesbeforeCommand', 'N/A')}\n"
            f"🔥 <b>Илова шуд:</b> +{likes_given}\n"
            f"📈 <b>Ҳоло:</b> {details.get('LikesafterCommand', 'N/A')}\n"
            "___________________________________\n\n"
            "✨ <i>Бозӣ бо @parnetka лаззатбахш аст!</i>"
        )
        await progress.edit_text(response_text, parse_mode="HTML")

    except Exception as e:
        await progress.edit_text(f"❌ <b>Хато дар сервер:</b> {str(e)}", parse_mode="HTML")

ptb_application = None

@app.on_event("startup")
async def startup_event():
    global ptb_application
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN missing!")

    ptb_application = ApplicationBuilder().token(BOT_TOKEN).build()
    ptb_application.add_handler(CommandHandler("like", like_command))

    await ptb_application.initialize()
    await ptb_application.start()

@app.post("/api/webhook")
async def telegram_webhook(request: Request):
    if ptb_application:
        data = await request.json()
        update = Update.de_json(data, ptb_application.bot)
        await ptb_application.process_update(update)
    return {"status": "ok"}


