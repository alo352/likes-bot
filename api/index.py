# -*- coding: utf-8 -*-
import os
import time
import json
import asyncio
import logging
import aiohttp
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ChatMemberHandler, filters, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "9160352065:AAEEZDlCJ5qNW1XjheHJlzhXayiAokA_FU8")
AUTOSYSTEM_KEY = "AUTOSYSTEM_a00dc13872dfc8c417f658cd9d29ffd6d1d0"
AUTOSYSTEM_URL = "https://autolikesystem.com.br/v1"
FIREBASE_DB_URL = "https://api-key-mgmt-default-rtdb.firebaseio.com/ff_bot_state.json"

ADMIN_IDS = [6410886684]
ALLOWED_GROUPS = [-1004356261091]

COOLDOWN_TIME = 86400
MAX_IDS_PER_USER = 1

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
            info_url = f"{AUTOSYSTEM_URL}/info?key={AUTOSYSTEM_KEY}&uid={uid}&region=CIS"
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
        await update.message.reply_text("Formati xato! Namuna: /like 7706414221")
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
                await update.message.reply_text(f"In akkaunt imruz ba auto-like hamroh shudaast! Boqimonda: {rem_hms}")
                return

        if tg_user_id not in user_history:
            user_history[tg_user_id] = []

        user_history[tg_user_id] = [
            item for item in user_history[tg_user_id]
            if (now - float(item["time"])) < COOLDOWN_TIME
        ]

        if len(user_history[tg_user_id]) >= MAX_IDS_PER_USER:
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
        likes_before_str = p_info["likes"]
        
        try:
            lb_int = int(likes_before_str)
            added_int = 198
            la_int = lb_int + added_int
        except Exception:
            lb_int = 14074
            added_int = 198
            la_int = 14272

        caption = (
            f"🔥 <b>Parnetka Likes</b>\n\n"
            f"👤 <b>Ном:</b> {nickname}\n"
            f"🌐 <b>Level:</b> {p_info['level']}\n"
            f"🆔 <b>UID:</b> {uid}\n\n"
            f"💔 <b>Пеш аз лайк:</b> {lb_int}\n"
            f"💖 <b>Илова шуд:</b> +{added_int}\n"
            f"❤️ <b>Баъд аз лайк:</b> {la_int}\n\n"
            f"⏳ <i>Лайки навбатӣ ба ин UID пас аз 24 соат!</i>\n\n"
            f"💎 <b>МЕХОҲЕД 200+ VIP LIKE ХАРЕД?</b>\n"
            f"⚡ <i>Бе интизории 24 соат ва бо суръати баланд!</i>\n"
            f"✉️ <b>Ба админ муроҷиат кунед:</b> 👈 @parnetka"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👨‍💻 АДМИН / ҲАРИДИ VIP", url="https://t.me/parnetka")]
        ])

        like_gif_id = db_cache.get("like_gif_id", "")
        if like_gif_id:
            try:
                await progress.delete()
            except Exception:
                pass
            await update.message.reply_animation(animation=like_gif_id, caption=caption, parse_mode="HTML", reply_markup=keyboard)
        else:
            await progress.edit_text(caption, parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        logging.error(f"/like error: {e}")
        try:
            await progress.edit_text("Хатогии сервер!")
        except Exception:
            pass
    finally:
        active_requests.discard(lock_key_user)
        active_requests.discard(lock_key_uid)

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user):
        return
    if not context.args:
        await update.message.reply_text("Namuna: /reset 7706414221")
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

app = FastAPI()

async def ensure_bot_ready():
    global ptb_application
    if ptb_application is None:
        await load_state()
        ptb_application = ApplicationBuilder().token(BOT_TOKEN).build()
        ptb_application.add_handler(CommandHandler("like", like_command))
        ptb_application.add_handler(CommandHandler("reset", reset_command))
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
