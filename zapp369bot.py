import os
import logging
import threading
import time
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
import asyncio

# ============================================================
# ⚡ ZAPP369 Telegram Bot — Tesla's Revolution on Solana
# ============================================================
# IMPORTANT: BOT_TOKEN is read from environment variable (Railway).
# Never paste your token directly in this file — it would leak
# in the public GitHub repo. Set BOT_TOKEN in Railway:
#   Project → Variables tab → BOT_TOKEN = <your token>
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "❌ BOT_TOKEN environment variable is missing.\n"
        "Set it in Railway → Variables tab → BOT_TOKEN = <your token>"
    )

GROUP_CHAT_ID = "@ZAPP369"

# ⚡ Token & pool addresses
TOKEN_MINT = "Ab16ce5SDbibTbXevxHLpqUnUvu9tNkkpaJcSDvCpump"
DEX_PAIR   = "gnnkakwk2pxb4ekcshro2xspjpde6b1pzxbk2h6pqpgx"  # ← updated to new pool

# ⚡ Official links
JUPITER_URL    = f"https://jup.ag/tokens/{TOKEN_MINT}"
DEX_URL        = f"https://dexscreener.com/solana/{DEX_PAIR}"
PUMPFUN_URL    = f"https://pump.fun/coin/{TOKEN_MINT}"
WEBSITE_URL    = "https://zapp369.energy"
HOW_TO_BUY_URL = "https://zapp369.energy/how-to-buy"  # ← NEW: 6-language guide
WHITEPAPER_URL = "https://zapp369.energy/ZAPP_Whitepaper_369.pdf"

MILESTONES = [20000, 30000, 45000, 100000, 500000, 1000000, 10000000]
announced_milestones = set()

logging.basicConfig(level=logging.INFO)


def fetch_price_data():
    try:
        url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{DEX_PAIR}"
        r = requests.get(url, timeout=10)
        pair = r.json().get("pair", {})
        return {
            "price": pair.get("priceUsd", "N/A"),
            "mcap": pair.get("fdv", None),
            "change_24h": pair.get("priceChange", {}).get("h24", "N/A"),
            "volume_24h": pair.get("volume", {}).get("h24", "N/A"),
            "liquidity": pair.get("liquidity", {}).get("usd", "N/A"),
        }
    except Exception as e:
        logging.error(f"fetch_price_data error: {e}")
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
        "/howtobuy — step-by-step buy guide (6 languages)\n"
        "/price — current price\n"
        "/mcap — market cap\n"
        "/buy — where to buy\n"
        "/links — all official links\n"
        "/bounty — earn ZAPP\n"
        "/whitepaper — read whitepaper\n\n"
        "3 · 6 · 9 ∞"
    )


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = fetch_price_data()
    if not data:
        await update.message.reply_text("⚠️ Could not fetch price. Try again in a moment.")
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
    keyboard = [
        [InlineKeyboardButton("📊 View Chart", url=DEX_URL)],
        [InlineKeyboardButton("📖 How to Buy", url=HOW_TO_BUY_URL)],
    ]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def mcap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = fetch_price_data()
    if not data or not data["mcap"]:
        await update.message.reply_text("⚠️ Could not fetch market cap. Try again in a moment.")
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
    keyboard = [
        [InlineKeyboardButton("⭐ Star on Jupiter", url=JUPITER_URL)],
        [InlineKeyboardButton("📊 View Chart", url=DEX_URL)],
    ]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def howtobuy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """NEW: walks new arrivals through the multilingual how-to-buy page."""
    msg = (
        "⚡ HOW TO BUY ⚡ZAPP\n\n"
        "Step-by-step guide in 6 languages:\n"
        "🇬🇧 English · 🇪🇸 Español · 🇷🇴 Română\n"
        "🇮🇹 Italiano · 🇫🇷 Français · 🇨🇳 中文\n\n"
        "No jargon. One-tap Phantom buy. Suspiciously honest.\n\n"
        f"🔗 {HOW_TO_BUY_URL}\n\n"
        "3 · 6 · 9 ∞"
    )
    keyboard = [
        [InlineKeyboardButton("📖 Open How to Buy", url=HOW_TO_BUY_URL)],
        [InlineKeyboardButton("⭐ Buy on Jupiter", url=JUPITER_URL)],
    ]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "⚡ ZAPP OFFICIAL LINKS ⚡\n\n"
        f"🌐 Website: {WEBSITE_URL}\n"
        f"📖 How to Buy: {HOW_TO_BUY_URL}\n"
        f"⭐ Jupiter: {JUPITER_URL}\n"
        f"🚀 DEXScreener: {DEX_URL}\n"
        f"⚡ pump.fun: {PUMPFUN_URL}\n"
        f"📄 Whitepaper: {WHITEPAPER_URL}\n\n"
        "3 · 6 · 9 ∞"
    )
    keyboard = [
        [InlineKeyboardButton("🌐 Website", url=WEBSITE_URL),
         InlineKeyboardButton("📖 How to Buy", url=HOW_TO_BUY_URL)],
        [InlineKeyboardButton("⭐ Jupiter", url=JUPITER_URL),
         InlineKeyboardButton("🚀 DEXScreener", url=DEX_URL)],
        [InlineKeyboardButton("📄 Whitepaper", url=WHITEPAPER_URL)],
    ]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "⚡ BUY $ZAPP NOW\n\n"
        f"📖 New here? Start with the guide: {HOW_TO_BUY_URL}\n"
        "(6 languages, one-tap Phantom buy)\n\n"
        f"⭐ Jupiter (best price): {JUPITER_URL}\n"
        f"⚡ pump.fun: {PUMPFUN_URL}\n\n"
        "CA: Ab16ce5SDbibTbXevxHLpqUnUvu9tNkkpaJcSDvCpump\n\n"
        "3 · 6 · 9 ∞ Free Energy = Free Money"
    )
    keyboard = [
        [InlineKeyboardButton("📖 How to Buy (guide)", url=HOW_TO_BUY_URL)],
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
        "Hit 🚀 Rocket or 🔥 Fire on our chart\n\n"
        "⭐ MISSION 2 — Jupiter\n"
        "Click the ⭐ Star on our Jupiter page (top left, next to our ⚡ZAPP)\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💰 REWARD: $1 in ZAPP instantly!\n\n"
        "HOW TO CLAIM:\n"
        "1. Complete both missions\n"
        "2. Screenshot DEX reactions + Jupiter star\n"
        "3. Post screenshots here with your SOL wallet\n\n"
        f"📖 New to ZAPP? {HOW_TO_BUY_URL}\n\n"
        "Use your MAIN wallet!\n\n"
        "3 · 6 · 9 ∞ ⚡"
    )
    keyboard = [
        [InlineKeyboardButton("⭐ Star on Jupiter", url=JUPITER_URL)],
        [InlineKeyboardButton("🚀 React on DEXScreener", url=DEX_URL)],
        [InlineKeyboardButton("📖 How to Buy", url=HOW_TO_BUY_URL)],
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


def background_scheduler(bot):
    """Runs in a separate thread — sends auto reminders and checks milestones."""
    global announced_milestones
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    bounty_interval = 4 * 60 * 60  # 4 hours
    milestone_interval = 10 * 60   # 10 minutes
    last_bounty = time.time() - bounty_interval + 3600  # first reminder after 1h
    last_milestone = time.time()

    BOUNTY_MSG = (
        "⚡ REMINDER — BOUNTY STILL LIVE 🌀\n\n"
        "Have you completed your missions yet?\n\n"
        f"⭐ Star us on Jupiter → {JUPITER_URL}\n"
        f"🚀🔥 React on DEX → {DEX_URL}\n\n"
        f"📖 New here? Start at {HOW_TO_BUY_URL}\n\n"
        "Post screenshots + SOL wallet = $1 ZAPP instantly!\n"
        "3 · 6 · 9 ∞ ⚡"
    )
    BOUNTY_KEYBOARD = InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ Star on Jupiter", url=JUPITER_URL)],
        [InlineKeyboardButton("🚀 React on DEXScreener", url=DEX_URL)],
        [InlineKeyboardButton("📖 How to Buy", url=HOW_TO_BUY_URL)],
    ])

    while True:
        now = time.time()

        # Auto bounty reminder
        if now - last_bounty >= bounty_interval:
            try:
                loop.run_until_complete(
                    bot.send_message(chat_id=GROUP_CHAT_ID, text=BOUNTY_MSG, reply_markup=BOUNTY_KEYBOARD)
                )
                last_bounty = now
            except Exception as e:
                logging.error(f"Bounty reminder error: {e}")

        # Milestone check
        if now - last_milestone >= milestone_interval:
            try:
                data = fetch_price_data()
                if data and data["mcap"]:
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
                            keyboard = InlineKeyboardMarkup([
                                [InlineKeyboardButton("📊 View Chart", url=DEX_URL)],
                                [InlineKeyboardButton("📖 How to Buy", url=HOW_TO_BUY_URL)],
                                [InlineKeyboardButton("⭐ Star on Jupiter", url=JUPITER_URL)],
                            ])
                            loop.run_until_complete(
                                bot.send_message(chat_id=GROUP_CHAT_ID, text=msg, reply_markup=keyboard)
                            )
                last_milestone = now
            except Exception as e:
                logging.error(f"Milestone check error: {e}")

        time.sleep(30)


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("mcap", mcap))
    app.add_handler(CommandHandler("howtobuy", howtobuy))   # ← NEW command
    app.add_handler(CommandHandler("links", links))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("bounty", bounty))
    app.add_handler(CommandHandler("whitepaper", whitepaper))

    # Start background scheduler in separate thread
    t = threading.Thread(target=background_scheduler, args=(app.bot,), daemon=True)
    t.start()

    print("⚡ ZAPP369bot is running with all features!")
    app.run_polling()


if __name__ == "__main__":
    main()
