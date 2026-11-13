import os
import time
import json
import asyncio
import logging
import aiohttp
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "9160352065:AAEEZDlCJ5qNW1XjheHJlzhXayiAokA_FU8")
LIKES_API_KEY = os.getenv("API_KEY", "Parnetka_43455514")
BASE_API_URL = "https://fflikes.ucbot.net"

# ID-и шахсии шумо (@parnetka)
ADMIN_IDS = [6410886684]  
ALLOWED_GROUPS = [-1004354930364]

COOLDOWN_TIME = 86400  # 24 соат ба сония
CACHE_FILE = "/tmp/ff_cooldowns.json"

memory_users = {}
memory_uids = {}
ptb_application = None

DEVELOPER_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("👨‍💻 АДМИН / ХАРИДИ VIP", url="https://t.me/parnetka")]
])

VIP_PROMO_FOOTER = (
    "\n\n💎 **МЕХОҲЕД 400+ VIP LIKE ХАРЕД?**\n"
    "⚡️ *Бе интизории 24 соат ва бо суръати баланд!*\n"
    "📩 **Ба админ муроҷиат кунед:** 👉 **@parnetka**"
)

ACCESS_DENIED_TEXT = (
    "🚫 **ДАСТРАСӢ МАҲДУД АСТ!**\n"
    "━━━━━━━━━━━\n"
    "🔒 *Ин бот танҳо дар гурӯҳҳои расмии мо ва барои мизоҷони VIP фаъол аст.*\n\n"
    "✨ **Мехоҳед ба ботамон дастрасӣ ё Ключи шахсӣ гиред?**\n"
    "📩 Ҳозир ба админ муроҷиат кунед:\n"
    "👉 **@parnetka**\n"
    "━━━━━━━━━━━\n"
    "⚡ *Хизматрасонии зуд ва боэътимод!*"
)

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

def clean_name(name: str) -> str:
    if not name:
        return "Корбар"
    return name.replace("`", "").replace("*", "").replace("_", "")

def is_admin_user(user) -> bool:
    return bool(user and user.id in ADMIN_IDS)

def find_level_recursive(obj) -> str:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in ("level", "playerlevel", "accountlevel", "player_level") and v is not None:
                val_str = str(v).strip()
                if val_str.isdigit():
                    return val_str
        for v in obj.values():
            res = find_level_recursive(v)
            if res:
                return res
    elif isinstance(obj, list):
        for item in obj:
            res = find_level_recursive(item)
            if res:
                return res
    return ""

async def fetch_player_level(uid: str) -> str:
    info_urls = [
        f"{BASE_API_URL}/info?api_key={LIKES_API_KEY}&uid={uid}",
        f"https://ff.ggbluewhale.store/api/info?uid={uid}",
    ]
    async with aiohttp.ClientSession() as session:
        for url in info_urls:
            try:
                async with session.get(url, timeout=4) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        parsed = json.loads(text)
                        lvl = find_level_recursive(parsed)
                        if lvl:
                            return lvl
            except Exception:
                continue
    return "N/A"

async def check_group_access(update: Update) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if not chat:
        return False
    if is_admin_user(user):
        return True
    if chat.type == "private":
        if update.message:
            await update.message.reply_text(
                ACCESS_DENIED_TEXT,
                parse_mode="Markdown",
                reply_markup=DEVELOPER_KEYBOARD
            )
        return False
    return chat.id in ALLOWED_GROUPS

async def handle_private_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if is_admin_user(user):
        return
    if chat and chat.type == "private" and update.message:
        await update.message.reply_text(
            ACCESS_DENIED_TEXT,
            parse_mode="Markdown",
            reply_markup=DEVELOPER_KEYBOARD
        )

async def fetch_likes_api(api_url: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get(api_url, timeout=15) as resp:
            return await resp.text()

async def run_kinematic_animation(progress_msg, uid: str):
    frames = [
        ("⚡ **Пайвастшавӣ ба сервери FF...**", "██░░░░░░░░", "18%"),
        ("🔍 **Ҷустуҷӯи аккаунти бозингар...**", "████░░░░░░", "44%"),
        ("🚀 **Омодасозии бастаи лайкҳо...**", "███████░░░", "71%"),
        ("🔥 **Фиристодани лайкҳо ба UID...**", "█████████░", "93%"),
    ]
    for title, bar, pct in frames:
        await asyncio.sleep(0.85)
        try:
            await progress_msg.edit_text(
                f"{title}\n"
                f"🆔 **UID:** `{uid}`\n"
                f"`[{bar}]` **{pct}**",
                parse_mode="Markdown"
            )
        except Exception:
            pass

async def like_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_group_access(update):
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ **ТАРЗИ ИСТИФОДАБАРӢ**\n"
            "━━━━━━━━━━━\n"
            "👉 Фармон: `/like {UID}`\n"
            "📌 Намуна: `/like 13961132846`"
            f"{VIP_PROMO_FOOTER}",
            parse_mode="Markdown",
            reply_markup=DEVELOPER_KEYBOARD
        )
        return

    uid = context.args[0].strip()

    if len(context.args) != 1 or not uid.isdigit() or not (8 <= len(uid) <= 12):
        await update.message.reply_text(
            "❌ **ID-И ХАТО!**\n\n"
            "Free Fire UID бояд танҳо аз рақамҳо (аз 8 то 12 рақам) иборат бошад.",
            parse_mode="Markdown"
        )
        return

    user = update.effective_user
    user_id = str(user.id)
    admin_flag = is_admin_user(user)
    now = time.time()

    load_cache()

    if not admin_flag:
        if user_id in memory_users:
            elapsed = now - memory_users[user_id]
            if elapsed < COOLDOWN_TIME:
                rem = format_remaining(COOLDOWN_TIME - elapsed)
                await update.message.reply_text(
                    "⏳ **ЛИМИТИ 24-СОАТА**\n"
                    "━━━━━━━━━━━\n"
                    f"👤 **Корбар:** `{clean_name(user.first_name)}`\n"
                    "🚫 Шумо имрӯз аллакай лайк гирифтаед!\n\n"
                    f"🕒 **Боқимонда:** `{rem}`\n"
                    "💡 *Пас аз гузаштани вақт дубора кӯшиш кунед.*"
                    f"{VIP_PROMO_FOOTER}",
                    parse_mode="Markdown",
                    reply_markup=DEVELOPER_KEYBOARD
                )
                return

        if uid in memory_uids:
            elapsed_uid = now - memory_uids[uid]
            if elapsed_uid < COOLDOWN_TIME:
                rem = format_remaining(COOLDOWN_TIME - elapsed_uid)
                await update.message.reply_text(
                    "⚠️ **АККАУНТ ДАР ЛИМИТ**\n"
                    "━━━━━━━━━━━\n"
                    f"🆔 **UID:** `{uid}`\n"
                    "🚫 Ба ин аккаунт имрӯз аллакай лайк фиристода шудааст!\n\n"
                    f"🕒 **Боқимонда:** `{rem}`"
                    f"{VIP_PROMO_FOOTER}",
                    parse_mode="Markdown",
                    reply_markup=DEVELOPER_KEYBOARD
                )
                return

        memory_users[user_id] = now
        memory_uids[uid] = now
        save_cache()

    progress = await update.message.reply_text(
        "⚡ **Оғози раванди фиристодан...**\n"
        f"🆔 **UID:** `{uid}`\n"
        "`[░░░░░░░░░░]` **1%**",
        parse_mode="Markdown"
    )
    api_url = f"{BASE_API_URL}/like?api_key={LIKES_API_KEY}&uid={uid}"

    try:
        api_task = asyncio.create_task(fetch_likes_api(api_url))
        await run_kinematic_animation(progress, uid)
        raw_response = await api_task

        try:
            data = json.loads(raw_response)
        except ValueError:
            data = None

        if not isinstance(data, dict) or data.get("success") is not True:
            err_msg = str(data.get("error", "")) if isinstance(data, dict) else raw_response[:200]

            if "maximum" in err_msg.lower() or "already" in err_msg.lower():
                await progress.edit_text(
                    "⏳ **ЛИМИТИ РӮЗОНА ПУР**\n"
                    "━━━━━━━━━━━\n"
                    f"🆔 **UID:** `{uid}`\n"
                    "⚠️ Ин аккаунт имрӯз аллакай лимити максималии лайкро гирифтааст!\n\n"
                    "🕒 **Лутфан пас аз 24 соат кӯшиш кунед.**"
                    f"{VIP_PROMO_FOOTER}",
                    parse_mode="Markdown",
                    reply_markup=DEVELOPER_KEYBOARD
                )
                return

            if not admin_flag:
                memory_users.pop(user_id, None)
                memory_uids.pop(uid, None)
                save_cache()

            await progress.edit_text(
                "❌ **Хатогӣ ҳангоми иҷро!**\n"
                f"📝 *Сабаб:* `{err_msg}`",
                parse_mode="Markdown",
                reply_markup=DEVELOPER_KEYBOARD
            )
            return

        details = data.get("data", {})
        player_level = find_level_recursive(data)
        if not player_level:
            player_level = await fetch_player_level(uid)

        likes_given = details.get("LikesGivenByAPI", 0)
        try:
            likes_given_int = int(likes_given)
        except (ValueError, TypeError):
            likes_given_int = 0

        if likes_given_int <= 0:
            if not admin_flag:
                memory_users.pop(user_id, None)
                memory_uids.pop(uid, None)
                save_cache()

            await progress.edit_text(
                "❌ **ЛАЙК НАРАФТ**\n"
                "━━━━━━━━━━━\n"
                f"👤 **Ном:** `{details.get('PlayerNickname', 'N/A')}`\n"
                f"📶 **Level:** `{player_level}`\n"
                f"🆔 **UID:** `{details.get('UID', uid)}`\n"
                f"📊 **Пеш аз лайк:** `{details.get('LikesbeforeCommand', 'N/A')}`\n"
                f"👍 **Илова шуд:** `0`\n\n"
                "⚠️ *Аккаунт лимити рӯзона дорад ё ном рамзҳои махсус дорад.*"
                f"{VIP_PROMO_FOOTER}",
                parse_mode="Markdown",
                reply_markup=DEVELOPER_KEYBOARD
            )
            return

        try:
            await progress.edit_text(
                "✨ **Анҷом ёфт!**\n"
                f"🆔 **UID:** `{uid}`\n"
                "`[██████████]` **100%**",
                parse_mode="Markdown"
            )
            await asyncio.sleep(0.55)
        except Exception:
            pass

        message = (
            "✅ **ЛАЙКҲО ФИРИСТОДА ШУДАНД!**\n"
            "━━━━━━━━━━━\n"
            f"👤 **Ном:** `{details.get('PlayerNickname', 'N/A')}`\n"
            f"📶 **Level:** `{player_level}`\n"
            f"🆔 **UID:** `{details.get('UID', uid)}`\n"
            "━━━━━━━━━━━\n"
            f"📉 **Пеш аз лайк:** `{details.get('LikesbeforeCommand', 'N/A')}`\n"
            f"🔥 **Лайкҳои иловашуда:** `+{likes_given}`\n"
            f"📈 **Баъд аз лайк:** `{details.get('LikesafterCommand', 'N/A')}`\n"
            "━━━━━━━━━━━\n"
            "⏳ *Дархости навбатӣ пас аз 24 соат!*"
            f"{VIP_PROMO_FOOTER}"
        )

        await progress.edit_text(
            message,
            parse_mode="Markdown",
            reply_markup=DEVELOPER_KEYBOARD
        )

    except Exception as e:
        if not admin_flag:
            memory_users.pop(user_id, None)
            memory_uids.pop(uid, None)
            save_cache()
        logging.error(f"/like error: {e}")
        await progress.edit_text("❌ **Хатогии сервер!** Лутфан каме баъдтар кӯшиш кунед.", parse_mode="Markdown")


async def remain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return

    api_url = f"{BASE_API_URL}/status?api_key={LIKES_API_KEY}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=10) as resp:
                raw = await resp.text()
        await update.message.reply_text(
            "📊 **ҲОЛАТИ БАЛАНС / API**\n"
            "━━━━━━━━━━━\n"
            f"`{raw}`",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"/remain error: {e}")
        await update.message.reply_text("❌ Хатогӣ ҳангоми гирифтани маълумот.")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ **Лутфан ID ё UID-и мушаххасро нависед!**\n"
            "📌 Намуна: `/reset 15853201485`",
            parse_mode="Markdown"
        )
        return

    load_cache()
    target = context.args[0].strip()
    removed_user = memory_users.pop(target, None)
    removed_uid = memory_uids.pop(target, None)
    save_cache()

    if removed_user or removed_uid:
        await update.message.reply_text(
            f"✅ **Лимит танҳо барои `{target}` бомуваффақият гирифта шуд!**",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"ℹ️ Барои `{target}` ягон лимити фаъол ёфт нашуд.",
            parse_mode="Markdown"
        )


app = FastAPI()

async def ensure_bot_ready():
    global ptb_application
    if ptb_application is None:
        load_cache()
        ptb_application = ApplicationBuilder().token(BOT_TOKEN).build()
        ptb_application.add_handler(CommandHandler("like", like_command))
        ptb_application.add_handler(CommandHandler("remain", remain_command))
        ptb_application.add_handler(CommandHandler("reset", reset_command))
        ptb_application.add_handler(MessageHandler(filters.ChatType.PRIVATE, handle_private_messages))
        await ptb_application.initialize()
        await ptb_application.start()

@app.on_event("startup")
async def startup_event():
    await ensure_bot_ready()

@app.get("/")
@app.get("/api/webhook")
async def health_check():
    return {"status": "Bot is alive!"}

@app.post("/api/webhook")
async def telegram_webhook(request: Request):
    await ensure_bot_ready()
    req = await request.json()
    update = Update.de_json(req, ptb_application.bot)
    await ptb_application.process_update(update)
    return {"status": "ok"}
