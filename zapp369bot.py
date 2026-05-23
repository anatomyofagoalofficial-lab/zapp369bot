"""
⚡ ZAPP369bot v2 — SINGLE-FILE BUILD
====================================
Everything in one file for easy deployment. Drop this in your repo, set BOT_TOKEN,
and run.  Procfile:  worker: python zapp369bot.py

Env vars (Railway > Variables):
    BOT_TOKEN  (required)  token from @BotFather
    DB_PATH    (optional)  sqlite path. Default zapp369.db. For data that survives
                           redeploys, attach a Railway Volume at /data and set
                           DB_PATH=/data/zapp369.db
"""
import os
import re
import html
import time
import random
import secrets
import sqlite3
import logging
import fnmatch
import types as _types
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone, time as dtime

from telegram import (
    Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.constants import ParseMode, ChatType, ChatMemberStatus
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

__version__ = "2.0.0"

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing in Railway Variables")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("zapp369")


# ===========================================================================
# ---- db ------------------------------------------------------------
# ===========================================================================

"""
Database layer for ZAPP369bot.
SQLite with WAL mode. One connection per call (the polling loop is single-threaded,
so contention is negligible). All feature modules go through these helpers.
"""
import os
import sqlite3
import secrets

DB_PATH = os.environ.get("DB_PATH", "zapp369.db")


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                chat_id INTEGER PRIMARY KEY,
                rules TEXT,
                welcome TEXT,
                welcome_on INTEGER DEFAULT 1,
                goodbye TEXT,
                goodbye_on INTEGER DEFAULT 0,
                captcha_on INTEGER DEFAULT 0,
                captcha_mode TEXT DEFAULT 'button',
                clean_service INTEGER DEFAULT 0,
                clean_welcome INTEGER DEFAULT 0,
                warn_limit INTEGER DEFAULT 3,
                warn_action TEXT DEFAULT 'mute',
                flood_limit INTEGER DEFAULT 0,
                flood_action TEXT DEFAULT 'mute',
                blocklist_action TEXT DEFAULT 'delete',
                antiraid_on INTEGER DEFAULT 0,
                antiraid_threshold INTEGER DEFAULT 5,
                antiraid_window INTEGER DEFAULT 30,
                antiraid_action TEXT DEFAULT 'mute',
                nightmode_on INTEGER DEFAULT 0,
                nightmode_start TEXT DEFAULT '23:00',
                nightmode_end TEXT DEFAULT '07:00',
                log_channel INTEGER
            );
            CREATE TABLE IF NOT EXISTS warns (
                chat_id INTEGER, user_id INTEGER, count INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS warn_reasons (
                chat_id INTEGER, user_id INTEGER, reason TEXT, ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS notes (
                chat_id INTEGER, name TEXT, content TEXT, kind TEXT DEFAULT 'text',
                file_id TEXT,
                PRIMARY KEY (chat_id, name)
            );
            CREATE TABLE IF NOT EXISTS filters (
                chat_id INTEGER, keyword TEXT, reply TEXT,
                PRIMARY KEY (chat_id, keyword)
            );
            CREATE TABLE IF NOT EXISTS locks (
                chat_id INTEGER, lock_type TEXT,
                PRIMARY KEY (chat_id, lock_type)
            );
            CREATE TABLE IF NOT EXISTS blocklist (
                chat_id INTEGER, trigger TEXT,
                PRIMARY KEY (chat_id, trigger)
            );
            CREATE TABLE IF NOT EXISTS approved (
                chat_id INTEGER, user_id INTEGER,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS disabled (
                chat_id INTEGER, command TEXT,
                PRIMARY KEY (chat_id, command)
            );
            CREATE TABLE IF NOT EXISTS afk (
                user_id INTEGER PRIMARY KEY, reason TEXT, since INTEGER
            );
            CREATE TABLE IF NOT EXISTS feds (
                fed_id TEXT PRIMARY KEY, name TEXT, owner_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS fed_chats (
                fed_id TEXT, chat_id INTEGER,
                PRIMARY KEY (fed_id, chat_id)
            );
            CREATE TABLE IF NOT EXISTS chat_fed (
                chat_id INTEGER PRIMARY KEY, fed_id TEXT
            );
            CREATE TABLE IF NOT EXISTS fed_bans (
                fed_id TEXT, user_id INTEGER, reason TEXT,
                PRIMARY KEY (fed_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS fed_admins (
                fed_id TEXT, user_id INTEGER,
                PRIMARY KEY (fed_id, user_id)
            );
            """
        )
        c.commit()


# ---- generic helpers -------------------------------------------------------
def query(sql, params=(), one=False):
    with _conn() as c:
        cur = c.execute(sql, params)
        rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, params=()):
    with _conn() as c:
        c.execute(sql, params)
        c.commit()


# ---- settings --------------------------------------------------------------
def get_settings(chat_id):
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO settings (chat_id) VALUES (?)", (chat_id,))
        c.commit()
        return c.execute("SELECT * FROM settings WHERE chat_id=?", (chat_id,)).fetchone()


def set_setting(chat_id, key, value):
    # key is from a fixed internal whitelist (never user input) — safe to interpolate
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO settings (chat_id) VALUES (?)", (chat_id,))
        c.execute(f"UPDATE settings SET {key}=? WHERE chat_id=?", (value, chat_id))
        c.commit()


# ---- federations -----------------------------------------------------------
def new_fed_id():
    return secrets.token_hex(8)


# --- db namespace shim (so db.X calls from the original modules keep working) ---
db = _types.SimpleNamespace(
    init_db=init_db, query=query, execute=execute,
    get_settings=get_settings, set_setting=set_setting, new_fed_id=new_fed_id,
)


# ===========================================================================
# ---- utils ------------------------------------------------------------
# ===========================================================================

"""
Shared utilities for ZAPP369bot: decorators, admin caching, target resolution,
duration & glob parsing, welcome formatting, and in-memory state.
"""
import re
import html
import time
import fnmatch
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes


BRAND = "⚡ ZAPP ⚡"

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------
flood_tracker = defaultdict(lambda: deque(maxlen=64))       # (chat,user) -> timestamps
join_tracker = defaultdict(lambda: deque(maxlen=128))       # chat -> join timestamps
raid_active = {}                                            # chat -> expiry_ts
pm_connections = {}                                         # user_id -> chat_id (DM control)

_admin_cache = {}                                           # chat_id -> (expiry, set(admin_ids))
ADMIN_CACHE_TTL = 300
FLOOD_WINDOW = 10


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def esc(text) -> str:
    return html.escape(str(text)) if text is not None else ""


def mention(user) -> str:
    name = esc(getattr(user, "first_name", None) or "user")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def mention_id(uid, name=None) -> str:
    return f'<a href="tg://user?id={uid}">{esc(name) if name else uid}</a>'


# ---------------------------------------------------------------------------
# Admin handling (cached)
# ---------------------------------------------------------------------------
async def admin_ids(chat_id, context) -> set:
    cached = _admin_cache.get(chat_id)
    if cached and cached[0] > time.time():
        return cached[1]
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        ids = {a.user.id for a in admins}
        _admin_cache[chat_id] = (time.time() + ADMIN_CACHE_TTL, ids)
        return ids
    except (BadRequest, Forbidden):
        return cached[1] if cached else set()


def invalidate_admin_cache(chat_id):
    _admin_cache.pop(chat_id, None)


async def is_admin(chat_id, user_id, context) -> bool:
    if chat_id == user_id:           # private chat: caller is "admin" of their DM
        return True
    return user_id in await admin_ids(chat_id, context)


async def is_chat_admin(update: Update, context, user_id) -> bool:
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        return True
    return await is_admin(chat.id, user_id, context)


async def bot_can(update: Update, context) -> bool:
    """Is the bot itself an admin here?"""
    return await is_admin(update.effective_chat.id, context.bot.id, context)


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------
def group_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type == ChatType.PRIVATE and not pm_connections.get(
                update.effective_user.id):
            await update.effective_message.reply_text(
                "⚡ Use this inside a group, or /connect to a group first.")
            return
        return await func(update, context)
    return wrapper


def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if chat.type == ChatType.PRIVATE:
            target = pm_connections.get(update.effective_user.id)
            if target and not await is_admin(target, update.effective_user.id, context):
                await update.effective_message.reply_text("🚫 You're not an admin of the connected group.")
                return
            return await func(update, context)
        if not await is_admin(chat.id, update.effective_user.id, context):
            await update.effective_message.reply_text("🚫 Only admins can use that.")
            return
        return await func(update, context)
    return wrapper


def target_chat(update: Update):
    """Resolve which chat a command acts on (real chat, or DM-connected chat)."""
    if update.effective_chat.type == ChatType.PRIVATE:
        return pm_connections.get(update.effective_user.id, update.effective_chat.id)
    return update.effective_chat.id


# ---------------------------------------------------------------------------
# Target user resolution
# ---------------------------------------------------------------------------
async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return (user_id, display_html) for the user a command targets, or (None, None)."""
    msg = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        return u.id, mention(u)
    # text_mention entity in args
    for ent in (msg.entities or []):
        if ent.type == "text_mention" and ent.user:
            return ent.user.id, mention(ent.user)
    if context.args:
        arg = context.args[0]
        if arg.lstrip("-").isdigit():
            uid = int(arg)
            return uid, mention_id(uid)
        if arg.startswith("@"):
            try:
                chat = await context.bot.get_chat(arg)
                return chat.id, mention(chat)
            except BadRequest:
                return None, None
    return None, None


def reason_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    args = list(context.args)
    if not update.effective_message.reply_to_message and args:
        args = args[1:]            # drop the @user / id token
    # drop a trailing duration token if present
    if args and parse_duration(args[-1]) is not None:
        args = args[:-1]
    return " ".join(args).strip()


def duration_arg(context: ContextTypes.DEFAULT_TYPE):
    for a in context.args:
        s = parse_duration(a)
        if s is not None:
            return s, a
    return None, None


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
_DUR = re.compile(r"^(\d+)\s*([smhdw])$", re.IGNORECASE)
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text):
    if not text:
        return None
    m = _DUR.match(text.strip())
    return int(m.group(1)) * _UNIT[m.group(2).lower()] if m else None


def until_from(seconds):
    return datetime.now(timezone.utc) + timedelta(seconds=seconds) if seconds else None


def html_body(message, drop=1):
    """
    Return the message text/caption AS HTML (preserving premium custom emoji and
    any native formatting), with the leading `drop` whitespace-separated tokens
    removed (e.g. the command, or command + note-name).
    """
    if message is None:
        return ""
    if message.text is not None:
        h = message.text_html
    elif message.caption is not None:
        h = message.caption_html or ""
    else:
        return ""
    if not h:
        return ""
    pieces = h.split(" ", drop)
    return pieces[drop].strip() if len(pieces) > drop else ""


def html_full(message):
    """Full text/caption of a message as HTML (no token stripping)."""
    if message is None:
        return ""
    if message.text is not None:
        return message.text_html
    if message.caption is not None:
        return message.caption_html or ""
    return ""


def glob_match(pattern: str, text: str) -> bool:
    """Case-insensitive glob (supports * and ?). Plain words match as whole words."""
    text = text.lower()
    pattern = pattern.lower()
    if any(ch in pattern for ch in "*?[]"):
        return fnmatch.fnmatch(text, pattern) or any(
            fnmatch.fnmatch(w, pattern) for w in re.findall(r"\S+", text))
    return re.search(rf"\b{re.escape(pattern)}\b", text) is not None


# ---------------------------------------------------------------------------
# Welcome / note formatting with {placeholders} and [button](buttonurl://...)
# ---------------------------------------------------------------------------
_BTN = re.compile(r"\[([^\]]+)\]\(buttonurl://([^\s)]+?)(:same)?\)")


def build_message(template: str, *, user=None, chat=None, count=None):
    """Returns (html_text, InlineKeyboardMarkup|None) from a template string."""
    text = template or ""
    if user is not None:
        text = text.replace("{name}", mention(user))
        text = text.replace("{first}", esc(getattr(user, "first_name", "")))
        text = text.replace("{id}", str(getattr(user, "id", "")))
        uname = getattr(user, "username", None)
        text = text.replace("{username}", f"@{uname}" if uname else mention(user))
    if chat is not None:
        text = text.replace("{group}", esc(getattr(chat, "title", "the group")))
    if count is not None:
        text = text.replace("{count}", str(count))

    rows, current = [], []
    def _btn(m):
        label, url, same = m.group(1), m.group(2), m.group(3)
        btn = InlineKeyboardButton(label, url=url)
        if same and current:
            current.append(btn)
        else:
            if current:
                rows.append(list(current)); current.clear()
            current.append(btn)
        return ""
    clean = _BTN.sub(_btn, text)
    if current:
        rows.append(list(current))
    markup = InlineKeyboardMarkup(rows) if rows else None
    return clean.strip(), markup


# ---------------------------------------------------------------------------
# Action verbs
# ---------------------------------------------------------------------------
async def do_action(action, chat_id, uid, context, *, seconds=None):
    """Apply mute/kick/ban. Returns a past-tense verb or raises BadRequest."""
    if action == "ban":
        await context.bot.ban_chat_member(chat_id, uid, until_date=until_from(seconds))
        return "banned"
    if action == "kick":
        await context.bot.ban_chat_member(chat_id, uid)
        await context.bot.unban_chat_member(chat_id, uid)
        return "kicked"
    # mute
    from telegram import ChatPermissions
    await context.bot.restrict_chat_member(
        chat_id, uid, permissions=ChatPermissions(can_send_messages=False),
        until_date=until_from(seconds))
    return "muted"


# ===========================================================================
# ---- logchannel ------------------------------------------------------------
# ===========================================================================

"""Send admin-action logs to a configured channel."""
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden



async def log_action(context, chat_id, text):
    s = db.get_settings(chat_id)
    target = s["log_channel"]
    if not target:
        return
    try:
        await context.bot.send_message(target, text, parse_mode=ParseMode.HTML,
                                       disable_web_page_preview=True)
    except (BadRequest, Forbidden):
        pass


# ===========================================================================
# ---- content ------------------------------------------------------------
# ===========================================================================

"""Notes, filters, rules, and blocklist."""
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import CommandHandler, ContextTypes



# --------------------------- NOTES -----------------------------------------
@group_only
@admin_only
async def save_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not context.args:
        await msg.reply_text("Usage: /save name your text  (or reply to a message/photo)")
        return
    chat_id = target_chat(update)
    name = context.args[0].lower().lstrip("#")
    kind, file_id, content = "text", None, ""
    reply = msg.reply_to_message
    if reply:
        if reply.photo:
            kind, file_id = "photo", reply.photo[-1].file_id
            content = reply.caption_html or ""
        elif reply.animation:
            kind, file_id, content = "animation", reply.animation.file_id, reply.caption_html or ""
        elif reply.video:
            kind, file_id, content = "video", reply.video.file_id, reply.caption_html or ""
        elif reply.sticker:
            kind, file_id = "sticker", reply.sticker.file_id
        elif reply.document:
            kind, file_id, content = "document", reply.document.file_id, reply.caption_html or ""
        else:
            content = html_full(reply)
    else:
        content = html_body(msg, drop=2)
    if not content and not file_id:
        await msg.reply_text("Give me some content to save.")
        return
    db.execute("INSERT OR REPLACE INTO notes (chat_id,name,content,kind,file_id) VALUES (?,?,?,?,?)",
               (chat_id, name, content, kind, file_id))
    await msg.reply_text(f"✅ Saved note <code>#{esc(name)}</code>.", parse_mode=ParseMode.HTML)


async def send_note(update, context, name):
    chat_id = target_chat(update)
    row = db.query("SELECT content,kind,file_id FROM notes WHERE chat_id=? AND name=?",
                   (chat_id, name), one=True)
    if not row:
        return False
    text, markup = build_message(row["content"], user=update.effective_user,
                                 chat=update.effective_chat)
    msg = update.effective_message
    try:
        if row["kind"] == "photo":
            await msg.reply_photo(row["file_id"], caption=text or None,
                                  parse_mode=ParseMode.HTML, reply_markup=markup)
        elif row["kind"] == "animation":
            await msg.reply_animation(row["file_id"], caption=text or None,
                                      parse_mode=ParseMode.HTML, reply_markup=markup)
        elif row["kind"] == "video":
            await msg.reply_video(row["file_id"], caption=text or None,
                                  parse_mode=ParseMode.HTML, reply_markup=markup)
        elif row["kind"] == "sticker":
            await msg.reply_sticker(row["file_id"])
        elif row["kind"] == "document":
            await msg.reply_document(row["file_id"], caption=text or None,
                                     parse_mode=ParseMode.HTML, reply_markup=markup)
        else:
            await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup,
                                 disable_web_page_preview=True)
    except BadRequest:
        await msg.reply_text(text or "(note)", parse_mode=ParseMode.HTML)
    return True


@group_only
async def get_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Usage: /get name")
        return
    if not await send_note(update, context, context.args[0].lower().lstrip("#")):
        await update.effective_message.reply_text("No note by that name.")


@group_only
async def list_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.query("SELECT name FROM notes WHERE chat_id=? ORDER BY name", (target_chat(update),))
    if not rows:
        await update.effective_message.reply_text("No notes saved yet.")
        return
    names = "\n".join(f"• <code>#{esc(r['name'])}</code>" for r in rows)
    await update.effective_message.reply_text(f"📝 <b>Notes</b>\n{names}", parse_mode=ParseMode.HTML)


@group_only
@admin_only
async def clear_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Usage: /clear name")
        return
    name = context.args[0].lower().lstrip("#")
    db.execute("DELETE FROM notes WHERE chat_id=? AND name=?", (target_chat(update), name))
    await update.effective_message.reply_text(f"🗑 Cleared #{esc(name)}.", parse_mode=ParseMode.HTML)


# --------------------------- FILTERS ---------------------------------------
@group_only
@admin_only
async def add_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /filter keyword reply text")
        return
    keyword = context.args[0].lower()
    reply = html_body(update.effective_message, drop=2)
    db.execute("INSERT OR REPLACE INTO filters (chat_id,keyword,reply) VALUES (?,?,?)",
               (target_chat(update), keyword, reply))
    await update.effective_message.reply_text(f"✅ Filter on “{esc(keyword)}” added.",
                                              parse_mode=ParseMode.HTML)


@group_only
async def list_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.query("SELECT keyword FROM filters WHERE chat_id=? ORDER BY keyword", (target_chat(update),))
    if not rows:
        await update.effective_message.reply_text("No filters set.")
        return
    await update.effective_message.reply_text(
        "🧲 <b>Filters</b>\n" + "\n".join(f"• {esc(r['keyword'])}" for r in rows),
        parse_mode=ParseMode.HTML)


@group_only
@admin_only
async def stop_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Usage: /stop keyword")
        return
    kw = context.args[0].lower()
    db.execute("DELETE FROM filters WHERE chat_id=? AND keyword=?", (target_chat(update), kw))
    await update.effective_message.reply_text(f"🛑 Removed filter “{esc(kw)}”.", parse_mode=ParseMode.HTML)


# --------------------------- RULES -----------------------------------------
@group_only
@admin_only
async def setrules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = html_body(update.effective_message, drop=1)
    if not text:
        await update.effective_message.reply_text("Usage: /setrules <your rules text>")
        return
    db.set_setting(target_chat(update), "rules", text)
    await update.effective_message.reply_text("✅ Rules saved.")


@group_only
async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = db.get_settings(target_chat(update))
    if s["rules"]:
        text, markup = build_message(s["rules"], chat=update.effective_chat)
        await update.effective_message.reply_text(f"📜 <b>Rules</b>\n\n{text}",
                                                  parse_mode=ParseMode.HTML,
                                                  reply_markup=markup,
                                                  disable_web_page_preview=True)
    else:
        await update.effective_message.reply_text("No rules set. Admins: /setrules <text>")


# --------------------------- BLOCKLIST -------------------------------------
@group_only
@admin_only
async def add_blocklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /addblocklist word  (supports * wildcards)")
        return
    trig = " ".join(context.args).lower()
    db.execute("INSERT OR REPLACE INTO blocklist (chat_id,trigger) VALUES (?,?)",
               (target_chat(update), trig))
    await update.effective_message.reply_text(f"⛔ Blocklisted “{esc(trig)}”.", parse_mode=ParseMode.HTML)


@group_only
async def list_blocklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    rows = db.query("SELECT trigger FROM blocklist WHERE chat_id=? ORDER BY trigger", (chat_id,))
    s = db.get_settings(chat_id)
    if not rows:
        await update.effective_message.reply_text("No blocklisted words.")
        return
    await update.effective_message.reply_text(
        f"⛔ <b>Blocklist</b> (action: {s['blocklist_action']})\n"
        + "\n".join(f"• {esc(r['trigger'])}" for r in rows), parse_mode=ParseMode.HTML)


@group_only
@admin_only
async def rm_blocklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Usage: /unblocklist word")
        return
    trig = " ".join(context.args).lower()
    db.execute("DELETE FROM blocklist WHERE chat_id=? AND trigger=?", (target_chat(update), trig))
    await update.effective_message.reply_text(f"✅ Removed “{esc(trig)}” from blocklist.",
                                              parse_mode=ParseMode.HTML)


@group_only
@admin_only
async def set_blocklist_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower() not in ("delete", "warn", "mute", "kick", "ban"):
        await update.effective_message.reply_text("Usage: /blocklistaction delete|warn|mute|kick|ban")
        return
    db.set_setting(target_chat(update), "blocklist_action", context.args[0].lower())
    await update.effective_message.reply_text(f"✅ Blocklist action: {context.args[0].lower()}.")


def register_content(app):
    app.add_handler(CommandHandler("save", save_note))
    app.add_handler(CommandHandler("get", get_note))
    app.add_handler(CommandHandler("notes", list_notes))
    app.add_handler(CommandHandler("clear", clear_note))
    app.add_handler(CommandHandler("filter", add_filter))
    app.add_handler(CommandHandler("filters", list_filters))
    app.add_handler(CommandHandler("stop", stop_filter))
    app.add_handler(CommandHandler("setrules", setrules))
    app.add_handler(CommandHandler("rules", rules))
    app.add_handler(CommandHandler(["addblocklist", "blocklist"], add_blocklist))
    app.add_handler(CommandHandler("blocklists", list_blocklist))
    app.add_handler(CommandHandler(["unblocklist", "rmblocklist"], rm_blocklist))
    app.add_handler(CommandHandler("blocklistaction", set_blocklist_action))


# ===========================================================================
# ---- federation ------------------------------------------------------------
# ===========================================================================

"""
Federations — group a set of chats under one ban list.
A fedban bans the user across every chat in the federation, and enforces on sight.
"""
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import CommandHandler, ContextTypes



def _fed_of(chat_id):
    row = db.query("SELECT fed_id FROM chat_fed WHERE chat_id=?", (chat_id,), one=True)
    return row["fed_id"] if row else None


def _is_fed_admin(fed_id, user_id):
    fed = db.query("SELECT owner_id FROM feds WHERE fed_id=?", (fed_id,), one=True)
    if fed and fed["owner_id"] == user_id:
        return True
    return db.query("SELECT 1 FROM fed_admins WHERE fed_id=? AND user_id=?",
                    (fed_id, user_id), one=True) is not None


async def newfed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args).strip()
    if not name:
        await update.effective_message.reply_text("Usage: /newfed <federation name>")
        return
    fid = db.new_fed_id()
    db.execute("INSERT INTO feds (fed_id,name,owner_id) VALUES (?,?,?)",
               (fid, name, update.effective_user.id))
    await update.effective_message.reply_text(
        f"🌐 Federation <b>{esc(name)}</b> created.\nID: <code>{fid}</code>\n\n"
        f"In each group you own, run:\n<code>/joinfed {fid}</code>",
        parse_mode=ParseMode.HTML)


@group_only
async def joinfed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.effective_message.reply_text("Run this inside the group you want to add.")
        return
    member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    if member.status != "creator":
        await update.effective_message.reply_text("Only the group creator can join a federation.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /joinfed <fed_id>")
        return
    fid = context.args[0]
    fed = db.query("SELECT name FROM feds WHERE fed_id=?", (fid,), one=True)
    if not fed:
        await update.effective_message.reply_text("No federation with that ID.")
        return
    db.execute("INSERT OR REPLACE INTO chat_fed (chat_id,fed_id) VALUES (?,?)",
               (update.effective_chat.id, fid))
    db.execute("INSERT OR IGNORE INTO fed_chats (fed_id,chat_id) VALUES (?,?)",
               (fid, update.effective_chat.id))
    await update.effective_message.reply_text(
        f"✅ This group joined federation <b>{esc(fed['name'])}</b>.", parse_mode=ParseMode.HTML)


@group_only
async def leavefed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    if member.status != "creator":
        await update.effective_message.reply_text("Only the group creator can leave a federation.")
        return
    fid = _fed_of(update.effective_chat.id)
    if not fid:
        await update.effective_message.reply_text("This group isn't in a federation.")
        return
    db.execute("DELETE FROM chat_fed WHERE chat_id=?", (update.effective_chat.id,))
    db.execute("DELETE FROM fed_chats WHERE fed_id=? AND chat_id=?", (fid, update.effective_chat.id))
    await update.effective_message.reply_text("✅ Left the federation.")


@group_only
async def fedban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    fid = _fed_of(chat_id)
    if not fid:
        await update.effective_message.reply_text("This group isn't in a federation. /joinfed first.")
        return
    if not _is_fed_admin(fid, update.effective_user.id):
        await update.effective_message.reply_text("🚫 Only federation admins can fedban.")
        return
    uid, disp = await resolve_target(update, context)
    if not uid:
        await update.effective_message.reply_text("Reply to a user or pass @username / id.")
        return
    reason = reason_text(update, context) or "no reason"
    db.execute("INSERT OR REPLACE INTO fed_bans (fed_id,user_id,reason) VALUES (?,?,?)",
               (fid, uid, reason))
    chats = db.query("SELECT chat_id FROM fed_chats WHERE fed_id=?", (fid,))
    done = 0
    for c in chats:
        try:
            await context.bot.ban_chat_member(c["chat_id"], uid)
            done += 1
        except BadRequest:
            pass
    await update.effective_message.reply_text(
        f"🌐🔨 Fed-banned {disp} across {done} group(s).\nReason: {esc(reason)}",
        parse_mode=ParseMode.HTML)
    await log_action(context, chat_id,
                     f"🌐 <b>FedBan</b>\nUser: {disp}\nBy: {mention(update.effective_user)}\n"
                     f"Reason: {esc(reason)}")


@group_only
async def unfedban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    fid = _fed_of(chat_id)
    if not fid or not _is_fed_admin(fid, update.effective_user.id):
        await update.effective_message.reply_text("🚫 Only federation admins can do that.")
        return
    uid, disp = await resolve_target(update, context)
    if not uid:
        await update.effective_message.reply_text("Reply to a user or pass @username / id.")
        return
    db.execute("DELETE FROM fed_bans WHERE fed_id=? AND user_id=?", (fid, uid))
    chats = db.query("SELECT chat_id FROM fed_chats WHERE fed_id=?", (fid,))
    for c in chats:
        try:
            await context.bot.unban_chat_member(c["chat_id"], uid, only_if_banned=True)
        except BadRequest:
            pass
    await update.effective_message.reply_text(f"🌐✅ Un-fedbanned {disp}.", parse_mode=ParseMode.HTML)


@group_only
async def fedinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fid = _fed_of(update.effective_chat.id)
    if not fid:
        await update.effective_message.reply_text("This group isn't in a federation.")
        return
    fed = db.query("SELECT name,owner_id FROM feds WHERE fed_id=?", (fid,), one=True)
    nchats = len(db.query("SELECT 1 FROM fed_chats WHERE fed_id=?", (fid,)))
    nbans = len(db.query("SELECT 1 FROM fed_bans WHERE fed_id=?", (fid,)))
    await update.effective_message.reply_text(
        f"🌐 <b>{esc(fed['name'])}</b>\nID: <code>{fid}</code>\n"
        f"Owner: {mention_id(fed['owner_id'])}\nGroups: {nchats}\nBanned users: {nbans}",
        parse_mode=ParseMode.HTML)


@group_only
async def fedpromote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fid = _fed_of(update.effective_chat.id)
    fed = db.query("SELECT owner_id FROM feds WHERE fed_id=?", (fid,), one=True) if fid else None
    if not fed or fed["owner_id"] != update.effective_user.id:
        await update.effective_message.reply_text("🚫 Only the fed owner can promote fed admins.")
        return
    uid, disp = await resolve_target(update, context)
    if not uid:
        await update.effective_message.reply_text("Reply to a user or pass @username / id.")
        return
    db.execute("INSERT OR IGNORE INTO fed_admins (fed_id,user_id) VALUES (?,?)", (fid, uid))
    await update.effective_message.reply_text(f"🌐⭐ {disp} is now a fed admin.", parse_mode=ParseMode.HTML)


async def enforce_fedban(update: Update, context) -> bool:
    """Called by the watcher: ban a fed-banned user the moment they speak. Returns True if banned."""
    chat_id = update.effective_chat.id
    fid = _fed_of(chat_id)
    if not fid:
        return False
    uid = update.effective_user.id
    banned = db.query("SELECT 1 FROM fed_bans WHERE fed_id=? AND user_id=?", (fid, uid), one=True)
    if not banned:
        return False
    try:
        await context.bot.ban_chat_member(chat_id, uid)
    except BadRequest:
        pass
    return True


def register_federation(app):
    app.add_handler(CommandHandler("newfed", newfed))
    app.add_handler(CommandHandler("joinfed", joinfed))
    app.add_handler(CommandHandler("leavefed", leavefed))
    app.add_handler(CommandHandler("fedban", fedban))
    app.add_handler(CommandHandler("unfedban", unfedban))
    app.add_handler(CommandHandler("fedinfo", fedinfo))
    app.add_handler(CommandHandler("fedpromote", fedpromote))


# ===========================================================================
# ---- extras ------------------------------------------------------------
# ===========================================================================

"""AFK, connections, approvals, disabling, log channel, info, report, help, start."""
import time
from telegram import Update
from telegram.constants import ParseMode, ChatType
from telegram.error import BadRequest
from telegram.ext import CommandHandler, ContextTypes



# --------------------------- START / HELP ----------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"{BRAND}  <i>v{__version__}</i>\n\n"
        "I'm your group's guardian — moderation, anti-spam, welcomes, federations and more.\n\n"
        "➕ Add me to a group and make me <b>admin</b>.\n"
        "📖 <code>/help</code> for the full command list.\n\n"
        "<i>3·6·9 ∞</i>",
        parse_mode=ParseMode.HTML)


HELP_TEXT = (
    f"{BRAND} <b>— Commands</b>\n\n"
    "<b>🛡 Moderation</b>\n"
    "<code>/ban /unban /kick /mute /unmute</code> — reply or @user; add <code>30m 2h 1d</code> for temp\n"
    "<code>/promote /demote /pin /unpin /purge /del</code>\n\n"
    "<b>⚠️ Warnings</b>\n"
    "<code>/warn /unwarn /warns /resetwarns /setwarnlimit /setwarnaction</code>\n\n"
    "<b>👋 Greetings</b>\n"
    "<code>/setwelcome /welcome /setgoodbye /goodbye /cleanservice /captcha</code>\n"
    "Placeholders: {name} {first} {username} {id} {group} — buttons via [Txt](buttonurl://link)\n\n"
    "<b>📝 Content</b>\n"
    "<code>/save /get /notes /clear</code> (or <code>#note</code>)  •  <code>/filter /filters /stop</code>  •  <code>/setrules /rules</code>\n\n"
    "<b>🔒 Protection</b>\n"
    "<code>/lock /unlock /locks</code>  •  <code>/setflood /flood</code>  •  <code>/antiraid</code>  •  <code>/nightmode</code>\n"
    "<code>/addblocklist /blocklists /unblocklist /blocklistaction</code>\n\n"
    "<b>🌐 Federations</b>\n"
    "<code>/newfed /joinfed /leavefed /fedban /unfedban /fedinfo /fedpromote</code>\n\n"
    "<b>🧰 Extras</b>\n"
    "<code>/afk /approve /unapprove /approved /connect /disconnect</code>\n"
    "<code>/disable /enable /disabled /setlog /unsetlog /settings</code>\n"
    "<code>/id /info /stickerid /report</code>\n"
)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML,
                                              disable_web_page_preview=True)


# --------------------------- AFK -------------------------------------------
async def afk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = " ".join(context.args).strip() or "AFK"
    db.execute("INSERT OR REPLACE INTO afk (user_id,reason,since) VALUES (?,?,?)",
               (update.effective_user.id, reason, int(time.time())))
    await update.effective_message.reply_text(
        f"💤 {mention(update.effective_user)} is now AFK.\n{esc(reason)}",
        parse_mode=ParseMode.HTML)


async def afk_watch(update: Update, context):
    """Watcher hook: clear AFK when user talks; announce AFK when someone pings an AFK user."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    # returning from AFK
    row = db.query("SELECT since FROM afk WHERE user_id=?", (user.id,), one=True)
    if row and not (msg.text or "").startswith("/afk"):
        db.execute("DELETE FROM afk WHERE user_id=?", (user.id,))
        mins = max(1, int((time.time() - row["since"]) / 60))
        await msg.reply_text(f"👋 Welcome back {mention(user)} — AFK for ~{mins} min.",
                             parse_mode=ParseMode.HTML)
        return
    # pinged an AFK user?
    targets = set()
    if msg.reply_to_message and msg.reply_to_message.from_user:
        targets.add(msg.reply_to_message.from_user.id)
    for ent in (msg.entities or []):
        if ent.type == "text_mention" and ent.user:
            targets.add(ent.user.id)
    for t in targets:
        r = db.query("SELECT reason,since FROM afk WHERE user_id=?", (t,), one=True)
        if r:
            await msg.reply_text(f"💤 That user is AFK: {esc(r['reason'])}")
            break


# --------------------------- APPROVALS -------------------------------------
@group_only
@admin_only
async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    uid, disp = await resolve_target(update, context)
    if not uid:
        await update.effective_message.reply_text("Reply to a user or pass @username / id.")
        return
    db.execute("INSERT OR IGNORE INTO approved (chat_id,user_id) VALUES (?,?)", (chat_id, uid))
    await update.effective_message.reply_text(
        f"✅ {disp} is approved — exempt from locks, blocklist, anti-flood.",
        parse_mode=ParseMode.HTML)


@group_only
@admin_only
async def unapprove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    uid, disp = await resolve_target(update, context)
    if not uid:
        await update.effective_message.reply_text("Reply to a user or pass @username / id.")
        return
    db.execute("DELETE FROM approved WHERE chat_id=? AND user_id=?", (chat_id, uid))
    await update.effective_message.reply_text(f"♻️ Removed approval from {disp}.",
                                              parse_mode=ParseMode.HTML)


@group_only
async def approved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.query("SELECT user_id FROM approved WHERE chat_id=?", (target_chat(update),))
    if not rows:
        await update.effective_message.reply_text("No approved users.")
        return
    await update.effective_message.reply_text(
        "✅ <b>Approved</b>\n" + "\n".join(f"• {mention_id(r['user_id'])}" for r in rows),
        parse_mode=ParseMode.HTML)


# --------------------------- CONNECTIONS (DM control) ----------------------
async def connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if update.effective_chat.type != ChatType.PRIVATE:
        # connect to this group from inside it
        if await is_admin(update.effective_chat.id, user_id, context):
            pm_connections[user_id] = update.effective_chat.id
            await update.effective_message.reply_text(
                "🔗 Connected. Manage this group from my DM now.")
        else:
            await update.effective_message.reply_text("🚫 Admins only.")
        return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.effective_message.reply_text(
            "In a group, send /connect. Or here: /connect <group_id>.")
        return
    chat_id = int(context.args[0])
    if not await is_admin(chat_id, user_id, context):
        await update.effective_message.reply_text("🚫 You're not an admin there.")
        return
    pm_connections[user_id] = chat_id
    try:
        chat = await context.bot.get_chat(chat_id)
        title = esc(chat.title)
    except BadRequest:
        title = str(chat_id)
    await update.effective_message.reply_text(f"🔗 Connected to <b>{title}</b>.",
                                              parse_mode=ParseMode.HTML)


async def disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pm_connections.pop(update.effective_user.id, None)
    await update.effective_message.reply_text("🔌 Disconnected.")


# --------------------------- DISABLING -------------------------------------
@group_only
@admin_only
async def disable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Usage: /disable <command>")
        return
    cmd = context.args[0].lower().lstrip("/")
    db.execute("INSERT OR IGNORE INTO disabled (chat_id,command) VALUES (?,?)",
               (target_chat(update), cmd))
    await update.effective_message.reply_text(f"🚫 Disabled /{esc(cmd)} for non-admins.",
                                              parse_mode=ParseMode.HTML)


@group_only
@admin_only
async def enable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Usage: /enable <command>")
        return
    cmd = context.args[0].lower().lstrip("/")
    db.execute("DELETE FROM disabled WHERE chat_id=? AND command=?", (target_chat(update), cmd))
    await update.effective_message.reply_text(f"✅ Enabled /{esc(cmd)}.", parse_mode=ParseMode.HTML)


@group_only
async def disabled(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.query("SELECT command FROM disabled WHERE chat_id=?", (target_chat(update),))
    if not rows:
        await update.effective_message.reply_text("No disabled commands.")
        return
    await update.effective_message.reply_text(
        "🚫 <b>Disabled</b>\n" + "\n".join(f"• /{esc(r['command'])}" for r in rows),
        parse_mode=ParseMode.HTML)


# --------------------------- LOG CHANNEL -----------------------------------
@group_only
@admin_only
async def setlog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    if context.args and context.args[0].lstrip("-").isdigit():
        target = int(context.args[0])
    else:
        await update.effective_message.reply_text(
            "Make me admin in your log channel, then run:\n"
            "/setlog <channel_id>  (e.g. -1001234567890)\n"
            "Tip: forward a channel message to @userinfobot to get its id.")
        return
    db.set_setting(chat_id, "log_channel", target)
    try:
        await context.bot.send_message(target, "✅ This channel is now the ZAPP action log.")
    except BadRequest:
        await update.effective_message.reply_text("Saved, but I couldn't post there — am I admin in it?")
        return
    await update.effective_message.reply_text("✅ Log channel set.")


@group_only
@admin_only
async def unsetlog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.set_setting(target_chat(update), "log_channel", None)
    await update.effective_message.reply_text("✅ Log channel cleared.")


# --------------------------- SETTINGS PANEL --------------------------------
@group_only
async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    s = db.get_settings(chat_id)
    locks = len(db.query("SELECT 1 FROM locks WHERE chat_id=?", (chat_id,)))
    notes = len(db.query("SELECT 1 FROM notes WHERE chat_id=?", (chat_id,)))
    flt = len(db.query("SELECT 1 FROM filters WHERE chat_id=?", (chat_id,)))
    blk = len(db.query("SELECT 1 FROM blocklist WHERE chat_id=?", (chat_id,)))
    fed = db.query("SELECT fed_id FROM chat_fed WHERE chat_id=?", (chat_id,), one=True)
    txt = (
        "⚙️ <b>Group settings</b>\n\n"
        f"👋 Welcome: {'on' if s['welcome_on'] else 'off'}  •  Goodbye: {'on' if s['goodbye_on'] else 'off'}\n"
        f"🤖 Captcha: {'on ('+s['captcha_mode']+')' if s['captcha_on'] else 'off'}\n"
        f"🧹 Clean service: {'on' if s['clean_service'] else 'off'}\n"
        f"⚠️ Warns: limit {s['warn_limit']} → {s['warn_action']}\n"
        f"🌊 Anti-flood: {s['flood_limit'] or 'off'} → {s['flood_action']}\n"
        f"🛡 Anti-raid: {'on' if s['antiraid_on'] else 'off'} "
        f"({s['antiraid_threshold']}/{s['antiraid_window']}s → {s['antiraid_action']})\n"
        f"🌙 Night mode: {'on' if s['nightmode_on'] else 'off'} "
        f"({s['nightmode_start']}–{s['nightmode_end']} UTC)\n"
        f"⛔ Blocklist: {blk} words → {s['blocklist_action']}\n"
        f"🔒 Locks: {locks}  •  📝 Notes: {notes}  •  🧲 Filters: {flt}\n"
        f"🌐 Federation: {'yes' if fed else 'no'}\n"
        f"📋 Log channel: {'set' if s['log_channel'] else 'none'}"
    )
    await update.effective_message.reply_text(txt, parse_mode=ParseMode.HTML)


# --------------------------- INFO / ID / STICKER / REPORT ------------------
async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    parts = [f"💬 <b>Chat ID:</b> <code>{update.effective_chat.id}</code>"]
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        parts.append(f"👤 <b>{esc(u.first_name)}:</b> <code>{u.id}</code>")
        if msg.reply_to_message.forward_origin:
            parts.append("↪️ (forwarded)")
    else:
        parts.append(f"👤 <b>Your ID:</b> <code>{update.effective_user.id}</code>")
    await msg.reply_text("\n".join(parts), parse_mode=ParseMode.HTML)


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, _ = await resolve_target(update, context)
    if not uid:
        uid = update.effective_user.id
    try:
        chat = await context.bot.get_chat(uid)
    except BadRequest:
        await update.effective_message.reply_text("Couldn't fetch that user.")
        return
    lines = [f"<b>ID:</b> <code>{chat.id}</code>",
             f"<b>Name:</b> {esc(chat.first_name or '—')}"]
    if chat.username:
        lines.append(f"<b>Username:</b> @{esc(chat.username)}")
    if update.effective_chat.type != ChatType.PRIVATE:
        try:
            m = await context.bot.get_chat_member(update.effective_chat.id, uid)
            lines.append(f"<b>Status:</b> {m.status}")
            w = db.query("SELECT count FROM warns WHERE chat_id=? AND user_id=?",
                         (update.effective_chat.id, uid), one=True)
            if w and w["count"]:
                lines.append(f"<b>Warns:</b> {w['count']}")
        except BadRequest:
            pass
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def stickerid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = update.effective_message.reply_to_message
    if not reply or not reply.sticker:
        await update.effective_message.reply_text("Reply to a sticker with /stickerid.")
        return
    s = reply.sticker
    await update.effective_message.reply_text(
        f"✅ <b>Sticker</b>\n<b>ID:</b>\n<code>{s.file_id}</code>\n"
        f"<b>Emoji:</b> {s.emoji or 'none'}\n<b>Pack:</b> {s.set_name or 'unknown'}",
        parse_mode=ParseMode.HTML)


@group_only
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("Reply to the message you're reporting with /report.")
        return
    try:
        admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    except BadRequest:
        return
    pings = "".join(f'<a href="tg://user?id={a.user.id}">\u2063</a>'
                    for a in admins if not a.user.is_bot)
    await msg.reply_to_message.reply_text(
        f"🚨 {mention(update.effective_user)} reported this to admins.{pings}",
        parse_mode=ParseMode.HTML)


def register_extras(app):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("afk", afk))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("unapprove", unapprove))
    app.add_handler(CommandHandler("approved", approved))
    app.add_handler(CommandHandler("connect", connect))
    app.add_handler(CommandHandler("disconnect", disconnect))
    app.add_handler(CommandHandler("disable", disable))
    app.add_handler(CommandHandler("enable", enable))
    app.add_handler(CommandHandler("disabled", disabled))
    app.add_handler(CommandHandler("setlog", setlog))
    app.add_handler(CommandHandler("unsetlog", unsetlog))
    app.add_handler(CommandHandler(["settings", "config"], settings_cmd))
    app.add_handler(CommandHandler("id", get_id))
    app.add_handler(CommandHandler("info", info))
    app.add_handler(CommandHandler("stickerid", stickerid))
    app.add_handler(CommandHandler("report", report))


# ===========================================================================
# ---- moderation ------------------------------------------------------------
# ===========================================================================

"""Moderation commands."""
from telegram import Update, ChatPermissions
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import CommandHandler, ContextTypes


MUTED = ChatPermissions(can_send_messages=False)
FULL = ChatPermissions(
    can_send_messages=True, can_send_polls=True, can_send_other_messages=True,
    can_add_web_page_previews=True, can_invite_users=True,
)


async def _guard(update, context, need_bot_admin=True):
    """Return (chat_id, uid, disp) or (None, ...) after replying with the problem."""
    chat_id = target_chat(update)
    if need_bot_admin and not await is_admin(chat_id, context.bot.id, context):
        await update.effective_message.reply_text("⚠️ Make me admin with the right permissions first.")
        return None, None, None
    uid, disp = await resolve_target(update, context)
    if not uid:
        await update.effective_message.reply_text("Reply to a user, or pass @username / id.")
        return None, None, None
    if uid == context.bot.id:
        await update.effective_message.reply_text("😅 I'm not going to do that to myself.")
        return None, None, None
    if await is_admin(chat_id, uid, context):
        await update.effective_message.reply_text("😅 That user is an admin — I'll pass.")
        return None, None, None
    return chat_id, uid, disp


@group_only
@admin_only
async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id, uid, disp = await _guard(update, context)
    if not uid:
        return
    secs, raw = duration_arg(context)
    reason = reason_text(update, context)
    try:
        await context.bot.ban_chat_member(chat_id, uid, until_date=until_from(secs))
        when = f" for {raw}" if secs else ""
        because = f"\nReason: {reason}" if reason else ""
        await update.effective_message.reply_text(
            f"🔨 Banned {disp}{when}.{because}", parse_mode=ParseMode.HTML)
        await log_action(context, chat_id,
                         f"🔨 <b>Ban</b>{when}\nUser: {disp}\nBy: {mention(update.effective_user)}{because}")
    except BadRequest as e:
        await update.effective_message.reply_text(f"Couldn't ban: {e.message}")


@group_only
@admin_only
async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    uid, disp = await resolve_target(update, context)
    if not uid:
        await update.effective_message.reply_text("Reply to a user or pass @username / id.")
        return
    try:
        await context.bot.unban_chat_member(chat_id, uid, only_if_banned=True)
        await update.effective_message.reply_text(f"✅ Unbanned {disp}.", parse_mode=ParseMode.HTML)
    except BadRequest as e:
        await update.effective_message.reply_text(f"Couldn't unban: {e.message}")


@group_only
@admin_only
async def kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id, uid, disp = await _guard(update, context)
    if not uid:
        return
    try:
        await context.bot.ban_chat_member(chat_id, uid)
        await context.bot.unban_chat_member(chat_id, uid)
        await update.effective_message.reply_text(f"👢 Kicked {disp}.", parse_mode=ParseMode.HTML)
        await log_action(context, chat_id,
                         f"👢 <b>Kick</b>\nUser: {disp}\nBy: {mention(update.effective_user)}")
    except BadRequest as e:
        await update.effective_message.reply_text(f"Couldn't kick: {e.message}")


@group_only
@admin_only
async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id, uid, disp = await _guard(update, context)
    if not uid:
        return
    secs, raw = duration_arg(context)
    try:
        await context.bot.restrict_chat_member(chat_id, uid, permissions=MUTED,
                                               until_date=until_from(secs))
        when = f" for {raw}" if secs else ""
        await update.effective_message.reply_text(f"🔇 Muted {disp}{when}.", parse_mode=ParseMode.HTML)
        await log_action(context, chat_id,
                         f"🔇 <b>Mute</b>{when}\nUser: {disp}\nBy: {mention(update.effective_user)}")
    except BadRequest as e:
        await update.effective_message.reply_text(f"Couldn't mute: {e.message}")


@group_only
@admin_only
async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    uid, disp = await resolve_target(update, context)
    if not uid:
        await update.effective_message.reply_text("Reply to a user or pass @username / id.")
        return
    try:
        await context.bot.restrict_chat_member(chat_id, uid, permissions=FULL)
        await update.effective_message.reply_text(f"🔊 Unmuted {disp}.", parse_mode=ParseMode.HTML)
    except BadRequest as e:
        await update.effective_message.reply_text(f"Couldn't unmute: {e.message}")


@group_only
@admin_only
async def promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    uid, disp = await resolve_target(update, context)
    if not uid:
        await update.effective_message.reply_text("Reply to a user or pass @username / id.")
        return
    title = " ".join(context.args[1:]) if context.args and not update.effective_message.reply_to_message else " ".join(context.args)
    try:
        await context.bot.promote_chat_member(
            chat_id, uid, can_delete_messages=True, can_restrict_members=True,
            can_pin_messages=True, can_invite_users=True, can_manage_video_chats=True)
        invalidate_admin_cache(chat_id)
        if title.strip():
            try:
                await context.bot.set_chat_administrator_custom_title(chat_id, uid, title.strip()[:16])
            except BadRequest:
                pass
        await update.effective_message.reply_text(f"⭐ Promoted {disp}.", parse_mode=ParseMode.HTML)
        await log_action(context, chat_id,
                         f"⭐ <b>Promote</b>\nUser: {disp}\nBy: {mention(update.effective_user)}")
    except BadRequest as e:
        await update.effective_message.reply_text(f"Couldn't promote: {e.message}")


@group_only
@admin_only
async def demote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    uid, disp = await resolve_target(update, context)
    if not uid:
        await update.effective_message.reply_text("Reply to a user or pass @username / id.")
        return
    try:
        await context.bot.promote_chat_member(
            chat_id, uid, can_delete_messages=False, can_restrict_members=False,
            can_pin_messages=False, can_invite_users=False, can_change_info=False,
            can_promote_members=False, can_manage_video_chats=False)
        invalidate_admin_cache(chat_id)
        await update.effective_message.reply_text(f"⬇️ Demoted {disp}.", parse_mode=ParseMode.HTML)
    except BadRequest as e:
        await update.effective_message.reply_text(f"Couldn't demote: {e.message}")


@group_only
@admin_only
async def pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("Reply to the message you want pinned (add 'loud' to notify).")
        return
    loud = bool(context.args) and context.args[0].lower() in ("loud", "notify", "hard")
    try:
        await context.bot.pin_chat_message(update.effective_chat.id,
                                           msg.reply_to_message.message_id,
                                           disable_notification=not loud)
    except BadRequest as e:
        await msg.reply_text(f"Couldn't pin: {e.message}")


@group_only
@admin_only
async def unpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    try:
        if msg.reply_to_message:
            await context.bot.unpin_chat_message(update.effective_chat.id,
                                                 msg.reply_to_message.message_id)
        elif context.args and context.args[0].lower() == "all":
            await context.bot.unpin_all_chat_messages(update.effective_chat.id)
        else:
            await context.bot.unpin_chat_message(update.effective_chat.id)
        await msg.reply_text("📌 Unpinned.")
    except BadRequest as e:
        await msg.reply_text(f"Couldn't unpin: {e.message}")


@group_only
@admin_only
async def purge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    if not await is_admin(chat_id, context.bot.id, context):
        await msg.reply_text("⚠️ I need delete-message rights.")
        return
    # /purge N  (delete last N) OR reply-based purge
    if context.args and context.args[0].isdigit():
        n = min(int(context.args[0]), 200)
        start_id = msg.message_id - n
        end_id = msg.message_id
    elif msg.reply_to_message:
        start_id = msg.reply_to_message.message_id
        end_id = msg.message_id
    else:
        await msg.reply_text("Reply to a message, or use /purge <number>.")
        return
    deleted = 0
    for mid in range(start_id, end_id + 1):
        try:
            await context.bot.delete_message(chat_id, mid)
            deleted += 1
        except BadRequest:
            pass
    note = await context.bot.send_message(chat_id, f"🧹 Purged {deleted} messages.")
    if context.job_queue:
        context.job_queue.run_once(
            lambda c: c.bot.delete_message(chat_id, note.message_id), 4)


@group_only
@admin_only
async def delete_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("Reply to the message you want deleted.")
        return
    try:
        await context.bot.delete_message(update.effective_chat.id,
                                         msg.reply_to_message.message_id)
        await context.bot.delete_message(update.effective_chat.id, msg.message_id)
    except BadRequest:
        pass


def register_moderation(app):
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler(["tban", "tempban"], ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("kick", kick))
    app.add_handler(CommandHandler(["mute", "tmute"], mute))
    app.add_handler(CommandHandler("unmute", unmute))
    app.add_handler(CommandHandler("promote", promote))
    app.add_handler(CommandHandler("demote", demote))
    app.add_handler(CommandHandler("pin", pin))
    app.add_handler(CommandHandler("unpin", unpin))
    app.add_handler(CommandHandler("purge", purge))
    app.add_handler(CommandHandler(["del", "delete"], delete_msg))


# ===========================================================================
# ---- warnings ------------------------------------------------------------
# ===========================================================================

"""Warnings system."""
import time
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import CommandHandler, ContextTypes



@group_only
@admin_only
async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    uid, disp = await resolve_target(update, context)
    if not uid:
        await update.effective_message.reply_text("Reply to a user or pass @username / id.")
        return
    if await is_admin(chat_id, uid, context):
        await update.effective_message.reply_text("😅 I won't warn an admin.")
        return
    reason = reason_text(update, context) or "no reason given"
    s = db.get_settings(chat_id)
    limit, action = s["warn_limit"], s["warn_action"]

    db.execute("INSERT OR IGNORE INTO warns (chat_id,user_id,count) VALUES (?,?,0)", (chat_id, uid))
    db.execute("UPDATE warns SET count=count+1 WHERE chat_id=? AND user_id=?", (chat_id, uid))
    db.execute("INSERT INTO warn_reasons (chat_id,user_id,reason,ts) VALUES (?,?,?,?)",
               (chat_id, uid, reason, int(time.time())))
    count = db.query("SELECT count FROM warns WHERE chat_id=? AND user_id=?",
                     (chat_id, uid), one=True)["count"]

    if count >= limit:
        db.execute("UPDATE warns SET count=0 WHERE chat_id=? AND user_id=?", (chat_id, uid))
        db.execute("DELETE FROM warn_reasons WHERE chat_id=? AND user_id=?", (chat_id, uid))
        try:
            verb = await do_action(action, chat_id, uid, context)
            await update.effective_message.reply_text(
                f"⚠️ {disp} hit the warn limit ({limit}) and was <b>{verb}</b>.",
                parse_mode=ParseMode.HTML)
            await log_action(context, chat_id,
                             f"⚠️ <b>Warn limit → {verb}</b>\nUser: {disp}\n"
                             f"By: {mention(update.effective_user)}")
        except BadRequest as e:
            await update.effective_message.reply_text(f"Limit reached but action failed: {e.message}")
    else:
        await update.effective_message.reply_text(
            f"⚠️ {disp} warned ({count}/{limit}).\nReason: {reason}",
            parse_mode=ParseMode.HTML)


@group_only
@admin_only
async def unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    uid, disp = await resolve_target(update, context)
    if not uid:
        await update.effective_message.reply_text("Reply to a user or pass @username / id.")
        return
    db.execute("UPDATE warns SET count=MAX(count-1,0) WHERE chat_id=? AND user_id=?", (chat_id, uid))
    row = db.query("SELECT count FROM warns WHERE chat_id=? AND user_id=?", (chat_id, uid), one=True)
    await update.effective_message.reply_text(
        f"➖ Removed a warn from {disp}. Now {row['count'] if row else 0}.",
        parse_mode=ParseMode.HTML)


@group_only
async def warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    uid, disp = await resolve_target(update, context)
    if not uid:
        uid, disp = update.effective_user.id, mention(update.effective_user)
    s = db.get_settings(chat_id)
    row = db.query("SELECT count FROM warns WHERE chat_id=? AND user_id=?", (chat_id, uid), one=True)
    count = row["count"] if row else 0
    reasons = db.query("SELECT reason FROM warn_reasons WHERE chat_id=? AND user_id=? ORDER BY ts",
                       (chat_id, uid))
    extra = ""
    if reasons:
        extra = "\n" + "\n".join(f"• {r['reason']}" for r in reasons[-10:])
    await update.effective_message.reply_text(
        f"⚠️ {disp} has {count}/{s['warn_limit']} warns.{extra}", parse_mode=ParseMode.HTML)


@group_only
@admin_only
async def resetwarns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    uid, disp = await resolve_target(update, context)
    if not uid:
        await update.effective_message.reply_text("Reply to a user or pass @username / id.")
        return
    db.execute("UPDATE warns SET count=0 WHERE chat_id=? AND user_id=?", (chat_id, uid))
    db.execute("DELETE FROM warn_reasons WHERE chat_id=? AND user_id=?", (chat_id, uid))
    await update.effective_message.reply_text(f"♻️ Reset warns for {disp}.", parse_mode=ParseMode.HTML)


@group_only
@admin_only
async def setwarnlimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit() or int(context.args[0]) < 1:
        await update.effective_message.reply_text("Usage: /setwarnlimit 3")
        return
    db.set_setting(target_chat(update), "warn_limit", int(context.args[0]))
    await update.effective_message.reply_text(f"✅ Warn limit set to {context.args[0]}.")


@group_only
@admin_only
async def setwarnaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower() not in ("mute", "kick", "ban"):
        await update.effective_message.reply_text("Usage: /setwarnaction mute|kick|ban")
        return
    db.set_setting(target_chat(update), "warn_action", context.args[0].lower())
    await update.effective_message.reply_text(f"✅ Warn action set to {context.args[0].lower()}.")


def register_warnings(app):
    app.add_handler(CommandHandler("warn", warn))
    app.add_handler(CommandHandler("unwarn", unwarn))
    app.add_handler(CommandHandler(["warns", "warnings"], warns))
    app.add_handler(CommandHandler("resetwarns", resetwarns))
    app.add_handler(CommandHandler("setwarnlimit", setwarnlimit))
    app.add_handler(CommandHandler("setwarnaction", setwarnaction))


# ===========================================================================
# ---- greetings ------------------------------------------------------------
# ===========================================================================

"""Welcome / goodbye / captcha / clean-service, plus the new-member entry point."""
import time
import random
from telegram import (
    Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.error import BadRequest
from telegram.ext import (
    CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters,
)


MUTED = ChatPermissions(can_send_messages=False)
FULL = ChatPermissions(
    can_send_messages=True, can_send_polls=True, can_send_other_messages=True,
    can_add_web_page_previews=True, can_invite_users=True,
)
DEFAULT_WELCOME = "👋 Welcome {name} to {group}!\nCheck the /rules and enjoy. " + BRAND

# pending captcha solutions: (chat_id, user_id) -> answer
_captcha_answers = {}


# ---- settings commands -----------------------------------------------------
@group_only
@admin_only
async def setwelcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = html_body(update.effective_message, drop=1)
    if not text:
        await update.effective_message.reply_text(
            "Usage: /setwelcome <text>\nPlaceholders: {name} {first} {username} {id} {group} {count}\n"
            "Buttons: [Label](buttonurl://https://link.com)  (add :same to keep on one row)")
        return
    db.set_setting(target_chat(update), "welcome", text)
    await update.effective_message.reply_text("✅ Welcome message saved.")


@group_only
@admin_only
async def setgoodbye(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = html_body(update.effective_message, drop=1)
    if not text:
        await update.effective_message.reply_text("Usage: /setgoodbye <text>  (placeholders supported)")
        return
    db.set_setting(target_chat(update), "goodbye", text)
    await update.effective_message.reply_text("✅ Goodbye message saved.")


def _toggle(setting):
    @group_only
    @admin_only
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        arg = (context.args[0].lower() if context.args else "")
        if arg not in ("on", "off"):
            await update.effective_message.reply_text(f"Usage: /{setting.split('_')[0]} on|off")
            return
        db.set_setting(target_chat(update), setting, 1 if arg == "on" else 0)
        await update.effective_message.reply_text(f"✅ {setting.replace('_', ' ')} {arg}.")
    return handler


@group_only
@admin_only
async def captcha_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = (context.args[0].lower() if context.args else "")
    if arg in ("button", "math"):
        db.set_setting(target_chat(update), "captcha_on", 1)
        db.set_setting(target_chat(update), "captcha_mode", arg)
        await update.effective_message.reply_text(f"✅ Captcha on ({arg} mode).")
    elif arg == "on":
        db.set_setting(target_chat(update), "captcha_on", 1)
        await update.effective_message.reply_text("✅ Captcha on.")
    elif arg == "off":
        db.set_setting(target_chat(update), "captcha_on", 0)
        await update.effective_message.reply_text("✅ Captcha off.")
    else:
        await update.effective_message.reply_text("Usage: /captcha on|off|button|math")


# ---- captcha jobs ----------------------------------------------------------
async def _kick_unverified(context: ContextTypes.DEFAULT_TYPE):
    chat_id, uid = context.job.data
    try:
        m = await context.bot.get_chat_member(chat_id, uid)
        if m.status == ChatMemberStatus.RESTRICTED and not m.can_send_messages:
            await context.bot.ban_chat_member(chat_id, uid)
            await context.bot.unban_chat_member(chat_id, uid)
    except BadRequest:
        pass
    _captcha_answers.pop((chat_id, uid), None)


# ---- new member entry ------------------------------------------------------
async def on_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    s = db.get_settings(chat.id)
    bot_admin = await bot_can(update, context)

    # clean the "X joined" service message
    if s["clean_service"] and bot_admin:
        try:
            await update.effective_message.delete()
        except BadRequest:
            pass

    now = time.time()
    for member in update.effective_message.new_chat_members:
        if member.id == context.bot.id:
            continue

        # --- anti-raid ---
        if s["antiraid_on"] and bot_admin:
            dq = join_tracker[chat.id]
            dq.append(now)
            recent = [t for t in dq if now - t <= s["antiraid_window"]]
            if raid_active.get(chat.id, 0) > now or len(recent) >= s["antiraid_threshold"]:
                raid_active[chat.id] = now + 300   # lockdown 5 min
                try:
                    if s["antiraid_action"] == "ban":
                        await context.bot.ban_chat_member(chat.id, member.id)
                    elif s["antiraid_action"] == "kick":
                        await context.bot.ban_chat_member(chat.id, member.id)
                        await context.bot.unban_chat_member(chat.id, member.id)
                    else:
                        await context.bot.restrict_chat_member(chat.id, member.id, permissions=MUTED)
                except BadRequest:
                    pass
                continue

        # --- captcha ---
        if s["captcha_on"] and bot_admin:
            try:
                await context.bot.restrict_chat_member(chat.id, member.id, permissions=MUTED)
            except BadRequest:
                pass
            if s["captcha_mode"] == "math":
                a, b = random.randint(2, 9), random.randint(2, 9)
                _captcha_answers[(chat.id, member.id)] = a + b
                opts = {a + b, a + b + random.randint(1, 3), abs(a - b) + 1}
                while len(opts) < 3:
                    opts.add(a + b + random.randint(-4, 4))
                buttons = [InlineKeyboardButton(str(o), callback_data=f"cap:{member.id}:{o}")
                           for o in random.sample(list(opts), len(opts))]
                kb = InlineKeyboardMarkup([buttons])
                await update.effective_message.reply_text(
                    f"🤖 {mention(member)}, solve to chat: <b>{a} + {b} = ?</b>",
                    parse_mode=ParseMode.HTML, reply_markup=kb)
            else:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                    "✅ I'm human", callback_data=f"cap:{member.id}:ok")]])
                txt, _ = build_message(s["welcome"] or DEFAULT_WELCOME, user=member, chat=chat)
                await update.effective_message.reply_text(
                    txt + "\n\n🤖 Tap below to unlock chat.",
                    parse_mode=ParseMode.HTML, reply_markup=kb)
            if context.job_queue:
                context.job_queue.run_once(_kick_unverified, 120, data=(chat.id, member.id))
            continue

        # --- normal welcome ---
        if s["welcome_on"]:
            txt, markup = build_message(s["welcome"] or DEFAULT_WELCOME,
                                        user=member, chat=chat,
                                        count=chat.id and None)
            sent = await update.effective_message.reply_text(
                txt, parse_mode=ParseMode.HTML, reply_markup=markup,
                disable_web_page_preview=True)
            if s["clean_welcome"] and context.job_queue:
                context.job_queue.run_once(
                    lambda c, cid=chat.id, mid=sent.message_id:
                        c.bot.delete_message(cid, mid), 120)


async def on_left_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    s = db.get_settings(chat.id)
    if s["clean_service"] and await bot_can(update, context):
        try:
            await update.effective_message.delete()
        except BadRequest:
            pass
    member = update.effective_message.left_chat_member
    if s["goodbye_on"] and member and member.id != context.bot.id:
        txt, markup = build_message(s["goodbye"] or "👋 {first} left the group.",
                                    user=member, chat=chat)
        await update.effective_message.reply_text(txt, parse_mode=ParseMode.HTML,
                                                  reply_markup=markup)


async def captcha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, target, answer = q.data.split(":")
    target = int(target)
    if q.from_user.id != target:
        await q.answer("This isn't for you.", show_alert=True)
        return
    chat_id = q.message.chat.id
    ok = (answer == "ok") or (
        (chat_id, target) in _captcha_answers and str(_captcha_answers[(chat_id, target)]) == answer)
    if not ok:
        await q.answer("Wrong — try again.", show_alert=True)
        return
    try:
        await context.bot.restrict_chat_member(chat_id, target, permissions=FULL)
        await q.answer("Verified — welcome! ⚡")
        await q.edit_message_reply_markup(reply_markup=None)
    except BadRequest:
        await q.answer("Couldn't verify, ask an admin.", show_alert=True)
    _captcha_answers.pop((chat_id, target), None)


def register_greetings(app):
    app.add_handler(CommandHandler("setwelcome", setwelcome))
    app.add_handler(CommandHandler("welcome", _toggle("welcome_on")))
    app.add_handler(CommandHandler("setgoodbye", setgoodbye))
    app.add_handler(CommandHandler("goodbye", _toggle("goodbye_on")))
    app.add_handler(CommandHandler("cleanservice", _toggle("clean_service")))
    app.add_handler(CommandHandler("cleanwelcome", _toggle("clean_welcome")))
    app.add_handler(CommandHandler("captcha", captcha_toggle))
    app.add_handler(CallbackQueryHandler(captcha_callback, pattern=r"^cap:"))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_member))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, on_left_member))


# ===========================================================================
# ---- protection ------------------------------------------------------------
# ===========================================================================

"""Locks, anti-flood, anti-raid, and night-mode configuration + scheduling."""
from datetime import time as dtime, timezone

from telegram import Update, ChatPermissions
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import CommandHandler, ContextTypes


VALID_LOCKS = {"url", "sticker", "gif", "photo", "video", "forward", "mention",
               "document", "audio", "voice", "poll", "game", "invite", "email"}

LOCKED = ChatPermissions(can_send_messages=False)
OPEN = ChatPermissions(
    can_send_messages=True, can_send_polls=True, can_send_other_messages=True,
    can_add_web_page_previews=True, can_invite_users=True,
)


# --------------------------- LOCKS -----------------------------------------
@group_only
@admin_only
async def lock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Lock types: " + ", ".join(sorted(VALID_LOCKS)))
        return
    chat_id = target_chat(update)
    added = []
    for lt in (a.lower() for a in context.args):
        if lt in VALID_LOCKS:
            db.execute("INSERT OR IGNORE INTO locks (chat_id,lock_type) VALUES (?,?)", (chat_id, lt))
            added.append(lt)
    if added:
        await update.effective_message.reply_text("🔒 Locked: " + ", ".join(added))
    else:
        await update.effective_message.reply_text("Unknown lock type.")


@group_only
@admin_only
async def unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Usage: /unlock <type ...>")
        return
    chat_id = target_chat(update)
    for lt in (a.lower() for a in context.args):
        db.execute("DELETE FROM locks WHERE chat_id=? AND lock_type=?", (chat_id, lt))
    await update.effective_message.reply_text("🔓 Unlocked: " + ", ".join(a.lower() for a in context.args))


@group_only
async def list_locks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.query("SELECT lock_type FROM locks WHERE chat_id=?", (target_chat(update),))
    active = {r["lock_type"] for r in rows}
    lines = [f"{'🔒' if t in active else '🔓'} {t}" for t in sorted(VALID_LOCKS)]
    await update.effective_message.reply_text("<b>Locks</b>\n" + "\n".join(lines),
                                              parse_mode=ParseMode.HTML)


# --------------------------- ANTI-FLOOD ------------------------------------
@group_only
@admin_only
async def setflood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /setflood 8   (msgs / 10s; 0 = off)")
        return
    n = int(context.args[0])
    db.set_setting(target_chat(update), "flood_limit", n)
    await update.effective_message.reply_text(
        f"✅ Anti-flood set to {n} msgs/10s." if n else "✅ Anti-flood disabled.")


@group_only
@admin_only
async def setfloodaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower() not in ("mute", "kick", "ban"):
        await update.effective_message.reply_text("Usage: /setfloodaction mute|kick|ban")
        return
    db.set_setting(target_chat(update), "flood_action", context.args[0].lower())
    await update.effective_message.reply_text(f"✅ Flood action: {context.args[0].lower()}.")


@group_only
async def flood_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = db.get_settings(target_chat(update))
    n = s["flood_limit"]
    await update.effective_message.reply_text(
        f"🌊 Anti-flood: {'off' if not n else f'{n} msgs/10s → {s['flood_action']}'}.")


# --------------------------- ANTI-RAID -------------------------------------
@group_only
@admin_only
async def antiraid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    args = [a.lower() for a in context.args]
    if not args or args[0] not in ("on", "off"):
        s = db.get_settings(chat_id)
        await update.effective_message.reply_text(
            f"🛡 Anti-raid: {'ON' if s['antiraid_on'] else 'OFF'} "
            f"({s['antiraid_threshold']} joins / {s['antiraid_window']}s → {s['antiraid_action']})\n"
            "Usage: /antiraid on|off [threshold] [seconds] [mute|kick|ban]")
        return
    db.set_setting(chat_id, "antiraid_on", 1 if args[0] == "on" else 0)
    rest = args[1:]
    nums = [int(x) for x in rest if x.isdigit()]
    if len(nums) >= 1:
        db.set_setting(chat_id, "antiraid_threshold", nums[0])
    if len(nums) >= 2:
        db.set_setting(chat_id, "antiraid_window", nums[1])
    for a in rest:
        if a in ("mute", "kick", "ban"):
            db.set_setting(chat_id, "antiraid_action", a)
    await update.effective_message.reply_text(f"🛡 Anti-raid {args[0]}.")


# --------------------------- NIGHT MODE ------------------------------------
async def _night_lock(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    try:
        await context.bot.set_chat_permissions(chat_id, LOCKED)
        await context.bot.send_message(chat_id, "🌙 Night mode: chat locked until morning.")
    except BadRequest:
        pass


async def _night_unlock(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    try:
        await context.bot.set_chat_permissions(chat_id, OPEN)
        await context.bot.send_message(chat_id, "☀️ Good morning! Chat is open.")
    except BadRequest:
        pass


def _parse_hhmm(s):
    try:
        h, m = s.split(":")
        return dtime(int(h), int(m), tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def schedule_nightmode(app, chat_id):
    jq = app.job_queue
    if not jq:
        return
    for name in (f"nl_{chat_id}", f"nu_{chat_id}"):
        for j in jq.get_jobs_by_name(name):
            j.schedule_removal()
    s = db.get_settings(chat_id)
    if not s["nightmode_on"]:
        return
    start, end = _parse_hhmm(s["nightmode_start"]), _parse_hhmm(s["nightmode_end"])
    if start:
        jq.run_daily(_night_lock, start, data=chat_id, name=f"nl_{chat_id}")
    if end:
        jq.run_daily(_night_unlock, end, data=chat_id, name=f"nu_{chat_id}")


@group_only
@admin_only
async def nightmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    args = [a.lower() for a in context.args]
    if not args or args[0] not in ("on", "off"):
        s = db.get_settings(chat_id)
        await update.effective_message.reply_text(
            f"🌙 Night mode: {'ON' if s['nightmode_on'] else 'OFF'} "
            f"({s['nightmode_start']}–{s['nightmode_end']} UTC)\n"
            "Usage: /nightmode on|off [HH:MM HH:MM]  (start end, 24h UTC)")
        return
    db.set_setting(chat_id, "nightmode_on", 1 if args[0] == "on" else 0)
    times = [a for a in args[1:] if ":" in a]
    if len(times) >= 2:
        db.set_setting(chat_id, "nightmode_start", times[0])
        db.set_setting(chat_id, "nightmode_end", times[1])
    schedule_nightmode(context.application, chat_id)
    await update.effective_message.reply_text(f"🌙 Night mode {args[0]}.")


def register_protection(app):
    app.add_handler(CommandHandler("lock", lock))
    app.add_handler(CommandHandler("unlock", unlock))
    app.add_handler(CommandHandler("locks", list_locks))
    app.add_handler(CommandHandler("setflood", setflood))
    app.add_handler(CommandHandler("setfloodaction", setfloodaction))
    app.add_handler(CommandHandler("flood", flood_status))
    app.add_handler(CommandHandler("antiraid", antiraid))
    app.add_handler(CommandHandler("nightmode", nightmode))


def reschedule_all(app):
    """On startup, schedule night-mode jobs for chats that have it enabled."""
    rows = db.query("SELECT chat_id FROM settings WHERE nightmode_on=1")
    for r in rows:
        schedule_nightmode(app, r["chat_id"])


# ===========================================================================
# ---- watcher ------------------------------------------------------------
# ===========================================================================

"""
The catch-all watcher for non-command messages. Runs the passive enforcement:
fed-ban, #note shortcut, locks, blocklist, anti-flood, filters, AFK.
"""
import re
import time
from telegram import Update
from telegram.constants import ParseMode, ChatType
from telegram.error import BadRequest
from telegram.ext import MessageHandler, ContextTypes, filters



def _is_approved(chat_id, user_id):
    return db.query("SELECT 1 FROM approved WHERE chat_id=? AND user_id=?",
                    (chat_id, user_id), one=True) is not None


def _lock_triggered(locks, msg):
    ents = (msg.entities or []) + (msg.caption_entities or [])
    text = (msg.text or msg.caption or "")
    return (
        ("sticker" in locks and msg.sticker) or
        ("gif" in locks and msg.animation) or
        ("photo" in locks and msg.photo) or
        ("video" in locks and (msg.video or msg.video_note)) or
        ("document" in locks and msg.document) or
        ("audio" in locks and msg.audio) or
        ("voice" in locks and msg.voice) or
        ("poll" in locks and msg.poll) or
        ("game" in locks and msg.game) or
        ("forward" in locks and msg.forward_origin) or
        ("url" in locks and any(e.type in ("url", "text_link") for e in ents)) or
        ("invite" in locks and re.search(r"t\.me/|telegram\.me/|joinchat", text, re.I)) or
        ("email" in locks and any(e.type == "email" for e in ents)) or
        ("mention" in locks and any(e.type in ("mention", "text_mention") for e in ents))
    )


async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or update.effective_chat.type == ChatType.PRIVATE:
        return
    chat_id = update.effective_chat.id
    user = update.effective_user
    if not user:
        return

    # 1) Federation ban enforcement (highest priority)
    if await enforce_fedban(update, context):
        try:
            await msg.delete()
        except BadRequest:
            pass
        return

    user_is_admin = await is_admin(chat_id, user.id, context)

    # 2) AFK handling (works for everyone, including admins)
    await afk_watch(update, context)

    # 3) #note shortcut
    if msg.text and msg.text.startswith("#") and len(msg.text) > 1:
        name = re.split(r"\s", msg.text[1:])[0].lower()
        if await send_note(update, context, name):
            return

    # Admins & approved users bypass passive enforcement below
    if user_is_admin or _is_approved(chat_id, user.id):
        await _run_filters(update, msg, chat_id)
        return

    # 4) Locks
    locks = {r["lock_type"] for r in db.query(
        "SELECT lock_type FROM locks WHERE chat_id=?", (chat_id,))}
    if locks and _lock_triggered(locks, msg):
        try:
            await msg.delete()
        except BadRequest:
            pass
        return

    # 5) Blocklist
    text = (msg.text or msg.caption or "")
    if text:
        blocked = db.query("SELECT trigger FROM blocklist WHERE chat_id=?", (chat_id,))
        for b in blocked:
            if glob_match(b["trigger"], text):
                s = db.get_settings(chat_id)
                action = s["blocklist_action"]
                try:
                    await msg.delete()
                except BadRequest:
                    pass
                if action != "delete":
                    try:
                        verb = await do_action("ban" if action == "ban" else
                                               "kick" if action == "kick" else "mute",
                                               chat_id, user.id, context)
                        await context.bot.send_message(
                            chat_id, f"⛔ {mention(user)} {verb} (blocklisted word).",
                            parse_mode=ParseMode.HTML)
                    except BadRequest:
                        pass
                return

    # 6) Anti-flood
    s = db.get_settings(chat_id)
    limit = s["flood_limit"]
    if limit and limit > 0:
        dq = flood_tracker[(chat_id, user.id)]
        now = time.time()
        dq.append(now)
        recent = [t for t in dq if now - t <= FLOOD_WINDOW]
        if len(recent) >= limit:
            dq.clear()
            try:
                verb = await do_action(s["flood_action"], chat_id, user.id, context,
                                       seconds=3600 if s["flood_action"] == "mute" else None)
                await msg.reply_text(f"🌊 {mention(user)} {verb} for flooding.",
                                     parse_mode=ParseMode.HTML)
            except BadRequest:
                pass
            return

    # 7) Filters
    await _run_filters(update, msg, chat_id)


async def _run_filters(update, msg, chat_id):
    if not msg.text:
        return
    low = msg.text.lower()
    words = set(re.findall(r"\w+", low))
    for r in db.query("SELECT keyword,reply FROM filters WHERE chat_id=?", (chat_id,)):
        kw = r["keyword"]
        if (" " in kw and kw in low) or (kw in words):
            await msg.reply_text(r["reply"], parse_mode=ParseMode.HTML,
                                 disable_web_page_preview=True)
            return


def register_watcher(app):
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION | filters.Sticker.ALL | filters.PHOTO |
         filters.VIDEO | filters.VIDEO_NOTE | filters.ANIMATION | filters.AUDIO |
         filters.VOICE | filters.Document.ALL | filters.POLL | filters.FORWARDED)
        & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
        watch))


# ===========================================================================
# Entry point
# ===========================================================================
async def _on_error(update, context):
    logger.error("Update error: %s", context.error, exc_info=context.error)


async def _post_init(app):
    reschedule_all(app)
    try:
        await app.bot.set_my_commands([
            ("help", "Show all commands"),
            ("rules", "Show the group rules"),
            ("report", "Report a message to admins"),
            ("afk", "Set yourself away"),
            ("settings", "Show group settings"),
        ])
    except Exception as e:  # noqa: BLE001
        logger.warning("set_my_commands failed: %s", e)


def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()
    register_extras(app)
    register_moderation(app)
    register_warnings(app)
    register_greetings(app)
    register_content(app)
    register_protection(app)
    register_federation(app)
    register_watcher(app)
    app.add_error_handler(_on_error)
    total = sum(len(v) for v in app.handlers.values())
    logger.info("⚡ ZAPP369bot v2 (single file) running — %d handlers", total)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
