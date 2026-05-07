import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

# ⚡ PASTE YOUR NEW TOKEN HERE
BOT_TOKEN = "7889720955:AAF6NHU37-hDzOQSSzhp42BDgRfwJyfQ4Nc"

# Your Telegram group username or numeric ID
GROUP_CHAT_ID = "@ZAPP369"

logging.basicConfig(level=logging.INFO)

BOUNTY_MESSAGE = """⚡ ZAPP BOUNTY IS LIVE 🌀

We are getting VERIFIED on Jupiter. We need YOUR help RIGHT NOW.

━━━━━━━━━━━━━━━━━━━━
🚀 MISSION 1 — DEXScreener
Hit 🚀 Rocket + ❤️ Heart on our chart:
👉 https://dexscreener.com/solana/awguynxlmr7kohfqs9cdmatckvnhxbrg1gmukftpywfr

⭐ MISSION 2 — Jupiter
Click the ⭐ Star on our Jupiter page:
👉 https://jup.ag/tokens/Ab16ce5SDbibTbXevxHLpqUnUvu9tNkkpaJcSDvCpump

━━━━━━━━━━━━━━━━━━━━
💰 REWARD: $0.1 - $10 in ⚡ZAPP instantly!

HOW TO CLAIM:
1. Complete both missions
2. Screenshot DEX reactions + Jupiter star
3. Send to admins screenshots with your ⚡ZAPP wallet

Use your MAIN wallet (trading history = Smart Likes = faster verification!)

3 · 6 · 9 ∞ ⚡ The frequency cannot be stopped."""

REMINDER_MESSAGE = """⚡ REMINDER — BOUNTY STILL LIVE 🌀

Have you completed your missions yet?

⭐ Star us on Jupiter → https://jup.ag/tokens/Ab16ce5SDbibTbXevxHLpqUnUvu9tNkkpaJcSDvCpump
🚀❤️ React on DEX → https://dexscreener.com/solana/awguynxlmr7kohfqs9cdmatckvnhxbrg1gmukftpywfr

Post screenshots + SOL wallet = $1 ZAPP instantly!
3 · 6 · 9 ∞ ⚡"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ ZAPP369bot is live! Use /bounty to post the bounty mission.")


async def bounty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("⭐ Star on Jupiter", url="https://jup.ag/tokens/Ab16ce5SDbibTbXevxHLpqUnUvu9tNkkpaJcSDvCpump")],
        [InlineKeyboardButton("🚀 React on DEXScreener", url="https://dexscreener.com/solana/awguynxlmr7kohfqs9cdmatckvnhxbrg1gmukftpywfr")],
        [InlineKeyboardButton("🌐 Website", url="https://zapp369.energy")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(BOUNTY_MESSAGE, reply_markup=reply_markup)


async def reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("⭐ Star on Jupiter", url="https://jup.ag/tokens/Ab16ce5SDbibTbXevxHLpqUnUvu9tNkkpaJcSDvCpump")],
        [InlineKeyboardButton("🚀 React on DEXScreener", url="https://dexscreener.com/solana/awguynxlmr7kohfqs9cdmatckvnhxbrg1gmukftpywfr")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(REMINDER_MESSAGE, reply_markup=reply_markup)


async def whitepaper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡ ZAPP Whitepaper — Tesla's Unfinished Revolution\n\n"
        "Read the full whitepaper here:\n"
        "👉 https://zapp369.energy/ZAPP_Whitepaper_369.pdf\n\n"
        "3 · 6 · 9 ∞ Free Energy = Free Money"
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("bounty", bounty))
    app.add_handler(CommandHandler("reminder", reminder))
    app.add_handler(CommandHandler("whitepaper", whitepaper))

    print("⚡ ZAPP369bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
