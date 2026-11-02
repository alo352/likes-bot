import logging
import aiohttp
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = "9160352065:AAHleKcS8Nh0CnxUCKJdPxq_V-OXn59XgCE"
ALLOWED_GROUPS = [-5494553901]
LIKES_API_KEY = "mypasskey2026"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

ptb_application = None

def is_allowed(chat) -> bool:
    return chat.type in ["group", "supergroup"]



async def like_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    if not is_allowed(chat):
        return

    if not context.args or len(context.args) != 2:
        await update.message.reply_text(
            "Usage: `/like {region} {uid}`",
            parse_mode="Markdown"
        )
        return

    region, uid = context.args
    region = region.lower()

    if region == "ind":
        region = "ind"
    elif region in ["br", "latam", "us", "na"]:
        region = "br"
    else:
        region = "sg"


    progress = await update.message.reply_text("📤 Sending likes...")

    api_url = (
        f"https://ff.ggbluewhale.store/api/likes"
        f"?region={region}&uid={uid}&key={LIKES_API_KEY}"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as resp:
                raw_response = await resp.text()

        try:
            data = __import__("json").loads(raw_response)
        except ValueError:
            data = None

        if not isinstance(data, dict) or data.get("status") != 1 or "response" not in data:
            error_message = (
                "❌ **API returned an error**\n\n"
                f"**Raw server response:**\n`{raw_response[:3500]}`"
            )
            await progress.edit_text(error_message, parse_mode="Markdown")
            return

        details = data["response"]

        message = (
            f"✅ **Likes Added Successfully!**\n\n"
            f"**👤 Player:** `{details['PlayerNickname']}`\n"
            f"**💬 UID:** `{details['UID']}`\n"
            f"**📊 Likes Before:** `{details['LikesbeforeCommand']}`\n"
            f"**👍 Likes Given:** `{details['LikesGivenByAPI']}`\n"
            f"**🧩 Likes Credited:** `{details['LikesCreditedToAccount']}`\n"
            f"**📈 Likes After:** `{details['LikesafterCommand']}`"
        )

        await progress.edit_text(message, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"/like error: {e}")
        await progress.edit_text("❌ API error. Please try again later.")


async def remain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    if not is_allowed(chat):
        return

    url = f"https://api-key-mgmt-default-rtdb.firebaseio.com/likes_api_keys/{LIKES_API_KEY}.json"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                raw = await resp.text()

        await update.message.reply_text(raw)

    except Exception as e:
        logging.error(f"/remain error: {e}")
        await update.message.reply_text("❌ Failed to fetch data.")

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    global ptb_application

    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN env variable is missing!")

    ptb_application = ApplicationBuilder().token(BOT_TOKEN).build()
    ptb_application.add_handler(CommandHandler("like", like_command))
    ptb_application.add_handler(CommandHandler("remain", remain_command))

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
