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
DEFAULT_LIKES_AMOUNT = 200
CACHE_FILE = "/tmp/ff_cooldowns.json"

memory_users = {}  # { "tg_user_id": timestamp }
memory_uids = {}   # { "ff_uid": {"time": timestamp, "user_id": "tg_user_id", "name": "..."} }
vip_uids = {}      # { "ff_uid": expire_timestamp }
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


async def load_state():
    global memory_users, memory_uids, vip_uids
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r") as f:
                data = json.load(f)
                memory_users = data.get("users", {})
                memory_uids = data.get("uids", {})
                vip_uids = data.get("vips", {})
    except Exception:
        pass

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(FIREBASE_DB_URL, timeout=4) as resp:
                if resp.status == 200:
                    fb_data = await resp.json()
                    if isinstance(fb_data, dict):
                        memory_users = fb_data.get("users", memory_users) or {}
                        memory_uids = fb_data.get("uids", memory_uids) or {}
                        vip_uids = fb_data.get("vips", vip_uids) or {}
                        with open(CACHE_FILE, "w") as f:
                            json.dump({"users": memory_users, "uids": memory_uids, "vips": vip_uids}, f)
    except Exception:
        pass


async def save_state():
    payload = {
        "users": memory_users,
        "uids": memory_uids,
        "vips": vip_uids
    }
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(payload, f)
    except Exception:
        pass

    try:
        async with aiohttp.ClientSession() as session:
            async with session.put(FIREBASE_DB_URL, json=payload, timeout=4):
                pass
    except Exception:
        pass


def is_vip_uid(uid: str) -> bool:
    if uid not in vip_uids:
        return False
    if time.time() < float(vip_uids[uid]):
        return True
    vip_uids.pop(uid, None)
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
    """Бо URL-и стандартии API дархост мефиристад, то 200 (ё барои VIP то 700) лайк диҳад."""
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
        for i in range(max_calls):
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
                        last_error = "Лимити рӯзонаи аккаунт пур шудааст"
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
            "⚠️ **ТАРЗИ ИСТИФОДАБАРӢ**\n"
            "━━━━━━━━━━━\n"
            "👉 `/like {UID}` ё `/like 200 {UID}`\n"
            "📌 Намуна: `/like 18778177236`\n"
            "📌 Намуна 2: `/like 200 18778177236`"
            f"{VIP_PROMO_FOOTER}",
            parse_mode="Markdown",
            reply_markup=DEVELOPER_KEYBOARD
        )
        return

    if not amount_str.isdigit() or not uid.isdigit() or not (8 <= len(uid) <= 12):
        if not await check_group_access(update):
            return
        await update.message.reply_text(
            "❌ **ФОРМАТИ ХАТО!**\n"
            "━━━━━━━━━━━\n"
            "Лутфан дуруст нависед:\n"
            "📌 `/like 18778177236` ё `/like 200 18778177236`",
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
        if amount != 200:
            await update.message.reply_text(
                "⚠️ **ТАНҲО 200 ЛАЙК ДАСТРАС АСТ!**\n"
                "━━━━━━━━━━━\n"
                "Барои корбарони оддӣ фармонҳои дуруст:\n"
                f"👉 `/like {uid}`\n"
                f"👉 `/like 200 {uid}`\n\n"
                "💎 *Барои гирифтани 400–700 лайк дар як рӯз, VIP харед!*"
                f"{VIP_PROMO_FOOTER}",
                parse_mode="Markdown",
                reply_markup=DEVELOPER_KEYBOARD
            )
            return
    else:
        if amount not in (100, 200, 300, 400, 500, 600, 700):
            await update.message.reply_text(
                "❌ Миқдори лайк бояд яке аз инҳо бошад: `100`, `200`, `400` ё `700`.",
                parse_mode="Markdown"
            )
            return

    user_id = str(user.id)
    now = time.time()

    if not admin_flag and not vip_flag:
        if user_id in memory_users:
            elapsed = now - float(memory_users[user_id])
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
            uid_entry = memory_uids[uid]
            uid_time = float(uid_entry["time"]) if isinstance(uid_entry, dict) else float(uid_entry)
            elapsed_uid = now - uid_time
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
        memory_uids[uid] = {
            "time": now,
            "user_id": user_id,
            "name": clean_name(user.first_name)
        }
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

            if not admin_flag and not vip_flag:
                memory_users.pop(user_id, None)
                memory_uids.pop(uid, None)
                await save_state()

            await progress.edit_text(
                "❌ **Хатогӣ ҳангоми иҷро!**\n"
                f"📝 *Сабаб:* `{err_msg}`",
                parse_mode="Markdown",
                reply_markup=DEVELOPER_KEYBOARD
            )
            return

        player_level = find_level_recursive(res["raw_first"]) or await fetch_player_level(uid)
        likes_given = res["total_added"]
        before_likes = res["first_before"]
        after_likes = res["last_after"]
        nickname = res["nickname"]
        final_uid = res["uid"] or uid

        try:
            await progress.edit_text(
                "✨ **Анҷом ёфт!**\n"
                f"🆔 **UID:** `{uid}`\n"
                "`[██████████]` **100%**",
                parse_mode="Markdown"
            )
            await asyncio.sleep(0.5)
        except Exception:
            pass

        status_line = (
            f"💎 **VIP STATUS:** Боқимонда `{format_remaining(float(vip_uids[uid]) - time.time())}`"
            if vip_flag else
            "⏳ *Дархости навбатӣ пас аз 24 соат!*"
        )

        message = (
            "✅ **ЛАЙКҲО ФИРИСТОДА ШУДАНД!**\n"
            "━━━━━━━━━━━\n"
            f"👤 **Ном:** `{nickname}`\n"
            f"📶 **Level:** `{player_level}`\n"
            f"🆔 **UID:** `{final_uid}`\n"
            "━━━━━━━━━━━\n"
            f"📉 **Пеш аз лайк:** `{before_likes}`\n"
            f"🔥 **Лайкҳои иловашуда:** `+{likes_given}`\n"
            f"📈 **Баъд аз лайк:** `{after_likes}`\n"
            "━━━━━━━━━━━\n"
            f"{status_line}"
            f"{'' if vip_flag else VIP_PROMO_FOOTER}"
        )

        await progress.edit_text(
            message,
            parse_mode="Markdown",
            reply_markup=DEVELOPER_KEYBOARD
        )

    except Exception as e:
        if not admin_flag and not vip_flag:
            memory_users.pop(user_id, None)
            memory_uids.pop(uid, None)
            await save_state()
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
            "Лимити гурӯҳ пок нашуд.\n\n"
            "📌 Намуна: `/reset 18778177236`",
            parse_mode="Markdown"
        )
        return

    target = context.args[0].strip()
    if not target.isdigit():
        await update.message.reply_text("❌ Танҳо рақами UID ё Telegram ID-ро ворид кунед.")
        return

    await load_state()

    removed_user = memory_users.pop(target, None)
    removed_uid = memory_uids.pop(target, None)

    linked_user_id = None
    if isinstance(removed_uid, dict):
        linked_user_id = removed_uid.get("user_id")
        if linked_user_id:
            memory_users.pop(str(linked_user_id), None)

    await save_state()

    if removed_user is not None or removed_uid is not None:
        extra = f" (ва корбари `{linked_user_id}`)" if linked_user_id else ""
        await update.message.reply_text(
            f"✅ **Лимит ТАНҲО барои `{target}`{extra} гирифта шуд!**\n"
            "Дигар аъзоёни гурӯҳ дар лимит мемонанд.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"ℹ️ Барои `{target}` ягон лимити фаъол ёфт нашуд.",
            parse_mode="Markdown"
        )


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Санҷиши як UID: /check <UID ё User_ID>"""
    if not is_admin_user(update.effective_user):
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ **ТАРЗИ ИСТИФОДАБАРӢ:**\n"
            "📌 Намуна: `/check 18778177236`",
            parse_mode="Markdown"
        )
        return

    target = context.args[0].strip()
    if not target.isdigit():
        await update.message.reply_text("❌ Танҳо рақами UID ё Telegram ID-ро ворид кунед.")
        return

    await load_state()
    now = time.time()
    tj_tz = timezone(timedelta(hours=5))

    if is_vip_uid(target):
        exp_ts = float(vip_uids[target])
        rem_vip = format_remaining(exp_ts - now)
        exp_str = datetime.fromtimestamp(exp_ts, tz=tj_tz).strftime("%d.%m.%Y %H:%M")
        await update.message.reply_text(
            "💎 **ҲОЛАТИ АККАУНТ: VIP**\n"
            "━━━━━━━━━━━\n"
            f"🆔 **ID:** `{target}`\n"
            "✅ Бе лимити 24-соата кор мекунад!\n"
            f"⏳ **Мӯҳлати VIP:** `{rem_vip}` (то `{exp_str}`)",
            parse_mode="Markdown"
        )
        return

    found_ts = None
    linked_user = None

    if target in memory_uids:
        entry = memory_uids[target]
        if isinstance(entry, dict):
            found_ts = float(entry.get("time", 0))
            linked_user = entry.get("user_id")
        else:
            found_ts = float(entry)
    elif target in memory_users:
        found_ts = float(memory_users[target])

    if found_ts and (now - found_ts) < COOLDOWN_TIME:
        unlock_ts = found_ts + COOLDOWN_TIME
        rem_str = format_remaining(unlock_ts - now)
        unlock_time_str = datetime.fromtimestamp(unlock_ts, tz=tj_tz).strftime("%d.%m.%Y соати %H:%M:%S")
        user_info = f"\n👤 **Telegram ID:** `{linked_user}`" if linked_user else ""

        await update.message.reply_text(
            "⏳ **ҲОЛАТИ ЛИМИТ: ФАЪОЛ**\n"
            "━━━━━━━━━━━\n"
            f"🆔 **ID:** `{target}`{user_info}\n"
            f"🕒 **Боқимонда:** `{rem_str}`\n"
            f"🔓 **Вақти кушодашавӣ:** `{unlock_time_str}`",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "✅ **ЛИМИТ НЕСТ (ОЗОД АСТ)!**\n"
            "━━━━━━━━━━━\n"
            f"🆔 **ID:** `{target}`\n"
            "🚀 Ин аккаунт метавонад ҳамин ҳозир `/like` занад.",
            parse_mode="Markdown"
        )


async def limits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рӯйхати ҲАМАИ ID-ҳои гурӯҳ, ки дар лимити 24-соата ҳастанд (/limits)"""
    if not is_admin_user(update.effective_user):
        return

    await load_state()
    now = time.time()
    tj_tz = timezone(timedelta(hours=5))

    active_items = []
    expired_uids = []
    expired_users = []

    for uid, entry in memory_uids.items():
        if isinstance(entry, dict):
            ts = float(entry.get("time", 0))
            u_id = entry.get("user_id", "")
            u_name = entry.get("name", "Корбар")
        else:
            ts = float(entry)
            u_id = ""
            u_name = "Корбар"

        unlock_ts = ts + COOLDOWN_TIME
        if unlock_ts > now:
            active_items.append((unlock_ts, uid, u_name, u_id))
        else:
            expired_uids.append(uid)

    for u_id, ts in memory_users.items():
        if (float(ts) + COOLDOWN_TIME) <= now:
            expired_users.append(u_id)

    # Пок кардани лимитҳои аллакай тамомшуда
    if expired_uids or expired_users:
        for u in expired_uids:
            memory_uids.pop(u, None)
        for usr in expired_users:
            memory_users.pop(usr, None)
        await save_state()

    if not active_items:
        await update.message.reply_text(
            "✅ **ҲАМАИ АЪЗОЁНИ ГУРӮҲ ОЗОДАНД!**\n"
            "━━━━━━━━━━━\n"
            "Айни ҳол ягон ID дар лимити 24-соата нест. Ҳама метавонанд `/like` зананд!",
            parse_mode="Markdown"
        )
        return

    # Тартиб додан аз рӯи наздиктарин вақти кушодашавӣ
    active_items.sort(key=lambda x: x[0])

    lines = [
        f"📋 **РӮЙХАТИ ЛИМИТҲОИ 24-СОАТА ({len(active_items)} нафар):**",
        "━━━━━━━━━━━"
    ]

    for idx, (unlock_ts, uid, u_name, u_id) in enumerate(active_items, start=1):
        rem_str = format_remaining(unlock_ts - now)
        unlock_clock = datetime.fromtimestamp(unlock_ts, tz=tj_tz).strftime("%H:%M:%S (%d.%m)")
        lines.append(
            f"{idx}. 🆔 `{uid}` ({u_name})\n"
            f"   ⏳ **Боқимонда:** `{rem_str}`\n"
            f"   🔓 **Кушодашавӣ:** `{unlock_clock}`"
        )

    # Агар рӯйхат хеле дароз бошад, ба қисмҳо тақсим мекунад, то Telegram хато надиҳад
    full_text = "\n".join(lines)
    if len(full_text) <= 4000:
        await update.message.reply_text(full_text, parse_mode="Markdown")
    else:
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 2 > 3900:
                await update.message.reply_text(chunk, parse_mode="Markdown")
                chunk = line
            else:
                chunk = f"{chunk}\n{line}" if chunk else line
        if chunk:
            await update.message.reply_text(chunk, parse_mode="Markdown")


async def addvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return

    if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.message.reply_text(
            "⚠️ **ТАРЗИ ИСТИФОДАБАРӢ:**\n"
            "`/addvip <UID> <рӯз>`\n\n"
            "📌 Намуна (барои 10 рӯз):\n"
            "`/addvip 18778177236 10`",
            parse_mode="Markdown"
        )
        return

    uid = context.args[0].strip()
    days = int(context.args[1].strip())
    if days <= 0:
        await update.message.reply_text("❌ Шумораи рӯз бояд аз 0 зиёд бошад.")
        return

    await load_state()
    vip_uids[uid] = time.time() + (days * 86400)
    removed_uid = memory_uids.pop(uid, None)
    if isinstance(removed_uid, dict) and removed_uid.get("user_id"):
        memory_users.pop(str(removed_uid["user_id"]), None)
    await save_state()

    await update.message.reply_text(
        "💎 **VIP БОМУВАФФАҚИЯТ ФАЪОЛ ШУД!**\n"
        "━━━━━━━━━━━\n"
        f"🆔 **UID:** `{uid}`\n"
        f"⏳ **Мӯҳлат:** `{days} рӯз`\n"
        "✅ Акнун ин муштарӣ метавонад `/like <UID>`, `/like 400 <UID>` ё `/like 700 <UID>` занад!",
        parse_mode="Markdown"
    )


async def delvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("📌 Намуна: `/delvip 18778177236`", parse_mode="Markdown")
        return

    uid = context.args[0].strip()
    await load_state()
    removed = vip_uids.pop(uid, None)
    await save_state()

    if removed:
        await update.message.reply_text(f"🗑 UID `{uid}` аз рӯйхати VIP хориҷ карда шуд.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ UID `{uid}` дар рӯйхати VIP набуд.", parse_mode="Markdown")


async def viplist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return

    await load_state()
    now = time.time()
    active_lines = []
    expired_keys = []

    for uid, exp in vip_uids.items():
        if float(exp) > now:
            rem = format_remaining(float(exp) - now)
            active_lines.append(f"• `{uid}` — боқимонда: **{rem}**")
        else:
            expired_keys.append(uid)

    if expired_keys:
        for k in expired_keys:
            vip_uids.pop(k, None)
        await save_state()

    if not active_lines:
        await update.message.reply_text("📭 Айни ҳол ягон муштарии VIP фаъол нест.")
        return

    await update.message.reply_text(
        "💎 **РӮЙХАТИ VIP-МУШТАРИЁН:**\n"
        "━━━━━━━━━━━\n" + "\n".join(active_lines),
        parse_mode="Markdown"
    )


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
