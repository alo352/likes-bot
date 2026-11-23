import io
import os
import time
import json
import hmac
import asyncio
import logging
import aiohttp
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request, HTTPException
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ChatMemberHandler,
    filters,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Дар сервер (масалан Vercel Environment Variables) BOT_TOKEN, API_KEY, FIREBASE_DB_URL ва WEBHOOK_SECRET-ро насб кунед
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
LIKES_API_KEY = os.getenv("API_KEY", "")
BASE_API_URL = os.getenv("BASE_API_URL", "https://fflikes.ucbot.net")
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

ADMIN_IDS = [6410886684]
ALLOWED_GROUPS = [-1004356261091]

COOLDOWN_TIME = 86400  # 24 соат
DEFAULT_LIKES_AMOUNT = 100  # Барои корбарони оддӣ 100 лайк
ADMIN_DEFAULT_LIKES = 200  # Барои Админ ба таври автоматӣ 200 лайк
MAX_UIDS_PER_USER = 1  # Ҳар як аккаунти Telegram дар 24 соат танҳо ба 1 UID лайк зада метавонад
USER_SPAM_DELAY = 4.0  # Фосилаи ҳадди ақал байни дархостҳои пайдарпайи корбари оддӣ (сония)

db_cache = {
    "uids": {},
    "user_history": {},
    "vips": {},
    "like_gif_id": "",
    "daily_stats": {},
}
ptb_application = None

# Қулфҳои зидди пахши чандкарата ва анти-спам
active_requests = set()
user_last_attempt = {}

DEVELOPER_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("👨‍💻 АДМИН / ХАРИДИ VIP", url="https://t.me/parnetka")]]
)

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


def get_tj_now() -> datetime:
    tj_tz = timezone(timedelta(hours=5))
    return datetime.now(tz=tj_tz)


def get_today_key() -> str:
    return get_tj_now().strftime("%Y-%m-%d")


def cleanup_expired_state(now: float):
    uids = db_cache.get("uids", {})
    for uid in list(uids.keys()):
        entry = uids[uid]
        ts = float(entry["time"]) if isinstance(entry, dict) else float(entry)
        if (now - ts) >= COOLDOWN_TIME:
            uids.pop(uid, None)
    db_cache["uids"] = uids

    history = db_cache.get("user_history", {})
    for tg_id in list(history.keys()):
        items = [
            item for item in history.get(tg_id, [])
            if (now - float(item.get("time", 0))) < COOLDOWN_TIME
        ]
        if items:
            history[tg_id] = items
        else:
            history.pop(tg_id, None)
    db_cache["user_history"] = history

    vips = db_cache.get("vips", {})
    for uid in list(vips.keys()):
        if float(vips[uid]) <= now:
            vips.pop(uid, None)
    db_cache["vips"] = vips


async def load_state():
    global db_cache
    if not FIREBASE_DB_URL:
        cleanup_expired_state(time.time())
        return
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
                        db_cache["daily_stats"] = fb_data.get("daily_stats", {}) or {}
    except Exception as e:
        logging.error(f"Load state error: {e}")
    cleanup_expired_state(time.time())


async def save_state():
    global db_cache
    cleanup_expired_state(time.time())
    if not FIREBASE_DB_URL:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.put(FIREBASE_DB_URL, json=db_cache, timeout=5):
                pass
    except Exception as e:
        logging.error(f"Save state error: {e}")


def record_daily_stat(likes_added: int, is_admin: bool, is_vip: bool):
    day_key = get_today_key()
    stats = db_cache.setdefault("daily_stats", {})
    day_entry = stats.setdefault(
        day_key,
        {
            "requests": 0,
            "likes_sent": 0,
            "admin_requests": 0,
            "vip_requests": 0,
            "regular_requests": 0,
        },
    )
    day_entry["requests"] = int(day_entry.get("requests", 0)) + 1
    day_entry["likes_sent"] = int(day_entry.get("likes_sent", 0)) + int(likes_added)
    if is_admin:
        day_entry["admin_requests"] = int(day_entry.get("admin_requests", 0)) + 1
    elif is_vip:
        day_entry["vip_requests"] = int(day_entry.get("vip_requests", 0)) + 1
    else:
        day_entry["regular_requests"] = int(day_entry.get("regular_requests", 0)) + 1


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
    return f"{hours:02d}ч : {minutes:02d}м : {seconds:02d}с"


def format_unlock_clock(unlock_timestamp: float) -> str:
    tj_tz = timezone(timedelta(hours=5))
    return datetime.fromtimestamp(unlock_timestamp, tz=tj_tz).strftime("%H:%M:%S (%d.%m.%Y)")


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
    if not LIKES_API_KEY:
        return "N/A"
    info_url = f"{BASE_API_URL}/check-id?api_key={LIKES_API_KEY}&uid={uid}"
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


def _get_font_candidates():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(base_dir)
    return [
        os.path.join(base_dir, "font.ttf"),
        os.path.join(parent_dir, "font.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]


def _get_loaded_fonts(size: int):
    loaded = []
    seen_paths = set()
    for path in _get_font_candidates():
        if path and path not in seen_paths and os.path.exists(path):
            seen_paths.add(path)
            try:
                loaded.append(ImageFont.truetype(path, size=size))
            except Exception:
                pass
    if not loaded:
        try:
            loaded.append(ImageFont.load_default(size=size))
        except TypeError:
            loaded.append(ImageFont.load_default())
    return loaded


def _char_supported(font, ch: str) -> bool:
    if ch == " ":
        return True
    try:
        mask = font.getmask(ch)
        return bool(mask and mask.getbbox() is not None)
    except Exception:
        return False


def _draw_mixed_text(draw: ImageDraw.ImageDraw, xy, text: str, size: int, fill):
    fonts = _get_loaded_fonts(size)
    x, y = xy
    for ch in str(text):
        chosen_font = fonts[-1]
        for f in fonts:
            if _char_supported(f, ch):
                chosen_font = f
                break
        draw.text((x, y), ch, font=chosen_font, fill=fill)
        try:
            bbox = draw.textbbox((x, y), ch, font=chosen_font)
            char_w = max(0, bbox[2] - bbox[0])
            if char_w == 0 and ch == " ":
                char_w = max(6, size // 3)
            x += char_w
        except Exception:
            x += max(10, int(size * 0.55))


def _sanitize_for_card(text: str, fallback: str = "") -> str:
    cleaned = "".join(ch for ch in str(text or "") if ord(ch) < 0x10000 and ch not in ("\r", "\n", "\t")).strip()
    return cleaned if cleaned else fallback


def build_like_result_card(
    nickname: str,
    uid: str,
    level: str,
    before_likes: str,
    added_likes: int,
    after_likes: str,
    is_vip: bool = False,
    is_admin: bool = False,
) -> io.BytesIO:
    width, height = 960, 260
    img = Image.new("RGB", (width, height), (14, 17, 28))
    draw = ImageDraw.Draw(img)

    # Градиенти замина ва корти тозаи уфуқӣ бе 3 катаки поён
    for y in range(height):
        ratio = y / max(1, height - 1)
        r = int(14 + ratio * 14)
        g = int(17 + ratio * 18)
        b = int(28 + ratio * 28)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    accent = (245, 158, 11) if is_vip else ((168, 85, 247) if is_admin else (56, 189, 248))
    panel_bg = (22, 27, 42)

    draw.rounded_rectangle([18, 18, width - 18, height - 18], radius=24, fill=panel_bg, outline=accent, width=3)
    draw.rounded_rectangle([40, 40, 210, height - 40], radius=18, fill=(32, 39, 58), outline=accent, width=2)

    badge_text = "VIP" if is_vip else ("ADM" if is_admin else "FF")
    _draw_mixed_text(draw, (92, 110), badge_text, size=30, fill=accent)

    safe_nick = _sanitize_for_card(nickname, "Player")
    if len(safe_nick) > 20:
        safe_nick = safe_nick[:19] + "..."
    safe_uid = _sanitize_for_card(uid, "0")
    safe_lvl = _sanitize_for_card(level, "")
    safe_after = _sanitize_for_card(after_likes, "0")

    # Номи калон бо санҷиши худкори шрифт барои ҳар як ҳарф/рамз
    _draw_mixed_text(draw, (236, 52), safe_nick, size=48, fill=(255, 255, 255))

    meta_items = [f"UID: {safe_uid}", f"LIKES: {safe_after} (+{int(added_likes)})"]
    if safe_lvl and safe_lvl.upper() != "N/A":
        lvl_txt = safe_lvl if safe_lvl.upper().startswith("LVL") else f"LVL {safe_lvl}"
        meta_items.append(lvl_txt)

    _draw_mixed_text(draw, (236, 154), "   |   ".join(meta_items), size=25, fill=accent)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


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

            try:
                user_full_name = escape_html(user.full_name or user.first_name or "Номаълум")
                user_mention = f"<a href='tg://user?id={user.id}'>{user_full_name}</a>"
                username_str = f" (@{user.username})" if user.username else ""
                chat_title = escape_html(chat.title or "Гурӯҳ")
                chat_username = f" (@{chat.username})" if chat.username else ""
                role_uz = "АДМИН" if new_status == "administrator" else "АЪЗО"

                alert_text = (
                    f"🛡 <b>КӮШИШИ ИЛОВА/АДМИН КАРДАНИ БОТ БАСТА ШУД!</b>\n"
                    f"━━━━━━━━━━━\n"
                    f"📢 <b>Гурӯҳ:</b> <b>{chat_title}</b>{escape_html(chat_username)} (ID: <code>{chat.id}</code>)\n"
                    f"👤 <b>Кӣ кард?:</b> {user_mention}{escape_html(username_str)}\n"
                    f"🆔 <b>TG ID:</b> <code>{user.id}</code>\n"
                    f"🎭 <b>Нақш:</b> <code>{role_uz}</code>\n"
                    f"🚪 <i>Бот худкор аз ин гурӯҳ баромад!</i>"
                )
                for admin_id in ADMIN_IDS:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=alert_text,
                        parse_mode="HTML",
                    )
            except Exception as e:
                logging.error(f"Unauthorized add alert error: {e}")
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
                f"🆔 <b>TG ID:</b> <code>{user.id}</code>\n"
                f"⚠️ <i>Бот бо муваффақият аз ин гурӯҳ ва аз ин аккаунт хориҷ карда шуд!</i>"
            )

            for admin_id in ADMIN_IDS:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=alert_text,
                    parse_mode="HTML",
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

        try:
            user_full_name = escape_html(user.full_name or user.first_name or "Номаълум")
            user_mention = f"<a href='tg://user?id={user.id}'>{user_full_name}</a>"
            username_str = f" (@{user.username})" if user.username else ""
            chat_title = escape_html(chat.title or "Номаълум")
            chat_username = f" (@{chat.username})" if chat.username else f" (ID: <code>{chat.id}</code>)"

            alert_text = (
                f"🚨 <b>ҲУШДОР АМНИЯТӢ!</b>\n"
                f"━━━━━━━━━━━\n"
                f"👤 <b>Ном:</b> {user_mention}{escape_html(username_str)}\n"
                f"🆔 <b>TG ID:</b> <code>{user.id}</code>\n"
                f"📢 <b>Гурӯҳ:</b> <b>{chat_title}</b>{chat_username}\n"
                f"⚡ <i>Кӯшиш кард боти шуморо дар ин гурӯҳ истифода барад! Бот аз гурӯҳ баромад.</i>"
            )

            for admin_id in ADMIN_IDS:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=alert_text,
                    parse_mode="HTML",
                )
        except Exception as e:
            logging.error(f"Security alert error: {e}")

        return False

    if chat.type == "private":
        if update.message:
            await update.message.reply_text(
                ACCESS_DENIED_TEXT,
                parse_mode="Markdown",
                reply_markup=DEVELOPER_KEYBOARD,
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
            parse_mode="Markdown",
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
            reply_markup=DEVELOPER_KEYBOARD,
        )


async def send_likes_batch(uid: str, target_amount: int) -> dict:
    total_added = 0
    first_before = None
    last_after = None
    nickname = "N/A"
    resp_uid = uid
    last_error = ""
    raw_first = {}

    if not LIKES_API_KEY:
        return {
            "total_added": 0,
            "first_before": "N/A",
            "last_after": "N/A",
            "nickname": nickname,
            "uid": resp_uid,
            "error": "API_KEY танзим нашудааст",
            "raw_first": {},
        }

    max_calls = max(1, (target_amount + 99) // 100)
    url = f"{BASE_API_URL}/like?api_key={LIKES_API_KEY}&uid={uid}"

    async with aiohttp.ClientSession() as session:
        for _ in range(max_calls):
            if total_added >= target_amount:
                break

            try:
                async with session.get(url, timeout=15) as resp:
                    raw = await resp.text()
                    http_status = resp.status

                if http_status == 429:
                    last_error = "Лимити дархостҳои API пур шудааст (429)"
                    break
                if http_status == 503:
                    last_error = "Захираи имрӯзаи сервер тамом шудааст ё муваққатан дастнорас аст (503)"
                    break

                data = json.loads(raw) if raw else {}

                if not isinstance(data, dict) or data.get("success") is not True:
                    last_error = str(data.get("error") or data.get("message") or f"HTTP {http_status}")
                    break

                if not raw_first:
                    raw_first = data

                details = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
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
        "raw_first": raw_first,
    }


async def run_kinematic_animation(progress_msg, uid: str, amount: int, vip_flag: bool = False, has_gif: bool = False):
    badge = "👑 <b>PARNETKA VIP</b>" if vip_flag else "⚡️ <b>PARNETKA ENGINE</b>"
    frames = [
        ("⠋", "Пайвастшавӣ ба сервер...", "▰▱▱▱▱▱▱▱", "14%"),
        ("⠙", "Санҷиши профили UID...", "▰▰▰▱▱▱▱▱", "32%"),
        ("⠹", f"Омодасозии +{amount} лайк...", "▰▰▰▰▰▱▱▱", "58%"),
        ("⠸", "Фиристодани лайкҳо...", "▰▰▰▰▰▰▰▱", "84%"),
        ("🏆", "Анҷом ёфт! Натиҷа...", "▰▰▰▰▰▰▰▰", "100%"),
    ]
    for spinner, status, bar, pct in frames:
        await asyncio.sleep(0.55)
        frame_text = (
            f"{badge} • <code>{uid}</code>\n"
            f"<blockquote>{spinner} <b>{status}</b>\n"
            f"<code>[{bar}]</code> <b>{pct}</b> • <code>+{amount} ❤️</code></blockquote>"
        )
        try:
            if has_gif:
                await progress_msg.edit_caption(caption=frame_text, parse_mode="HTML")
            else:
                await progress_msg.edit_text(text=frame_text, parse_mode="HTML")
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

    tg_user_id = str(user.id)
    now = time.time()

    if not admin_flag:
        last_try = user_last_attempt.get(tg_user_id, 0.0)
        if (now - last_try) < USER_SPAM_DELAY:
            wait_sec = max(1, int(USER_SPAM_DELAY - (now - last_try)))
            await update.message.reply_text(
                f"⏳ <i>Илтимос {wait_sec} сония интизор шавед ва дубора кӯшиш кунед.</i>",
                parse_mode="HTML",
            )
            return
        user_last_attempt[tg_user_id] = now

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
            reply_markup=DEVELOPER_KEYBOARD,
        )
        return

    if not amount_str.isdigit() or not uid.isdigit() or not (7 <= len(uid) <= 15):
        await update.message.reply_text(
            "❌ **ФОРМАТИ ХАТО!**\n"
            "📌 `/like 18778177236`",
            parse_mode="Markdown",
        )
        return

    amount = int(amount_str)

    lock_key_user = f"user_{tg_user_id}"
    lock_key_uid = f"uid_{uid}"
    if lock_key_user in active_requests or lock_key_uid in active_requests:
        return

    if not admin_flag and amount != DEFAULT_LIKES_AMOUNT:
        await update.message.reply_text(
            f"⚠️ <b>ТАНҲО {DEFAULT_LIKES_AMOUNT} ЛАЙК ДАСТРАС АСТ!</b>\n"
            f"📌 Нависед: <code>/like {uid}</code> ё <code>/like {DEFAULT_LIKES_AMOUNT} {uid}</code>"
            f"{VIP_PROMO_FOOTER}",
            parse_mode="HTML",
            reply_markup=DEVELOPER_KEYBOARD,
        )
        return

    tg_full_name = clean_name(user.full_name or user.first_name)
    memory_uids = db_cache.get("uids", {})
    user_history = db_cache.get("user_history", {})

    if not admin_flag and not vip_flag:
        if uid in memory_uids:
            uid_entry = memory_uids[uid]
            uid_time = float(uid_entry["time"]) if isinstance(uid_entry, dict) else float(uid_entry)
            elapsed_uid = now - uid_time
            if elapsed_uid < COOLDOWN_TIME:
                unlock_ts = uid_time + COOLDOWN_TIME
                rem_hms = format_hms(unlock_ts - now)
                unlock_str = format_unlock_clock(unlock_ts)
                await update.message.reply_text(
                    "⚠️ <b>ИН АКАУНТ АЛЛАКАЙ ЛАЙК ГИРИФТААСТ!</b>\n"
                    "━━━━━━━━━━━\n"
                    f"🆔 <b>UID:</b> <code>{uid}</code>\n"
                    f"⏳ <b>Боқимонда:</b> <code>{rem_hms}</code>\n"
                    f"🔓 <b>Вақти дубора лайк гирифтан:</b>\n"
                    f"👉 <code>{unlock_str}</code>\n\n"
                    "🚫 <i>Ба ин UID дар 24 соат аз ягон аккаунт лайк зада намешавад!</i>"
                    f"{VIP_PROMO_FOOTER}",
                    parse_mode="HTML",
                    reply_markup=DEVELOPER_KEYBOARD,
                )
                return

        if tg_user_id not in user_history:
            user_history[tg_user_id] = []

        user_history[tg_user_id] = [
            item for item in user_history[tg_user_id]
            if (now - float(item["time"])) < COOLDOWN_TIME
        ]

        if len(user_history[tg_user_id]) >= MAX_UIDS_PER_USER:
            oldest_item = min(user_history[tg_user_id], key=lambda x: float(x["time"]))
            unlock_ts = float(oldest_item["time"]) + COOLDOWN_TIME
            rem_hms = format_hms(unlock_ts - now)
            unlock_str = format_unlock_clock(unlock_ts)

            await update.message.reply_text(
                "⚠️ <b>ЛИМИТИ АККАУНТИ TELEGRAM ПУР АСТ!</b>\n"
                "━━━━━━━━━━━\n"
                f"👤 <b>Корбар:</b> <code>{escape_html(tg_full_name)}</code>\n"
                f"⏳ <b>Боқимонда:</b> <code>{rem_hms}</code>\n"
                f"🔓 <b>Вақти дубора лайк гирифтан:</b>\n"
                f"👉 <code>{unlock_str}</code>\n\n"
                f"🚫 <i>Шумо дар 24 соат танҳо ба {MAX_UIDS_PER_USER} UID лайк фиристода метавонед!</i>"
                f"{VIP_PROMO_FOOTER}",
                parse_mode="HTML",
                reply_markup=DEVELOPER_KEYBOARD,
            )
            return

    active_requests.add(lock_key_user)
    active_requests.add(lock_key_uid)

    likes_sent_successfully = False
    like_gif_id = db_cache.get("like_gif_id", "")
    has_gif = bool(like_gif_id)
    lock_start_time = time.time()
    progress = None

    try:
        if not admin_flag and not vip_flag:
            memory_uids[uid] = {
                "time": lock_start_time,
                "user_id": tg_user_id,
                "name": tg_full_name,
            }
            db_cache["uids"] = memory_uids
            user_history[tg_user_id].append({"uid": uid, "time": lock_start_time})
            db_cache["user_history"] = user_history
            await save_state()

        badge = "👑 <b>PARNETKA VIP</b>" if vip_flag else "⚡️ <b>PARNETKA ENGINE</b>"
        start_text = (
            f"{badge} • <code>{uid}</code>\n"
            f"<blockquote>🔄 <b>Оғози санҷиш...</b>\n"
            f"<code>[▱▱▱▱▱▱▱▱]</code> <b>1%</b> • <code>+{amount} ❤️</code></blockquote>"
        )

        if has_gif:
            progress = await update.message.reply_animation(
                animation=like_gif_id,
                caption=start_text,
                parse_mode="HTML",
            )
        else:
            progress = await update.message.reply_text(
                text=start_text,
                parse_mode="HTML",
            )

        api_task = asyncio.create_task(send_likes_batch(uid, amount))
        await run_kinematic_animation(progress, uid, amount, vip_flag, has_gif)
        res = await api_task

        if res["total_added"] <= 0:
            if not admin_flag and not vip_flag:
                memory_uids.pop(uid, None)
                db_cache["uids"] = memory_uids
                user_history[tg_user_id] = [
                    item for item in user_history.get(tg_user_id, [])
                    if item["uid"] != uid
                ]
                db_cache["user_history"] = user_history
                await save_state()

            err_reason = escape_html(res.get("error") or "Лимити рӯзонаи ин UID пур аст")
            unlock_ts = time.time() + COOLDOWN_TIME
            rem_hms = format_hms(COOLDOWN_TIME)
            unlock_str = format_unlock_clock(unlock_ts)
            err_text = (
                "⏳ <b>ЛИМИТИ РӮЗОНАИ ИН UID ПУР АСТ!</b>\n"
                "━━━━━━━━━━━\n"
                f"🆔 <b>UID:</b> <code>{uid}</code>\n"
                f"ℹ️ <i>Сабаб: {err_reason}.</i>\n\n"
                f"⏳ <b>Боқимонда:</b> <code>{rem_hms}</code>\n"
                f"🔓 <b>Вақти кушодашавӣ:</b> <code>{unlock_str}</code>"
                f"{VIP_PROMO_FOOTER}"
            )
            if has_gif:
                try:
                    await progress.delete()
                except Exception:
                    pass
                await update.message.reply_text(err_text, parse_mode="HTML", reply_markup=DEVELOPER_KEYBOARD)
            else:
                await progress.edit_text(err_text, parse_mode="HTML", reply_markup=DEVELOPER_KEYBOARD)
            return

        likes_sent_successfully = True
        completed_now = time.time()
        likes_given = res["total_added"]

        if not admin_flag and not vip_flag:
            memory_uids[uid] = {
                "time": completed_now,
                "user_id": tg_user_id,
                "name": tg_full_name,
            }
            db_cache["uids"] = memory_uids
            for item in user_history.get(tg_user_id, []):
                if item["uid"] == uid:
                    item["time"] = completed_now
            db_cache["user_history"] = user_history

        record_daily_stat(likes_given, admin_flag, vip_flag)
        await save_state()

        player_level = find_level_recursive(res["raw_first"]) or await fetch_player_level(uid)
        before_likes = str(res["first_before"])
        after_likes = str(res["last_after"])
        raw_nickname = str(res["nickname"])
        final_uid = str(res["uid"] or uid)

        level_line = (
            f"🏅 <b>Уровень:</b> <code>LVL {escape_html(player_level)}</code>\n"
            if player_level and player_level != "N/A"
            else ""
        )

        if vip_flag:
            vip_exp_time = float(db_cache["vips"][uid])
            vip_rem_str = format_remaining(vip_exp_time - completed_now)
            status_line = f"💎 <b>VIP-СТАТУС:</b> Осталось <code>{escape_html(vip_rem_str)}</code>"
        elif admin_flag:
            vip_rem_str = ""
            status_line = "👑 <b>РЕЖИМ АДМИНА:</b> Без ограничения 24 часа"
        else:
            vip_rem_str = ""
            unlock_ts = completed_now + COOLDOWN_TIME
            rem_hms = format_hms(unlock_ts - time.time())
            unlock_str = format_unlock_clock(unlock_ts)
            status_line = (
                f"⏳ <b>Осталось:</b> <code>{rem_hms}</code>\n"
                f"🔓 <b>Следующий лайк:</b> <code>{unlock_str}</code>"
            )

        caption_html = (
            f"⚡️ <b>PARNETKA LIKES • BOOSTED</b> ⚡️\n"
            f"🎮 <b>Игрок:</b> <code>{escape_html(raw_nickname)}</code>\n"
            f"{level_line}"
            f"🆔 <b>UID:</b> <code>{escape_html(final_uid)}</code>\n"
            f"💔 <b>Было:</b> <code>{escape_html(before_likes)}</code>  |  "
            f"❤️‍🔥 <b>Добавлено:</b> <b><code>+{likes_given}</code></b>  |  "
            f"🏆 <b>Стало:</b> <b><code>{escape_html(after_likes)}</code></b>\n"
            f"{status_line}"
        )

        if vip_flag:
            try:
                chat_info = update.effective_chat
                chat_name = escape_html(chat_info.title if chat_info.type != "private" else "Чат бо шахсият")
                user_mention = f"<a href='tg://user?id={user.id}'>{escape_html(user.full_name or 'Номаълум')}</a>"

                admin_notification = (
                    f"💎 <b>ГУЗОРИШИ АВТО-ЛАЙКИ VIP!</b>\n"
                    f"━━━━━━━━━━━\n"
                    f"👤 <b>Зананда:</b> {user_mention} (ID: <code>{user.id}</code>)\n"
                    f"📢 <b>Гурӯҳ:</b> <b>{chat_name}</b>\n"
                    f"🎮 <b>Бозингар:</b> <code>{escape_html(raw_nickname)}</code>\n"
                    f"🆔 <b>UID:</b> <code>{escape_html(final_uid)}</code>\n"
                    f"❤️‍🔥 <b>Илова шуд:</b> <code>+{likes_given} лайк</code>\n"
                    f"⏳ <b>VIP Боқимонда:</b> <code>{escape_html(vip_rem_str)}</code>"
                )
                for admin_id in ADMIN_IDS:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=admin_notification,
                        parse_mode="HTML",
                    )
            except Exception as admin_err:
                logging.error(f"VIP admin notification error: {admin_err}")

        card_buf = build_like_result_card(
            nickname=raw_nickname,
            uid=final_uid,
            level=player_level,
            before_likes=before_likes,
            added_likes=likes_given,
            after_likes=after_likes,
            is_vip=vip_flag,
            is_admin=admin_flag,
        )

        try:
            if progress is not None:
                await progress.delete()
        except Exception:
            pass

        await update.message.reply_photo(
            photo=card_buf,
            caption=caption_html,
            parse_mode="HTML",
        )

    except Exception as e:
        if not likes_sent_successfully and not admin_flag and not vip_flag:
            memory_uids.pop(uid, None)
            db_cache["uids"] = memory_uids
            user_history[tg_user_id] = [
                item for item in user_history.get(tg_user_id, [])
                if item["uid"] != uid
            ]
            db_cache["user_history"] = user_history
            await save_state()
        logging.error(f"/like error: {e}")
        try:
            if progress is not None and has_gif:
                await progress.edit_caption(caption="❌ **Хатогии сервер!**", parse_mode="Markdown")
            elif progress is not None:
                await progress.edit_text("❌ **Хатогии сервер!**", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ **Хатогии сервер!**", parse_mode="Markdown")
        except Exception:
            pass
    finally:
        active_requests.discard(lock_key_user)
        active_requests.discard(lock_key_uid)


async def checkid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_group_access(update, context):
        return

    if not context.args or not context.args[0].strip().isdigit() or not (7 <= len(context.args[0].strip()) <= 15):
        await update.message.reply_text(
            "📌 <b>ТАРЗИ САНҶИШИ UID:</b>\n"
            "👉 <code>/checkid 18778177236</code>",
            parse_mode="HTML",
        )
        return

    uid = context.args[0].strip()
    wait_msg = await update.message.reply_text(
        f"🔍 <i>Санҷиши профили UID <code>{uid}</code>...</i>",
        parse_mode="HTML",
    )

    url = f"{BASE_API_URL}/check-id?api_key={LIKES_API_KEY}&uid={uid}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                raw = await resp.text()
                status_code = resp.status

        data = json.loads(raw) if raw else {}
        if status_code != 200 or not isinstance(data, dict) or data.get("success") is False:
            err_msg = escape_html(data.get("error") or data.get("message") or f"HTTP {status_code}")
            await wait_msg.edit_text(
                f"❌ <b>САНҶИШИ UID НОМУВАФФАҚ ШУД!</b>\n"
                f"🆔 <b>UID:</b> <code>{uid}</code>\n"
                f"⚠️ <b>Сабаб:</b> <code>{err_msg}</code>",
                parse_mode="HTML",
            )
            return

        details = data.get("data") if isinstance(data.get("data"), dict) else data
        nickname = escape_html(
            details.get("PlayerNickname")
            or details.get("nickname")
            or details.get("name")
            or "N/A"
        )
        likes_now = escape_html(
            details.get("Likes")
            or details.get("likes")
            or details.get("currentLikes")
            or details.get("LikesbeforeCommand")
            or "N/A"
        )
        level = find_level_recursive(data) or "N/A"
        region = escape_html(details.get("region") or details.get("server") or "BD")

        eligible_raw = details.get("eligible200") if "eligible200" in details else details.get("canLike200")
        if eligible_raw is True:
            eligible_str = "✅ Озод (то 200 лайк)"
        elif eligible_raw is False:
            eligible_str = "⏳ Лимити 200 лайк пур аст"
        else:
            eligible_str = "ℹ️ Маълум нест"

        await wait_msg.edit_text(
            f"🔎 <b>НАТИҶАИ САНҶИШИ ПРОФИЛ</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎮 <b>Бозингар:</b> <code>{nickname}</code>\n"
            f"🆔 <b>UID:</b> <code>{uid}</code>\n"
            f"🌍 <b>Сервер:</b> <code>{region}</code>\n"
            f"🏅 <b>Сатҳ:</b> <code>{escape_html(level)}</code>\n"
            f"❤️ <b>Лайкҳои ҳозира:</b> <code>{likes_now}</code>\n"
            f"📊 <b>Ҳолати 24-соата:</b> <b>{eligible_str}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logging.error(f"/checkid error: {e}")
        await wait_msg.edit_text("❌ <b>Хатогӣ ҳангоми пайвастшавӣ ба сервер!</b>", parse_mode="HTML")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    await load_state()
    day_key = get_today_key()
    st = db_cache.get("daily_stats", {}).get(day_key, {})

    reqs = int(st.get("requests", 0))
    likes_sent = int(st.get("likes_sent", 0))
    adm_reqs = int(st.get("admin_requests", 0))
    vip_reqs = int(st.get("vip_requests", 0))
    reg_reqs = int(st.get("regular_requests", 0))
    active_limits = len(db_cache.get("uids", {}))
    active_vips = len(db_cache.get("vips", {}))

    text = (
        f"📈 <b>ОМОРИ РӮЗОНА ({escape_html(day_key)})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📨 <b>Дархостҳои муваффақ:</b> <code>{reqs}</code>\n"
        f"❤️‍🔥 <b>Лайкҳои фиристодашуда:</b> <code>+{likes_sent}</code>\n"
        f"👑 <b>Аз админ:</b> <code>{adm_reqs}</code> | 💎 <b>VIP:</b> <code>{vip_reqs}</code> | 👤 <b>Оддӣ:</b> <code>{reg_reqs}</code>\n"
        f"🔒 <b>UID-ҳои дар лимити 24ч:</b> <code>{active_limits}</code>\n"
        f"⭐️ <b>VIP-ҳои фаъол:</b> <code>{active_vips}</code>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def remain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    if not LIKES_API_KEY:
        await update.message.reply_text("❌ `API_KEY` танзим нашудааст.", parse_mode="Markdown")
        return
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_API_URL}/status?api_key={LIKES_API_KEY}", timeout=10) as resp:
            raw = await resp.text()
    await update.message.reply_text(f"📊 **БАЛАНС:**\n`{raw}`", parse_mode="Markdown")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    if not context.args:
        await update.message.reply_text("📌 Намуна: `/reset 18778177236` (UID ё TG ID)", parse_mode="Markdown")
        return

    target = context.args[0].strip()
    await load_state()
    removed = False

    if target in db_cache.get("uids", {}):
        entry = db_cache["uids"].pop(target)
        if isinstance(entry, dict) and entry.get("user_id"):
            linked_tg_id = str(entry["user_id"])
            if linked_tg_id in db_cache.get("user_history", {}):
                db_cache["user_history"][linked_tg_id] = [
                    item for item in db_cache["user_history"][linked_tg_id]
                    if item["uid"] != target
                ]
        for u_id in db_cache.get("user_history", {}):
            db_cache["user_history"][u_id] = [
                item for item in db_cache["user_history"][u_id]
                if item["uid"] != target
            ]
        removed = True

    if target in db_cache.get("user_history", {}):
        items = db_cache["user_history"].pop(target, [])
        for item in items:
            db_cache.get("uids", {}).pop(item["uid"], None)
        removed = True

    if removed:
        await save_state()
        await update.message.reply_text(
            f"✅ Лимит барои `{target}` (ҳам UID ва ҳам TG аккаунт) пурра кушода шуд!",
            parse_mode="Markdown",
        )
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
            unlock_ts = ts + COOLDOWN_TIME
            rem = format_hms(unlock_ts - now)
            unlock_str = format_unlock_clock(unlock_ts)
            await update.message.reply_text(
                f"⏳ UID `{target}` дар лимит аст.\nБоқимонда: `{rem}`\nКушодашавӣ: `{unlock_str}`",
                parse_mode="Markdown",
            )
            return
    await update.message.reply_text(f"✅ UID `{target}` озод аст.", parse_mode="Markdown")


async def limits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    await load_state()
    now = time.time()
    memory_uids = db_cache.get("uids", {})

    active_items = []
    for uid, entry in memory_uids.items():
        ts = float(entry["time"]) if isinstance(entry, dict) else float(entry)
        u_name = entry.get("name", "Корбар") if isinstance(entry, dict) else "Корбар"
        u_id = entry.get("user_id", "") if isinstance(entry, dict) else ""
        unlock_ts = ts + COOLDOWN_TIME
        if unlock_ts > now:
            active_items.append((unlock_ts, uid, u_name, u_id))

    if not active_items:
        await update.message.reply_text("✅ Ҳамаи UID-ҳо озоданд!", parse_mode="HTML")
        return

    active_items.sort(key=lambda x: x[0])
    lines = [f"🔥 <b>РӮЙХАТИ ЛИМИТҲО ({len(active_items)} то):</b>"]
    for idx, (unlock_ts, uid, u_name, u_id) in enumerate(active_items, start=1):
        rem_hms = format_hms(unlock_ts - now)
        unlock_clock = format_unlock_clock(unlock_ts)
        safe_name = escape_html(u_name)
        user_link = f'<a href="tg://user?id={u_id}">{safe_name}</a>' if u_id else f"<b>{safe_name}</b>"
        lines.append(
            f"<blockquote><b>{idx}.</b> 👤 {user_link}\n🆔 <code>{uid}</code>\n⏳ <code>{rem_hms}</code>\n🔓 <code>{unlock_clock}</code></blockquote>"
        )

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
    if not active:
        await update.message.reply_text("📭 VIP нест.")
        return
    await update.message.reply_text("💎 **VIP-МУШТАРИЁН:**\n" + "\n".join(active), parse_mode="Markdown")


app = FastAPI()


async def ensure_bot_ready():
    global ptb_application
    if ptb_application is None:
        if not BOT_TOKEN:
            raise RuntimeError("BOT_TOKEN танзим нашудааст")
        await load_state()
        ptb_application = ApplicationBuilder().token(BOT_TOKEN).build()
        ptb_application.add_handler(CommandHandler("like", like_command))
        ptb_application.add_handler(CommandHandler(["checkid", "check_id"], checkid_command))
        ptb_application.add_handler(CommandHandler("stats", stats_command))
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
    if WEBHOOK_SECRET:
        header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(header_secret, WEBHOOK_SECRET):
            raise HTTPException(status_code=403, detail="Invalid secret token")

    await ensure_bot_ready()
    req = await request.json()
    update = Update.de_json(req, ptb_application.bot)
    await ptb_application.process_update(update)
    return {"status": "ok"}
