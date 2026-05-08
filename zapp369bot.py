import asyncio
import logging
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, JobQueue

# ⚡ PASTE YOUR BOT TOKEN HERE
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

# Your Telegram group
GROUP_CHAT_ID = "@ZAPP369"

# Token & DEX info
TOKEN_MINT = "Ab16ce5SDbibTbXevxHLpqUnUvu9tNkkpaJcSDvCpump"
DEX_PAIR = "awguynxlmr7kohfqs9cdmatckvnhxbrg1gmukftpywfr"
JUPITER_URL = f"https://jup.ag/tokens/{TOKEN_MINT}"
DEX_URL = f"https://dexscreener.com/solana/{DEX_PAIR}"
PUMPFUN_URL = f"https://pump.fun/coin/{TOKEN_MINT}"
WEBSITE_URL = "https://zapp369.energy"
WHITEPAPER_URL = "https://zapp369.energy/ZAPP_Whitepaper_369.pdf"

# Milestone tracking
MILESTONES = [20000, 30000, 45000, 100000, 500000, 1000000, 10000000]
announced_milestones = set()

logging.basicConfig(level=logging.INFO)


async def fetch_price_data():
    url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{DEX_PAIR}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                pair = data.get("pair", {})
                return {
                    "price": pair.get("priceUsd", "N/A"),
                    "mcap": pair.get("fdv", None),
                    "change_24h": pair.get("priceChange", {}).get("h24", "N/A"),
                    "volume_24h": pair.get("volume", {}).get("h24", "N/A"),
                    "liquidity": pair.get("liquidity", {}).get("usd", "N/A"),
                }
    except Exception:
        return None


def format_number(n):
    try:
        n = float(n)
        if n >= 1_000_000:
            return f"${n/1_000_000:.2f}M"
        elif n >= 1_000:
            return f"${n/1_000:.2f}K"
        else:
            return f"${n:.4f}"
    except Exception:
        return str(n)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡ ZAPP369bot is live!\n\n"
        "Commands:\n"
        "/price — current price\n"
        "/mcap — market cap\n"
        "/links — official links\n"
        "/buy — where to buy\n"
        "/bounty — earn ZAPP\n"
        "/whitepaper — read whitepaper\n\n"
        "3 · 6 · 9 ∞"
    )


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = await fetch_price_data()
    if not data:
        await update.message.reply_text("⚠️ Could not fetch price. Try again later.")
        return
    change = data["change_24h"]
    arrow = "📉" if str(change).startswith("-") else "📈"
    msg = (
        f"⚡ ZAPP Price\n\n"
        f"💰 Price: ${data['price']}\n"
        f"{arrow} 24h Change: {change}%\n"
        f"💧 Liquidity: {format_number(data['liquidity'])}\n"
        f"📊 24h Volume: {format_number(data['volume_24h'])}\n\n"
        f"3 · 6 · 9 ∞"
    )
    keyboard = [[InlineKeyboardButton("📊 View Chart", url=DEX_URL)]]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def mcap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = await fetch_price_data()
    if not data or not data["mcap"]:
        await update.message.reply_text("⚠️ Could not fetch market cap. Try again later.")
        return
    mc = float(data["mcap"])
    next_milestone = next((m for m in MILESTONES if mc < m), None)
    next_text = f"\n🎯 Next milestone: {format_number(next_milestone)}" if next_milestone else "\n🚀 All milestones reached!"
    msg = (
        f"⚡ ZAPP Market Cap\n\n"
        f"📈 MCap: {format_number(mc)}\n"
        f"💰 Price: ${data['price']}{next_text}\n\n"
        f"3 · 6 · 9 ∞"
    )
    keyboard = [[InlineKeyboardButton("⭐ Star on Jupiter", url=JUPITER_URL)]]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "⚡ ZAPP OFFICIAL LINKS ⚡\n\n"
        f"🌐 Website: {WEBSITE_URL}\n"
        f"⭐ Jupiter: {JUPITER_URL}\n"
        f"🚀 DEX: {DEX_URL}\n"
        f"⚡ pump.fun: {PUMPFUN_URL}\n"
        f"📄 Whitepaper: {WHITEPAPER_URL}\n\n"
        "3 · 6 · 9 ∞"
    )
    keyboard = [
        [InlineKeyboardButton("🌐 Website", url=WEBSITE_URL),
         InlineKeyboardButton("⭐ Jupiter", url=JUPITER_URL)],
        [InlineKeyboardButton("🚀 DEXScreener", url=DEX_URL),
         InlineKeyboardButton("📄 Whitepaper", url=WHITEPAPER_URL)],
    ]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "⚡ BUY $ZAPP NOW\n\n"
        f"⭐ Jupiter (best price): {JUPITER_URL}\n"
        f"⚡ pump.fun: {PUMPFUN_URL}\n\n"
        "CA: Ab16ce5SDbibTbXevxHLpqUnUvu9tNkkpaJcSDvCpump\n\n"
        "3 · 6 · 9 ∞ Free Energy = Free Money"
    )
    keyboard = [
        [InlineKeyboardButton("⭐ Buy on Jupiter", url=JUPITER_URL)],
        [InlineKeyboardButton("⚡ Buy on pump.fun", url=PUMPFUN_URL)],
    ]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def bounty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "⚡ ZAPP BOUNTY IS LIVE 🌀\n\n"
        "Help us get VERIFIED on Jupiter!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 MISSION 1 — DEXScreener\n"
        "Hit 🚀 Rocket + ❤️ Heart on our chart\n\n"
        "⭐ MISSION 2 — Jupiter\n"
        "Click the ⭐ Star on Jupiter\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💰 REWARD: $1 in ZAPP instantly!\n\n"
        "HOW TO CLAIM:\n"
        "1. Complete both missions\n"
        "2. Screenshot DEX reactions + Jupiter star\n"
        "3. Post screenshots here with your SOL wallet\n\n"
        "Use your MAIN wallet!\n\n"
        "3 · 6 · 9 ∞ ⚡"
    )
    keyboard = [
        [InlineKeyboardButton("⭐ Star on Jupiter", url=JUPITER_URL)],
        [InlineKeyboardButton("🚀 React on DEXScreener", url=DEX_URL)],
        [InlineKeyboardButton("🌐 Website", url=WEBSITE_URL)],
    ]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def whitepaper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "⚡ ZAPP Whitepaper — Tesla's Unfinished Revolution\n\n"
        '"If you knew the magnificence of 3, 6 & 9,\n'
        'you would have a key to the universe." — Nikola Tesla\n\n'
        f"📄 Read here: {WHITEPAPER_URL}\n\n"
        "3 · 6 · 9 ∞ Free Energy = Free Money"
    )
    keyboard = [[InlineKeyboardButton("📄 Read Whitepaper", url=WHITEPAPER_URL)]]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def auto_bounty_reminder(context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "⚡ REMINDER — BOUNTY STILL LIVE 🌀\n\n"
        "Have you completed your missions yet?\n\n"
        f"⭐ Star us on Jupiter → {JUPITER_URL}\n"
        f"🚀❤️ React on DEX → {DEX_URL}\n\n"
        "Post screenshots + SOL wallet = $1 ZAPP instantly!\n"
        "3 · 6 · 9 ∞ ⚡"
    )
    keyboard = [
        [InlineKeyboardButton("⭐ Star on Jupiter", url=JUPITER_URL)],
        [InlineKeyboardButton("🚀 React on DEXScreener", url=DEX_URL)],
    ]
    await context.bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=msg,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def auto_milestone_check(context: ContextTypes.DEFAULT_TYPE):
    global announced_milestones
    data = await fetch_price_data()
    if not data or not data["mcap"]:
        return
    mc = float(data["mcap"])
    for milestone in MILESTONES:
        if mc >= milestone and milestone not in announced_milestones:
            announced_milestones.add(milestone)
            msg = (
                f"🚀⚡ MILESTONE REACHED! ⚡🚀\n\n"
                f"$ZAPP just hit {format_number(milestone)} Market Cap!\n\n"
                f"💰 Current MCap: {format_number(mc)}\n"
                f"💵 Price: ${data['price']}\n\n"
                f"This is only the beginning!\n"
                f"3 · 6 · 9 ∞ ⚡"
            )
            keyboard = [
                [InlineKeyboardButton("📊 View Chart", url=DEX_URL)],
                [InlineKeyboardButton("⭐ Star on Jupiter", url=JUPITER_URL)],
            ]
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=msg,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("mcap", mcap))
    app.add_handler(CommandHandler("links", links))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("bounty", bounty))
    app.add_handler(CommandHandler("whitepaper", whitepaper))

    job_queue = app.job_queue
    job_queue.run_repeating(auto_bounty_reminder, interval=14400, first=3600)  # every 4h
    job_queue.run_repeating(auto_milestone_check, interval=600, first=60)       # every 10 min

    print("⚡ ZAPP369bot is running with all features!")
    app.run_polling()


if __name__ == "__main__":
    main()
