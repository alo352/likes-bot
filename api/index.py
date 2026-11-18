# -*- coding: utf-8 -*-
import os
import time
import json
import asyncio
import logging
import aiohttp
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ChatMemberHandler, filters, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "9160352065:AAEEZDlCJ5qNW1XjheHJlzhXayiAokA_FU8")

# Сервери AutoLikeSystem
AUTOSYSTEM_KEY = os.getenv("AUTOSYSTEM_KEY", "AUTOSYSTEM_a00dc13872dfc8c417f658cd9d29ffd6d1d0")
AUTOSYSTEM_URL = "https://autolikesystem.com.br/v1"

FIREBASE_DB_URL = "https://api-key-mgmt-default-rtdb.firebaseio.com/ff_bot_state.json"

ADMIN_IDS = [6410886684]
ALLOWED_GROUPS = [-1004356261091]

COOLDOWN_TIME = 86400  # 24 соат
DEFAULT_LIKES_AMOUNT = 100  # Барои корбарони оддӣ 100 лайк
ADMIN_DEFAULT_LIKES = 200   # Барои Админ 200 лайк
MAX_UIDS_PER_USER = 1

db_cache = {
    "uids": {},
    "user_history": {},
    "vips": {},
    "like_gif_id": ""
}
ptb_application = None

active_requests = set()

DEVELOPER_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("👨‍💻 АДМИН / ҲАРИДИ VIP", url="https://t.me/parnetka")]
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
    "👉 **@parnetka**"
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
                        db_cache["user_history"] = fb_data.get("user_history", {}) or {}
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
            if k.lower() in ("level", "playerlevel", "accountlevel", "player_level", "nivel") and v is not None:
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


async def fetch_player_info_autosystem(uid: str) -> dict:
    """Маълумоти профили бозингарро тавассути AutoLikeSystem (/v1/info) мегирад"""
    async with aiohttp.ClientSession() as session:
        try:
            info_url = f"{AUTOSYSTEM_URL}/info?key={AUTOSYSTEM_KEY}&uid={uid}"
            async with session.get(info_url, timeout=10) as resp:
                if resp.status == 200:
                    parsed = json.loads(await resp.text())
                    lvl = find_level_recursive(parsed)
                    data = parsed.get("data", parsed) if isinstance(parsed, dict) else {}
                    nick = (
                        data.get("nickname") or data.get("nick") or data.get("nome")
                        or data.get("PlayerNickname") or data.get("name") or ""
                    )
                    likes = (
                        data.get("likes") or data.get("curtidas")
                        or data.get("Likes") or "N/A"
                    )
                    if nick or lvl:
                        return {"level": lvl or "N/A", "nickname": nick or "N/A", "likes": likes}
        except Exception as e:
            logging.error(f"Fetch player info error: {e}")

    return {"level": "N/A", "nickname": "N/A", "likes": "N/A"}


async def track_bot_removal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    if not result:
        return

    chat = result.chat
    user = result.from_user
    new_status = result.new_chat_member.status

    if new_status in ["member", "administrator"]:
        if chat.id not in ALLOWED_GROUPS and not is_admin_user(user):
            try:
                await context.bot.leave_chat(chat.id)
            except Exception as e:
                logging.error(f"Leave unauthorized chat error: {e}")
        return

    if new_status in ["left", "kicked"]:
        try:
            user_full_name = escape_html(user.full_name or user.first_name or "Номаълум")
            user_mention = f"<a href='tg://user?id={user.id}'>{user_full_name}</a>"
            username_str = f" (@{user.username})" if user.username else ""
            chat_title = escape_html(chat.title or "Гурӯҳ")

            alert_text = (
                f"🗑 <b>БОТ АЗ ГУРУҲ БАРОВАРДА ШУД!</b>\n"
                f"━━━━━━━━━━━\n"
                f"📢 <b>Гурӯҳ:</b> <b>{chat_title}</b> (ID: <code>{chat.id}</code>)\n"
                f"👤 <b>Кӣ баровард?:</b> {user_mention}{escape_html(username_str)}\n"
                f"🆔 <b>TG ID:</b> <code>{user.id}</code>"
            )

            for admin_id in ADMIN_IDS:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=alert_text,
                    parse_mode="HTML"
                )
        except Exception as e:
            logging.error(f"Bot removal tracking error: {e}")


async def check_group_access(update: Update, context: ContextTypes.DEFAULT_TYPE, is_vip: bool = False) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if not chat:
        return False
    if is_admin_user(user) or is_vip:
        return True

    if chat.type in ["group", "supergroup"] and chat.id not in ALLOWED_GROUPS:
        try:
            await context.bot.leave_chat(chat.id)
        except Exception:
            pass
        return False

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


async def send_likes_autosystem(uid: str, target_amount: int) -> dict:
    """Фиристодани лайк тавассути API-и AutoLikeSystem"""
    total_added = 0
    first_before = "N/A"
    last_after = "N/A"
    nickname = "N/A"
    resp_uid = uid
    last_error = ""
    raw_first = {}

    async with aiohttp.ClientSession() as session:
        if target_amount == 100:
            url_as = f"{AUTOSYSTEM_URL}/like?key={AUTOSYSTEM_KEY}&uid={uid}&qtd=100"
        else:
            url_as = f"{AUTOSYSTEM_URL}/like?key={AUTOSYSTEM_KEY}&uid={uid}"

        try:
            timeout_cfg = aiohttp.ClientTimeout(total=95)
            async with session.get(url_as, timeout=timeout_cfg) as resp_as:
                raw_as = await resp_as.text()
            data_as = json.loads(raw_as)

            if isinstance(data_as, dict) and data_as.get("sucesso") is True:
                raw_first = data_as
                d2 = data_as.get("data", data_as)
                nickname = d2.get("nick") or d2.get("nickname") or d2.get("nome") or nickname

                before_val = (
                    d2.get("likes_antes") if d2.get("likes_antes") is not None
                    else d2.get("antes") if d2.get("antes") is not None
                    else d2.get("likes_before")
                )
                after_val = (
                    d2.get("likes_depois") if d2.get("likes_depois") is not None
                    else d2.get("depois") if d2.get("depois") is not None
                    else d2.get("likes_after")
                )

                if before_val is not None:
                    first_before = str(before_val)
                if after_val is not None:
                    last_after = str(after_val)

                added_as = int(
                    d2.get("likes_enviados") or d2.get("enviados")
                    or d2.get("likes_sent") or d2.get("added") or 0
                )
                if added_as <= 0 and isinstance(before_val, int) and isinstance(after_val, int) and after_val > before_val:
                    added_as = after_val - before_val

                if added_as > 0:
                    total_added = added_as
                else:
                    last_error = str(
                        data_as.get("mensagem") or data_as.get("erro")
                        or data_as.get("message") or "AutoLikeSystem: 0 лайк"
                    )
            else:
                last_error = str(
                    data_as.get("mensagem") or data_as.get("erro")
                    or data_as.get("message") or raw_as[:150]
                )
        except asyncio.TimeoutError:
            last_error = "AutoLikeSystem: вақти интизорӣ (95с) тамом шуд"
        except Exception as e_as:
            last_error = f"AutoLikeSystem: {e_as}"

    return {
        "total_added": total_added,
        "first_before": first_before,
        "last_after": last_after,
        "nickname": nickname,
        "uid": resp_uid,
        "error": last_error,
        "raw_first": raw_first
    }


async def run_kinematic_animation(progress_msg, uid: str, amount: int, vip_flag: bool = False):
    vip_header = "💎 <b>VIP AUTOLIKE ФАЪОЛ</b>\n" if vip_flag else ""
    frames = [
        ("⚡ <b>Пайвастшавӣ ба сервери AutoLikeSystem...</b>", "▰▱▱▱▱▱▱▱▱▱", "17%", "🔄 <i>Тафтиши дастрасӣ...</i>"),
        ("🔍 <b>Ҷустуҷӯи аккаунти бозингар...</b>", "▰▰▰▱▱▱▱▱▱▱", "34%", "📡 <i>Дарёфти маълумоти UID...</i>"),
        (f"⚙️ <b>Омодасозии {amount} лайк...</b>", "▰▰▰▰▰▱▱▱▱▱", "52%", "📦 <i>Танзими бастаи лайкҳо...</i>"),
        ("🚀 <b>Фиристодани лайкҳо ба сервер...</b>", "▰▰▰▰▰▰▰▱▱", "71%", "🔥 <i>Ин раванд метавонад то 60-80 сония тӯл кашад...</i>"),
        ("❤️‍🔥 <b>Воридшавии лайкҳо ба аккаунт...</b>", "▰▰▰▰▰▰▰▰▰▱", "89%", "✨ <i>Санҷиши ҳисобкунаки профил...</i>"),
    ]
    for title, bar, pct, sub_status in frames:
        await asyncio.sleep(1.2)
        try:
            await progress_msg.edit_text(
                f"{vip_header}"
                f"🔥 <b>PARNETKA SYSTEM</b>\n"
                f"<blockquote>"
                f"{title}\n"
                f"🆔 <b>UID:</b> <code>{uid}</code>\n"
                f"📊 <b>Ҳолат:</b> <code>[{bar}]</code> <b>{pct}</b>\n"
                f"{sub_status}"
                f"</blockquote>",
                parse_mode="HTML"
            )
        except Exception:
            pass


async def like_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await load_state()

    user = update.effective_user
    admin_flag = is_admin_user(user)

    vip_flag = False
    if context.args and len(context.args) >= 1:
        check_uid = context.args[-1].strip()
        vip_flag = is_vip_uid(check_uid)

    if not await check_group_access(update, context, is_vip=vip_flag):
        return

    if len(context.args) == 1:
        amount_str = str(ADMIN_DEFAULT_LIKES if admin_flag else DEFAULT_LIKES_AMOUNT)
        uid = context.args[0].strip()
    elif len(context.args) == 2:
        amount_str = context.args[0].strip()
        uid = context.args[1].strip()
    else:
        await update.message.reply_text(
            "⚠️ <b>ТАРЗИ ИСТИФОДАБАРӢ</b>\n"
            "━━━━━━━━━━━\n"
            f"👉 <code>/like {{UID}}</code> ё <code>/like {DEFAULT_LIKES_AMOUNT} {{UID}}</code>\n"
            "📌 Намуна: <code>/like 18778177236</code>",
            parse_mode="HTML",
            reply_markup=DEVELOPER_KEYBOARD
        )
        return

    if not amount_str.isdigit() or not uid.isdigit() or not (8 <= len(uid) <= 12):
        await update.message.reply_text(
            "❌ **ФОРМАТИ ХАТО!**\n"
            "📌 `/like 18778177236`",
            parse_mode="Markdown"
        )
        return

    amount = int(amount_str)
    tg_user_id = str(user.id)

    lock_key_user = f"user_{tg_user_id}"
    lock_key_uid = f"uid_{uid}"
    if lock_key_user in active_requests or lock_key_uid in active_requests:
        return

    if not admin_flag:
        if amount != DEFAULT_LIKES_AMOUNT:
            await update.message.reply_text(
                f"⚠️ <b>ТАНҲО {DEFAULT_LIKES_AMOUNT} ЛАЙК ДАСТРАС АСТ!</b>\n"
                f"📌 Нависед: <code>/like {uid}</code> ё <code>/like {DEFAULT_LIKES_AMOUNT} {uid}</code>"
                f"{VIP_PROMO_FOOTER}",
                parse_mode="HTML",
                reply_markup=DEVELOPER_KEYBOARD
            )
            return

    now = time.time()
    tg_full_name = clean_name(user.full_name or user.first_name)
    memory_uids = db_cache.get("uids", {})
    user_history = db_cache.get("user_history", {})

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

        if tg_user_id not in user_history:
            user_history[tg_user_id] = []

        user_history[tg_user_id] = [
            item for item in user_history[tg_user_id]
            if (now - float(item["time"])) < COOLDOWN_TIME
        ]

        if len(user_history[tg_user_id]) >= MAX_IDS_PER_USER:
            oldest_item = min(user_history[tg_user_id], key=lambda x: float(x["time"]))
            rem_hms = format_hms(COOLDOWN_TIME - (now - float(oldest_item["time"])))

            await update.message.reply_text(
                "⚠️ <b>ЛИМИТИ АККАУНТИ TELEGRAM ПУР АСТ!</b>\n"
                "━━━━━━━━━━━\n"
                f"⏳ <b>Вақти боқимонда:</b> <code>{rem_hms}</code>"
                f"{VIP_PROMO_FOOTER}",
                parse_mode="HTML",
                reply_markup=DEVELOPER_KEYBOARD
            )
            return

    active_requests.add(lock_key_user)
    active_requests.add(lock_key_uid)

    likes_sent_successfully = False

    try:
        if not admin_flag and not vip_flag:
            user_history[tg_user_id].append({"uid": uid, "time": now})
            memory_uids[uid] = {
                "time": now,
                "user_id": tg_user_id,
                "name": tg_full_name
            }
            db_cache["uids"] = memory_uids
            db_cache["user_history"] = user_history
            await save_state()

        vip_header = "💎 <b>VIP AUTOLIKE ФАЪОЛ</b>\n" if vip_flag else ""
        progress = await update.message.reply_text(
            f"{vip_header}"
            f"🔥 <b>PARNETKA SYSTEM</b>\n"
            f"<blockquote>"
            f"⚡ <b>Оғози системаи лайк...</b>\n"
            f"🆔 <b>UID:</b> <code>{uid}</code>\n"
            f"📊 <b>Ҳолат:</b> <code>[▱▱▱▱▱▱▱▱▱▱]</code> <b>1%</b>\n"
            f"</blockquote>",
            parse_mode="HTML"
        )

        api_task = asyncio.create_task(send_likes_autosystem(uid, amount))
        await run_kinematic_animation(progress, uid, amount, vip_flag)
        res = await api_task

        if res["total_added"] <= 0:
            if not admin_flag and not vip_flag:
                memory_uids.pop(uid, None)
                user_history[tg_user_id] = [
                    item for item in user_history.get(tg_user_id, [])
                    if item["uid"] != uid
                ]
                db_cache["uids"] = memory_uids
                db_cache["user_history"] = user_history
                await save_state()

            err_reason = escape_html(res.get("error") or "Лимити рӯзона пур аст")
            await progress.edit_text(
                "⚠️ <b>ЛАЙК ФИРИСТОДА НАШУД (0 ЛАЙК)!</b>\n"
                "━━━━━━━━━━━\n"
                f"🆔 <b>UID:</b> <code>{uid}</code>\n"
                f"📌 <b>Сабаб:</b> <code>{err_reason}</code>"
                f"{VIP_PROMO_FOOTER}",
                parse_mode="HTML",
                reply_markup=DEVELOPER_KEYBOARD
            )
            return

        likes_sent_successfully = True

        p_info = await fetch_player_info_autosystem(uid)
        player_level = find_level_recursive(res["raw_first"]) or p_info["level"]
        nickname = res["nickname"] if res["nickname"] != "N/A" else p_info["nickname"]

        likes_given = res["total_added"]
        before_likes = escape_html(res["first_before"])
        after_likes = escape_html(res["last_after"])
        nickname = escape_html(nickname)
        final_uid = escape_html(res["uid"] or uid)

        vip_rem_str = ""
        if vip_flag:
            vip_exp_time = float(db_cache["vips"][uid])
            vip_rem_str = format_remaining(vip_exp_time - time.time())
            status_line = f"💎 <b>VIP STATUS:</b> Боқимонда <code>{vip_rem_str}</code>"
        else:
            status_line = "⏳ <i>Лайки навбатӣ ба ин UID пас аз 24 соат!</i>"

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
        if not likes_sent_successfully and not admin_flag and not vip_flag:
            memory_uids.pop(uid, None)
            user_history[tg_user_id] = [
                item for item in user_history.get(tg_user_id, [])
                if item["uid"] != uid
            ]
            db_cache["uids"] = memory_uids
            db_cache["user_history"] = user_history
            await save_state()
        logging.error(f"/like error: {e}")
        try:
            await progress.edit_text("❌ **Хатогии сервер!**", parse_mode="Markdown")
        except Exception:
            pass
    finally:
        active_requests.discard(lock_key_user)
        active_requests.discard(lock_key_uid)


async def turbo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("📌 Намуна: `/turbo 18778177236`", parse_mode="Markdown")
        return

    uid = context.args[0].strip()
    msg = await update.message.reply_text(
        f"⚡ **TURBO VIP ОҒОЗ ШУД!**\n🆔 UID: `{uid}`\n⏳ *Фиристодани лайкҳо...*",
        parse_mode="Markdown"
    )

    url = f"{AUTOSYSTEM_URL}/turbo?key={AUTOSYSTEM_KEY}&uid={uid}"
    try:
        timeout_cfg = aiohttp.ClientTimeout(total=95)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=timeout_cfg) as resp:
                raw = await resp.text()
        data = json.loads(raw)

        if isinstance(data, dict) and data.get("sucesso") is True:
            await msg.edit_text(
                f"✅ **TURBO VIP БОМУВАФФАҚИЯТ ФИРИСТОДА ШУД!**\n🆔 UID: `{uid}`",
                parse_mode="Markdown"
            )
        else:
            await msg.edit_text(f"⚠️ **Ҷавоби сервер:**\n`{raw[:300]}`", parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ **Хатогӣ:** `{e}`", parse_mode="Markdown")


async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_group_access(update, context):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("📌 Намуна: `/info 18778177236`", parse_mode="Markdown")
        return

    uid = context.args[0].strip()
    p_info = await fetch_player_info_autosystem(uid)
    await update.message.reply_text(
        f"👤 <b>МАЪЛУМОТИ АККАУНТ</b>\n"
        f"<blockquote>"
        f"🏷 <b>Ном:</b> <code>{escape_html(p_info['nickname'])}</code>\n"
        f"🆔 <b>UID:</b> <code>{uid}</code>\n"
        f"📶 <b>Level:</b> <code>{escape_html(p_info['level'])}</code>\n"
        f"❤️ <b>Лайкҳо:</b> <code>{escape_html(p_info['likes'])}</code>"
        f"</blockquote>",
        parse_mode="HTML",
        reply_markup=DEVELOPER_KEYBOARD
    )


async def remain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{AUTOSYSTEM_URL}/autolike/list?key={AUTOSYSTEM_KEY}", timeout=15) as resp_as:
            raw_as = await resp_as.text()
    await update.message.reply_text(
        f"📡 **ҲОЛАТИ AUTOLIKESYSTEM:**\n`{raw_as[:500]}`",
        parse_mode="Markdown"
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    if not context.args:
        await update.message.reply_text("📌 Намуна: `/reset 18778177236`", parse_mode="Markdown")
        return

    target = context.args[0].strip()
    await load_state()
    removed = False

    if target in db_cache.get("uids", {}):
        db_cache["uids"].pop(target)
        removed = True

    if target in db_cache.get("user_history", {}):
        db_cache["user_history"].pop(target)
        removed = True

    if removed:
        await save_state()
        await update.message.reply_text(f"✅ Лимит барои `{target}` кушода шуд!", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ `{target}` дар лимит набуд.", parse_mode="Markdown")


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
    for uid, entry in memory_uids.items():
        ts = float(entry["time"]) if isinstance(entry, dict) else float(entry)
        u_name = entry.get("name", "Корбар") if isinstance(entry, dict) else "Корбар"
        unlock_ts = ts + COOLDOWN_TIME
        if unlock_ts > now:
            active_items.append((unlock_ts, uid, u_name))

    if not active_items:
        await update.message.reply_text("✅ Ҳамаи UID-ҳо озоданд!", parse_mode="HTML")
        return

    active_items.sort(key=lambda x: x[0])
    lines = [f"🔥 <b>РӮЙХАТИ ЛИМИТҲО ({len(active_items)} то):</b>"]
    for idx, (unlock_ts, uid, u_name) in enumerate(active_items, start=1):
        rem_hms = format_hms(unlock_ts - now)
        unlock_clock = datetime.fromtimestamp(unlock_ts, tz=tj_tz).strftime("%H:%M:%S (%d.%m)")
        lines.append(f"<blockquote><b>{idx}.</b> 👤 <b>{escape_html(u_name)}</b>\n🆔 <code>{uid}</code>\n⏳ <code>{rem_hms}</code></blockquote>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def addvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.message.reply_text("📌 Намуна: `/addvip 18778177236 5`", parse_mode="Markdown")
        return
    uid = context.args[0].strip()
    days = int(context.args[1].strip())
    await load_state()
    db_cache["vips"][uid] = time.time() + (days * 86400)
    db_cache["uids"].pop(uid, None)
    await save_state()

    try:
        url_auto = f"{AUTOSYSTEM_URL}/autolike/add?key={AUTOSYSTEM_KEY}&uid={uid}&dias={days}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url_auto, timeout=15) as resp:
                pass
    except Exception:
        pass

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
        try:
            url_rem = f"{AUTOSYSTEM_URL}/autolike/remove?key={AUTOSYSTEM_KEY}&uid={uid}"
            async with aiohttp.ClientSession() as session:
                await session.get(url_rem, timeout=15)
        except Exception:
            pass
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
        ptb_application.add_handler(CommandHandler("turbo", turbo_command))
        ptb_application.add_handler(CommandHandler("info", info_command))
        ptb_application.add_handler(CommandHandler("remain", remain_command))
        ptb_application.add_handler(CommandHandler("reset", reset_command))
        ptb_application.add_handler(CommandHandler("check", check_command))
        ptb_application.add_handler(CommandHandler("limits", limits_command))
        ptb_application.add_handler(CommandHandler("addvip", addvip_command))
        ptb_application.add_handler(CommandHandler("delvip", delvip_command))
        ptb_application.add_handler(CommandHandler("viplist", viplist_command))
        ptb_application.add_handler(CommandHandler("delgif", delgif_command))
        ptb_application.add_handler(ChatMemberHandler(track_bot_removal, ChatMemberHandler.MY_CHAT_MEMBER))
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
