import os
import time
import json
import asyncio
import logging
import aiohttp
from datetime import datetime, timezone, timedelta
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

FIREBASE_DB_URL = "https://api-key-mgmt-default-rtdb.firebaseio.com/ff_bot_state.json"

ADMIN_IDS = [6410886684]
ALLOWED_GROUPS = [-1004354930364]

COOLDOWN_TIME = 86400  # 24 соат
DEFAULT_LIKES_AMOUNT = 100

db_cache = {
    "uids": {},
    "vips": {},
    "like_gif_id": ""
}
ptb_application = None

DEVELOPER_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("👨‍💻 АДМИН / ХАРИДИ VIP", url="https://t.me/parnetka")]
])

VIP_PROMO_FOOTER = (
    "\n\n💎 <b>МЕХОҲЕД 200+ VIP LIKE ХАРЕД?</b>\n"
    "⚡️ <i>Бе интизории 24 соат ва бо суръати баланд!</i>\n"
    "📩 <b>Ба админ муроҷиат кунед:</b> 👉 <b>@parnetka</b>"
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


async def load_state():
    global db_cache
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(FIREBASE_DB_URL, timeout=5) as resp:
                if resp.status == 200:
                    fb_data = await resp.json()
                    if isinstance(fb_data, dict):
                        db_cache["uids"] = fb_data.get("uids", {}) or {}
                        db_cache["vips"] = fb_data.get("vips", {}) or {}
                        db_cache["like_gif_id"] = fb_data.get("like_gif_id", "") or ""
    except Exception as e:
        logging.error(f"Load state error: {e}")


async def save_state():
    global db_cache
    try:
        async with aiohttp.ClientSession() as session:
            async with session.put(FIREBASE_DB_URL, json=db_cache, timeout=5):
                pass
    except Exception as e:
        logging.error(f"Save state error: {e}")


def is_vip_uid(uid: str) -> bool:
    vips = db_cache.get("vips", {})
    if uid not in vips:
        return False
    if time.time() < float(vips[uid]):
        return True
    vips.pop(uid, None)
    return False


def format_remaining(seconds_left: float) -> str:
    total_sec = max(0, int(seconds_left))
    days = total_sec // 86400
    hours = (total_sec % 86400) // 3600
    minutes = (total_sec % 3600) // 60
    seconds = total_sec % 60
    if days > 0:
        return f"{days} рӯзу {hours} соату {minutes} дақиқа"
    if hours > 0:
        return f"{hours} соат, {minutes} дақиқа, {seconds} сония"
    if minutes > 0:
        return f"{minutes} дақиқа, {seconds} сония"
    return f"{seconds} сония"


def format_hms(seconds_left: float) -> str:
    total_sec = max(0, int(seconds_left))
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    seconds = total_sec % 60
    return f"{hours:02d}ҷ : {minutes:02d}м : {seconds:02d}сон"


def clean_name(name: str) -> str:
    if not name:
        return "Корбар"
    return name.replace("`", "").replace("*", "").replace("_", "")


def escape_html(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
    info_url = f"{BASE_API_URL}/info?api_key={LIKES_API_KEY}&uid={uid}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(info_url, timeout=4) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    parsed = json.loads(text)
                    lvl = find_level_recursive(parsed)
                    if lvl:
                        return lvl
        except Exception:
            pass
    return "N/A"


async def check_group_access(update: Update, is_vip: bool = False) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if not chat:
        return False
    if is_admin_user(user) or is_vip:
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


async def handle_admin_gif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global db_cache
    user = update.effective_user
    chat = update.effective_chat
    if not is_admin_user(user) or not chat or chat.type != "private":
        return

    if update.message and update.message.animation:
        await load_state()
        db_cache["like_gif_id"] = update.message.animation.file_id
        await save_state()
        await update.message.reply_text(
            "✅ **GIF-и Parnetka Likes бомуваффақият насб ва дар Firebase сабт шуд!**",
            parse_mode="Markdown"
        )


async def delgif_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global db_cache
    if not is_admin_user(update.effective_user):
        return
    await load_state()
    db_cache["like_gif_id"] = ""
    await save_state()
    await update.message.reply_text("🗑 GIF пок карда шуд.")


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


async def send_likes_batch(uid: str, target_amount: int) -> dict:
    total_added = 0
    first_before = None
    last_after = None
    nickname = "N/A"
    resp_uid = uid
    last_error = ""
    raw_first = {}

    max_calls = max(1, (target_amount + 99) // 100)
    url = f"{BASE_API_URL}/like?api_key={LIKES_API_KEY}&uid={uid}"

    async with aiohttp.ClientSession() as session:
        for _ in range(max_calls):
            if total_added >= target_amount:
                break

            try:
                async with session.get(url, timeout=15) as resp:
                    raw = await resp.text()
                data = json.loads(raw)

                if not isinstance(data, dict) or data.get("success") is not True:
                    last_error = str(data.get("error", "")) if isinstance(data, dict) else raw[:150]
                    break

                if not raw_first:
                    raw_first = data

                details = data.get("data", {})
                nickname = details.get("PlayerNickname", nickname)
                resp_uid = str(details.get("UID", resp_uid))

                if first_before is None:
                    first_before = details.get("LikesbeforeCommand", "N/A")

                added = int(details.get("LikesGivenByAPI", 0) or 0)
                if added <= 0:
                    if total_added == 0:
                        last_error = "Лимити рӯзонаи аккаунти бозӣ пур шудааст"
                    break

                total_added += added
                last_after = details.get("LikesafterCommand", last_after)

                if total_added >= target_amount:
                    break
                await asyncio.sleep(0.5)
            except Exception as e:
                last_error = str(e)
                break

    return {
        "total_added": total_added,
        "first_before": first_before if first_before is not None else "N/A",
        "last_after": last_after if last_after is not None else "N/A",
        "nickname": nickname,
        "uid": resp_uid,
        "error": last_error,
        "raw_first": raw_first
    }


async def run_kinematic_animation(progress_msg, uid: str, amount: int):
    frames = [
        ("⚡ **Пайвастшавӣ ба сервери FF...**", "██░░░░░░░░", "18%"),
        ("🔍 **Ҷустуҷӯи аккаунти бозингар...**", "████░░░░░░", "44%"),
        (f"🚀 **Омодасозии {amount} лайк...**", "███████░░░", "71%"),
        ("🔥 **Фиристодани лайкҳо ба UID...**", "█████████░", "93%"),
    ]
    for title, bar, pct in frames:
        await asyncio.sleep(0.8)
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
    await load_state()

    if len(context.args) == 1:
        amount_str = str(DEFAULT_LIKES_AMOUNT)
        uid = context.args[0].strip()
    elif len(context.args) == 2:
        amount_str = context.args[0].strip()
        uid = context.args[1].strip()
    else:
        if not await check_group_access(update):
            return
        await update.message.reply_text(
            "⚠️ <b>ТАРЗИ ИСТИФОДАБАРӢ</b>\n"
            "━━━━━━━━━━━\n"
            "👉 <code>/like {UID}</code> ё <code>/like 100 {UID}</code>\n"
            "📌 Намуна: <code>/like 18778177236</code>"
            f"{VIP_PROMO_FOOTER}",
            parse_mode="HTML",
            reply_markup=DEVELOPER_KEYBOARD
        )
        return

    if not amount_str.isdigit() or not uid.isdigit() or not (8 <= len(uid) <= 12):
        if not await check_group_access(update):
            return
        await update.message.reply_text(
            "❌ **ФОРМАТИ ХАТО!**\n"
            "📌 `/like 18778177236`",
            parse_mode="Markdown"
        )
        return

    amount = int(amount_str)
    vip_flag = is_vip_uid(uid)
    user = update.effective_user
    admin_flag = is_admin_user(user)

    if not await check_group_access(update, is_vip=vip_flag):
        return

    if not admin_flag and not vip_flag:
        if amount != 100:
            await update.message.reply_text(
                "⚠️ <b>ТАНҲО 100 ЛАЙК ДАСТРАС АСТ!</b>"
                f"{VIP_PROMO_FOOTER}",
                parse_mode="HTML",
                reply_markup=DEVELOPER_KEYBOARD
            )
            return

    now = time.time()
    tg_full_name = clean_name(user.full_name or user.first_name)
    memory_uids = db_cache.get("uids", {})

    if not admin_flag and not vip_flag:
        if uid in memory_uids:
            uid_entry = memory_uids[uid]
            uid_time = float(uid_entry["time"]) if isinstance(uid_entry, dict) else float(uid_entry)
            elapsed_uid = now - uid_time
            if elapsed_uid < COOLDOWN_TIME:
                rem_hms = format_hms(COOLDOWN_TIME - elapsed_uid)
                await update.message.reply_text(
                    "⚠️ <b>ИН АКАУНТ ИМРӮЗ ЛАЙК ГИРИФТААСТ!</b>\n"
                    "━━━━━━━━━━━\n"
                    f"🆔 <b>UID:</b> <code>{uid}</code>\n"
                    f"⏳ <b>Боқимонда:</b> <code>{rem_hms}</code>"
                    f"{VIP_PROMO_FOOTER}",
                    parse_mode="HTML",
                    reply_markup=DEVELOPER_KEYBOARD
                )
                return

        memory_uids[uid] = {
            "time": now,
            "user_id": str(user.id),
            "name": tg_full_name
        }
        db_cache["uids"] = memory_uids
        await save_state()

    vip_badge = "💎 **VIP AUTOLIKE ФАЪОЛ**\n" if vip_flag else ""
    progress = await update.message.reply_text(
        f"{vip_badge}"
        f"⚡ **Оғози фиристодани {amount} лайк...**\n"
        f"🆔 **UID:** `{uid}`\n"
        "`[░░░░░░░░░░]` **1%**",
        parse_mode="Markdown"
    )

    try:
        api_task = asyncio.create_task(send_likes_batch(uid, amount))
        await run_kinematic_animation(progress, uid, amount)
        res = await api_task

        if res["total_added"] <= 0:
            err_msg = res["error"]
            if not admin_flag and not vip_flag:
                # Агар сервер гӯяд, ки лимит пур аст, вақти кушодашавиро пурра 24 соат мемонем
                memory_uids[uid] = {
                    "time": now,
                    "user_id": str(user.id),
                    "name": tg_full_name
                }
                db_cache["uids"] = memory_uids
                await save_state()

            rem_hms = format_hms(COOLDOWN_TIME)
            await progress.edit_text(
                "⏳ <b>ЛИМИТИ РӮЗОНАИ ИН UID ПУР АСТ!</b>\n"
                "━━━━━━━━━━━\n"
                f"🆔 <b>UID:</b> <code>{uid}</code>\n"
                f"🚫 <i>Ин аккаунт аллакай дар сервер лайк гирифтааст.</i>\n\n"
                f"⏳ <b>Боқимонда то кушодашавӣ:</b>\n"
                f"👉 <code>{rem_hms}</code>"
                f"{VIP_PROMO_FOOTER}",
                parse_mode="HTML",
                reply_markup=DEVELOPER_KEYBOARD
            )
            return

        player_level = find_level_recursive(res["raw_first"]) or await fetch_player_level(uid)
        likes_given = res["total_added"]
        before_likes = escape_html(res["first_before"])
        after_likes = escape_html(res["last_after"])
        nickname = escape_html(res["nickname"])
        final_uid = escape_html(res["uid"] or uid)

        status_line = (
            f"💎 <b>VIP STATUS:</b> Боқимонда <code>{format_remaining(float(db_cache['vips'][uid]) - time.time())}</code>"
            if vip_flag else
            "⏳ <i>Лайки навбатӣ ба ин UID пас аз 24 соат!</i>"
        )

        caption_html = (
            "🔥 <b>Parnetka Likes</b>\n"
            "<blockquote>"
            f"👤 <b>Ном:</b> <code>{nickname}</code>\n"
            f"📶 <b>Level:</b> <code>{escape_html(player_level)}</code>\n"
            f"🆔 <b>UID:</b> <code>{final_uid}</code>\n\n"
            f"💔 <b>Пеш аз лайк:</b> <code>{before_likes}</code>\n"
            f"❤️‍🔥 <b>Илова шуд:</b> <code>+{likes_given}</code>\n"
            f"❤️ <b>Баъд аз лайк:</b> <code>{after_likes}</code>"
            "</blockquote>\n"
            f"{status_line}"
            f"{'' if vip_flag else VIP_PROMO_FOOTER}"
        )

        like_gif_id = db_cache.get("like_gif_id", "")
        if like_gif_id:
            try:
                await progress.delete()
            except Exception:
                pass
            await update.message.reply_animation(
                animation=like_gif_id,
                caption=caption_html,
                parse_mode="HTML",
                reply_markup=DEVELOPER_KEYBOARD
            )
        else:
            await progress.edit_text(
                caption_html,
                parse_mode="HTML",
                reply_markup=DEVELOPER_KEYBOARD
            )

    except Exception as e:
        if not admin_flag and not vip_flag:
            memory_uids.pop(uid, None)
            db_cache["uids"] = memory_uids
            await save_state()
        logging.error(f"/like error: {e}")
        await progress.edit_text("❌ **Хатогии сервер!**", parse_mode="Markdown")


async def remain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_API_URL}/status?api_key={LIKES_API_KEY}", timeout=10) as resp:
            raw = await resp.text()
    await update.message.reply_text(f"📊 **БАЛАНС:**\n`{raw}`", parse_mode="Markdown")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    if not context.args:
        await update.message.reply_text("📌 Намуна: `/reset 18778177236`", parse_mode="Markdown")
        return
    target = context.args[0].strip()
    await load_state()
    if target in db_cache["uids"]:
        db_cache["uids"].pop(target)
        await save_state()
        await update.message.reply_text(f"✅ Лимит барои UID `{target}` гирифта шуд!", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ UID `{target}` дар лимит набуд.", parse_mode="Markdown")


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    if not context.args:
        await update.message.reply_text("📌 Намуна: `/check 18778177236`", parse_mode="Markdown")
        return
    target = context.args[0].strip()
    await load_state()
    now = time.time()

    if is_vip_uid(target):
        await update.message.reply_text(f"💎 UID `{target}` VIP аст!", parse_mode="Markdown")
        return

    memory_uids = db_cache.get("uids", {})
    if target in memory_uids:
        entry = memory_uids[target]
        ts = float(entry["time"]) if isinstance(entry, dict) else float(entry)
        if (now - ts) < COOLDOWN_TIME:
            rem = format_remaining(COOLDOWN_TIME - (now - ts))
            await update.message.reply_text(f"⏳ UID `{target}` дар лимит аст. Боқимонда: `{rem}`", parse_mode="Markdown")
            return
    await update.message.reply_text(f"✅ UID `{target}` озод аст.", parse_mode="Markdown")


async def limits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    await load_state()
    now = time.time()
    tj_tz = timezone(timedelta(hours=5))
    memory_uids = db_cache.get("uids", {})

    active_items = []
    expired = []
    for uid, entry in memory_uids.items():
        ts = float(entry["time"]) if isinstance(entry, dict) else float(entry)
        u_name = entry.get("name", "Корбар") if isinstance(entry, dict) else "Корбар"
        u_id = entry.get("user_id", "") if isinstance(entry, dict) else ""
        unlock_ts = ts + COOLDOWN_TIME
        if unlock_ts > now:
            active_items.append((unlock_ts, uid, u_name, u_id))
        else:
            expired.append(uid)

    if expired:
        for u in expired:
            memory_uids.pop(u, None)
        db_cache["uids"] = memory_uids
        await save_state()

    if not active_items:
        await update.message.reply_text("✅ Ҳамаи UID-ҳо озоданд!", parse_mode="HTML")
        return

    active_items.sort(key=lambda x: x[0])
    lines = [f"🔥 <b>РӮЙХАТИ ЛИМИТҲО ({len(active_items)} то):</b>"]
    for idx, (unlock_ts, uid, u_name, u_id) in enumerate(active_items, start=1):
        rem_hms = format_hms(unlock_ts - now)
        unlock_clock = datetime.fromtimestamp(unlock_ts, tz=tj_tz).strftime("%H:%M:%S (%d.%m)")
        safe_name = escape_html(u_name)
        user_link = f'<a href="tg://user?id={u_id}">{safe_name}</a>' if u_id else f"<b>{safe_name}</b>"
        lines.append(f"<blockquote><b>{idx}.</b> 👤 {user_link}\n🆔 <code>{uid}</code>\n⏳ <code>{rem_hms}</code>\n🔓 <code>{unlock_clock}</code></blockquote>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def addvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.message.reply_text("📌 Намуна: `/addvip 18778177236 10`", parse_mode="Markdown")
        return
    uid = context.args[0].strip()
    days = int(context.args[1].strip())
    await load_state()
    db_cache["vips"][uid] = time.time() + (days * 86400)
    db_cache["uids"].pop(uid, None)
    await save_state()
    await update.message.reply_text(f"💎 VIP барои UID `{uid}` ба муҳлати `{days} рӯз` фаъол шуд!", parse_mode="Markdown")


async def delvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("📌 Намуна: `/delvip 18778177236`", parse_mode="Markdown")
        return
    uid = context.args[0].strip()
    await load_state()
    if uid in db_cache["vips"]:
        db_cache["vips"].pop(uid)
        await save_state()
        await update.message.reply_text(f"🗑 UID `{uid}` аз VIP хориҷ шуд.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ UID `{uid}` дар VIP набуд.", parse_mode="Markdown")


async def viplist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    await load_state()
    now = time.time()
    active = []
    for uid, exp in list(db_cache["vips"].items()):
        if float(exp) > now:
            active.append(f"• `{uid}` — боқимонда: **{format_remaining(float(exp) - now)}**")
        else:
            db_cache["vips"].pop(uid)
    await save_state()
    if not active:
        await update.message.reply_text("📭 VIP нест.")
        return
    await update.message.reply_text("💎 **VIP-МУШТАРИЁН:**\n" + "\n".join(active), parse_mode="Markdown")


app = FastAPI()

async def ensure_bot_ready():
    global ptb_application
    if ptb_application is None:
        await load_state()
        ptb_application = ApplicationBuilder().token(BOT_TOKEN).build()
        ptb_application.add_handler(CommandHandler("like", like_command))
        ptb_application.add_handler(CommandHandler("remain", remain_command))
        ptb_application.add_handler(CommandHandler("reset", reset_command))
        ptb_application.add_handler(CommandHandler("check", check_command))
        ptb_application.add_handler(CommandHandler("limits", limits_command))
        ptb_application.add_handler(CommandHandler("addvip", addvip_command))
        ptb_application.add_handler(CommandHandler("delvip", delvip_command))
        ptb_application.add_handler(CommandHandler("viplist", viplist_command))
        ptb_application.add_handler(CommandHandler("delgif", delgif_command))
        ptb_application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.ANIMATION, handle_admin_gif))
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
