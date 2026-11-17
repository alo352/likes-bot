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
AUTOSYSTEM_KEY = os.getenv("AUTOSYSTEM_KEY", "AUTOSYSTEM_a00dc13872dfc8c417f658cd9d29ffd6d1d0")
AUTOSYSTEM_URL = "https://autolikesystem.com.br/v1"
FIREBASE_DB_URL = "https://api-key-mgmt-default-rtdb.firebaseio.com/ff_bot_state.json"

ADMIN_IDS = [6410886684]
ALLOWED_GROUPS = [-1004356261091]

COOLDOWN_TIME = 86400
DEFAULT_LIKES_AMOUNT = 100
ADMIN_DEFAULT_LIKES = 200
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
    [InlineKeyboardButton("ADMIN / VIP", url="https://t.me/parnetka")]
])

ACCESS_DENIED_TEXT = (
    "DASTRASI MAHDUD AST!\n"
    "In bot tanho dar guruhhoi rasmii mo va baroi mizojoni VIP faol ast.\n\n"
    "Mekhohed ba botamon dastrasi yoki Klyuchi shakhsi gired?\n"
    "Ba admin murojiat kuned: @parnetka"
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
        return f"{days} ruzu {hours} soatu {minutes} daqiqah"
    if hours > 0:
        return f"{hours} soat, {minutes} daqiqah, {seconds} soniya"
    if minutes > 0:
        return f"{minutes} daqiqah, {seconds} soniya"
    return f"{seconds} soniya"

def format_hms(seconds_left: float) -> str:
    total_sec = max(0, int(seconds_left))
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    seconds = total_sec % 60
    return f"{hours:02d}soat : {minutes:02d}min : {seconds:02d}son"

def clean_name(name: str) -> str:
    if not name:
        return "Korbar"
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

async def fetch_player_info(uid: str) -> dict:
    async with aiohttp.ClientSession() as session:
        try:
            # Дархости умумӣ ба endpoint-и info бе филтр кардани регион
            info_url = f"{AUTOSYSTEM_URL}/info?key={AUTOSYSTEM_KEY}&uid={uid}"
            async with session.get(info_url, timeout=10) as resp:
                if resp.status == 200:
                    raw_text = await resp.text()
                    parsed = json.loads(raw_text)
                    lvl = find_level_recursive(parsed)
                    data = parsed.get("data", parsed) if isinstance(parsed, dict) else {}
                    nick = (
                        data.get("nickname") or data.get("nick") or data.get("nome")
                        or data.get("PlayerNickname") or data.get("name") or data.get("player_name") or "N/A"
                    )
                    likes = (
                        data.get("likes") or data.get("curtidas")
                        or data.get("Likes") or data.get("likes_antes") or "N/A"
                    )
                    return {"level": lvl or "N/A", "nickname": nick, "likes": likes}
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
            try:
                user_full_name = escape_html(user.full_name or user.first_name or "Nomalum")
                user_mention = f"<a href='tg://user?id={user.id}'>{user_full_name}</a>"
                username_str = f" (@{user.username})" if user.username else ""
                chat_title = escape_html(chat.title or "Guruh")
                alert_text = (
                    f"BOT AZ GURUH BAROVARDA SHUD!\n"
                    f"Guruh: {chat_title} (ID: {chat.id})\n"
                    f"Kard: {user_mention}{username_str}"
                )
                for admin_id in ADMIN_IDS:
                    await context.bot.send_message(chat_id=admin_id, text=alert_text, parse_mode="HTML")
            except Exception as e:
                logging.error(f"Alert error: {e}")

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
            await update.message.reply_text(ACCESS_DENIED_TEXT, reply_markup=DEVELOPER_KEYBOARD)
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
        await update.message.reply_text("GIF bomuvaffaqiyat nasb shud!")

async def delgif_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global db_cache
    if not is_admin_user(update.effective_user):
        return
    await load_state()
    db_cache["like_gif_id"] = ""
    await save_state()
    await update.message.reply_text("GIF pok karda shud.")

async def handle_private_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if is_admin_user(user):
        return
    if chat and chat.type == "private" and update.message:
        await update.message.reply_text(ACCESS_DENIED_TEXT, reply_markup=DEVELOPER_KEYBOARD)

async def add_autolike_id(uid: str) -> dict:
    last_error = ""
    success = False

    async with aiohttp.ClientSession() as session:
        url_as = f"{AUTOSYSTEM_URL}/autolike/add?key={AUTOSYSTEM_KEY}&uid={uid}&region=CIS"

        try:
            timeout_cfg = aiohttp.ClientTimeout(total=30)
            async with session.get(url_as, timeout=timeout_cfg) as resp_as:
                raw_bytes = await resp_as.read()
                raw_as = raw_bytes.decode('utf-8', errors='ignore')
            data_as = json.loads(raw_as)

            if isinstance(data_as, dict) and (data_as.get("sucesso") is True or "sucesso" not in data_as):
                success = True
            else:
                msg_err = data_as.get("mensagem") or data_as.get("erro") or data_as.get("message") or "Хатогӣ дар илова кардани ID."
                last_error = str(msg_err)
        except asyncio.TimeoutError:
            success = True
        except Exception as e_as:
            last_error = f"Xato: {e_as}"

    return {
        "success": success,
        "uid": uid,
        "error": last_error
    }

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
        uid = context.args[0].strip()
    elif len(context.args) >= 2:
        uid = context.args[1].strip()
    else:
        await update.message.reply_text("Tarzi istifoda: /like {UID}")
        return

    if not uid.isdigit() or not (8 <= len(uid) <= 12):
        await update.message.reply_text("Formati xato! Namuna: /like 18778177236")
        return

    tg_user_id = str(user.id)
    lock_key_user = f"user_{tg_user_id}"
    lock_key_uid = f"uid_{uid}"
    if lock_key_user in active_requests or lock_key_uid in active_requests:
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
                await update.message.reply_text(f"In akkaunt imruz ба авто-лайк ҳамроҳ шудааст! Boqimonda: {rem_hms}")
                return

        if tg_user_id not in user_history:
            user_history[tg_user_id] = []

        user_history[tg_user_id] = [
            item for item in user_history[tg_user_id]
            if (now - float(item["time"])) < COOLDOWN_TIME
        ]

        if len(user_history[tg_user_id]) >= MAX_UIDS_PER_USER:
            await update.message.reply_text("Limiti telegram pur ast! 24 soat boyad intizor shaved.")
            return

    active_requests.add(lock_key_user)
    active_requests.add(lock_key_uid)

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

        progress = await update.message.reply_text(f"Илова кардани UID {uid} ба авто-лайки рӯзона...")

        add_task = asyncio.create_task(add_autolike_id(uid))
        info_task = asyncio.create_task(fetch_player_info(uid))

        res = await add_task
        p_info = await info_task

        if not res["success"]:
            if not admin_flag and not vip_flag:
                memory_uids.pop(uid, None)
                user_history[tg_user_id] = [
                    item for item in user_history.get(tg_user_id, [])
                    if item["uid"] != uid
                ]
                db_cache["uids"] = memory_uids
                db_cache["user_history"] = user_history
                await save_state()

            err_reason = res.get("error") or "Илова карда нашуд."
            await progress.edit_text(f"Xato: {err_reason}")
            return

        nickname = p_info["nickname"]
        player_level = p_info["level"]
        likes_hozira = p_info["likes"]

        caption = (
            f"✅ UID با муваффақият ба Авто-Лайк илова шуд!\n\n"
            f"👤 Ном: {nickname}\n"
            f"⭐ Level: {player_level}\n"
            f"🆔 UID: {uid}\n"
            f"❤️ Лайкҳои ҳозира: {likes_hozira}\n\n"
            f"ℹ️ Акнун ин аккаунт ҳар рӯз ба таври худкор лайк мегирад!"
        )

        like_gif_id = db_cache.get("like_gif_id", "")
        if like_gif_id:
            try:
                await progress.delete()
            except Exception:
                pass
            await update.message.reply_animation(animation=like_gif_id, caption=caption)
        else:
            await progress.edit_text(caption)

    except Exception as e:
        logging.error(f"/like error: {e}")
        try:
            await progress.edit_text("Хатогии сервер!")
        except Exception:
            pass
    finally:
        active_requests.discard(lock_key_user)
        active_requests.discard(lock_key_uid)

async def turbo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Namuna: /turbo 18778177236")
        return
    uid = context.args[0].strip()
    msg = await update.message.reply_text(f"TURBO VIP ogoz shud! UID: {uid}")
    url = f"{AUTOSYSTEM_URL}/turbo?key={AUTOSYSTEM_KEY}&uid={uid}&region=CIS"
    try:
        timeout_cfg = aiohttp.ClientTimeout(total=95)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=timeout_cfg) as resp:
                raw_bytes = await resp.read()
                raw = raw_bytes.decode('utf-8', errors='ignore')
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("sucesso") is True:
            await msg.edit_text(f"TURBO VIP bomuvaffaqiyat firistoda shud! UID: {uid}")
        else:
            await msg.edit_text(f"Javobi server: {raw[:300]}")
    except Exception as e:
        await msg.edit_text(f"Xato: {e}")

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_group_access(update, context):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Namuna: /info 18778177236")
        return
    uid = context.args[0].strip()
    p_info = await fetch_player_info(uid)
    await update.message.reply_text(
        f"MA'LUMOTI AKKAUNT\n"
        f"Nom: {p_info['nickname']}\n"
        f"UID: {uid}\n"
        f"Level: {p_info['level']}\n"
        f"Like-hoi hozira: {p_info['likes']}"
    )

async def remain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{AUTOSYSTEM_URL}/autolike/list?key={AUTOSYSTEM_KEY}", timeout=15) as resp:
            raw_bytes = await resp.read()
            raw = raw_bytes.decode('utf-8', errors='ignore')
    await update.message.reply_text(f"ROYXATI AUTOLIKE DAR SERVER:\n{raw[:400]}")

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    if not context.args:
        await update.message.reply_text("Namuna: /reset 18778177236")
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
        await update.message.reply_text(f"Limiti {target} kushoda shud!")
    else:
        await update.message.reply_text(f"{target} dar limit nabud.")

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    if not context.args:
        await update.message.reply_text("Namuna: /check 18778177236")
        return
    target = context.args[0].strip()
    await load_state()
    now = time.time()
    if is_vip_uid(target):
        await update.message.reply_text(f"UID {target} VIP ast!")
        return
    memory_uids = db_cache.get("uids", {})
    if target in memory_uids:
        entry = memory_uids[target]
        ts = float(entry["time"]) if isinstance(entry, dict) else float(entry)
        if (now - ts) < COOLDOWN_TIME:
            rem = format_remaining(COOLDOWN_TIME - (now - ts))
            await update.message.reply_text(f"UID {target} dar limit ast. Boqimonda: {rem}")
            return
    await update.message.reply_text(f"UID {target} ozod ast.")

async def limits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    await load_state()
    now = time.time()
    memory_uids = db_cache.get("uids", {})
    active_items = []
    for uid, entry in memory_uids.items():
        ts = float(entry["time"]) if isinstance(entry, dict) else float(entry)
        unlock_ts = ts + COOLDOWN_TIME
        if unlock_ts > now:
            active_items.append((unlock_ts, uid))
    if not active_items:
        await update.message.reply_text("Hamai UID-ho ozodand!")
        return
    lines = ["ROYXATI LIMITHO:"]
    for idx, (unlock_ts, uid) in enumerate(active_items, start=1):
        rem_hms = format_hms(unlock_ts - now)
        lines.append(f"{idx}. UID: {uid} | Boqimonda: {rem_hms}")
    await update.message.reply_text("\n".join(lines))

async def addvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.message.reply_text("Namuna: /addvip 18778177236 5")
        return
    uid = context.args[0].strip()
    days = int(context.args[1].strip())
    await load_state()
    db_cache["vips"][uid] = time.time() + (days * 86400)
    db_cache["uids"].pop(uid, None)
    await save_state()
    await update.message.reply_text(f"VIP baroi UID {uid} ba muddati {days} ruz faol shud!")

async def delvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Namuna: /delvip 18778177236")
        return
    uid = context.args[0].strip()
    await load_state()
    if uid in db_cache["vips"]:
        db_cache["vips"].pop(uid)
        await save_state()
        await update.message.reply_text(f"UID {uid} az VIP xorij shud.")
    else:
        await update.message.reply_text(f"UID {uid} dar VIP nabud.")

async def viplist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    await load_state()
    now = time.time()
    active = []
    for uid, exp in list(db_cache["vips"].items()):
        if float(exp) > now:
            active.append(f"UID: {uid} — boqimonda: {format_remaining(float(exp) - now)}")
        else:
            db_cache["vips"].pop(uid)
    await save_state()
    if not active:
        await update.message.reply_text("VIP nest.")
        return
    await update.message.reply_text("VIP-MUSHTARIYON:\n" + "\n".join(active))

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
