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

ALLOWED_GROUPS = [-1004354930364]
COOLDOWN_TIME = 86400  # 24 соат ба сония
CACHE_FILE = "/tmp/ff_cooldowns.json"

# Хотираи дохилии сервер (ҳам барои корбар ва ҳам барои UID)
memory_users = {}
memory_uids = {}
ptb_application = None

def load_cache():
    global memory_users, memory_uids
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r") as f:
                data = json.load(f)
                memory_users = data.get("users", {})
                memory_uids = data.get("uids", {})
    except Exception:
        pass

def save_cache():
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump({"users": memory_users, "uids": memory_uids}, f)
    except Exception:
        pass

def format_remaining(seconds_left: float) -> str:
    hours = int(seconds_left // 3600)
    minutes = int((seconds_left % 3600) // 60)
    if hours > 0:
        return f"{hours} соату {minutes} дақиқа"
    return f"{minutes} дақиқа"

def is_allowed(chat) -> bool:
    if chat.type == "private":
        return True
    return chat.id in ALLOWED_GROUPS

async def like_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if not is_allowed(chat):
        return

    load_cache()

    if not context.args or len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text(
            "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
            "│  ⚠️ **ТАРЗИ ИСТИФОДАБАРӢ**  │\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            "👉 Фармон: `/like {UID}`\n"
            "📌 Намуна: `/like 13961132846`",
            parse_mode="Markdown"
        )
        return

    uid = context.args[0].strip()
    user_id = str(user.id)
    now = time.time()

    # 1. Санҷиши лимити 24-соатаи худи корбар
    if user_id in memory_users:
        elapsed = now - memory_users[user_id]
        if elapsed < COOLDOWN_TIME:
            rem = format_remaining(COOLDOWN_TIME - elapsed)
            await update.message.reply_text(
                "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
                "│    ⏳ **ЛИМИТИ 24-СОАТА**    │\n"
                "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
                f"👤 **Корбар:** {user.first_name}\n"
                "🚫 Шумо имрӯз аллакай лайк гирифтаед!\n\n"
                f"🕒 **Боқимонда:** `{rem}`\n"
                "💡 *Пас аз гузаштани вақт дубора кӯшиш кунед.*",
                parse_mode="Markdown"
            )
            return

    # 2. Санҷиши лимити 24-соатаи худи UID
    if uid in memory_uids:
        elapsed_uid = now - memory_uids[uid]
        if elapsed_uid < COOLDOWN_TIME:
            rem = format_remaining(COOLDOWN_TIME - elapsed_uid)
            await update.message.reply_text(
                "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
                "│   ⚠️ **АККАУНТ ДАР ЛИМИТ**   │\n"
                "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
                f"🆔 **UID:** `{uid}`\n"
                "🚫 Ба ин аккаунт имрӯз аллакай лайк фиристода шудааст!\n\n"
                f"🕒 **Боқимонда:** `{rem}`",
                parse_mode="Markdown"
            )
            return

    # Фавран лимитро қулф мекунем (то дубора зада нашавад)
    memory_users[user_id] = now
    memory_uids[uid] = now
    save_cache()

    progress = await update.message.reply_text("⚡ **Дар ҳоли фиристодани лайкҳо...**", parse_mode="Markdown")
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
            err_msg = str(data.get("error", "")) if isinstance(data, dict) else raw_response[:200]
            
            # Агар худи бозӣ гӯяд, ки имрӯз 200 лайк гирифтааст, лимити 24-соатаро нигоҳ медорем
            if "maximum" in err_msg.lower() or "already" in err_msg.lower():
                await progress.edit_text(
                    "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
                    "│   ⏳ **ЛИМИТИ РӮЗОНА ПУР**   │\n"
                    "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
                    f"🆔 **UID:** `{uid}`\n"
                    "⚠️ Ин аккаунт имрӯз аллакай лимити максималии лайкро гирифтааст!\n\n"
                    "🕒 **Лутфан пас аз 24 соат кӯшиш кунед.**",
                    parse_mode="Markdown"
                )
                return

            # Агар хатогии техникӣ бошад, лимитро бармегардонем
            memory_users.pop(user_id, None)
            memory_uids.pop(uid, None)
            save_cache()

            await progress.edit_text(
                "❌ **Хатогӣ ҳангоми иҷро!**\n"
                f"📝 *Сабаб:* `{err_msg}`",
                parse_mode="Markdown"
            )
            return

        details = data.get("data", {})
        likes_given = details.get("LikesGivenByAPI", 0)
        try:
            likes_given_int = int(likes_given)
        except (ValueError, TypeError):
            likes_given_int = 0

        if likes_given_int <= 0:
            memory_users.pop(user_id, None)
            memory_uids.pop(uid, None)
            save_cache()

            await progress.edit_text(
                "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
                "│     ❌ **ЛАЙК НАРАФТ**     │\n"
                "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
                f"👤 **Ном:** `{details.get('PlayerNickname', 'N/A')}`\n"
                f"🆔 **UID:** `{details.get('UID', uid)}`\n"
                f"📊 **Пеш аз лайк:** `{details.get('LikesbeforeCommand', 'N/A')}`\n"
                f"👍 **Илова шуд:** `0`\n\n"
                "⚠️ *Аккаунт лимити рӯзона дорад ё ном рамзҳои махсус дорад.*",
                parse_mode="Markdown"
            )
            return

        # Паёми зебои муваффақият
        message = (
            "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
            "│  ✅ **ЛАЙКҲО ФИРИСТОДА ШУДАНД!** │\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            f"👤 **Ном:** `{details.get('PlayerNickname', 'N/A')}`\n"
            f"🆔 **UID:** `{details.get('UID', uid)}`\n"
            "──────────────────────\n"
            f"📉 **Пеш аз лайк:** `{details.get('LikesbeforeCommand', 'N/A')}`\n"
            f"🔥 **Лайкҳои иловашуда:** `+{likes_given}`\n"
            f"📈 **Баъд аз лайк:** `{details.get('LikesafterCommand', 'N/A')}`\n"
            "──────────────────────\n"
            "⏳ *Дархости навбатӣ пас аз 24 соат!*"
        )

        await progress.edit_text(message, parse_mode="Markdown")

    except Exception as e:
        memory_users.pop(user_id, None)
        memory_uids.pop(uid, None)
        save_cache()
        logging.error(f"/like error: {e}")
        await progress.edit_text("❌ **Хатогии сервер!** Лутфан каме баъдтар кӯшиш кунед.", parse_mode="Markdown")


async def remain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not is_allowed(chat):
        return

    api_url = f"{BASE_API_URL}/status?api_key={LIKES_API_KEY}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=10) as resp:
                raw = await resp.text()
        await update.message.reply_text(
            "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
            "│   📊 **ҲОЛАТИ БАЛАНС/API**   │\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            f"`{raw}`",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"/remain error: {e}")
        await update.message.reply_text("❌ Хатогӣ ҳангоми гирифтани маълумот.")

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    global ptb_application
    load_cache()
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

