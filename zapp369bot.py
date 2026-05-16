"""
⚡ZAPP369bot — TEMPORARY (sticker ID extractor)
Replace this with the full bot once we have all sticker IDs.
"""

import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing in Railway Variables")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def stickerid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get the sticker ID from a replied-to sticker."""
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "⚠️ <b>How to use:</b>\n\n"
            "1. Send a sticker first\n"
            "2. Tap and hold on that sticker → Reply\n"
            "3. Type <code>/stickerid</code> and send\n\n"
            "I'll give you the ID. Repeat for each sticker.",
            parse_mode=ParseMode.HTML
        )
        return

    replied = update.message.reply_to_message
    if not replied.sticker:
        await update.message.reply_text(
            "⚠️ That's not a sticker. Reply to an actual sticker message."
        )
        return

    sticker = replied.sticker
    response = (
        f"✅ <b>Sticker captured!</b>\n\n"
        f"<b>ID:</b>\n<code>{sticker.file_id}</code>\n\n"
        f"<b>Emoji:</b> {sticker.emoji or 'none'}\n"
        f"<b>Pack:</b> {sticker.set_name or 'unknown'}\n\n"
        f"<i>Tap the ID to copy. Repeat for each sticker.</i>"
    )
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "⚡ <b>ZAPP369bot — Sticker ID Mode</b>\n\n"
        "Send a sticker, then reply to it with <code>/stickerid</code>.\n"
        "I'll give you the ID for the full bot build.\n\n"
        "<i>3·6·9 ∞</i>"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stickerid", stickerid))
    logger.info("⚡ZAPP Sticker ID Extractor — running")
    app.run_polling()


if __name__ == "__main__":
    main()
