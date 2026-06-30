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
import asyncio
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
    ChatMemberHandler, ContextTypes, filters,
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

# Resolve where the database lives, robustly:
#  1) explicit DB_PATH if set
#  2) else the Railway volume mount path (whatever it is) + zapp369.db
#  3) else a local file
_vol = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
DB_PATH = os.environ.get("DB_PATH") or (
    os.path.join(_vol, "zapp369.db") if _vol else "zapp369.db")

# Make sure the parent folder exists so SQLite can create the file instead of
# crash-looping with "unable to open database file".
_dbdir = os.path.dirname(DB_PATH)
if _dbdir:
    try:
        os.makedirs(_dbdir, exist_ok=True)
    except OSError:
        DB_PATH = "zapp369.db"   # last-resort fallback so the bot stays up


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
                log_channel INTEGER,
                rules_entities TEXT,
                welcome_entities TEXT,
                autopost_on INTEGER DEFAULT 0,
                autopost_tz TEXT DEFAULT 'Europe/Zurich',
                autopost_thread INTEGER,
                autotrivia_on INTEGER DEFAULT 0,
                autotrivia_tz TEXT DEFAULT 'Europe/Zurich',
                autotrivia_thread INTEGER,
                autoleaderboard_on INTEGER DEFAULT 0,
                autoleaderboard_tz TEXT DEFAULT 'Europe/Zurich',
                autoleaderboard_thread INTEGER,
                games_thread INTEGER,
                autoguard_on INTEGER DEFAULT 0,
                autofaq_on INTEGER DEFAULT 0
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
            CREATE TABLE IF NOT EXISTS points (
                chat_id INTEGER, user_id INTEGER,
                points INTEGER DEFAULT 0,
                name TEXT, username TEXT,
                last_award TEXT, streak INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
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
            CREATE TABLE IF NOT EXISTS daily_use (
                chat_id INTEGER, user_id INTEGER, game TEXT, day TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id, game)
            );
            CREATE TABLE IF NOT EXISTS milestone (
                chat_id INTEGER, target INTEGER, user_id INTEGER,
                name TEXT, won_at INTEGER,
                PRIMARY KEY (chat_id, target)
            );
            CREATE TABLE IF NOT EXISTS winners (
                chat_id INTEGER, user_id INTEGER, name TEXT, won_at INTEGER,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS reached369 (
                chat_id INTEGER, user_id INTEGER, target INTEGER, at INTEGER,
                PRIMARY KEY (chat_id, user_id, target)
            );
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER, user_id INTEGER, name TEXT,
                task TEXT, reward INTEGER, status TEXT DEFAULT 'pending',
                created INTEGER, reviewed_by INTEGER, reviewed_at INTEGER
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
        # migrations for existing databases (ignore if column already present)
        for stmt in (
            "ALTER TABLE settings ADD COLUMN rules_entities TEXT",
            "ALTER TABLE settings ADD COLUMN welcome_entities TEXT",
            "ALTER TABLE settings ADD COLUMN buy_image TEXT",
            "ALTER TABLE settings ADD COLUMN points_on INTEGER DEFAULT 1",
            "ALTER TABLE settings ADD COLUMN points_daily INTEGER DEFAULT 10",
            "ALTER TABLE settings ADD COLUMN welcome_image TEXT",
            "ALTER TABLE settings ADD COLUMN welcome_image_type TEXT DEFAULT 'photo'",
            "ALTER TABLE settings ADD COLUMN about TEXT",
            "ALTER TABLE settings ADD COLUMN about_entities TEXT",
            "ALTER TABLE settings ADD COLUMN rules_image TEXT",
            "ALTER TABLE settings ADD COLUMN rules_image_type TEXT DEFAULT 'photo'",
            "ALTER TABLE settings ADD COLUMN autopost_on INTEGER DEFAULT 0",
            "ALTER TABLE settings ADD COLUMN autopost_tz TEXT DEFAULT 'Europe/Zurich'",
            "ALTER TABLE settings ADD COLUMN autopost_thread INTEGER",
            "ALTER TABLE settings ADD COLUMN autotrivia_on INTEGER DEFAULT 0",
            "ALTER TABLE settings ADD COLUMN autotrivia_tz TEXT DEFAULT 'Europe/Zurich'",
            "ALTER TABLE settings ADD COLUMN autotrivia_thread INTEGER",
            "ALTER TABLE settings ADD COLUMN autoleaderboard_on INTEGER DEFAULT 0",
            "ALTER TABLE settings ADD COLUMN autoleaderboard_tz TEXT DEFAULT 'Europe/Zurich'",
            "ALTER TABLE settings ADD COLUMN autoleaderboard_thread INTEGER",
            "ALTER TABLE daily_use ADD COLUMN count INTEGER DEFAULT 0",
            "ALTER TABLE settings ADD COLUMN games_thread INTEGER",
            "ALTER TABLE settings ADD COLUMN autoguard_on INTEGER DEFAULT 0",
            "ALTER TABLE settings ADD COLUMN autofaq_on INTEGER DEFAULT 0",
        ):
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass
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
    _conn=_conn,
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
import json
import fnmatch
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, User,
)
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
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


def games_only(func):
    """Restrict a game to the configured 'games topic' (e.g. Contests & Giveaways).
    If no topic is set, games work everywhere (backward compatible)."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.effective_message
        chat = update.effective_chat
        # DMs / connected chats: don't thread-restrict
        if not msg or chat.type == ChatType.PRIVATE:
            return await func(update, context)
        chat_id = target_chat(update)
        try:
            allowed = db.get_settings(chat_id)["games_thread"]
        except Exception:  # noqa: BLE001
            allowed = None
        if not allowed:
            return await func(update, context)  # no restriction set
        if getattr(msg, "message_thread_id", None) == allowed:
            return await func(update, context)
        # wrong topic — point them to the right one
        await msg.reply_text(
            "🎮 Games live in the <b>Contests &amp; Giveaways ✨</b> topic — "
            "head there to play! ⚡", parse_mode=ParseMode.HTML)
        return
    return wrapper


# ---------------------------------------------------------------------------
# Target user resolution
# ---------------------------------------------------------------------------
def _lookup_username(chat_id, uname):
    """Find a user_id in this chat's points table by @username (case-insensitive).
    Reliable because the bot records everyone who chats. Returns (uid, name) or None."""
    uname = uname.lstrip("@").lower()
    if not uname:
        return None
    rows = db.query(
        "SELECT user_id,name,username FROM points WHERE chat_id=? AND lower(username)=?",
        (chat_id, uname))
    if rows:
        r = rows[0]
        return r["user_id"], (r["name"] or f"@{r['username']}")
    return None


async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return (user_id, display_html) for the user a command targets, or (None, None)."""
    msg = update.effective_message
    chat_id = msg.chat.id if msg and msg.chat else None
    # 1) reply to a message — the most reliable target
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        return u.id, mention(u)
    # 2) text_mention entity (a tap-mention of someone with no username) carries the user
    for ent in (msg.entities or []):
        if ent.type == "text_mention" and ent.user:
            return ent.user.id, mention(ent.user)
    # 3) @username mention entity -> resolve from the bot's DB (who has chatted)
    text = msg.text or msg.caption or ""
    for ent in (msg.entities or []):
        if ent.type == "mention":
            uname = text[ent.offset:ent.offset + ent.length]
            found = _lookup_username(chat_id, uname) if chat_id else None
            if found:
                return found[0], mention_id(found[0], found[1])
            # fall back to Telegram's resolver (works only for some accounts)
            try:
                c = await context.bot.get_chat(uname)
                return c.id, mention(c)
            except (BadRequest, Forbidden, AttributeError):
                return None, None
    # 4) explicit numeric id or @name passed as the first arg
    if context.args:
        arg = context.args[0]
        if arg.lstrip("-").isdigit():
            uid = int(arg)
            return uid, mention_id(uid)
        if arg.startswith("@"):
            found = _lookup_username(chat_id, arg) if chat_id else None
            if found:
                return found[0], mention_id(found[0], found[1])
            try:
                c = await context.bot.get_chat(arg)
                return c.id, mention(c)
            except (BadRequest, Forbidden, AttributeError):
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


# ---------------------------------------------------------------------------
# Rich content: capture & re-send messages with premium custom emoji intact,
# using Telegram message ENTITIES (not HTML) so nothing can leak as raw code.
# Falls back to plain text if Telegram won't let the bot send the custom emoji.
# ---------------------------------------------------------------------------
def split_body(text, ntokens=1):
    """Drop `ntokens` leading whitespace-separated tokens (command, or command+name).
    Returns (body, prefix_len). The prefix is always ASCII so prefix_len doubles as a
    UTF-16 offset, keeping entity math simple."""
    text = text or ""
    m = re.match(r"(?:\S+\s+){%d}" % ntokens, text)
    if not m:
        return "", len(text)
    return text[m.end():], m.end()


def rebase_entities(entities, prefix):
    """Shift entity offsets to be relative to the body; drop the command entity."""
    out = []
    for e in (entities or []):
        if e.type == MessageEntity.BOT_COMMAND or e.offset < prefix:
            continue
        d = {"type": e.type, "offset": e.offset - prefix, "length": e.length}
        for attr in ("custom_emoji_id", "url", "language"):
            v = getattr(e, attr, None)
            if v:
                d[attr] = v
        out.append(d)
    return out


def capture_rich(message, ntokens=1):
    """Return (body_text, [entity_dicts]) for a /set* command, preserving emoji."""
    text = message.text if message.text is not None else (message.caption or "")
    ents = message.entities or message.caption_entities or []
    body, prefix = split_body(text, ntokens)
    return body.rstrip(), rebase_entities(ents, prefix)


def build_entities(items):
    """Rebuild MessageEntity objects from stored dicts (incl. dynamic text_mention)."""
    out = []
    for d in (items or []):
        kw = {"type": d["type"], "offset": d["offset"], "length": d["length"]}
        if d["type"] == "text_mention" and d.get("user_id"):
            try:
                kw["user"] = User(id=d["user_id"], first_name=d.get("user_name") or "user",
                                  is_bot=False)
            except Exception:  # noqa: BLE001
                continue
        for attr in ("custom_emoji_id", "url", "language"):
            if d.get(attr):
                kw[attr] = d[attr]
        try:
            out.append(MessageEntity(**kw))
        except Exception:  # noqa: BLE001
            pass
    return out


def utf16_len(s):
    return len(s.encode("utf-16-le")) // 2 if s else 0


def substitute_entities(text, ent_dicts, repls):
    """
    Replace {placeholders} in `text` while keeping entity offsets correct.
    repls: list of (placeholder, replacement_text, user_tuple_or_None).
    If user_tuple is given, the replacement becomes a clickable text_mention.
    Returns (new_text, new_entity_dicts).
    """
    ents = [dict(e) for e in (ent_dicts or [])]
    for ph, repl, user in repls:
        repl = repl or ""
        idx = text.find(ph)
        while idx != -1:
            off16 = utf16_len(text[:idx])
            ph16 = utf16_len(ph)
            repl16 = utf16_len(repl)
            delta = repl16 - ph16
            new_ents = []
            for e in ents:
                eo, el = e["offset"], e["length"]
                if eo + el <= off16:
                    new_ents.append(e)                       # before placeholder
                elif eo >= off16 + ph16:
                    e2 = dict(e); e2["offset"] = eo + delta   # after placeholder
                    new_ents.append(e2)
                # else: overlaps placeholder -> drop
            if user and repl:
                new_ents.append({"type": "text_mention", "offset": off16, "length": repl16,
                                 "user_id": user[0], "user_name": user[1]})
            ents = new_ents
            text = text[:idx] + repl + text[idx + len(ph):]
            idx = text.find(ph, idx + len(repl))
    return text, ents


async def reply_rich(message, text, entities, **kwargs):
    """Send text with custom-emoji entities; fall back to plain text if rejected."""
    kwargs.setdefault("disable_web_page_preview", True)
    try:
        return await message.reply_text(text, entities=entities or None, **kwargs)
    except BadRequest:
        try:
            return await message.reply_text(text, **kwargs)
        except BadRequest:
            return None


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
# Resilient sending: always show the message, falling back if Telegram rejects
# the premium custom emoji or the HTML.
# ---------------------------------------------------------------------------
_CE_TAG = re.compile(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", re.S)


def strip_custom_emoji(text: str) -> str:
    """Replace <tg-emoji ...>x</tg-emoji> with its plain fallback emoji x."""
    return _CE_TAG.sub(r"\1", text or "")


async def safe_reply(message, text, **kwargs):
    """
    Reply with HTML. If Telegram rejects it (e.g. the bot can't send the premium
    custom emoji), retry with plain emoji; if still rejected, send as plain text.
    Guarantees the user sees something instead of silence.
    """
    kwargs.setdefault("parse_mode", ParseMode.HTML)
    kwargs.setdefault("disable_web_page_preview", True)
    try:
        return await message.reply_text(text, **kwargs)
    except BadRequest:
        try:
            return await message.reply_text(strip_custom_emoji(text), **kwargs)
        except BadRequest:
            plain = dict(kwargs)
            plain.pop("parse_mode", None)
            try:
                return await message.reply_text(strip_custom_emoji(text), **plain)
            except BadRequest:
                return None


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
import re
import html
import json
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
            content = reply.caption or ""
        elif reply.animation:
            kind, file_id, content = "animation", reply.animation.file_id, reply.caption or ""
        elif reply.video:
            kind, file_id, content = "video", reply.video.file_id, reply.caption or ""
        elif reply.sticker:
            kind, file_id = "sticker", reply.sticker.file_id
        elif reply.document:
            kind, file_id, content = "document", reply.document.file_id, reply.caption or ""
        else:
            content = reply.text or reply.caption or ""
    else:
        content = msg.text.partition(context.args[0])[2].strip()
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
            await safe_reply(msg, text, reply_markup=markup)
    except BadRequest:
        await safe_reply(msg, text or "(note)")
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
    reply = update.effective_message.text.partition(context.args[0])[2].strip()
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
async def _send_rich_media(message, kind, fid, text, entities):
    """Send rules/notes media with a formatted caption; retry plain so the image
    is never dropped if Telegram rejects the caption."""
    sender = {"animation": message.reply_animation,
              "video": message.reply_video}.get(kind, message.reply_photo)
    kw = dict(caption=text)
    if entities is not None:
        kw["caption_entities"] = entities or None
    else:
        kw["parse_mode"] = ParseMode.HTML
    try:
        return await sender(fid, **kw)
    except BadRequest:
        pass
    plain = html.unescape(re.sub(r"<[^>]+>", "", text))[:1024]
    try:
        return await sender(fid, caption=plain)
    except BadRequest:
        return None


@group_only
@admin_only
async def setrules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    body, ents = capture_rich(update.effective_message, 1)
    if not body:
        await update.effective_message.reply_text("Usage: /setrules <your rules text>")
        return
    chat_id = target_chat(update)
    db.set_setting(chat_id, "rules", body)
    db.set_setting(chat_id, "rules_entities", json.dumps(ents) if ents else None)
    await update.effective_message.reply_text("✅ Rules saved.")


@group_only
@admin_only
async def setrulesimage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    reply = msg.reply_to_message
    fid = kind = None
    if reply:
        if reply.animation:
            fid, kind = reply.animation.file_id, "animation"
        elif reply.video:
            fid, kind = reply.video.file_id, "video"
        elif reply.photo:
            fid, kind = reply.photo[-1].file_id, "photo"
    if not fid:
        await msg.reply_text(
            "Reply to a photo, GIF, or video with /setrulesimage to show it on /rules.")
        return
    db.set_setting(target_chat(update), "rules_image", fid)
    db.set_setting(target_chat(update), "rules_image_type", kind)
    label = {"animation": "GIF", "video": "video"}.get(kind, "image")
    await msg.reply_text(f"✅ Rules {label} set. Type /rules to preview.")


@group_only
@admin_only
async def delrulesimage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.set_setting(target_chat(update), "rules_image", None)
    await update.effective_message.reply_text(
        "🗑 Rules image removed. /rules will be text-only again.")


@group_only
async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = db.get_settings(target_chat(update))
    if not s["rules"]:
        await update.effective_message.reply_text("No rules set. Admins: /setrules <text>")
        return
    msg = update.effective_message
    img = s["rules_image"] if "rules_image" in s.keys() else None
    kind = (s["rules_image_type"] if "rules_image_type" in s.keys() else None) or "photo"
    raw_ents = s["rules_entities"] if "rules_entities" in s.keys() else None
    if raw_ents:
        ents = build_entities(json.loads(raw_ents))
        if img and await _send_rich_media(msg, kind, img, s["rules"], ents):
            return
        await reply_rich(msg, s["rules"], ents)
    else:
        text, markup = build_message(s["rules"], chat=update.effective_chat)
        full = f"📜 <b>Rules</b>\n\n{text}"
        if img and await _send_rich_media(msg, kind, img, full, None):
            return
        await safe_reply(msg, full, reply_markup=markup)


# --------------------------- ABOUT / LORE ----------------------------------
DEFAULT_ABOUT = (
    "⚡ <b>ZAPP — Tesla's Revolution</b> ⚡\n\n"
    "Money is energy. Energy moves at the speed of light — so why does money still "
    "crawl through banks, borders and fees?\n\n"
    "A century ago Nikola Tesla built a tower to give the world free energy. "
    "They pulled the funding and the signal went quiet. <b>⚡ZAPP is that signal, "
    "switched back on</b> — built on Solana, owned by no one, open to everyone.\n\n"
    "<b>Free Energy = Free Money ∞</b>\n"
    "• 0% tax · LP burned · community-owned\n"
    "• Built on the 3 · 6 · 9 frequency\n\n"
    "🌐 zapp369.energy\n"
    "𝕏 x.com/ZAPPonSOL\n"
    "💬 t.me/ZAPP369\n\n"
    "Type /buy to join the current. ⚡ 3 · 6 · 9 ∞"
)


@group_only
@admin_only
async def setabout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    body, ents = capture_rich(update.effective_message, 1)
    if not body:
        await update.effective_message.reply_text("Usage: /setabout <your About text>")
        return
    chat_id = target_chat(update)
    db.set_setting(chat_id, "about", body)
    db.set_setting(chat_id, "about_entities", json.dumps(ents) if ents else None)
    await update.effective_message.reply_text("✅ About saved.")


@group_only
async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = db.get_settings(target_chat(update))
    about_text = s["about"] if "about" in s.keys() else None
    if not about_text:
        await safe_reply(update.effective_message, DEFAULT_ABOUT)
        return
    raw_ents = s["about_entities"] if "about_entities" in s.keys() else None
    if raw_ents:
        await reply_rich(update.effective_message, about_text,
                         build_entities(json.loads(raw_ents)))
    else:
        await safe_reply(update.effective_message, about_text)


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
    app.add_handler(CommandHandler(["setrulesimage", "setrulespic"], setrulesimage))
    app.add_handler(CommandHandler(["delrulesimage", "delrulespic"], delrulesimage))
    app.add_handler(CommandHandler("rules", rules))
    app.add_handler(CommandHandler("setabout", setabout))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler(["addblocklist", "blocklist"], add_blocklist))
    app.add_handler(CommandHandler("blocklists", list_blocklist))
    app.add_handler(CommandHandler(["unblocklist", "rmblocklist"], rm_blocklist))
    app.add_handler(CommandHandler("blocklistaction", set_blocklist_action))


# ===========================================================================
# ---- buy ------------------------------------------------------------
# ===========================================================================

"""
⚡ /buy — MajorBuyBot-style buy card for ZAPP.

Sends an optional banner image, a tap-to-copy contract address, and a row of
clean link buttons (Jupiter / Chart / Website / How-to-buy / socials).
No voting button. Banner is optional and set by an admin via /setbuyimage.
"""
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler,
)


# --------------------------- ZAPP constants --------------------------------
CA = "Ab16ce5SDbibTbXevxHLpqUnUvu9tNkkpaJcSDvCpump"
WEBSITE = "https://zapp369.energy"
HOWTOBUY = "https://zapp369.energy/how-to-buy"
WHITEPAPER = "https://zapp369.energy/ZAPP_Whitepaper_369.pdf"
TWITTER = "https://x.com/ZAPPonSOL"
TELEGRAM = "https://t.me/ZAPP369"
INSTAGRAM = "https://www.instagram.com/zapp369.energy/"
CHART = f"https://dexscreener.com/solana/{CA}"
JUPITER = f"https://jup.ag/tokens/{CA}"
PUMPFUN = f"https://pump.fun/coin/{CA}"
# Phantom buy link (with project referral — earns fees)
PHANTOM = f"https://phantom.com/tokens/solana/{CA}?referralId=k31pepyasnt"
# Trusted Solana trading bots (official links). They handle wallets/keys, not us.
BONKBOT = "https://t.me/solana_bonkbot"
TROJAN = "https://t.me/solana_trojanbot"
DEXTBOT = "https://t.me/DextBuyBot"   # DexTools official Buy Bot (buy alerts + trending boost)
# TODO: add TikTok and Discord URLs when provided
TIKTOK = ""
DISCORD = ""


def _buy_keyboard():
    rows = [
        [InlineKeyboardButton("🪐 Buy on Jupiter", url=JUPITER),
         InlineKeyboardButton("💊 Buy on pump.fun", url=PUMPFUN)],
        [InlineKeyboardButton("👻 Buy on Phantom", url=PHANTOM)],
        [InlineKeyboardButton("📊 Chart", url=CHART),
         InlineKeyboardButton("📖 How to Buy", url=HOWTOBUY)],
        [InlineKeyboardButton("🌐 Website", url=WEBSITE),
         InlineKeyboardButton("📄 Whitepaper", url=WHITEPAPER)],
        [InlineKeyboardButton("𝕏 Twitter", url=TWITTER),
         InlineKeyboardButton("💬 Telegram", url=TELEGRAM)],
    ]
    # socials — only show buttons whose URL is set (Telegram rejects empty URLs)
    socials = [
        ("📸 Instagram", INSTAGRAM),
        ("🎵 TikTok", TIKTOK),
        ("👾 Discord", DISCORD),
    ]
    live = [InlineKeyboardButton(label, url=url) for label, url in socials if url]
    for i in range(0, len(live), 2):
        rows.append(live[i:i + 2])
    return InlineKeyboardMarkup(rows)


def _buy_caption():
    # <code> renders monospace, which is tap-to-copy on mobile Telegram.
    return (
        "⚡ <b>BUY ⚡ZAPP</b>\n"
        "∞ Free Energy = Free Money ∞\n\n"
        "<b>Official CA</b> (tap to copy):\n"
        f"<code>{esc(CA)}</code>\n\n"
        "Tap a button below to buy, chart, or learn more.\n"
        "∞ 3 · 6 · 9 ∞\n\n"
        "⚡ZAPP"
    )


def _ca_text():
    return (
        "🔴 <b>Official ⚡ZAPP CA</b> (tap to copy):\n"
        f"<code>{esc(CA)}</code>\n\n"
        "⚠️ Only ever use this CA. Admins NEVER DM first.\n"
        "∞ 3 · 6 · 9 ∞"
    )


async def ca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send just the contract address, tap-to-copy (slash command)."""
    await update.effective_message.reply_text(
        _ca_text(), parse_mode=ParseMode.HTML, reply_markup=_buy_keyboard(),
        disable_web_page_preview=True)


def _trade_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🦅 Trojan", url=TROJAN),
         InlineKeyboardButton("📊 DexTools Bot", url=DEXTBOT)],
        [InlineKeyboardButton("👻 Phantom", url=PHANTOM),
         InlineKeyboardButton("🪐 Jupiter", url=JUPITER)],
        [InlineKeyboardButton("💊 pump.fun", url=PUMPFUN)],
    ])


async def trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buy ⚡ZAPP directly inside Telegram via trusted trading bots."""
    await update.effective_message.reply_text(
        "🤖 <b>Buy ⚡ZAPP inside Telegram</b>\n\n"
        "1️⃣ Copy the CA below\n"
        "2️⃣ Open Trojan (or tap Phantom)\n"
        "3️⃣ Paste the CA, pick an amount, buy ⚡\n\n"
        "<b>CA</b> (tap to copy):\n"
        f"<code>{esc(CA)}</code>\n\n"
        "⚠️ Use ONLY the official buttons below. These bots make a wallet you fund "
        "with SOL — never share your seed phrase, and verify the CA matches before "
        "you buy. Admins NEVER DM first.\n\n∞ 3 · 6 · 9 ∞\n⚡ZAPP",
        parse_mode=ParseMode.HTML, reply_markup=_trade_keyboard(),
        disable_web_page_preview=True)


# --------------------------- LIVE PRICE ------------------------------------
DEX_API = f"https://api.dexscreener.com/latest/dex/tokens/{CA}"


def _short_usd(n):
    """Format a number as $1.23M / $456.0K / $1.23."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    a = abs(n)
    if a >= 1e9:
        return f"${n / 1e9:.2f}B"
    if a >= 1e6:
        return f"${n / 1e6:.2f}M"
    if a >= 1e3:
        return f"${n / 1e3:.2f}K"
    return f"${n:,.2f}"


def _price_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🪐 Jupiter", url=JUPITER),
         InlineKeyboardButton("💊 pump.fun", url=PUMPFUN)],
        [InlineKeyboardButton("📊 Chart", url=CHART),
         InlineKeyboardButton("🔄 Refresh", callback_data="zapp_price")],
    ])


def _format_price_pairs(pairs):
    """Build the price message from DexScreener pairs. Returns text or None."""
    if not pairs:
        return None
    # use the pair with the deepest liquidity (the real market)
    pair = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
    price = pair.get("priceUsd") or "?"
    mc = pair.get("marketCap") or pair.get("fdv")
    ch = (pair.get("priceChange") or {}).get("h24")
    vol = (pair.get("volume") or {}).get("h24")
    liq = (pair.get("liquidity") or {}).get("usd")
    try:
        chf = float(ch)
    except (TypeError, ValueError):
        chf = None
    if chf is None:
        chtxt = "—"
    else:
        arrow = "🟢" if chf >= 0 else "🔴"
        chtxt = f"{arrow} {'+' if chf >= 0 else ''}{chf:.2f}%"
    return (
        "⚡ <b>⚡ZAPP — Live</b>\n\n"
        f"💵 <b>Price:</b> ${price}\n"
        f"📊 <b>Market Cap:</b> {_short_usd(mc)}\n"
        f"📈 <b>24h:</b> {chtxt}\n"
        f"💧 <b>Liquidity:</b> {_short_usd(liq)}\n"
        f"🔄 <b>24h Volume:</b> {_short_usd(vol)}\n\n"
        "∞ 3 · 6 · 9 ∞\n⚡ZAPP"
    )


async def _fetch_price_text():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(DEX_API, headers={"User-Agent": "ZAPPbot/1.0"})
            r.raise_for_status()
            pairs = (r.json() or {}).get("pairs") or []
    except Exception:  # noqa: BLE001 — network/parse errors -> friendly message
        return None
    return _format_price_pairs(pairs)


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Live ⚡ZAPP price from DexScreener."""
    msg = update.effective_message
    text = await _fetch_price_text()
    if not text:
        await msg.reply_text(
            "⚡ Price feed is unavailable right now — try again in a moment.")
        return
    await msg.reply_text(text, parse_mode=ParseMode.HTML,
                         reply_markup=_price_keyboard(),
                         disable_web_page_preview=True)


async def price_refresh_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer("Refreshing… ⚡")
    except Exception:  # noqa: BLE001
        pass
    text = await _fetch_price_text()
    if not text:
        return
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=_price_keyboard(),
                                  disable_web_page_preview=True)
    except BadRequest:
        pass  # "message is not modified" if price hasn't changed — ignore


_PRICE_TRIGGERS = {"price", "mc", "marketcap", "market cap", "stats", "price?"}
_WP_TRIGGERS = {"whitepaper", "wp", "white paper", "litepaper", "paper"}


def _wp_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📄 Read the Whitepaper", url=WHITEPAPER)]])


async def whitepaper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """One clean button to the ZAPP whitepaper."""
    await update.effective_message.reply_text(
        "📄 <b>ZAPP Whitepaper</b>\n∞ 3 · 6 · 9 ∞",
        parse_mode=ParseMode.HTML, reply_markup=_wp_keyboard(),
        disable_web_page_preview=True)


async def keyword_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply when someone types 'price'/'mc' or 'whitepaper'/'wp' on its own."""
    msg = update.effective_message
    if not msg or not msg.text:
        return
    cleaned = msg.text.strip().lower().strip("?.!¿¡ ").strip()
    if cleaned in _PRICE_TRIGGERS:
        text = await _fetch_price_text()
        if text:
            await msg.reply_text(text, parse_mode=ParseMode.HTML,
                                 reply_markup=_price_keyboard(),
                                 disable_web_page_preview=True)
    elif cleaned in _WP_TRIGGERS:
        await msg.reply_text(
            "📄 <b>ZAPP Whitepaper</b>\n∞ 3 · 6 · 9 ∞",
            parse_mode=ParseMode.HTML, reply_markup=_wp_keyboard(),
            disable_web_page_preview=True)


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    caption = _buy_caption()
    kb = _buy_keyboard()
    chat_id = target_chat(update)
    s = db.get_settings(chat_id)
    img = s["buy_image"] if "buy_image" in s.keys() else None

    if img:
        try:
            await msg.reply_photo(img, caption=caption, parse_mode=ParseMode.HTML,
                                  reply_markup=kb)
            return
        except BadRequest:
            # stale/invalid file_id (e.g. after a redeploy) — fall back to text
            pass
    await msg.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb,
                         disable_web_page_preview=True)


@group_only
@admin_only
async def setbuyimage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    reply = msg.reply_to_message
    if not reply or not reply.photo:
        await msg.reply_text(
            "Reply to a photo with /setbuyimage to set the /buy banner.")
        return
    file_id = reply.photo[-1].file_id
    db.set_setting(target_chat(update), "buy_image", file_id)
    await msg.reply_text("✅ Buy banner set. Try /buy to preview.")


@group_only
@admin_only
async def delbuyimage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.set_setting(target_chat(update), "buy_image", None)
    await update.effective_message.reply_text(
        "🗑 Buy banner removed. /buy will now send text + buttons only.")


def register_buy(app):
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler(["ca", "contract"], ca))
    app.add_handler(CommandHandler(["price", "mc", "marketcap"], price))
    app.add_handler(CommandHandler(["trade", "buybot", "buyhere"], trade))
    app.add_handler(CommandHandler(["whitepaper", "wp", "paper"], whitepaper))
    app.add_handler(CommandHandler("setbuyimage", setbuyimage))
    app.add_handler(CommandHandler("delbuyimage", delbuyimage))
    app.add_handler(CallbackQueryHandler(price_refresh_cb, pattern="^zapp_price$"))
    # typed "price"/"mc"/"whitepaper" (no slash). Own group so it runs alongside
    # the moderation watcher (group 0) and points tracker (group 2).
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, keyword_router), group=3)


# ===========================================================================
# ---- points ------------------------------------------------------------
# ===========================================================================

"""
⚡ ZAPP Points & Leaderboard — gamified engagement.

- Members earn points automatically for showing up each day (daily bonus + streak).
- Admins can award/remove points by hand: /addpoints @user 300
- /points shows your score, rank, streak and ZAPP rank-title.
- /top shows the leaderboard.

Points are an internal score only — not crypto, not on-chain. What (if anything)
they unlock is up to the team.
"""
from datetime import datetime, timezone, timedelta
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatType
from telegram.error import BadRequest
from telegram.ext import (
    CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler,
)


# ZAPP rank tiers (threshold -> title)
TIERS = [
    (3690, "⚡ Tesla Tier"),
    (1000, "⚡ Super Conductor"),
    (369,  "⚡ Conductor"),
    (100,  "⚡ Charged"),
    (36,   "⚡ Spark"),
    (0,    "🔌 Plugged In"),
]


def _title(pts):
    for thr, name in TIERS:
        if pts >= thr:
            return name
    return TIERS[-1][1]


# --------------------------- Race to 369 -----------------------------------
MILESTONE_TARGET = 369


async def check_milestone(context, chat_id, uid, total, name=None):
    """Called after any point award. If this user is the FIRST to reach the
    target, lock them in (race-safe) and announce. Fires at most once per chat."""
    if total is None or total < MILESTONE_TARGET:
        return
    current_winner = db.query("SELECT user_id FROM milestone WHERE chat_id=? AND target=?",
                              (chat_id, MILESTONE_TARGET), one=True)
    past_winner = db.query("SELECT 1 FROM winners WHERE chat_id=? AND user_id=?",
                           (chat_id, uid), one=True)

    # FIRST to reach this round (no round winner yet, not a past champion) -> CROWN
    if not current_winner and not past_winner:
        db.execute(
            "INSERT OR IGNORE INTO milestone (chat_id,target,user_id,name,won_at) "
            "VALUES (?,?,?,?,?)",
            (chat_id, MILESTONE_TARGET, uid, name, int(time.time())))
        row = db.query("SELECT user_id FROM milestone WHERE chat_id=? AND target=?",
                       (chat_id, MILESTONE_TARGET), one=True)
        if row and row["user_id"] == uid:
            db.execute("INSERT OR IGNORE INTO winners (chat_id,user_id,name,won_at) "
                       "VALUES (?,?,?,?)", (chat_id, uid, name, int(time.time())))
            db.execute("INSERT OR IGNORE INTO reached369 (chat_id,user_id,target,at) "
                       "VALUES (?,?,?,?)", (chat_id, uid, MILESTONE_TARGET, int(time.time())))
            try:
                await context.bot.send_message(
                    chat_id,
                    "🏆⚡ <b>WE HAVE A WINNER!</b> ⚡🏆\n\n"
                    f"{mention_id(uid, name)} is the <b>FIRST to reach "
                    f"{MILESTONE_TARGET} points!</b> 🎉\n"
                    "The 3 · 6 · 9 frequency chose you. ⚡\n\n"
                    "🎁 An admin will be in touch with your reward.\n\n"
                    "Think you're next? /spin daily, play /tasks, keep the chat "
                    "charged. /top to see where you stand. 🔌\n\n"
                    "∞ Free Energy = Free Money ∞\n⚡ZAPP",
                    parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            except Exception:  # noqa: BLE001
                pass
            return

    # everyone else who reaches 369 -> "369 Club" shoutout, once per person
    seen = db.query("SELECT 1 FROM reached369 WHERE chat_id=? AND user_id=? AND target=?",
                    (chat_id, uid, MILESTONE_TARGET), one=True)
    if seen:
        return
    db.execute("INSERT OR IGNORE INTO reached369 (chat_id,user_id,target,at) "
               "VALUES (?,?,?,?)", (chat_id, uid, MILESTONE_TARGET, int(time.time())))
    try:
        await context.bot.send_message(
            chat_id,
            f"🎉⚡ {mention_id(uid, name)} just reached <b>{MILESTONE_TARGET} points</b> — "
            "welcome to the <b>369 Club!</b> 🔌\n"
            "The frequency is strong with this one. /top\n∞ 3 · 6 · 9 ∞",
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception:  # noqa: BLE001
        pass


@group_only
async def milestone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the Race to 369 status."""
    chat_id = target_chat(update)
    won = db.query("SELECT user_id,name,won_at FROM milestone WHERE chat_id=? AND target=?",
                   (chat_id, MILESTONE_TARGET), one=True)
    if won:
        who = esc(won["name"]) if won["name"] else mention_id(won["user_id"])
        await update.effective_message.reply_text(
            f"🏆 <b>Race to {MILESTONE_TARGET}</b> — already won by {who}! ⚡\n"
            "Admins can start a new round with /resetmilestone.",
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return
    leader = db.query(
        "SELECT name,points FROM points WHERE chat_id=? ORDER BY points DESC LIMIT 1",
        (chat_id,), one=True)
    lead_txt = ""
    if leader and leader["points"]:
        left = max(MILESTONE_TARGET - leader["points"], 0)
        lead_txt = (f"\n👑 Leader: <b>{esc(leader['name'] or 'someone')}</b> "
                    f"with {leader['points']:,} pts ({left:,} to go)")
    await update.effective_message.reply_text(
        f"🏁 <b>Race to {MILESTONE_TARGET} points!</b>\n"
        "First member to reach <b>369</b> wins a reward. ⚡\n"
        "Earn points: chat daily, /spin, /trivia." + lead_txt,
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@group_only
@admin_only
async def resetmilestone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear the winner so the Race to 369 can run again."""
    chat_id = target_chat(update)
    db.execute("DELETE FROM milestone WHERE chat_id=? AND target=?",
               (chat_id, MILESTONE_TARGET))
    hof = db.query("SELECT COUNT(*) c FROM winners WHERE chat_id=?", (chat_id,), one=True)
    n = hof["c"] if hof else 0
    await update.effective_message.reply_text(
        f"🔄 <b>Race to {MILESTONE_TARGET} reset!</b> A new round is live — "
        "first NEW member to 369 wins. ⚡\n"
        f"🏆 Past winners ({n}) can't win again — see /winners.\n"
        "(To wipe scores for a full fresh season, use /resetpoints first.)",
        parse_mode=ParseMode.HTML)


@group_only
async def winners_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the Race to 369 Hall of Fame."""
    chat_id = target_chat(update)
    rows = db.query("SELECT user_id,name,won_at FROM winners WHERE chat_id=? "
                    "ORDER BY won_at ASC LIMIT 50", (chat_id,))
    if not rows:
        await update.effective_message.reply_text(
            "🏆 <b>Hall of Fame</b>\nNo champions yet — be the first to reach "
            f"{MILESTONE_TARGET} points! /milestone", parse_mode=ParseMode.HTML)
        return
    lines = ["🏆 <b>⚡ZAPP Hall of Fame</b> — Race to 369 champions:\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(rows):
        tag = medals[i] if i < 3 else f"{i + 1}."
        who = esc(r["name"]) if r["name"] else mention_id(r["user_id"])
        lines.append(f"{tag} {who}")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@group_only
@admin_only
async def clearhof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wipe the Hall of Fame (lets everyone be eligible again)."""
    chat_id = target_chat(update)
    db.execute("DELETE FROM winners WHERE chat_id=?", (chat_id,))
    db.execute("DELETE FROM milestone WHERE chat_id=? AND target=?",
               (chat_id, MILESTONE_TARGET))
    db.execute("DELETE FROM reached369 WHERE chat_id=?", (chat_id,))
    await update.effective_message.reply_text(
        "🧹 Hall of Fame cleared — everyone is eligible for the Race to 369 again. ⚡",
        parse_mode=ParseMode.HTML)


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday():
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _row(chat_id, uid):
    return db.query("SELECT * FROM points WHERE chat_id=? AND user_id=?",
                    (chat_id, uid), one=True)


def _rank(chat_id, uid):
    row = _row(chat_id, uid)
    if not row:
        return None, 0
    higher = db.query("SELECT COUNT(*) c FROM points WHERE chat_id=? AND points>?",
                      (chat_id, row["points"]), one=True)["c"]
    return higher + 1, row["points"]


# --------------------------- auto daily award ------------------------------
async def activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs on every group message (separate handler group). Daily bonus + streak."""
    if not update.effective_message or update.effective_chat.type == ChatType.PRIVATE:
        return
    user = update.effective_user
    if not user or user.is_bot:
        return
    chat_id = update.effective_chat.id
    s = db.get_settings(chat_id)
    name = user.first_name or "user"
    uname = user.username or ""
    row = _row(chat_id, user.id)
    today = _today()
    points_on = bool(s["points_on"])
    if row is None:
        # always record the member (for /all); seed daily points only if enabled
        db.execute(
            "INSERT INTO points (chat_id,user_id,points,name,username,last_award,streak) "
            "VALUES (?,?,?,?,?,?,?)",
            (chat_id, user.id, s["points_daily"] if points_on else 0, name, uname,
             today if points_on else None, 1 if points_on else 0))
        return
    if not points_on or row["last_award"] == today:
        # already earned today (or points off) — just refresh the cached name
        db.execute("UPDATE points SET name=?,username=? WHERE chat_id=? AND user_id=?",
                   (name, uname, chat_id, user.id))
        return
    streak = (row["streak"] or 0) + 1 if row["last_award"] == _yesterday() else 1
    db.execute(
        "UPDATE points SET points=points+?,name=?,username=?,last_award=?,streak=? "
        "WHERE chat_id=? AND user_id=?",
        (s["points_daily"], name, uname, today, streak, chat_id, user.id))
    new = db.query("SELECT points FROM points WHERE chat_id=? AND user_id=?",
                   (chat_id, user.id), one=True)
    await check_milestone(context, chat_id, user.id, new["points"] if new else None, name)


# --------------------------- /points ---------------------------------------
@group_only
async def points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    uid, disp = await resolve_target(update, context)
    if uid is None:
        uid = update.effective_user.id
        disp = mention_id(uid, update.effective_user.first_name)
    rank, pts = _rank(chat_id, uid)
    row = _row(chat_id, uid)
    if row is None:
        await update.effective_message.reply_text(
            f"{disp} has no points yet — send a message to start earning. ⚡",
            parse_mode=ParseMode.HTML)
        return
    streak = row["streak"] or 0
    streak_line = f"\n🔥 <b>{streak}-day</b> streak" if streak > 1 else ""
    await update.effective_message.reply_text(
        f"{disp}\n"
        f"⚡ <b>{pts:,}</b> points  ·  rank <b>#{rank}</b>\n"
        f"{_title(pts)}{streak_line}",
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# --------------------------- /top ------------------------------------------
@group_only
async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    rows = db.query(
        "SELECT user_id,points,name FROM points WHERE chat_id=? AND points>0 "
        "ORDER BY points DESC LIMIT 10", (chat_id,))
    if not rows:
        await update.effective_message.reply_text(
            "No points yet — start chatting to climb the board. ⚡")
        return
    medals = ["🥇", "🥈", "🥉"] + ["⚡"] * 7
    lines = []
    for i, r in enumerate(rows):
        who = mention_id(r["user_id"], r["name"])
        lines.append(f"{medals[i]} {who} — <b>{r['points']:,}</b>")
    await update.effective_message.reply_text(
        "🏆 <b>ZAPP Leaderboard</b>\n\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# --------------------------- admin: add / set / reset ----------------------
def _amount(context, skip_first):
    args = list(context.args)
    if skip_first and args:
        args = args[1:]
    for a in args:
        if a.lstrip("-").isdigit():
            return int(a)
    return None


@group_only
@admin_only
async def addpoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = target_chat(update)
    uid, disp = await resolve_target(update, context)
    if uid is None:
        await msg.reply_text("Reply to someone (or @mention) with an amount:\n"
                             "/addpoints @user 300   (use a minus to remove)")
        return
    amt = _amount(context, skip_first=not msg.reply_to_message)
    if amt is None:
        await msg.reply_text("How many? e.g. /addpoints @user 300")
        return
    row = _row(chat_id, uid)
    if row is None:
        db.execute("INSERT INTO points (chat_id,user_id,points,name) VALUES (?,?,?,?)",
                   (chat_id, uid, max(amt, 0), None))
        new = max(amt, 0)
    else:
        new = max((row["points"] or 0) + amt, 0)
        db.execute("UPDATE points SET points=? WHERE chat_id=? AND user_id=?",
                   (new, chat_id, uid))
    verb = "added to" if amt >= 0 else "removed from"
    await msg.reply_text(
        f"✅ <b>{abs(amt):,}</b> points {verb} {disp}.\nNew total: <b>{new:,}</b> ⚡",
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    await check_milestone(context, chat_id, uid, new, disp)


def _apply_points(chat_id, uid, amt, name=None):
    """Add amt points to uid (floor 0). Returns new total."""
    row = _row(chat_id, uid)
    if row is None:
        new = max(amt, 0)
        db.execute("INSERT INTO points (chat_id,user_id,points,name) VALUES (?,?,?,?)",
                   (chat_id, uid, new, name))
    else:
        new = max((row["points"] or 0) + amt, 0)
        db.execute("UPDATE points SET points=? WHERE chat_id=? AND user_id=?",
                   (new, chat_id, uid))
    return new


@group_only
@admin_only
async def give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Easiest way to reward: reply to someone with /give → tap a quick amount.
    Or /give 50 (reply) to award directly, or /give @user 50."""
    msg = update.effective_message
    chat_id = target_chat(update)
    uid, disp = await resolve_target(update, context)
    if uid is None:
        await msg.reply_text(
            "💡 <b>Easiest way:</b> reply to the person's message with <b>/give</b> "
            "and tap an amount. Or: /give @user 50",
            parse_mode=ParseMode.HTML)
        return
    amt = _amount(context, skip_first=not msg.reply_to_message)
    if amt is not None:
        new = _apply_points(chat_id, uid, amt)
        verb = "added to" if amt >= 0 else "removed from"
        await msg.reply_text(
            f"✅ <b>{abs(amt):,}</b> points {verb} {disp}.\nNew total: <b>{new:,}</b> ⚡",
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        await check_milestone(context, chat_id, uid, new, disp)
        return
    # no amount given -> show quick-reward buttons
    amounts = [10, 50, 100, 369]
    row1 = [InlineKeyboardButton(f"⚡ +{a}", callback_data=f"gp:{uid}:{a}") for a in amounts]
    kb = InlineKeyboardMarkup([row1,
                               [InlineKeyboardButton("❌ Cancel", callback_data="gp:0:0")]])
    await msg.reply_text(f"🎁 Reward {disp} — tap an amount:",
                         parse_mode=ParseMode.HTML, reply_markup=kb,
                         disable_web_page_preview=True)


async def give_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a tap on a /give quick-amount button. Admins only."""
    q = update.callback_query
    chat_id = q.message.chat.id
    # only admins can award
    if not await is_admin(chat_id, q.from_user.id, context):
        await q.answer("Admins only. ⚡", show_alert=True)
        return
    try:
        _, uid_s, amt_s = q.data.split(":")
        uid, amt = int(uid_s), int(amt_s)
    except (ValueError, IndexError):
        await q.answer()
        return
    if uid == 0:  # cancel
        await q.answer("Cancelled")
        try:
            await q.edit_message_text("❌ Cancelled.")
        except BadRequest:
            pass
        return
    new = _apply_points(chat_id, uid, amt)
    await q.answer(f"✅ +{amt} given!")
    try:
        await q.edit_message_text(
            f"🎁 {mention_id(uid)} got <b>+{amt:,}</b> points → <b>{new:,}</b> total ⚡\n"
            f"Awarded by {mention(q.from_user)}",
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except BadRequest:
        pass
    await check_milestone(context, chat_id, uid, new)


@group_only
@admin_only
async def setpoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = target_chat(update)
    uid, disp = await resolve_target(update, context)
    amt = _amount(context, skip_first=not msg.reply_to_message)
    if uid is None or amt is None:
        await msg.reply_text("Usage: /setpoints @user 1000  (sets their exact total)")
        return
    amt = max(amt, 0)
    if _row(chat_id, uid) is None:
        db.execute("INSERT INTO points (chat_id,user_id,points) VALUES (?,?,?)",
                   (chat_id, uid, amt))
    else:
        db.execute("UPDATE points SET points=? WHERE chat_id=? AND user_id=?",
                   (amt, chat_id, uid))
    await msg.reply_text(f"✅ {disp} now has <b>{amt:,}</b> points. ⚡",
                         parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@group_only
@admin_only
async def resetpoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    if context.args and context.args[0].lower() == "all":
        db.execute("DELETE FROM points WHERE chat_id=?", (chat_id,))
        await update.effective_message.reply_text("🧹 All points reset to zero.")
        return
    uid, disp = await resolve_target(update, context)
    if uid is None:
        await update.effective_message.reply_text(
            "Usage: /resetpoints all   — or reply to one member to zero just them.")
        return
    db.execute("DELETE FROM points WHERE chat_id=? AND user_id=?", (chat_id, uid))
    await update.effective_message.reply_text(f"🧹 Reset {disp} to zero.",
                                              parse_mode=ParseMode.HTML)


@group_only
@admin_only
async def setdaily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.effective_message.reply_text(
            "Usage: /setdailypoints 10   (points each member earns per active day; 0 = off)")
        return
    val = max(int(context.args[0]), 0)
    db.set_setting(target_chat(update), "points_daily", val)
    db.set_setting(target_chat(update), "points_on", 1 if val > 0 else 0)
    state = f"{val} points/day" if val else "off"
    await update.effective_message.reply_text(f"✅ Daily points: {state}.")


@group_only
@admin_only
async def tag_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    rows = db.query("SELECT user_id,name FROM points WHERE chat_id=? ORDER BY points DESC",
                    (chat_id,))
    if not rows:
        await update.effective_message.reply_text(
            "I haven't seen anyone chat yet, so there's no one to tag. "
            "Once members start talking I'll remember them. ⚡")
        return
    note = update.effective_message.text.partition(" ")[2].strip() or "📣 Attention ⚡ZAPP fam!"
    mentions = [mention_id(r["user_id"], r["name"] or "member") for r in rows][:200]
    CHUNK = 20
    first = True
    for i in range(0, len(mentions), CHUNK):
        part = " ".join(mentions[i:i + CHUNK])
        text = (f"{esc(note)}\n\n{part}") if first else part
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        first = False


def register_points(app):
    app.add_handler(CommandHandler(["points", "p", "rank"], points))
    app.add_handler(CommandHandler(["top", "leaderboard", "lb"], top))
    app.add_handler(CommandHandler(["milestone", "race", "race369"], milestone))
    app.add_handler(CommandHandler("resetmilestone", resetmilestone))
    app.add_handler(CommandHandler(["winners", "halloffame", "hof"], winners_cmd))
    app.add_handler(CommandHandler("clearhof", clearhof))
    app.add_handler(CommandHandler(["addpoints", "givepoints"], addpoints))
    app.add_handler(CommandHandler(["give", "reward"], give))
    app.add_handler(CallbackQueryHandler(give_cb, pattern=r"^gp:-?\d+:-?\d+$"))
    app.add_handler(CommandHandler("setpoints", setpoints))
    app.add_handler(CommandHandler("resetpoints", resetpoints))
    app.add_handler(CommandHandler(["setdailypoints", "setdaily"], setdaily))
    app.add_handler(CommandHandler(["all", "tagall", "everyone", "mentionall"], tag_all))
    # passive daily-award tracker runs in its own handler group so it never
    # interferes with the moderation watcher (group 0)
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL, activity), group=2)


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
    kb = InlineKeyboardMarkup([
        # Row 1 — featured games
        [InlineKeyboardButton("🎰 Spin", callback_data="game:spin"),
         InlineKeyboardButton("🧠 Trivia", callback_data="game:trivia"),
         InlineKeyboardButton("🔮 Fortune", callback_data="game:fortune")],
        # Row 2 — sports games
        [InlineKeyboardButton("⚽ Football", callback_data="game:football"),
         InlineKeyboardButton("🏀 Basket", callback_data="game:basket"),
         InlineKeyboardButton("🎯 Dart", callback_data="game:dart")],
        # Row 3 — chance games
        [InlineKeyboardButton("✊ RPS", callback_data="game:rps"),
         InlineKeyboardButton("🪙 Flip", callback_data="game:flip"),
         InlineKeyboardButton("🎲 Roll", callback_data="game:roll")],
        # Row 4 — your stats
        [InlineKeyboardButton("🏆 My Points", callback_data="info:points"),
         InlineKeyboardButton("📊 Leaderboard", callback_data="info:top")],
        # Row 5 — links
        [InlineKeyboardButton("💰 Buy ZAPP", callback_data="info:buy"),
         InlineKeyboardButton("📖 Help", callback_data="info:help")],
        # Row 6 — externals
        [InlineKeyboardButton("🌐 Website", url=WEBSITE),
         InlineKeyboardButton("𝕏 Twitter", url=TWITTER)],
    ])
    await update.effective_message.reply_text(
        f"{BRAND}  <i>v{__version__}</i>\n\n"
        "⚡ Welcome to the <b>⚡ZAPP arcade</b> ⚡\n"
        "Tap a game to play, climb the leaderboard, win points 👇\n\n"
        "🎮 <b>8 games</b> · 🏆 <b>real rewards</b> · ⚡ <b>3·6·9 energy</b>\n\n"
        "<i>Just chat daily to earn points. Stay charged.</i>\n\n"
        "<i>∞ 3 · 6 · 9 ∞</i>",
        parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)


async def game_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route /start menu game + info buttons to the matching handlers."""
    q = update.callback_query
    action = q.data.split(":", 1)[1]
    kind = q.data.split(":", 1)[0]
    await q.answer()
    if kind == "game":
        handlers = {
            "spin": spin, "fortune": fortune, "rps": rps,
            "flip": flip, "roll": roll, "dart": dart,
            "basket": basket, "football": football, "trivia": trivia,
        }
        fn = handlers.get(action)
        if fn:
            await fn(update, context)
    elif kind == "info":
        info_handlers = {
            "points": points, "top": top, "buy": buy, "help": help_cmd,
        }
        fn = info_handlers.get(action)
        if fn:
            await fn(update, context)


@group_only
@admin_only
async def setgamestopic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run inside a topic to lock games to it (e.g. Contests & Giveaways)."""
    msg = update.effective_message
    chat_id = target_chat(update)
    thread = getattr(msg, "message_thread_id", None)
    if not thread:
        await msg.reply_text(
            "⚠️ Run <b>/setgamestopic</b> <i>inside</i> the <b>Contests &amp; Giveaways ✨</b> "
            "topic (not in General) so I know which topic to lock games to.",
            parse_mode=ParseMode.HTML)
        return
    db.set_setting(chat_id, "games_thread", thread)
    await msg.reply_text(
        "🎮 <b>Games locked to this topic!</b> ⚡\n"
        "All games (/spin, /trivia, /rps, /flip, /roll, /dart, /basket, /8ball, "
        "/rate, /guess, /duel, /fortune) now only work here.\n"
        "Undo anytime with /cleargamestopic.",
        parse_mode=ParseMode.HTML)


@group_only
@admin_only
async def cleargamestopic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    db.set_setting(chat_id, "games_thread", None)
    await update.effective_message.reply_text(
        "🎮 Games are now allowed in <b>every</b> topic again. ⚡",
        parse_mode=ParseMode.HTML)


HELP_TEXT = (
    f"{BRAND} <b>— What I can do</b>\n\n"
    "<b>💰 ZAPP &amp; Buying</b>\n"
    "/buy — contract + all buy links\n"
    "/price · /ca · /chart · /whitepaper\n"
    "/website · /socials · /stats\n\n"
    "<b>🎮 Games &amp; Fun</b>\n"
    "/spin — slot machine (3/day) 🎰\n"
    "/trivia — answer to earn +9 points 🧠\n"
    "/football ⚽ · /basket 🏀 · /dart 🎯\n"
    "/rps · /flip · /roll · /fortune\n"
    "/8ball [question] — magic 8-ball\n"
    "/rate [thing] — rate out of 10\n"
    "Say <i>gm</i> or <i>gn</i> — I greet you back ⚡\n\n"
    "<b>🏆 Points &amp; Ranks</b>\n"
    "/points — your points + rank + streak\n"
    "/top — the leaderboard\n"
    "/milestone — Race to 369\n"
    "/winners — Hall of Fame\n"
    "<i>Tip: chat daily, keep your streak alive, climb the ranks.</i>\n\n"
    "<b>✨ Earn More</b>\n"
    "/tasks — earn-by-sharing quests\n"
    "/submit &lt;type&gt; — submit proof (reply to your screenshot)\n\n"
    "<b>🛠 Tools</b>\n"
    "/tr — translate (reply to a message)\n"
    "/afk — set yourself away\n"
    "/report — alert admins (reply to a message)\n\n"
    "<i>Admins: see /adminhelp for moderation commands.</i>\n\n"
    "⚡ <i>∞ 3 · 6 · 9 ∞</i>"
)

ADMIN_HELP_TEXT = (
    f"{BRAND} <b>— Admin toolkit</b>\n\n"
    "<b>🛡 Moderation</b>\n"
    "/ban · /unban · /kick · /mute · /unmute (reply or @user; add <code>30m 2h 1d</code> for temp)\n"
    "/promote · /demote · /pin · /unpin · /purge · /del\n\n"
    "<b>⚠️ Warnings</b>\n"
    "/warn · /unwarn · /warns · /resetwarns · /setwarnlimit · /setwarnaction\n\n"
    "<b>🔒 Protection</b>\n"
    "/godmode — one-tap max security 🛡️\n"
    "/lock · /unlock · /locks · /setflood · /flood · /antiraid · /nightmode\n"
    "/addblocklist · /blocklists · /unblocklist · /blocklistaction\n\n"
    "<b>🎁 Rewards</b>\n"
    "/give (reply) — quick-tap reward buttons\n"
    "/addpoints @user N · /setpoints · /resetpoints · /resetmilestone\n"
    "/pending · /approve · /reject — submission queue\n\n"
    "<b>👋 Greetings</b>\n"
    "/setwelcome · /welcome · /setgoodbye · /goodbye · /cleanservice · /captcha\n"
    "/setwelcomeimage · /setrulesimage · /setbuyimage (reply to a photo)\n"
    "/testwelcome — preview the welcome\n\n"
    "<b>📝 Content</b>\n"
    "/save · /get · /notes · /clear (or <code>#note</code>)\n"
    "/filter · /filters · /stop\n"
    "/setrules · /rules · /about · /setabout\n\n"
    "<b>⏰ Auto-posts &amp; Trivia</b>\n"
    "/autopost on|off|here|tz|test — daily posts (9am, 12, 3, 6, 9pm)\n"
    "/autotrivia on|off|here|tz|test — scheduled trivia rounds\n"
    "/raid — start a raid event\n\n"
    "<b>🎮 Game Routing</b>\n"
    "/setgamestopic — lock games to a topic (run inside it)\n"
    "/cleargamestopic — unlock games everywhere\n\n"
    "<b>🌐 Federations &amp; System</b>\n"
    "/newfed · /joinfed · /fedban · /fedinfo\n"
    "/all — tag everyone\n"
    "/connect · /disconnect · /connection — manage group from your DM with the bot\n"
    "/disable · /enable · /setlog · /settings · /id\n"
)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML,
                                              disable_web_page_preview=True)


async def admin_help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(ADMIN_HELP_TEXT, parse_mode=ParseMode.HTML,
                                              disable_web_page_preview=True)


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bring up the /start menu anywhere."""
    await start(update, context)


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
    user_id = update.effective_user.id
    prev = pm_connections.pop(user_id, None)
    if prev is None:
        await update.effective_message.reply_text(
            "🔌 You weren't connected to anything. Use /connect &lt;group_id&gt; "
            "in DM, or /connect inside the group itself.", parse_mode=ParseMode.HTML)
        return
    try:
        chat = await context.bot.get_chat(prev)
        title = esc(chat.title or str(prev))
    except BadRequest:
        title = str(prev)
    await update.effective_message.reply_text(
        f"🔌 Disconnected from <b>{title}</b>. Run /connect &lt;id&gt; to reconnect.",
        parse_mode=ParseMode.HTML)


async def connection_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the currently connected group (if any)."""
    user_id = update.effective_user.id
    chat_id = pm_connections.get(user_id)
    if chat_id is None:
        await update.effective_message.reply_text(
            "🔌 You're not connected to any group right now.\n\n"
            "<b>How to connect:</b>\n"
            "• Inside a group you admin: <code>/connect</code>\n"
            "• Here in DM: <code>/connect &lt;group_id&gt;</code>\n\n"
            "Use /id inside a group to see its ID.",
            parse_mode=ParseMode.HTML)
        return
    try:
        chat = await context.bot.get_chat(chat_id)
        title = esc(chat.title or str(chat_id))
        link = f"<a href=\"https://t.me/c/{str(chat_id).lstrip('-100')}\">{title}</a>" \
            if str(chat_id).startswith("-100") else title
    except BadRequest:
        link = str(chat_id)
    await update.effective_message.reply_text(
        f"🔗 Currently connected to: {link}\n\n"
        f"All admin commands you run here will apply to that group.\n"
        f"Run /disconnect to detach.",
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


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
    app.add_handler(CallbackQueryHandler(game_cb, pattern=r"^game:"))
    app.add_handler(CommandHandler(["setgamestopic", "setgameschannel"], setgamestopic))
    app.add_handler(CommandHandler("cleargamestopic", cleargamestopic))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler(["adminhelp", "modhelp"], admin_help_cmd))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("afk", afk))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("unapprove", unapprove))
    app.add_handler(CommandHandler("approved", approved))
    app.add_handler(CommandHandler("connect", connect))
    app.add_handler(CommandHandler("disconnect", disconnect))
    app.add_handler(CommandHandler(["connection", "connections"], connection_status))
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
import re
import html
import json
import time
import random
from telegram import (
    Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.error import BadRequest, Forbidden
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
    body, ents = capture_rich(update.effective_message, 1)
    if not body:
        await update.effective_message.reply_text(
            "Usage: /setwelcome <text>\nPlaceholders: {name} {first} {username} {id} {group} {count}\n"
            "Buttons: [Label](buttonurl://https://link.com)  (add :same to keep on one row)")
        return
    chat_id = target_chat(update)
    db.set_setting(chat_id, "welcome", body)
    db.set_setting(chat_id, "welcome_entities", json.dumps(ents) if ents else None)
    await update.effective_message.reply_text("✅ Welcome message saved.")


@group_only
@admin_only
async def setgoodbye(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.effective_message.text.partition(" ")[2].strip()
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
def _welcome_repls(member, chat):
    name = member.first_name or "there"
    uname = ("@" + member.username) if member.username else name
    return [
        ("{name}", name, (member.id, name)),
        ("{first}", member.first_name or "", None),
        ("{username}", uname, None),
        ("{id}", str(member.id), None),
        ("{group}", chat.title or "the group", None),
        ("{count}", "", None),
    ]


async def _send_media(message, kind, fid, text, entities, reply_markup):
    """Send the welcome as a photo / GIF / video. If Telegram rejects the
    formatted caption (e.g. URL/emoji entities on a media caption), retry with a
    plain caption so the IMAGE still shows instead of dropping to text-only."""
    sender = {
        "animation": message.reply_animation,
        "video": message.reply_video,
    }.get(kind, message.reply_photo)
    # attempt 1 — formatted caption
    kw = dict(caption=text, reply_markup=reply_markup)
    if entities is not None:
        kw["caption_entities"] = entities or None
    else:
        kw["parse_mode"] = ParseMode.HTML
    try:
        return await sender(fid, **kw)
    except BadRequest:
        pass
    # attempt 2 — plain caption (links auto-detect; keeps the picture)
    plain = html.unescape(re.sub(r"<[^>]+>", "", text))[:1024]
    try:
        return await sender(fid, caption=plain, reply_markup=reply_markup)
    except BadRequest:
        return None


async def _send_welcome(message, settings, member, chat, extra="", reply_markup=None):
    """Send the welcome. Uses a photo/GIF/video if one is set; preserves premium
    emoji via stored entities; always falls back gracefully to plain text."""
    img = settings["welcome_image"] if "welcome_image" in settings.keys() else None
    kind = (settings["welcome_image_type"] if "welcome_image_type" in settings.keys()
            else None) or "photo"
    raw = settings["welcome_entities"] if "welcome_entities" in settings.keys() else None

    if raw and settings["welcome"]:
        text, ents = substitute_entities(settings["welcome"], json.loads(raw),
                                         _welcome_repls(member, chat))
        if extra:
            text = text + extra
        entities = build_entities(ents)
        if img:
            sent = await _send_media(message, kind, img, text, entities, reply_markup)
            if sent:
                return sent
        return await reply_rich(message, text, entities, reply_markup=reply_markup)

    txt, markup = build_message(settings["welcome"] or DEFAULT_WELCOME, user=member, chat=chat)
    if extra:
        txt = txt + extra
    rm = reply_markup or markup
    if img:
        sent = await _send_media(message, kind, img, txt, None, rm)
        if sent:
            return sent
    return await safe_reply(message, txt, reply_markup=rm)


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
                await _send_welcome(update.effective_message, s, member, chat,
                                    extra="\n\n🤖 Tap below to unlock chat.", reply_markup=kb)
            _mark_welcomed(chat.id, member.id)
            if context.job_queue:
                context.job_queue.run_once(_kick_unverified, 120, data=(chat.id, member.id))
            continue

        # --- normal welcome ---
        if s["welcome_on"]:
            _mark_welcomed(chat.id, member.id)
            sent = await _send_welcome(update.effective_message, s, member, chat)
            if s["clean_welcome"] and sent and context.job_queue:
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
        await safe_reply(update.effective_message, txt, reply_markup=markup)


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


@group_only
@admin_only
async def setwelcomeimage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    reply = msg.reply_to_message
    fid = kind = None
    if reply:
        if reply.animation:
            fid, kind = reply.animation.file_id, "animation"
        elif reply.video:
            fid, kind = reply.video.file_id, "video"
        elif reply.photo:
            fid, kind = reply.photo[-1].file_id, "photo"
    if not fid:
        await msg.reply_text(
            "Reply to a photo, GIF, or video with /setwelcomeimage to show it on every welcome.")
        return
    db.set_setting(target_chat(update), "welcome_image", fid)
    db.set_setting(target_chat(update), "welcome_image_type", kind)
    label = {"animation": "GIF", "video": "video"}.get(kind, "image")
    await msg.reply_text(f"✅ Welcome {label} set. Use /testwelcome to preview.")


@group_only
@admin_only
async def delwelcomeimage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.set_setting(target_chat(update), "welcome_image", None)
    await update.effective_message.reply_text(
        "🗑 Welcome media removed. Welcomes will be text-only again.")


# topic icon colours Telegram accepts (fallback if an emoji icon isn't available)
_TOPIC_COLORS = [0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F]
# (topic name, preferred emoji icon) — emoji icon is used when Telegram allows it
_ZAPP_TOPICS = [
    ("⚡ Announcements", "📢"),
    ("💬 General Chat", "💬"),
    ("😂 ZAPP Memes & GIFs", "😂"),
    ("🔥 Post Your Buys", "🔥"),
    ("🛒 How to Buy", "🛒"),
    ("📊 Charts & Buys", "📊"),
    ("📣 Social Media", "📣"),
    ("🎁 Contests & Giveaways", "🎁"),
    ("📖 About ZAPP", "📖"),
]


@group_only
@admin_only
async def setup_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if getattr(chat, "type", None) == "private":
        await update.effective_message.reply_text("Run /setuptopics inside the group.")
        return
    if not getattr(chat, "is_forum", False):
        await update.effective_message.reply_text(
            "First turn Topics on (one-time):\n"
            "Open the group → Edit (pencil) → toggle <b>Topics</b> on.\n"
            "Then run /setuptopics again and I'll create all the sections. ⚡",
            parse_mode=ParseMode.HTML)
        return
    # try to use real emoji icons (like the cool ones MOOS has); fall back to colours
    icon_map = {}
    try:
        for st in await context.bot.get_forum_topic_icon_stickers():
            if getattr(st, "emoji", None) and st.emoji not in icon_map:
                icon_map[st.emoji] = st.custom_emoji_id
    except Exception:
        icon_map = {}
    created, failed = [], []
    perm_error = False
    for i, (name, emoji) in enumerate(_ZAPP_TOPICS):
        color = _TOPIC_COLORS[i % len(_TOPIC_COLORS)]
        custom = icon_map.get(emoji)
        made = False
        for attempt in ("emoji", "color"):
            if attempt == "emoji" and not custom:
                continue
            kwargs = {"name": name}
            if attempt == "emoji":
                kwargs["icon_custom_emoji_id"] = custom
            else:
                kwargs["icon_color"] = color
            try:
                await context.bot.create_forum_topic(chat.id, **kwargs)
                created.append(name)
                made = True
                break
            except Forbidden:
                perm_error = True
                break
            except BadRequest as e:
                m = str(e).lower()
                if "right" in m or "admin" in m or "permission" in m:
                    perm_error = True
                    break
                # otherwise (e.g. icon rejected) fall through and try a plain colour
        if perm_error:
            break
        if not made:
            failed.append(name)
    if perm_error:
        await update.effective_message.reply_text(
            "🚫 I can't create sections yet — I need the <b>Manage Topics</b> admin "
            "permission.\n\nEasiest way (on your phone): open the group → tap the group "
            "name → <b>Administrators</b> → tap <b>ZAPPbot</b> → turn on <b>Manage "
            "Topics</b> → back out to save.\n\nThen run /setuptopics again. ⚡",
            parse_mode=ParseMode.HTML)
        return
    txt = "✅ Created: " + ", ".join(created) if created else "No topics created."
    if failed:
        txt += "\n⚠️ Skipped: " + ", ".join(failed)
    txt += "\n\nTip: run this only once — running again makes duplicates."
    await update.effective_message.reply_text(txt)


@group_only
@admin_only
async def test_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Preview the welcome message, using the admin who ran it as the 'new member'."""
    chat = update.effective_chat
    s = db.get_settings(target_chat(update))
    await update.effective_message.reply_text("👇 Welcome preview (this is what new members see):")
    await _send_welcome(update.effective_message, s, update.effective_user, chat)


# --- welcome de-dupe + chat_member fallback ------------------------------------
# Some forum/privacy setups suppress the "X joined" service message, so the
# NEW_CHAT_MEMBERS MessageHandler never fires. We also listen to chat_member
# updates as a fallback. This dict makes sure a member is welcomed exactly once.
_welcomed_recent: dict = {}

def _mark_welcomed(chat_id, uid):
    _welcomed_recent[(chat_id, uid)] = time.time()
    # prune
    if len(_welcomed_recent) > 4000:
        cut = time.time() - 120
        for k in [k for k, t in _welcomed_recent.items() if t < cut]:
            _welcomed_recent.pop(k, None)

def _was_welcomed(chat_id, uid, window=45):
    t = _welcomed_recent.get((chat_id, uid))
    return t is not None and (time.time() - t) < window


class _GeneralSender:
    """Adapter so _send_welcome can post a fresh message into the General topic
    (message_thread_id=None) when there's no service message to reply to."""
    def __init__(self, bot, chat_id):
        self._bot, self._cid = bot, chat_id
    async def reply_text(self, text, **kw):
        return await self._bot.send_message(self._cid, text, message_thread_id=None, **kw)
    async def reply_photo(self, fid, **kw):
        return await self._bot.send_photo(self._cid, fid, message_thread_id=None, **kw)
    async def reply_animation(self, fid, **kw):
        return await self._bot.send_animation(self._cid, fid, message_thread_id=None, **kw)
    async def reply_video(self, fid, **kw):
        return await self._bot.send_video(self._cid, fid, message_thread_id=None, **kw)


async def on_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback welcome via chat_member updates (fires when the join service
    message is missing). Posts into General only; respects captcha/anti-raid by
    deferring to the service-message path when those are on."""
    cmu = update.chat_member
    if not cmu:
        return
    chat = update.effective_chat
    old = cmu.old_chat_member.status
    new = cmu.new_chat_member.status
    joined = (old in ("left", "kicked")) and (new in ("member", "restricted"))
    if not joined:
        return
    member = cmu.new_chat_member.user
    if member.is_bot or member.id == context.bot.id:
        return
    if _was_welcomed(chat.id, member.id):
        return
    s = db.get_settings(chat.id)
    if not s["welcome_on"]:
        return
    # if captcha or anti-raid is active, let the service-message path handle it
    if s["captcha_on"] or s["antiraid_on"]:
        return
    _mark_welcomed(chat.id, member.id)
    sender = _GeneralSender(context.bot, chat.id)
    try:
        await _send_welcome(sender, s, member, chat)
    except Exception as e:  # noqa: BLE001
        logger.warning("fallback welcome failed: %s", e)


def register_greetings(app):
    app.add_handler(CommandHandler("setwelcome", setwelcome))
    app.add_handler(CommandHandler(["setwelcomeimage", "setwelcomepic", "setwelcomegif"], setwelcomeimage))
    app.add_handler(CommandHandler(["delwelcomeimage", "delwelcomepic"], delwelcomeimage))
    app.add_handler(CommandHandler(["setuptopics", "createtopics"], setup_topics))
    app.add_handler(CommandHandler(["testwelcome", "welcometest", "previewwelcome"], test_welcome))
    app.add_handler(CommandHandler("welcome", _toggle("welcome_on")))
    app.add_handler(CommandHandler("setgoodbye", setgoodbye))
    app.add_handler(CommandHandler("goodbye", _toggle("goodbye_on")))
    app.add_handler(CommandHandler("cleanservice", _toggle("clean_service")))
    app.add_handler(CommandHandler("cleanwelcome", _toggle("clean_welcome")))
    app.add_handler(CommandHandler("captcha", captcha_toggle))
    app.add_handler(CallbackQueryHandler(captcha_callback, pattern=r"^cap:"))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_member))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, on_left_member))
    app.add_handler(ChatMemberHandler(on_member_update, ChatMemberHandler.CHAT_MEMBER))


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
# ---- powerpack ------------------------------------------------------------
# ===========================================================================

"""
ZAPP Power Pack — high-value community commands that build on the existing
modules. Kept in its own file so nothing else can break.

Adds:
  /godmode [on|off]        one-tap max security (captcha+antiraid+flood+locks+blocklist)
  /socials                 all social links
  /website /site           website button
  /chart                   chart button
  /stats                   live token + group stats
  /raid                    raid call-to-action post (engagement)
  /autopost on|off|...      scheduled daily messages (6am,12,3,6,9pm)

It references constants/helpers from the buy module. In the single-file bundle
everything shares one namespace, so we read them from globals() with safe
fallbacks (works both bundled and as a package).
"""
import random
from datetime import time as dtime
try:
    from zoneinfo import ZoneInfo
except Exception:  # noqa: BLE001
    ZoneInfo = None

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, ContextTypes



# --- pull shared values from the buy module (bundle shares globals) ----------
def _g(name, default=None):
    return globals().get(name, default)


def _const(name, default=""):
    # works in the bundle (globals) and as a package (import from .buy)
    val = globals().get(name)
    if val is not None:
        return val
    try:
        return getattr(_buy, name, default)
    except Exception:  # noqa: BLE001
        return default


# --------------------------- /godmode --------------------------------------
@group_only
@admin_only
async def godmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Turn on (or off) every protection at once."""
    chat_id = target_chat(update)
    msg = update.effective_message
    off = bool(context.args) and context.args[0].lower() in ("off", "0", "stop")

    if off:
        db.set_setting(chat_id, "captcha_on", 0)
        db.set_setting(chat_id, "antiraid_on", 0)
        db.set_setting(chat_id, "flood_limit", 0)
        await msg.reply_text(
            "🔓 <b>God Mode OFF.</b>\n"
            "Captcha, anti-raid and anti-flood relaxed. Locks &amp; blocklist "
            "left as-is (use /unlock and /blocklistaction to change those).",
            parse_mode=ParseMode.HTML)
        return

    # max security
    db.set_setting(chat_id, "captcha_on", 1)
    db.set_setting(chat_id, "captcha_mode", "button")
    db.set_setting(chat_id, "antiraid_on", 1)
    db.set_setting(chat_id, "antiraid_threshold", 5)
    db.set_setting(chat_id, "antiraid_window", 30)
    db.set_setting(chat_id, "antiraid_action", "mute")
    db.set_setting(chat_id, "flood_limit", 6)
    db.set_setting(chat_id, "flood_action", "mute")
    db.set_setting(chat_id, "blocklist_action", "delete")
    db.set_setting(chat_id, "autoguard_on", 1)
    db.set_setting(chat_id, "autofaq_on", 1)
    for lt in ("url", "forward", "invite"):
        db.execute("INSERT OR IGNORE INTO locks (chat_id,lock_type) VALUES (?,?)",
                   (chat_id, lt))

    await msg.reply_text(
        "🛡️ <b>GOD MODE ENGAGED</b> ⚡\n\n"
        "✅ Captcha: <b>on</b> (button)\n"
        "✅ Anti-raid: <b>on</b> (5 joins / 30s → mute)\n"
        "✅ Anti-flood: <b>6 msgs → mute</b>\n"
        "✅ Locked: <b>links, forwards, invites</b>\n"
        "✅ Blocklist: <b>auto-delete</b>\n"
        "✅ Auto-Guard: <b>on</b> (auto-removes scams/phishing/fake CAs)\n\n"
        "The room is sealed. Scammers and raid bots get nothing — even while "
        "you're away.\nReminder: I need <b>Ban Users</b> + <b>Delete Messages</b> "
        "admin rights for this to bite.\n\n∞ 3 · 6 · 9 ∞\n⚡ZAPP",
        parse_mode=ParseMode.HTML)


@group_only
@admin_only
async def autoguard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle the autonomous anti-scam guard."""
    chat_id = target_chat(update)
    msg = update.effective_message
    arg = context.args[0].lower() if context.args else ""
    if arg == "on":
        db.set_setting(chat_id, "autoguard_on", 1)
        await msg.reply_text(
            "🛡️ <b>Auto-Guard ON</b> ⚡\nI'll auto-remove phishing, fake-support/admin "
            "impersonation, fake airdrops, doubler scams and foreign/fake contract "
            "addresses — and mute repeat offenders. Perfect for when you're away.",
            parse_mode=ParseMode.HTML)
    elif arg == "off":
        db.set_setting(chat_id, "autoguard_on", 0)
        await msg.reply_text("🛡️ Auto-Guard <b>OFF</b>.", parse_mode=ParseMode.HTML)
    else:
        s = db.get_settings(chat_id)
        state = "ON ✅" if s["autoguard_on"] else "OFF"
        await msg.reply_text(
            f"🛡️ <b>Auto-Guard:</b> {state}\n"
            "Catches: wallet phishing, seed/key requests, fake airdrops, admin/"
            "support impersonation, doubler scams, foreign contract addresses.\n"
            "Action: delete + warn, then mute after 3 strikes.\n"
            "Usage: <code>/autoguard on|off</code>  (also turned on by /godmode)",
            parse_mode=ParseMode.HTML)


@group_only
@admin_only
async def autofaq_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle the auto-answer FAQ (group answers common questions itself)."""
    chat_id = target_chat(update)
    msg = update.effective_message
    arg = context.args[0].lower() if context.args else ""
    if arg == "on":
        db.set_setting(chat_id, "autofaq_on", 1)
        await msg.reply_text(
            "💬 <b>Auto-FAQ ON</b> ⚡\nI'll auto-answer when members ask about buying, "
            "the CA, price, chart, safety, whitepaper, socials, or 'when moon' — "
            "so the group stays helpful even when no admin is online.",
            parse_mode=ParseMode.HTML)
    elif arg == "off":
        db.set_setting(chat_id, "autofaq_on", 0)
        await msg.reply_text("💬 Auto-FAQ <b>OFF</b>.", parse_mode=ParseMode.HTML)
    else:
        s = db.get_settings(chat_id)
        state = "ON ✅" if s["autofaq_on"] else "OFF"
        await msg.reply_text(
            f"💬 <b>Auto-FAQ:</b> {state}\n"
            "Auto-answers: how to buy, CA, price, chart, is-it-safe, whitepaper, "
            "socials, when-moon. Rate-limited so it never spams.\n"
            "Usage: <code>/autofaq on|off</code>  (also turned on by /godmode)",
            parse_mode=ParseMode.HTML)


# --------------------------- quick link commands ---------------------------
async def socials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = [
        [InlineKeyboardButton("🌐 Website", url=_const("WEBSITE")),
         InlineKeyboardButton("𝕏 Twitter", url=_const("TWITTER"))],
        [InlineKeyboardButton("💬 Telegram", url=_const("TELEGRAM")),
         InlineKeyboardButton("📸 Instagram", url=_const("INSTAGRAM"))],
    ]
    extra = []
    if _const("TIKTOK"):
        extra.append(InlineKeyboardButton("🎵 TikTok", url=_const("TIKTOK")))
    if _const("DISCORD"):
        extra.append(InlineKeyboardButton("👾 Discord", url=_const("DISCORD")))
    for i in range(0, len(extra), 2):
        rows.append(extra[i:i + 2])
    await update.effective_message.reply_text(
        "📣 <b>⚡ZAPP Socials</b>\nFollow, like, repost — keep the signal loud. ⚡\n"
        "∞ 3 · 6 · 9 ∞",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows),
        disable_web_page_preview=True)


async def website(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🌐 <b>⚡ZAPP Website</b>\n∞ Free Energy = Free Money ∞",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🌐 Open zapp369.energy", url=_const("WEBSITE"))]]),
        disable_web_page_preview=True)


async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📊 <b>⚡ZAPP Chart</b>\nTrack the current live. ⚡",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 Chart", url=_const("CHART")),
            InlineKeyboardButton("🪐 Buy", url=_const("JUPITER")),
        ]]),
        disable_web_page_preview=True)


# --------------------------- /stats ----------------------------------------
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    try:
        members = await context.bot.get_chat_member_count(chat.id)
    except Exception:  # noqa: BLE001
        members = None
    chat_id = target_chat(update)
    tracked = db.query("SELECT COUNT(*) c FROM points WHERE chat_id=?", (chat_id,))
    ntracked = tracked[0]["c"] if tracked else 0

    body = "📊 <b>⚡ZAPP Stats</b>\n\n"
    if members:
        body += f"👥 <b>Members:</b> {members:,}\n"
    body += f"⚡ <b>Active members tracked:</b> {ntracked:,}\n∞ 3 · 6 · 9 ∞"

    # append live price if the buy module's fetcher is available
    fetch = _g("_fetch_price_text")
    if fetch:
        txt = await fetch()
        if txt:
            body += "\n\n" + txt

    await msg.reply_text(body, parse_mode=ParseMode.HTML,
                         disable_web_page_preview=True)


# --------------------------- /raid -----------------------------------------
@group_only
@admin_only
async def raid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Post a raid call-to-action. Optional: /raid <tweet link> to target a post."""
    target = ""
    if context.args:
        cand = context.args[0]
        if cand.startswith("http"):
            target = cand
    link = target or _const("TWITTER")
    label = "🐦 Go to the Post" if target else "𝕏 Go to ⚡ZAPP on X"
    await update.effective_message.reply_text(
        "⚡ <b>RAID TIME</b> ⚡\n\n"
        "All hands on deck — like, repost, comment. Let's make ⚡ZAPP loud. 🔊\n"
        "Every engagement powers the signal. 🔌\n\n"
        "∞ Free Energy = Free Money ∞\n3 · 6 · 9 🚀",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(label, url=link)]]))


# --------------------------- AUTO-POSTS ------------------------------------
# Daily scheduled messages. Times are 6am, 12pm, 3pm, 6pm, 9pm (3·6·9 themed).
AUTOPOST_SLOTS = [
    ("morning", 9, 0),
    ("noon", 12, 0),
    ("three", 15, 0),
    ("six", 18, 0),
    ("nine", 21, 0),
]


def _ap_buy_kb():
    rows = [[InlineKeyboardButton("🪐 Buy", url=_const("JUPITER")),
             InlineKeyboardButton("📊 Chart", url=_const("CHART"))]]
    ph = _const("PHANTOM")
    if ph:
        rows.append([InlineKeyboardButton("👻 Phantom", url=ph)])
    return InlineKeyboardMarkup(rows)


def _ap_socials_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Website", url=_const("WEBSITE")),
         InlineKeyboardButton("𝕏 Twitter", url=_const("TWITTER"))],
        [InlineKeyboardButton("💬 Telegram", url=_const("TELEGRAM"))],
    ])


def _autopost_message(slot):
    """Return (text, reply_markup) for a slot. A little randomness keeps it fresh."""
    ca = _const("CA")
    if slot == "morning":
        text = random.choice([
            "🌅 <b>gm ⚡ZAPP fam!</b>\nThe current is back on. New day, same mission: "
            "free energy = free money. ⚡\n\nNew here? Tap below to grab ⚡ZAPP.\n∞ 3 · 6 · 9 ∞",
            "🌅 <b>gm!</b> ⚡\nRise and charge up. The signal never sleeps and neither does "
            "the grind. Secure your ⚡ZAPP today.\n∞ Free Energy = Free Money ∞",
        ])
        return text, _ap_buy_kb()
    if slot == "noon":
        text = ("☀️ <b>Midday charge</b> ⚡\nSecured your ⚡ZAPP today?\n\n"
                f"<b>CA</b> (tap to copy):\n<code>{ca}</code>\n\n"
                "⚠️ Only ever use this CA. Admins never DM first.\n∞ 3 · 6 · 9 ∞")
        return text, _ap_buy_kb()
    if slot == "three":
        text = random.choice([
            "⚡ <b>3 o'clock — the 3·6·9 hour</b> ⚡\nGrind your points: /spin your daily "
            "slot 🎰, smash /trivia 🧠, climb /top.\n🏁 First to <b>369 points</b> wins a "
            "reward — could be you. Check /milestone!",
            "🔌 <b>Afternoon energy check</b> ⚡\nDaily /spin done? /trivia played?\n"
            "Every point gets you closer to the <b>Race to 369</b> reward. /milestone",
        ])
        return text, None
    if slot == "six":
        text = random.choice([
            "📊 <b>Evening check</b> ⚡\nTrack the chart, stack your bag. The revolution "
            "compounds. ⚡\n∞ 3 · 6 · 9 ∞",
            "🔥 <b>6pm — prime time</b> ⚡\nChart's open, community's loud. Don't fade the "
            "frequency.\n∞ Free Energy = Free Money ∞",
        ])
        return text, _ap_buy_kb()
    # nine
    text = random.choice([
        "🌙 <b>gn ⚡ZAPP fam.</b>\nThe signal stays on while you rest. Follow our socials "
        "so you never miss a beat. ⚡\n∞ 3 · 6 · 9 ∞",
        "🌙 <b>gn!</b> ⚡\nGreat day for the current. Stay plugged in — big things charge "
        "up overnight. ⚡",
    ])
    return text, _ap_socials_kb()


async def _autopost_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    chat_id = data.get("chat_id")
    slot = data.get("slot")
    if chat_id is None:
        return
    # only post if still enabled
    s = db.get_settings(chat_id)
    if not s["autopost_on"]:
        return
    text, kb = _autopost_message(slot)
    kwargs = {"parse_mode": ParseMode.HTML, "disable_web_page_preview": True}
    if kb:
        kwargs["reply_markup"] = kb
    thread = s["autopost_thread"]
    if thread:
        kwargs["message_thread_id"] = thread
    try:
        await context.bot.send_message(chat_id, text, **kwargs)
    except Exception:  # noqa: BLE001
        pass


def _ap_tz(tzname):
    if ZoneInfo:
        try:
            return ZoneInfo(tzname)
        except Exception:  # noqa: BLE001
            pass
    return None  # PTB falls back to UTC if tzinfo is None on a naive time -> use UTC


def schedule_autoposts(app, chat_id):
    """(Re)schedule the 5 daily auto-posts for one chat."""
    jq = app.job_queue
    if not jq:
        return
    # clear existing
    for slot, _h, _m in AUTOPOST_SLOTS:
        for j in jq.get_jobs_by_name(f"ap_{chat_id}_{slot}"):
            j.schedule_removal()
    s = db.get_settings(chat_id)
    if not s["autopost_on"]:
        return
    tz = _ap_tz(s["autopost_tz"] or "Europe/Zurich")
    for slot, h, m in AUTOPOST_SLOTS:
        t = dtime(h, m, tzinfo=tz) if tz else dtime(h, m)
        jq.run_daily(_autopost_job, t, data={"chat_id": chat_id, "slot": slot},
                     name=f"ap_{chat_id}_{slot}")


def reschedule_autoposts(app):
    """On startup, schedule auto-posts for every chat that has them enabled."""
    try:
        rows = db.query("SELECT chat_id FROM settings WHERE autopost_on=1")
    except Exception:  # noqa: BLE001
        rows = []
    for r in rows:
        schedule_autoposts(app, r["chat_id"])


@group_only
@admin_only
async def autopost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    msg = update.effective_message
    args = [a for a in context.args]
    sub = args[0].lower() if args else "status"
    s = db.get_settings(chat_id)

    if sub == "on":
        db.set_setting(chat_id, "autopost_on", 1)
        # if run inside a forum topic, post there; else General
        thread = getattr(msg, "message_thread_id", None)
        db.set_setting(chat_id, "autopost_thread", thread)
        schedule_autoposts(context.application, chat_id)
        where = "this topic" if thread else "the group (General)"
        await msg.reply_text(
            f"⏰ <b>Auto-posts ON</b> ⚡\nDaily messages at <b>6am, 12pm, 3pm, 6pm, 9pm</b> "
            f"({s['autopost_tz']}) in {where}.\n"
            "Change zone: /autopost tz Europe/London\nPreview now: /autopost test",
            parse_mode=ParseMode.HTML)
        return
    if sub == "off":
        db.set_setting(chat_id, "autopost_on", 0)
        schedule_autoposts(context.application, chat_id)
        await msg.reply_text("⏰ Auto-posts <b>OFF</b>.", parse_mode=ParseMode.HTML)
        return
    if sub == "tz" and len(args) >= 2:
        tzname = args[1]
        if _ap_tz(tzname) is None and ZoneInfo is not None:
            await msg.reply_text(
                "❓ Unknown timezone. Use a name like <code>Europe/Zurich</code>, "
                "<code>America/New_York</code>, or <code>UTC</code>.",
                parse_mode=ParseMode.HTML)
            return
        db.set_setting(chat_id, "autopost_tz", tzname)
        if s["autopost_on"]:
            schedule_autoposts(context.application, chat_id)
        await msg.reply_text(f"🕒 Auto-post timezone set to <b>{tzname}</b>.",
                             parse_mode=ParseMode.HTML)
        return
    if sub == "test":
        slot = args[1] if len(args) >= 2 and args[1] in dict((s2, 1) for s2, _, _ in AUTOPOST_SLOTS) else "three"
        text, kb = _autopost_message(slot)
        kwargs = {"parse_mode": ParseMode.HTML, "disable_web_page_preview": True}
        if kb:
            kwargs["reply_markup"] = kb
        await msg.reply_text("👀 <b>Preview</b> — this is what an auto-post looks like:",
                             parse_mode=ParseMode.HTML)
        await msg.reply_text(text, **kwargs)
        return

    # status
    state = "ON ✅" if s["autopost_on"] else "OFF"
    await msg.reply_text(
        f"⏰ <b>Auto-posts:</b> {state}\n"
        f"🕒 Times: 6am, 12pm, 3pm, 6pm, 9pm ({s['autopost_tz']})\n\n"
        "Commands:\n"
        "/autopost on — enable (posts here)\n"
        "/autopost off — disable\n"
        "/autopost tz &lt;Zone&gt; — set timezone\n"
        "/autopost test — preview a message",
        parse_mode=ParseMode.HTML)


def register_powerpack(app):
    app.add_handler(CommandHandler("godmode", godmode))
    app.add_handler(CommandHandler(["autoguard", "antiscam"], autoguard))
    app.add_handler(CommandHandler(["autofaq", "faq"], autofaq_cmd))
    app.add_handler(CommandHandler("socials", socials))
    app.add_handler(CommandHandler(["website", "site"], website))
    app.add_handler(CommandHandler("chart", chart))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("raid", raid))
    app.add_handler(CommandHandler(["autopost", "autoposts"], autopost))


# ===========================================================================
# ---- fun ------------------------------------------------------------
# ===========================================================================

"""
⚡ ZAPP Engagement Pack — fun, interactive commands that keep the chat alive.

  /spin           daily slot spin → win points (777 jackpot = 369 ⚡)
  /flip           coin flip
  /roll [n]       roll a dice (Telegram animated)
  /dart /basket   throw a dart / shoot a hoop (animated)
  /8ball <q>      magic 8-ball
  /rate <thing>   rate something /10
  /trivia         ZAPP/crypto trivia with answer buttons → points to first correct
  gm / gn         the bot greets back (crypto culture)

Game point rewards plug into the existing points table. Daily-limited where it
could be farmed, so the economy stays meaningful.
"""
import random
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatType
from telegram.error import BadRequest
from telegram.ext import (
    CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler,
)



def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _award(chat_id, user, n):
    """Add n points to a user, creating their row if needed. Returns new total."""
    name = (user.first_name or "user")
    uname = user.username or ""
    row = db.query("SELECT points FROM points WHERE chat_id=? AND user_id=?",
                   (chat_id, user.id), one=True)
    if row is None:
        db.execute("INSERT INTO points (chat_id,user_id,points,name,username) "
                   "VALUES (?,?,?,?,?)", (chat_id, user.id, max(n, 0), name, uname))
        return max(n, 0)
    new = max((row["points"] or 0) + n, 0)
    db.execute("UPDATE points SET points=?,name=?,username=? WHERE chat_id=? AND user_id=?",
               (new, name, uname, chat_id, user.id))
    return new


async def _check_milestone(context, chat_id, uid, total, name=None):
    """Call the points module's Race-to-369 checker (shared bundle namespace)."""
    fn = globals().get("check_milestone")
    if fn is None:
        try:
            fn = _p.check_milestone
        except Exception:  # noqa: BLE001
            return
    await fn(context, chat_id, uid, total, name)


# --------------------------- /spin (daily slot) ----------------------------
SPIN_DAILY_MAX = 3
# Telegram 🎰 reel symbols, index 0..3 (matches the value decode below)
_SLOT_SYMBOLS = ["🍫", "🍇", "🍋", "7️⃣"]  # BAR, grapes, lemon, seven


def _decode_slot(value):
    """Decode Telegram 🎰 dice value (1..64) into 3 reel symbol indices."""
    v = value - 1
    return [v & 0b11, (v >> 2) & 0b11, (v >> 4) & 0b11]


def _slot_payout(reels):
    """Return (reward, headline) for a set of 3 reel indices. Payouts kept modest."""
    sevens = reels.count(3)
    if sevens == 3:
        return 36, "💰🎰 <b>MEGA JACKPOT — 777!!!</b> 🎰💰\nThe current is OVERFLOWING ⚡⚡⚡"
    counts = {}
    for r in reels:
        counts[r] = counts.get(r, 0) + 1
    best = max(counts.values())
    if best == 3:
        return 18, "🎉 <b>TRIPLE MATCH!</b> Three of a kind — huge! ⚡"
    if best == 2 and sevens >= 1:
        return 9, "🔥 <b>Double + Lucky 7!</b> Nice pull ⚡"
    if best == 2:
        return 6, "✨ <b>Two of a kind!</b> Small win ⚡"
    if sevens >= 1:
        return 3, "🍀 A lucky 7 landed — small charge ⚡"
    return 1, "🎰 No match this time — spin again! ⚡"


def _slot_payout_table():
    return (
        "🎰 <b>PAYOUT TABLE</b>\n"
        "7️⃣7️⃣7️⃣ Mega Jackpot → <b>+36</b>\n"
        "Any triple → <b>+18</b>\n"
        "Pair + 7️⃣ → <b>+9</b>\n"
        "Any pair → <b>+6</b>\n"
        "A single 7️⃣ → <b>+3</b>\n"
        "No match → <b>+1</b>"
    )


@games_only
async def spin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if not user or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("🎰 Spin inside the group. ⚡")
        return
    chat_id = target_chat(update)
    today = _today()
    row = db.query(
        "SELECT day, count FROM daily_use WHERE chat_id=? AND user_id=? AND game='spin'",
        (chat_id, user.id), one=True)
    used = row["count"] if (row and row["day"] == today and row["count"]) else 0
    if used >= SPIN_DAILY_MAX:
        await update.effective_message.reply_text(
            f"🎰 You've used all <b>{SPIN_DAILY_MAX}</b> spins today — the machine's "
            f"resting. Come back tomorrow! ⚡\n\n{_slot_payout_table()}",
            parse_mode=ParseMode.HTML)
        return
    # spin the native animated slot machine
    try:
        dm = await context.bot.send_dice(chat.id, emoji="🎰")
        val = dm.dice.value
    except Exception:  # noqa: BLE001
        val = random.randint(1, 64)
    # record the spin (resets count automatically on a new day)
    db.execute(
        "INSERT INTO daily_use (chat_id,user_id,game,day,count) VALUES (?,?,'spin',?,1) "
        "ON CONFLICT(chat_id,user_id,game) DO UPDATE SET day=excluded.day, "
        "count=CASE WHEN daily_use.day=excluded.day THEN daily_use.count+1 ELSE 1 END",
        (chat_id, user.id, today))
    spins_left = SPIN_DAILY_MAX - (used + 1)

    reels = _decode_slot(val)
    reel_str = " | ".join(_SLOT_SYMBOLS[i] for i in reels)
    reward, headline = _slot_payout(reels)
    total = _award(chat_id, user, reward)

    left_line = (f"🎟 Spins left today: <b>{spins_left}</b>/{SPIN_DAILY_MAX}"
                 if spins_left > 0 else "🎟 That was your last spin today!")
    await update.effective_message.reply_text(
        f"{headline}\n\n"
        f"〜〜〜〜〜〜〜〜〜\n"
        f"   {reel_str}\n"
        f"〜〜〜〜〜〜〜〜〜\n\n"
        f"💸 Won <b>+{reward}</b> → <b>{total:,}</b> ⚡ZAPP credits\n"
        f"{left_line}\n∞ 3 · 6 · 9 ∞",
        parse_mode=ParseMode.HTML)
    await _check_milestone(context, chat_id, user.id, total, user.first_name)


# --------------------------- quick games -----------------------------------
@games_only
async def flip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = target_chat(update)
    result = random.choice(["HEADS", "TAILS"])
    points = 1
    total = _award(chat_id, user, points) if user else None
    footer = f"\n\n+{points} point → <b>{total:,}</b> ⚡" if total else ""
    await update.effective_message.reply_text(
        f"🪙 <b>{result}</b> ⚡{footer}", parse_mode=ParseMode.HTML)


@games_only
async def roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = target_chat(update)
    try:
        m = await context.bot.send_dice(update.effective_chat.id, emoji="🎲")
        val = m.dice.value if m and m.dice else random.randint(1, 6)
    except Exception:  # noqa: BLE001
        val = random.randint(1, 6)
    await asyncio.sleep(3)
    points = 6 if val == 6 else (3 if val == 5 else 1)
    flavor = "🎲 SIX! Lucky charge ⚡" if val == 6 else (
        "🎲 Five — close to the top ⚡" if val == 5 else f"🎲 Rolled <b>{val}</b> ⚡")
    total = _award(chat_id, user, points) if user else None
    footer = f"\n+{points} point{'s' if points != 1 else ''} → <b>{total:,}</b> ⚡" if total else ""
    await update.effective_message.reply_text(
        f"{flavor}{footer}", parse_mode=ParseMode.HTML)


@games_only
async def dart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = target_chat(update)
    try:
        m = await context.bot.send_dice(update.effective_chat.id, emoji="🎯")
        val = m.dice.value if m and m.dice else random.randint(1, 6)
    except Exception:  # noqa: BLE001
        val = random.randint(1, 6)
    await asyncio.sleep(3)
    if val == 6:
        flavor, points = "🎯 <b>BULLSEYE!</b> Dead center ⚡", 6
    elif val >= 4:
        flavor, points = "🎯 Solid hit on the board ⚡", 3
    else:
        flavor, points = "🎯 Off the mark — try again ⚡", 1
    total = _award(chat_id, user, points) if user else None
    footer = f"\n+{points} point{'s' if points != 1 else ''} → <b>{total:,}</b> ⚡" if total else ""
    await update.effective_message.reply_text(
        f"{flavor}{footer}", parse_mode=ParseMode.HTML)


@games_only
async def basket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = target_chat(update)
    try:
        m = await context.bot.send_dice(update.effective_chat.id, emoji="🏀")
        val = m.dice.value if m and m.dice else random.randint(1, 5)
    except Exception:  # noqa: BLE001
        val = random.randint(1, 5)
    await asyncio.sleep(3)
    # native basket dice: 4 and 5 are made shots
    if val >= 4:
        flavor, points = "🏀 <b>SWISH!</b> Nothing but net ⚡", 6
    elif val == 3:
        flavor, points = "🏀 Rolls in off the rim ⚡", 3
    else:
        flavor, points = "🧤 Brick — off the backboard ⚡", 1
    total = _award(chat_id, user, points) if user else None
    footer = f"\n+{points} point{'s' if points != 1 else ''} → <b>{total:,}</b> ⚡" if total else ""
    await update.effective_message.reply_text(
        f"{flavor}{footer}", parse_mode=ParseMode.HTML)


@games_only
async def football(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """⚽ Penalty shot — native football dice. Values 3/4/5 = GOAL."""
    user = update.effective_user
    chat_id = target_chat(update)
    try:
        m = await context.bot.send_dice(update.effective_chat.id, emoji="⚽")
        val = m.dice.value if m and m.dice else 0
    except Exception:  # noqa: BLE001
        val = 0
    await asyncio.sleep(3)
    goal = val >= 3
    flavor = ("⚽ <b>GOOOAL!</b> ⚡ Back of the net! 🥅" if goal
              else "🧤 <b>SAVED!</b> Keeper denies you — try again ⚡")
    points = 6 if goal else 1
    total = _award(chat_id, user, points) if user else None
    footer = f"\n+{points} point{'s' if points != 1 else ''} → <b>{total:,}</b> ⚡" if total else ""
    await update.effective_message.reply_text(
        f"{flavor}{footer}", parse_mode=ParseMode.HTML)


_8BALL = [
    "Yes. ⚡", "Absolutely.", "No doubt about it.", "The signal says yes.",
    "Ask again later.", "Hmm... unclear.", "Don't count on it.", "No.",
    "The current points to yes.", "3 · 6 · 9 says... maybe.",
]


@games_only
async def eightball(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    if not q:
        await update.effective_message.reply_text("🎱 Ask me a yes/no question: /8ball will we moon?")
        return
    await update.effective_message.reply_text("🎱 " + random.choice(_8BALL))


@games_only
async def rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thing = " ".join(context.args).strip()
    if not thing:
        await update.effective_message.reply_text("Usage: /rate <something>")
        return
    n = random.randint(1, 10)
    extra = " 🔥" if n >= 8 else (" 💀" if n <= 3 else "")
    await update.effective_message.reply_text(
        f"I rate {esc(thing)} a <b>{n}/10</b>{extra}", parse_mode=ParseMode.HTML)


# --------------------------- /trivia (button quiz) -------------------------
# (question, options, correct_index)
_TRIVIA = [
    ("⚡ What number sequence is ZAPP built on?",
     ["1 · 2 · 3", "3 · 6 · 9", "7 · 7 · 7", "4 · 2 · 0"], 1),
    ("⚡ Which blockchain is ⚡ZAPP on?",
     ["Ethereum", "Solana", "BNB", "Bitcoin"], 1),
    ("⚡ Who inspired the ZAPP theme?",
     ["Edison", "Einstein", "Nikola Tesla", "Newton"], 2),
    ("⚡ ZAPP's tagline: Free Energy = ___?",
     ["Free Money", "Free Lunch", "Free Time", "Free Wifi"], 0),
    ("What does 'CA' stand for in crypto?",
     ["Cash App", "Contract Address", "Crypto Account", "Coin Alert"], 1),
    ("What wallet is most popular on Solana?",
     ["MetaMask", "Phantom", "Trust", "Ledger"], 1),
    ("What does 'gm' mean in crypto culture?",
     ["Good Move", "Good Morning", "Get Money", "Gas Mode"], 1),
    ("What's a 'rug pull'?",
     ["A big buy", "A scam exit by devs", "A price chart", "A type of wallet"], 1),
    ("⚡ What does the 'ZAPP' lightning stand for?",
     ["Energy", "Speed", "Anger", "Rain"], 0),
    ("How many lightning bolts in 3·6·9 philosophy's core?",
     ["3, 6, 9", "1, 2, 3", "9, 9, 9", "6, 6, 6"], 0),
    ("What is 'HODL'?",
     ["Sell fast", "Hold long-term", "A wallet", "A DEX"], 1),
    ("What's an airdrop?",
     ["Free token distribution", "A price crash", "A type of chart", "A scam only"], 0),
    ("Which is a Solana DEX aggregator?",
     ["Uniswap", "Jupiter", "PancakeSwap", "SushiSwap"], 1),
    ("What does 'DYOR' mean?",
     ["Do Your Own Research", "Don't Yell Or Run", "Daily Yield Of Returns", "Drop Your Old Rug"], 0),
    ("What's 'market cap'?",
     ["Price × supply", "Daily volume", "Number of holders", "Liquidity only"], 0),
    ("What was Tesla's dream for energy?",
     ["Expensive grids", "Free wireless power", "Oil only", "Coal plants"], 1),
    ("What's a 'whale' in crypto?",
     ["A tiny holder", "A large holder", "A scammer", "A developer"], 1),
    ("⚡ Where do you buy ⚡ZAPP safely?",
     ["Random DMs", "The official CA only", "Any lookalike", "Email links"], 1),
    ("What does 'LP' mean?",
     ["Liquidity Pool", "Long Position", "Low Price", "Last Pump"], 0),
    ("What's 'FOMO'?",
     ["Fear Of Missing Out", "Found On My Own", "Fast Order Market Open", "Free Online Money"], 0),
    ("What's 'diamond hands'?",
     ["Selling quickly", "Holding through dips", "A type of wallet", "A mining rig"], 1),
]

# active trivia per chat: {chat_id: {"answer": idx, "msg_id": id, "solved": bool}}
_active_trivia = {}


@games_only
@group_only
@admin_only
async def trivia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    q, opts, correct = random.choice(_TRIVIA)
    letters = ["🅰️", "🅱️", "🇨", "🇩"]
    kb = [[InlineKeyboardButton(f"{letters[i]} {opts[i]}",
                                callback_data=f"trv:{i}")] for i in range(len(opts))]
    m = await update.effective_message.reply_text(
        f"🧠 <b>ZAPP Trivia</b>\n\n{q}\n\nFirst correct answer wins <b>9</b> points ⚡",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    _active_trivia[chat_id] = {"answer": correct, "msg_id": m.message_id,
                               "solved": False, "q": q, "opts": opts}


async def trivia_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    chat_id = q.message.chat.id
    state = _active_trivia.get(chat_id)
    if not state or state["msg_id"] != q.message.message_id:
        await q.answer("This round is over. ⚡")
        return
    if state["solved"]:
        await q.answer("Someone already got it! ⚡")
        return
    picked = int(q.data.split(":")[1])
    if picked != state["answer"]:
        await q.answer("❌ Not quite — let someone else try!")
        return
    # correct + first
    state["solved"] = True
    user = q.from_user
    total = _award(chat_id, user, 9)
    await q.answer("✅ Correct! +9 points")
    right = state["opts"][state["answer"]]
    try:
        await q.edit_message_text(
            f"🧠 <b>ZAPP Trivia</b>\n\n{state['q']}\n\n"
            f"✅ Answer: <b>{esc(right)}</b>\n"
            f"🏆 {mention(user)} got it first! +9 points → <b>{total:,}</b> ⚡",
            parse_mode=ParseMode.HTML)
    except BadRequest:
        pass
    await _check_milestone(context, chat_id, user.id, total, user.first_name)


# --------------------------- AUTO-TRIVIA (scheduled) -----------------------
# Posts a trivia question automatically at 9am, 12pm, 3pm, 6pm, 9pm.
# Routes to a chosen channel/topic (e.g. Contests & Giveaways), leaving General alone.
AUTOTRIVIA_SLOTS = [
    ("t9", 9, 0),
    ("t12", 12, 0),
    ("t15", 15, 0),
    ("t18", 18, 0),
    ("t21", 21, 0),
]


async def _post_trivia(context, chat_id, thread=None):
    """Post one trivia round to chat_id (optionally in a forum topic/thread)."""
    q, opts, correct = random.choice(_TRIVIA)
    letters = ["🅰️", "🅱️", "🇨", "🇩"]
    kb = [[InlineKeyboardButton(f"{letters[i]} {opts[i]}",
                                callback_data=f"trv:{i}")] for i in range(len(opts))]
    kwargs = {"parse_mode": ParseMode.HTML,
              "reply_markup": InlineKeyboardMarkup(kb)}
    if thread:
        kwargs["message_thread_id"] = thread
    try:
        m = await context.bot.send_message(
            chat_id,
            f"🧠 <b>ZAPP Trivia</b> ⚡\n\n{q}\n\nFirst correct answer wins <b>9</b> points ⚡",
            **kwargs)
    except Exception:  # noqa: BLE001
        return
    _active_trivia[chat_id] = {"answer": correct, "msg_id": m.message_id,
                               "solved": False, "q": q, "opts": opts}
    # auto-reveal the answer after 5 minutes if nobody got it
    if context.job_queue:
        context.job_queue.run_once(
            _trivia_reveal_job, 300,
            data={"chat_id": chat_id, "msg_id": m.message_id},
            name=f"trvreveal_{chat_id}_{m.message_id}")


async def _trivia_reveal_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    chat_id = data.get("chat_id")
    msg_id = data.get("msg_id")
    state = _active_trivia.get(chat_id)
    if not state or state["msg_id"] != msg_id or state["solved"]:
        return
    state["solved"] = True
    right = state["opts"][state["answer"]]
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text=(f"🧠 <b>ZAPP Trivia</b> ⚡\n\n{state['q']}\n\n"
                  f"⏰ Time's up! Answer: <b>{esc(right)}</b>\n"
                  f"Next round soon — stay charged ⚡"),
            parse_mode=ParseMode.HTML)
    except Exception:  # noqa: BLE001
        pass


async def _autotrivia_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    chat_id = data.get("chat_id")
    if chat_id is None:
        return
    s = db.get_settings(chat_id)
    if not s["autotrivia_on"]:
        return
    await _post_trivia(context, chat_id, s["autotrivia_thread"])


def schedule_autotrivia(app, chat_id):
    """(Re)schedule the 5 daily auto-trivia rounds for one chat."""
    jq = app.job_queue
    if not jq:
        return
    for slot, _h, _m in AUTOTRIVIA_SLOTS:
        for j in jq.get_jobs_by_name(f"at_{chat_id}_{slot}"):
            j.schedule_removal()
    s = db.get_settings(chat_id)
    if not s["autotrivia_on"]:
        return
    tz = _ap_tz(s["autotrivia_tz"] or "Europe/Zurich")
    for slot, h, m in AUTOTRIVIA_SLOTS:
        t = dtime(h, m, tzinfo=tz) if tz else dtime(h, m)
        jq.run_daily(_autotrivia_job, t, data={"chat_id": chat_id, "slot": slot},
                     name=f"at_{chat_id}_{slot}")


def reschedule_autotrivia(app):
    try:
        rows = db.query("SELECT chat_id FROM settings WHERE autotrivia_on=1")
    except Exception:  # noqa: BLE001
        rows = []
    for r in rows:
        schedule_autotrivia(app, r["chat_id"])


# --------------------------- AUTO-LEADERBOARD (weekly) ---------------------
# Posts the top 10 every Sunday at 21:00 (Europe/Zurich by default).
async def _autoleaderboard_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    chat_id = data.get("chat_id")
    if chat_id is None:
        return
    s = db.get_settings(chat_id)
    if not s["autoleaderboard_on"]:
        return
    rows = db.query(
        "SELECT name, points, streak FROM points WHERE chat_id=? "
        "ORDER BY points DESC LIMIT 10", (chat_id,))
    if not rows:
        return
    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(rows):
        prefix = medals[i] if i < 3 else f"  {i+1}."
        streak = f" 🔥{r['streak']}" if r["streak"] else ""
        lines.append(f"{prefix} <b>{esc(r['name'] or 'anon')}</b> — {r['points']:,} pts{streak}")
    txt = (
        "🏆 <b>Weekly ⚡ZAPP Leaderboard</b> 🏆\n\n"
        + "\n".join(lines)
        + "\n\n⚡ Keep chatting, keep playing, keep climbing.\n"
        "<i>Race to the top resets nothing — every point counts.</i>"
    )
    kwargs = {"parse_mode": ParseMode.HTML, "disable_web_page_preview": True}
    thread = s["autoleaderboard_thread"]
    if thread:
        kwargs["message_thread_id"] = thread
    try:
        await context.bot.send_message(chat_id, txt, **kwargs)
    except Exception:  # noqa: BLE001
        pass


def schedule_autoleaderboard(app, chat_id):
    """(Re)schedule the weekly Sunday-21:00 leaderboard post for one chat."""
    jq = app.job_queue
    if not jq:
        return
    for j in jq.get_jobs_by_name(f"alb_{chat_id}"):
        j.schedule_removal()
    s = db.get_settings(chat_id)
    if not s["autoleaderboard_on"]:
        return
    tz = _ap_tz(s["autoleaderboard_tz"] or "Europe/Zurich")
    t = dtime(21, 0, tzinfo=tz) if tz else dtime(21, 0)
    # PTB run_daily days convention: 0=Sunday, 1=Monday, ..., 6=Saturday
    jq.run_daily(_autoleaderboard_job, t, days=(0,),
                 data={"chat_id": chat_id}, name=f"alb_{chat_id}")


def reschedule_autoleaderboard(app):
    try:
        rows = db.query("SELECT chat_id FROM settings WHERE autoleaderboard_on=1")
    except Exception:  # noqa: BLE001
        rows = []
    for r in rows:
        schedule_autoleaderboard(app, r["chat_id"])


@group_only
@admin_only
async def autoleaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    msg = update.effective_message
    args = [a for a in context.args]
    sub = args[0].lower() if args else "status"
    s = db.get_settings(chat_id)

    if sub in ("on", "here"):
        db.set_setting(chat_id, "autoleaderboard_on", 1)
        thread = getattr(msg, "message_thread_id", None)
        db.set_setting(chat_id, "autoleaderboard_thread", thread)
        schedule_autoleaderboard(context.application, chat_id)
        where = "this topic" if thread else "the group (General)"
        await msg.reply_text(
            f"🏆 <b>Auto-leaderboard ON</b> ⚡\nThe Top 10 will post every "
            f"<b>Sunday 9pm</b> ({s['autoleaderboard_tz'] or 'Europe/Zurich'}) in {where}.\n"
            "Try one now: /autoleaderboard test",
            parse_mode=ParseMode.HTML)
        return
    if sub == "off":
        db.set_setting(chat_id, "autoleaderboard_on", 0)
        schedule_autoleaderboard(context.application, chat_id)
        await msg.reply_text("🏆 Auto-leaderboard <b>OFF</b>.", parse_mode=ParseMode.HTML)
        return
    if sub == "test":
        # invoke the job inline with a minimal stub context
        class _Stub:
            def __init__(self, app, chat_id):
                self.bot = app.bot
                self.job = type("J", (), {"data": {"chat_id": chat_id}})
        await _autoleaderboard_job(_Stub(context.application, chat_id))
        return

    state = "ON ✅" if s["autoleaderboard_on"] else "OFF"
    await msg.reply_text(
        f"🏆 <b>Auto-leaderboard:</b> {state}\n"
        f"🕒 Sundays 9pm ({s['autoleaderboard_tz'] or 'Europe/Zurich'})\n\n"
        "Commands:\n"
        "/autoleaderboard here — enable in THIS topic\n"
        "/autoleaderboard off — disable\n"
        "/autoleaderboard test — post one now",
        parse_mode=ParseMode.HTML)


@group_only
@admin_only
async def autotrivia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    msg = update.effective_message
    args = [a for a in context.args]
    sub = args[0].lower() if args else "status"
    s = db.get_settings(chat_id)

    if sub in ("on", "here"):
        db.set_setting(chat_id, "autotrivia_on", 1)
        thread = getattr(msg, "message_thread_id", None)
        db.set_setting(chat_id, "autotrivia_thread", thread)
        schedule_autotrivia(context.application, chat_id)
        where = "this topic" if thread else "the group (General)"
        await msg.reply_text(
            f"🧠 <b>Auto-trivia ON</b> ⚡\nRounds at <b>9am, 12pm, 3pm, 6pm, 9pm</b> "
            f"({s['autotrivia_tz'] or 'Europe/Zurich'}) in {where}.\n"
            "Run this inside your Contests &amp; Giveaways topic to post there.\n"
            "Change zone: /autotrivia tz Europe/London\nTry one now: /autotrivia test",
            parse_mode=ParseMode.HTML)
        return
    if sub == "off":
        db.set_setting(chat_id, "autotrivia_on", 0)
        schedule_autotrivia(context.application, chat_id)
        await msg.reply_text("🧠 Auto-trivia <b>OFF</b>.", parse_mode=ParseMode.HTML)
        return
    if sub == "tz" and len(args) >= 2:
        tzname = args[1]
        if _ap_tz(tzname) is None and ZoneInfo is not None:
            await msg.reply_text(
                "❓ Unknown timezone. Use e.g. <code>Europe/Zurich</code> or <code>UTC</code>.",
                parse_mode=ParseMode.HTML)
            return
        db.set_setting(chat_id, "autotrivia_tz", tzname)
        if s["autotrivia_on"]:
            schedule_autotrivia(context.application, chat_id)
        await msg.reply_text(f"🕒 Auto-trivia timezone set to <b>{tzname}</b>.",
                             parse_mode=ParseMode.HTML)
        return
    if sub == "test":
        thread = getattr(msg, "message_thread_id", None)
        await _post_trivia(context, chat_id, thread)
        return

    state = "ON ✅" if s["autotrivia_on"] else "OFF"
    await msg.reply_text(
        f"🧠 <b>Auto-trivia:</b> {state}\n"
        f"🕒 Times: 9am, 12pm, 3pm, 6pm, 9pm ({s['autotrivia_tz'] or 'Europe/Zurich'})\n\n"
        "Commands:\n"
        "/autotrivia here — enable in THIS topic/channel\n"
        "/autotrivia off — disable\n"
        "/autotrivia tz &lt;Zone&gt; — set timezone\n"
        "/autotrivia test — post one round now",
        parse_mode=ParseMode.HTML)
async def greet_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return
    word = msg.text.strip().lower().strip("!. ")
    if word in ("gm", "good morning", "gm fam", "gm gm"):
        await msg.reply_text(random.choice(
            ["gm ⚡", "gm fam ⚡ the current is flowing", "gm ⚡ 3 · 6 · 9", "gm ⚡⚡"]))
    elif word in ("gn", "good night", "gn fam"):
        await msg.reply_text(random.choice(
            ["gn ⚡ rest up, signal stays on", "gn fam ⚡", "gn ⚡ 3 · 6 · 9"]))


# --------------------------- more games (pure fun, no points) --------------
@games_only
async def rps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rock paper scissors vs the bot — tap a button."""
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🪨 Rock", callback_data="rps:rock"),
        InlineKeyboardButton("📄 Paper", callback_data="rps:paper"),
        InlineKeyboardButton("✂️ Scissors", callback_data="rps:scissors"),
    ]])
    await update.effective_message.reply_text(
        "✊ <b>Rock · Paper · Scissors</b> ⚡\nPick your move:",
        parse_mode=ParseMode.HTML, reply_markup=kb)


_RPS_EMOJI = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
_RPS_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


async def rps_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    you = q.data.split(":")[1]
    bot_pick = random.choice(list(_RPS_EMOJI))
    if you == bot_pick:
        result = "🤝 It's a draw!"
    elif _RPS_BEATS[you] == bot_pick:
        result = "🏆 You win! ⚡"
    else:
        result = "🤖 Bot wins! Try again."
    await q.answer()
    try:
        await q.edit_message_text(
            f"✊ <b>Rock · Paper · Scissors</b>\n\n"
            f"You: {_RPS_EMOJI[you]}   vs   Bot: {_RPS_EMOJI[bot_pick]}\n\n{result}",
            parse_mode=ParseMode.HTML)
    except BadRequest:
        pass


@games_only
async def guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guess a number 1–10. /guess 7"""
    args = context.args
    if not args or not args[0].lstrip("-").isdigit():
        await update.effective_message.reply_text(
            "🔢 Guess a number 1–10: <code>/guess 7</code>", parse_mode=ParseMode.HTML)
        return
    pick = int(args[0])
    n = random.randint(1, 10)
    if pick == n:
        msg = f"🎯 <b>{n}</b> — spot on! You read the frequency. ⚡"
    elif abs(pick - n) == 1:
        msg = f"🔥 So close! It was <b>{n}</b>."
    else:
        msg = f"❌ It was <b>{n}</b>. Try again!"
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)


@games_only
@group_only
async def duel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Challenge another member to a ⚡ dice duel — reply to them with /duel."""
    msg = update.effective_message
    reply = msg.reply_to_message
    if not reply or not reply.from_user:
        await msg.reply_text("⚔️ Reply to someone with <b>/duel</b> to challenge them!",
                             parse_mode=ParseMode.HTML)
        return
    challenger = update.effective_user
    target = reply.from_user
    if target.is_bot:
        await msg.reply_text("🤖 You can't duel a bot. Pick a real challenger!")
        return
    a, b = random.randint(1, 6), random.randint(1, 6)
    while a == b:
        a, b = random.randint(1, 6), random.randint(1, 6)
    winner = challenger if a > b else target
    await msg.reply_text(
        f"⚔️ <b>⚡ DUEL ⚡</b>\n\n"
        f"{mention(challenger)} rolled 🎲 <b>{a}</b>\n"
        f"{mention(target)} rolled 🎲 <b>{b}</b>\n\n"
        f"🏆 {mention(winner)} wins the charge! ⚡",
        parse_mode=ParseMode.HTML)


_FORTUNES = [
    "The current favors the bold today. ⚡",
    "Diamond hands shall be rewarded. 💎",
    "3 · 6 · 9 — your numbers are aligning. 🔮",
    "Patience now, power later. The signal builds. 🔌",
    "Today is a good day to spread the signal. 📣",
    "A green candle approaches... maybe. 🕯️📈",
    "The energy is strong. Stay plugged in. ⚡",
    "Fortune favors those who HODL. 🙌",
]


@games_only
async def fortune(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🔮 <b>⚡ZAPP Fortune</b>\n" + random.choice(_FORTUNES),
        parse_mode=ParseMode.HTML)


# --------------------------- translator ------------------------------------
# Common language codes for friendly help text
_TR_COMMON = {
    "en": "English", "es": "Spanish", "ro": "Romanian", "fr": "French",
    "de": "German", "it": "Italian", "pt": "Portuguese", "ru": "Russian",
    "tr": "Turkish", "ar": "Arabic", "hi": "Hindi", "zh-CN": "Chinese",
    "ja": "Japanese", "ko": "Korean", "nl": "Dutch", "pl": "Polish",
    "uk": "Ukrainian", "id": "Indonesian", "vi": "Vietnamese", "fa": "Persian",
}


async def _do_translate(text, target):
    """Translate via Google's free endpoint over httpx. Returns (text, detected_src)."""
    params = {"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": text[:4500]}
    url = "https://translate.googleapis.com/translate_a/single"
    async with httpx.AsyncClient(timeout=12) as client:
        r = await client.get(url, params=params,
                             headers={"User-Agent": "Mozilla/5.0 (ZAPPbot)"})
        r.raise_for_status()
        data = r.json()
    # data[0] = list of segments; each seg[0] is a translated chunk
    segments = data[0] if data and isinstance(data[0], list) else []
    translated = "".join(seg[0] for seg in segments if seg and seg[0])
    detected = data[2] if len(data) > 2 and isinstance(data[2], str) else "auto"
    return translated, detected


async def translate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    args = list(context.args)
    reply = msg.reply_to_message
    target = "en"  # default: translate into English

    # optional leading language code, e.g. /tr es  or  /tr es hola
    if args and re.fullmatch(r"[a-zA-Z]{2}(-[a-zA-Z]{2})?", args[0]) and (reply or len(args) > 1):
        target = args[0].lower()
        if target == "zh":
            target = "zh-CN"
        args = args[1:]

    if args:
        text = " ".join(args)
    elif reply:
        text = reply.text or reply.caption
    else:
        text = None

    if not text:
        codes = ", ".join(list(_TR_COMMON)[:10])
        await msg.reply_text(
            "🌐 <b>Translator</b>\n"
            "• Reply to a message with <code>/tr</code> → translate to English\n"
            "• <code>/tr es</code> (reply) → translate to Spanish\n"
            "• <code>/tr ro Good morning</code> → translate text to Romanian\n\n"
            f"Common codes: {codes} …",
            parse_mode=ParseMode.HTML)
        return

    try:
        translated, detected = await _do_translate(text, target)
    except Exception:  # noqa: BLE001
        await msg.reply_text(
            "🌐 Couldn't reach the translator right now — please try again in a moment.",
            parse_mode=ParseMode.HTML)
        return

    if not translated:
        await msg.reply_text("🌐 Nothing to translate there.")
        return

    lang_name = _TR_COMMON.get(target, target)
    src_name = _TR_COMMON.get(detected, detected)
    await msg.reply_text(
        f"🌐 <b>{esc(src_name)} → {esc(lang_name)}</b>\n{esc(translated)}",
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# ===========================================================================
#  SPREAD THE SIGNAL — shill-to-earn (social tasks + one-tap admin approval)
# ===========================================================================
# task -> (display label, reward points, max per day)
SOCIAL_TASKS = {
    "twitter":  ("𝕏 Repost / Quote", 30, 1),
    "rt":       ("𝕏 Repost / Quote", 30, 1),
    "repost":   ("𝕏 Repost / Quote", 30, 1),
    "tweet":    ("𝕏 Original tweet", 40, 1),
    "story":    ("📸 Instagram/Story", 25, 1),
    "insta":    ("📸 Instagram/Story", 25, 1),
    "tiktok":   ("🎵 TikTok post", 40, 1),
    "share":    ("📤 Group share (WhatsApp/TG)", 20, 9),
    "whatsapp": ("📤 WhatsApp share", 20, 9),
    "like":     ("❤️ Like", 10, 9),
    "comment":  ("💬 Comment", 15, 9),
    "meme":     ("😂 Meme creation", 50, 9),
    "gif":      ("🎞 GIF creation", 50, 9),
    "sticker":  ("🩷 Sticker creation", 50, 9),
}
SOCIAL_DEFAULT = ("✨ Shill / proof", 20, 9)


def _social_task(name):
    if not name:
        return SOCIAL_DEFAULT, "shill"
    key = name.lower().lstrip("/#")
    if key in SOCIAL_TASKS:
        return SOCIAL_TASKS[key], key
    return SOCIAL_DEFAULT, "shill"


def _social_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _social_count_today(chat_id, uid, task):
    start = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                   microsecond=0).timestamp())
    row = db.query(
        "SELECT COUNT(*) c FROM submissions WHERE chat_id=? AND user_id=? AND task=? "
        "AND created>=? AND status IN ('pending','approved')",
        (chat_id, uid, task, start), one=True)
    return row["c"] if row else 0


async def tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the Spread-the-Signal reward menu."""
    lines = ["⚡ <b>SPREAD THE SIGNAL — earn points</b> ⚡",
             "Do the action, then reply to your proof (screenshot/link) with "
             "<code>/submit &lt;type&gt;</code>. An admin approves → you get points. 🔌\n"]
    seen = set()
    order = ["tweet", "twitter", "story", "tiktok", "share", "meme", "gif",
             "sticker", "like", "comment"]
    for k in order:
        label, reward, cap = SOCIAL_TASKS[k]
        if label in seen:
            continue
        seen.add(label)
        lines.append(f"• <code>/submit {k}</code> — {label}  →  <b>+{reward}</b> "
                     f"(max {cap}/day)")
    lines.append("\nExample: repost our tweet, screenshot it, reply to the "
                 "screenshot with <code>/submit twitter</code> ✅")
    lines.append("Points feed /top and the 🏁 Race to 369. ∞ 3 · 6 · 9 ∞")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@group_only
async def submit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    user = update.effective_user
    reply = msg.reply_to_message
    if not reply:
        await msg.reply_text(
            "📨 <b>How to submit:</b> do the action, then <b>reply to your proof</b> "
            "(a screenshot, your tweet link, the meme/GIF/sticker) with "
            "<code>/submit &lt;type&gt;</code>.\nSee all tasks &amp; rewards: /tasks",
            parse_mode=ParseMode.HTML)
        return
    (label, reward, cap), taskkey = _social_task(context.args[0] if context.args else None)
    # daily cap
    if _social_count_today(chat_id, user.id, taskkey) >= cap:
        await msg.reply_text(
            f"⏳ You've hit today's limit for <b>{label}</b> ({cap}/day). "
            "Try another task or come back tomorrow. /tasks",
            parse_mode=ParseMode.HTML)
        return
    # too many pending overall?
    pend = db.query("SELECT COUNT(*) c FROM submissions WHERE chat_id=? AND user_id=? "
                    "AND status='pending'", (chat_id, user.id), one=True)
    if pend and pend["c"] >= 9:
        await msg.reply_text("⏳ You have several submissions awaiting review — "
                             "let admins catch up first. 🙏")
        return
    name = user.first_name or "member"
    with db._conn() as _c:
        cur = _c.execute(
            "INSERT INTO submissions (chat_id,user_id,name,task,reward,status,created) "
            "VALUES (?,?,?,?,?, 'pending', ?)",
            (chat_id, user.id, name, taskkey, reward, int(time.time())))
        sid = cur.lastrowid
        _c.commit()
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"sub:ok:{sid}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"sub:no:{sid}"),
    ]])
    # post the review card as a reply to the proof so admins see it in context
    await reply.reply_text(
        f"🗳 <b>Submission #{sid}</b>\n"
        f"From: {mention(user)}\n"
        f"Task: {label}  →  <b>+{reward}</b> points\n"
        f"👆 proof above. Admins, review:",
        parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)
    await msg.reply_text("📨 Submitted for review! Admins will approve shortly. ⚡")


async def submit_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    chat_id = q.message.chat.id
    if not await is_admin(chat_id, q.from_user.id, context):
        await q.answer("Admins only. ⚡", show_alert=True)
        return
    try:
        _, action, sid_s = q.data.split(":")
        sid = int(sid_s)
    except (ValueError, IndexError):
        await q.answer()
        return
    row = db.query("SELECT * FROM submissions WHERE id=?", (sid,), one=True)
    if not row:
        await q.answer("Not found.")
        return
    if row["status"] != "pending":
        await q.answer(f"Already {row['status']}.")
        return
    label = _social_task(row["task"])[0][0]
    if action == "no":
        db.execute("UPDATE submissions SET status='rejected',reviewed_by=?,reviewed_at=? "
                   "WHERE id=?", (q.from_user.id, int(time.time()), sid))
        await q.answer("Rejected")
        try:
            await q.edit_message_text(
                f"❌ <b>Submission #{sid} rejected</b> ({label}) by {mention(q.from_user)}.",
                parse_mode=ParseMode.HTML)
        except BadRequest:
            pass
        return
    # approve -> award
    db.execute("UPDATE submissions SET status='approved',reviewed_by=?,reviewed_at=? "
               "WHERE id=?", (q.from_user.id, int(time.time()), sid))
    new = _apply_points(chat_id, row["user_id"], row["reward"], row["name"])
    await q.answer(f"✅ +{row['reward']} awarded")
    try:
        await q.edit_message_text(
            f"✅ <b>Submission #{sid} approved!</b> ({label})\n"
            f"🎁 {mention_id(row['user_id'], row['name'])} earned "
            f"<b>+{row['reward']}</b> → <b>{new:,}</b> total ⚡\n"
            f"Approved by {mention(q.from_user)}",
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except BadRequest:
        pass
    await check_milestone(context, chat_id, row["user_id"], new, row["name"])


@group_only
@admin_only
async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = target_chat(update)
    rows = db.query("SELECT * FROM submissions WHERE chat_id=? AND status='pending' "
                    "ORDER BY created ASC LIMIT 15", (chat_id,))
    if not rows:
        await update.effective_message.reply_text("✅ No pending submissions. All clear! ⚡")
        return
    lines = ["🗳 <b>Pending submissions</b>:"]
    for r in rows:
        label = _social_task(r["task"])[0][0]
        lines.append(f"#{r['id']} — {esc(r['name'] or 'member')} — {label} (+{r['reward']})")
    lines.append("\nApprove/reject with the buttons on each submission card, or "
                 "/approve &lt;id&gt; · /reject &lt;id&gt;.")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML)


@group_only
@admin_only
async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _review_by_id(update, context, approve=True)


@group_only
@admin_only
async def reject_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _review_by_id(update, context, approve=False)


async def _review_by_id(update, context, approve):
    msg = update.effective_message
    chat_id = target_chat(update)
    if not context.args or not context.args[0].lstrip("#").isdigit():
        await msg.reply_text("Usage: /approve <id>  (see /pending)")
        return
    sid = int(context.args[0].lstrip("#"))
    row = db.query("SELECT * FROM submissions WHERE id=? AND chat_id=?", (sid, chat_id),
                   one=True)
    if not row:
        await msg.reply_text("Submission not found.")
        return
    if row["status"] != "pending":
        await msg.reply_text(f"Already {row['status']}.")
        return
    label = _social_task(row["task"])[0][0]
    if not approve:
        db.execute("UPDATE submissions SET status='rejected',reviewed_by=?,reviewed_at=? "
                   "WHERE id=?", (update.effective_user.id, int(time.time()), sid))
        await msg.reply_text(f"❌ #{sid} rejected ({label}).")
        return
    db.execute("UPDATE submissions SET status='approved',reviewed_by=?,reviewed_at=? "
               "WHERE id=?", (update.effective_user.id, int(time.time()), sid))
    new = _apply_points(chat_id, row["user_id"], row["reward"], row["name"])
    await msg.reply_text(
        f"✅ #{sid} approved ({label}) — {mention_id(row['user_id'], row['name'])} "
        f"earned +{row['reward']} → <b>{new:,}</b> ⚡", parse_mode=ParseMode.HTML)
    await check_milestone(context, chat_id, row["user_id"], new, row["name"])


def register_social(app):
    app.add_handler(CommandHandler(["tasks", "earn", "quests"], tasks_cmd))
    app.add_handler(CommandHandler(["submit", "shill", "proof"], submit_cmd))
    app.add_handler(CommandHandler(["pending", "queue"], pending_cmd))
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("reject", reject_cmd))
    app.add_handler(CallbackQueryHandler(submit_cb, pattern=r"^sub:(ok|no):\d+$"))


def register_fun(app):
    app.add_handler(CommandHandler("spin", spin))
    app.add_handler(CommandHandler(["flip", "coinflip"], flip))
    app.add_handler(CommandHandler("roll", roll))
    app.add_handler(CommandHandler("dart", dart))
    app.add_handler(CommandHandler(["basket", "hoop"], basket))
    app.add_handler(CommandHandler(["football", "penalty", "soccer"], football))
    app.add_handler(CommandHandler(["autotrivia", "autoquiz"], autotrivia))
    app.add_handler(CommandHandler(["autoleaderboard", "autotop", "autolb"], autoleaderboard))
    app.add_handler(CommandHandler(["8ball", "eightball"], eightball))
    app.add_handler(CommandHandler("rate", rate))
    app.add_handler(CommandHandler(["rps", "rockpaperscissors"], rps))
    app.add_handler(CallbackQueryHandler(rps_cb, pattern=r"^rps:(rock|paper|scissors)$"))
    app.add_handler(CommandHandler("guess", guess))
    app.add_handler(CommandHandler(["duel", "fight"], duel))
    app.add_handler(CommandHandler(["fortune", "luck"], fortune))
    app.add_handler(CommandHandler(["tr", "translate", "trans"], translate_cmd))
    app.add_handler(CommandHandler(["trivia", "quiz"], trivia))
    app.add_handler(CallbackQueryHandler(trivia_cb, pattern=r"^trv:\d+$"))
    # gm/gn greet-back — own group so it never blocks moderation/points
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, greet_keyword), group=4)


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


# ===========================================================================
#  AUTO-GUARD — autonomous anti-scam (runs without an admin present)
# ===========================================================================
# High-precision scam patterns. Kept tight to avoid false-positives on real members.
_AG_PATTERNS = [
    (re.compile(r"\bseed phrase\b", re.I), "seed-phrase phishing"),
    (re.compile(r"\bprivate key\b", re.I), "private-key phishing"),
    (re.compile(r"\brecovery phrase\b", re.I), "recovery-phrase phishing"),
    (re.compile(r"\b(connect|verify|validate|sync|restore|import)\b.{0,16}\bwallet\b", re.I), "wallet phishing"),
    (re.compile(r"\bwallet\b.{0,16}\b(validation|verification|sync|connect|restore)\b", re.I), "wallet phishing"),
    (re.compile(r"\bclaim\b.{0,16}\bairdrop\b", re.I), "fake airdrop"),
    (re.compile(r"\bairdrop\b.{0,12}\bis (now )?live\b", re.I), "fake airdrop"),
    (re.compile(r"\bclaiming is (now )?live\b", re.I), "fake airdrop"),
    (re.compile(r"\bi('?m| am)\b.{0,14}\b(admin|mod|moderator|support|founder|the dev)\b", re.I), "admin impersonation"),
    (re.compile(r"\b(official|customer|technical)\s+support\b", re.I), "fake support"),
    (re.compile(r"\bdm\b.{0,8}\b(me|admin|support)\b.{0,18}\b(for|to|help|claim|issue|problem)\b", re.I), "DM bait"),
    (re.compile(r"\bsend\b.{0,6}\b\d.{0,12}\b(get|receive|double)\b.{0,12}\bback\b", re.I), "doubler scam"),
    (re.compile(r"\bwhatsapp\b.{0,12}(\+?\d[\d\s-]{7,})", re.I), "off-platform contact"),
]
_AG_FOREIGN_CA = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}pump\b")
_ag_hits = defaultdict(int)  # (chat_id, user_id) -> autoguard strikes this session


def _autoguard_reason(text):
    """Return a short reason string if text looks like a scam, else None."""
    if not text:
        return None
    for rx, reason in _AG_PATTERNS:
        if rx.search(text):
            return reason
    official = globals().get("CA")
    for m in _AG_FOREIGN_CA.findall(text):
        if official and m != official:
            return "foreign/fake contract address"
    return None


# ===========================================================================
# Personality system — ZAPP's in-character responses
# ===========================================================================
# Free, template-based "AI-style" personality. The bot responds in-character
# when @-mentioned, replied to, or when natural conversational triggers fire.
# Cooldown prevents spam; randomization keeps replies feeling fresh.

_zapp_last_reply: dict = {}      # (chat_id, user_id) -> timestamp
_zapp_chat_cooldown: dict = {}   # chat_id -> timestamp (global per-chat throttle)
PERSONALITY_USER_COOLDOWN = 30   # same user can't trigger more than once / 30s
PERSONALITY_CHAT_COOLDOWN = 8    # bot won't speak more than once / 8s in a chat

ZAPP_REPLIES = {
    "greet": [
        "GM {name} ⚡ welcome to the current",
        "ayy {name} 🔌 plugged in?",
        "{name} the lightning recognizes its own ⚡",
        "current detected. hello {name} ⚡",
        "GM fren — stay charged ⚡",
        "{name} good to see another node online 🔌",
    ],
    "gn": [
        "GN {name} ⚡ the current never sleeps tho",
        "rest well {name}, the chain runs all night ⚡",
        "{name} 🌙 dream in 3·6·9",
        "GN fren — see you on the next current ⚡",
    ],
    "moon": [
        "the moon is just a meter we haven't unplugged yet ⚡",
        "moon? we're building a tower, fren 🔌",
        "3·6·9 is the path. moon is a side effect ⚡",
        "wen real holders → wen everything {name} ⚡",
        "we don't promise moons, we promise current ⚡",
    ],
    "pump": [
        "the pump is whoever shows up today {name} ⚡",
        "we don't pump, we plug in 🔌",
        "real holders > pumped charts. always ⚡",
        "if you want pumps, you want elsewhere. if you want current, stay ⚡",
    ],
    "dump": [
        "red days fund green days {name} ⚡ stay charged",
        "the current doesn't care about candles 🔌",
        "weak hands fold, the tower stays ⚡",
    ],
    "rug": [
        "mint revoked. freeze revoked. fair launch. verify on Solscan, don't trust me ⚡",
        "rugs are the word that doesn't apply here {name} — check the chain ⚡",
        "not your average memecoin: every claim is checkable. that's the whole point ⚡",
    ],
    "scam": [
        "i'd say yes but don't trust me — trust Solscan {name} ⚡ verify everything",
        "anyone DMing you offering a 'team deal' is a scam. admins NEVER DM first ⚡",
        "verify the contract from /ca before doing anything {name} ⚡",
    ],
    "wagmi": [
        "WAGMI {name} ⚡ the current is with us",
        "we're all gonna make it. one current at a time ⚡",
        "{name} 🔌 NGMI is a vibe, WAGMI is a choice",
    ],
    "based": [
        "based {name} ⚡",
        "real recognizes real 🔌",
        "{name} gets it ⚡",
    ],
    "love": [
        "the current loves you back {name} ⚡",
        "{name} 🔌 same energy",
        "this is how movements start ⚡",
    ],
    "thanks": [
        "anytime {name} ⚡",
        "🔌 happy to help",
        "the current flows both ways {name} ⚡",
    ],
    "who": [
        "i'm ⚡ZAPP — the current. the spark. the 3·6·9 made tradeable.",
        "i'm what happens when free energy meets free money ⚡",
        "memecoin? frequency? lifestyle? yes ⚡",
    ],
    "lore": [
        "Tesla wanted to give the world free energy. they killed his tower.\nwe're the second tower. but you can't kill a token ⚡",
        "3 = the spark. 6 = the bloom. 9 = the network. that's the whole thesis ⚡",
        "ZAPP runs on what was already there. just nobody had named it yet ⚡",
    ],
    "price": [
        "type /price for the live one {name} ⚡",
        "live numbers in /price 🔌 always fresh",
    ],
    "buy": [
        "/buy has the contract + every link you need {name} ⚡",
        "🔌 /buy → tap CA → done",
    ],
    "default_mention": [
        "{name} ⚡ what's the current carrying today?",
        "🔌 here. what do you need {name}?",
        "{name} the lightning's listening ⚡",
        "ZAPP online. ask away {name} ⚡",
        "yo {name} 🔌",
    ],
    "default_reply": [
        "🔌 {name}",
        "{name} ⚡",
        "the current hears you {name} ⚡",
        "🔌",
    ],
}

_KEYWORD_TRIGGERS = [
    (r"\b(gm|good\s*morning)\b", "greet"),
    (r"\b(gn|good\s*night|goodnight)\b", "gn"),
    (r"\b(wen|when)\s+moon\b|\bmoon\s+wh?en\b|\bto\s+the\s+moon\b", "moon"),
    (r"\b(wen|when)\s+pump\b|\bpump\s+wh?en\b|\bpumpin?g?\b", "pump"),
    (r"\b(dump(ing|ed)?|crashing|bleeding)\b", "dump"),
    (r"\brug(\s*pull)?\b|\bgonna\s+rug\b", "rug"),
    (r"\b(scam|safe|legit|honest|trust(worthy)?)\b", "scam"),
    (r"\bwagmi\b|\bngmi\b", "wagmi"),
    (r"\b(based|legendary|chad)\b", "based"),
    (r"\b(i\s+love\s+zapp|love\s+this\s+coin|love\s+(it|u|you))\b", "love"),
    (r"\b(thank(s|\s+you)?|ty|tysm)\b", "thanks"),
    (r"\b(who\s+(are|r)\s+(u|you)|what\s+(are|r)\s+(u|you))\b", "who"),
    (r"\b(tell\s+me|story|lore|history|tesla|3\W*6\W*9|369)\b", "lore"),
    (r"\b(price|mcap|market\s*cap|how\s+much)\b", "price"),
    (r"\b(how\s+do\s+i\s+buy|where.*buy|wanna\s+buy|want\s+to\s+buy)\b", "buy"),
]


def _personality_bank(text: str, replied_to_bot: bool, mentioned_bot: bool) -> str:
    low = (text or "").lower()
    for pattern, bank in _KEYWORD_TRIGGERS:
        if re.search(pattern, low):
            return bank
    if mentioned_bot:
        return "default_mention"
    if replied_to_bot:
        return "default_reply"
    return ""


async def _personality_try_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """If the message triggers a personality reply, send one. Returns True if sent."""
    msg = update.effective_message
    user = update.effective_user
    chat_id = update.effective_chat.id
    text = (msg.text or msg.caption or "").strip()
    if not text or not user:
        return False

    bot_username = (context.bot.username or "").lower()
    mentioned_bot = bool(bot_username and f"@{bot_username}" in text.lower())
    replied_to_bot = bool(
        msg.reply_to_message
        and msg.reply_to_message.from_user
        and msg.reply_to_message.from_user.id == context.bot.id
    )

    bank = _personality_bank(text, replied_to_bot, mentioned_bot)
    if not bank:
        return False
    keyword_only = bank in ("greet", "gn")
    # Bare keyword triggers fire only when engaged (mention/reply) OR for gm/gn
    if not (mentioned_bot or replied_to_bot or keyword_only):
        return False

    now = time.time()
    last_user = _zapp_last_reply.get((chat_id, user.id), 0)
    last_chat = _zapp_chat_cooldown.get(chat_id, 0)
    if now - last_user < PERSONALITY_USER_COOLDOWN:
        return False
    if now - last_chat < PERSONALITY_CHAT_COOLDOWN:
        return False

    # gm/gn from random users: only respond ~1 in 4 to avoid being annoying
    if keyword_only and not (mentioned_bot or replied_to_bot):
        if random.random() > 0.25:
            return False

    options = ZAPP_REPLIES.get(bank) or ZAPP_REPLIES["default_mention"]
    template = random.choice(options)
    name = mention(user)
    reply_text = template.replace("{name}", name).replace("{brand}", BRAND)

    try:
        await msg.reply_text(reply_text, parse_mode=ParseMode.HTML,
                             disable_web_page_preview=True)
        _zapp_last_reply[(chat_id, user.id)] = now
        _zapp_chat_cooldown[chat_id] = now
        return True
    except BadRequest:
        return False


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
        if await _personality_try_reply(update, context):
            return
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
    # 5.5) Auto-Guard — autonomous anti-scam
    if s["autoguard_on"] and text:
        reason = _autoguard_reason(text)
        if reason:
            try:
                await msg.delete()
            except BadRequest:
                pass
            _ag_hits[(chat_id, user.id)] += 1
            hits = _ag_hits[(chat_id, user.id)]
            if hits >= 3:
                try:
                    verb = await do_action("mute", chat_id, user.id, context, seconds=86400)
                    await context.bot.send_message(
                        chat_id,
                        f"🛡️ {mention(user)} {verb} — repeated scam attempts "
                        f"({reason}). Stay safe, fam. ⚡", parse_mode=ParseMode.HTML)
                except BadRequest:
                    pass
            else:
                try:
                    await context.bot.send_message(
                        chat_id,
                        "🛡️ <b>Auto-Guard removed a likely scam</b> "
                        f"({esc(reason)}).\n⚠️ Admins NEVER DM first. Only ever use the "
                        "official CA from /ca. Never share your seed phrase. ⚡",
                        parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                except BadRequest:
                    pass
            return
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

    # 7) Personality — in-character ZAPP replies
    if await _personality_try_reply(update, context):
        return

    # 8) Filters
    await _run_filters(update, msg, chat_id)


async def _run_filters(update, msg, chat_id):
    if not msg.text:
        return
    low = msg.text.lower()
    words = set(re.findall(r"\w+", low))
    for r in db.query("SELECT keyword,reply FROM filters WHERE chat_id=?", (chat_id,)):
        kw = r["keyword"]
        if (" " in kw and kw in low) or (kw in words):
            reply = r["reply"] or ""
            # If the filter's reply contains the contract address, render it as
            # tap-to-copy (monospace) with the buy buttons — like /ca.
            _CA = globals().get("CA")
            _kbd = globals().get("_buy_keyboard")
            _escfn = globals().get("esc")
            if _CA and _kbd and _escfn and _CA in reply:
                from html import escape as _h
                before, _, after = reply.partition(_CA)
                body = (_h(before).strip() + "\n" if before.strip() else "")
                body += f"<code>{_escfn(_CA)}</code>"
                if after.strip():
                    body += "\n" + _h(after).strip()
                try:
                    await msg.reply_text(
                        body, parse_mode=ParseMode.HTML,
                        reply_markup=_kbd(),
                        disable_web_page_preview=True)
                    return
                except BadRequest:
                    pass  # fall back to plain below
            await safe_reply(msg, reply)
            return
    # No custom filter matched — try Auto-FAQ (if enabled)
    await _autofaq(update, msg, chat_id)


# --- Auto-FAQ: the group answers common questions itself, 24/7 -------------
_faq_cooldown = {}  # (chat_id, intent) -> last reply timestamp
_FAQ_COOLDOWN_SECS = 75


def _autofaq_intent(text):
    t = (text or "").lower().strip()
    if not t or len(t) > 200:
        return None
    is_q = ("?" in t) or bool(re.match(
        r"^(how|what|where|when|is|are|can|could|does|do|why|who|anyone know)\b", t))
    if re.search(r"\bhow\b.{0,14}\bbuy\b|where.{0,10}\bbuy\b|how.{0,10}\bpurchase\b", t):
        return "buy"
    if is_q and re.search(r"\bcontract address\b|\bcontract\b|\bwhat'?s the ca\b|\bca\?\b", t):
        return "ca"
    if is_q and re.search(r"\bprice\b|\bmarket ?cap\b|\bmcap\b|\bmc\b", t):
        return "price"
    if is_q and re.search(r"\bchart\b|\bdexscreener\b", t):
        return "chart"
    if is_q and re.search(r"\b(scam|rug|legit|safe|safu|trust)\b", t):
        return "safe"
    if is_q and re.search(r"\bwhitepaper\b|\bwhite paper\b", t):
        return "wp"
    if is_q and re.search(r"\bsocials?\b|\btwitter\b|\bwebsite\b|\binstagram\b", t):
        return "socials"
    if re.search(r"\bwhen\b.{0,6}\b(moon|pump|lambo|ath|listing)\b", t):
        return "moon"
    return None


async def _autofaq(update, msg, chat_id):
    try:
        if not db.get_settings(chat_id)["autofaq_on"]:
            return
    except Exception:  # noqa: BLE001
        return
    intent = _autofaq_intent(msg.text)
    if not intent:
        return
    now = time.time()
    key = (chat_id, intent)
    if now - _faq_cooldown.get(key, 0) < _FAQ_COOLDOWN_SECS:
        return  # don't spam the same answer repeatedly
    _faq_cooldown[key] = now
    kbd = globals().get("_buy_keyboard")
    try:
        if intent in ("buy", "ca"):
            await msg.reply_text(_ca_text(), parse_mode=ParseMode.HTML,
                                 reply_markup=kbd() if kbd else None,
                                 disable_web_page_preview=True)
        elif intent == "price":
            txt = await _fetch_price_text()
            await msg.reply_text(txt or "📊 Check the live chart for the latest price ⚡",
                                 parse_mode=ParseMode.HTML,
                                 reply_markup=InlineKeyboardMarkup([[
                                     InlineKeyboardButton("📊 Chart", url=CHART),
                                     InlineKeyboardButton("🪐 Buy", url=JUPITER)]]),
                                 disable_web_page_preview=True)
        elif intent == "chart":
            await msg.reply_text(
                "📊 <b>⚡ZAPP Chart</b>", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📊 Chart", url=CHART),
                    InlineKeyboardButton("🪐 Buy", url=JUPITER)]]))
        elif intent == "safe":
            await msg.reply_text(
                "✅ <b>Staying safe with ⚡ZAPP</b>\n"
                "• Only ever use the official CA (below) — verify before buying\n"
                "• Admins will <b>NEVER</b> DM you first\n"
                "• Never share your seed phrase or connect your wallet to random links\n\n"
                f"<code>{esc(CA)}</code>\n∞ 3 · 6 · 9 ∞",
                parse_mode=ParseMode.HTML, reply_markup=kbd() if kbd else None,
                disable_web_page_preview=True)
        elif intent == "wp":
            await msg.reply_text(
                "📄 <b>⚡ZAPP Whitepaper</b>", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📄 Read the Whitepaper", url=WHITEPAPER)]]))
        elif intent == "socials":
            await msg.reply_text(
                "📣 <b>⚡ZAPP Socials</b>", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌐 Website", url=WEBSITE),
                     InlineKeyboardButton("𝕏 Twitter", url=TWITTER)],
                    [InlineKeyboardButton("💬 Telegram", url=TELEGRAM)]]),
                disable_web_page_preview=True)
        elif intent == "moon":
            await msg.reply_text(random.choice([
                "🚀 Soon™ — the current builds quietly. Stack, hold, spread the signal. ⚡",
                "🌙 When the frequency aligns. 3 · 6 · 9. Keep charging. ⚡",
                "📈 No one rings a bell at the bottom. Stay plugged in. ⚡"]))
    except BadRequest:
        pass


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
    reschedule_autoposts(app)
    reschedule_autotrivia(app)
    reschedule_autoleaderboard(app)
    try:
        await app.bot.set_my_commands([
            # Discovery / navigation (top of list = most visible)
            ("menu", "Open the ZAPP arcade menu"),
            ("help", "What I can do"),
            ("buy", "How to buy ZAPP (CA + links)"),
            ("price", "Live ZAPP price"),
            # Stats
            ("points", "Your points + rank + streak"),
            ("top", "The leaderboard"),
            ("milestone", "Race to 369 status"),
            # Games
            ("spin", "🎰 Daily slot — win points"),
            ("trivia", "🧠 Trivia — +9 pts for first correct"),
            ("football", "⚽ Penalty shot"),
            ("basket", "🏀 Shoot a hoop"),
            ("dart", "🎯 Throw a dart"),
            ("flip", "🪙 Flip a coin"),
            ("roll", "🎲 Roll the dice"),
            ("rps", "✊ Rock paper scissors"),
            ("fortune", "🔮 Daily fortune"),
            # Earning
            ("tasks", "Earn-by-sharing quests"),
            ("submit", "Submit proof to earn points"),
            # Info
            ("ca", "Contract address"),
            ("chart", "Live chart"),
            ("whitepaper", "Whitepaper"),
            ("socials", "All socials"),
            ("stats", "Live token + group stats"),
            ("rules", "Group rules"),
            # Utilities
            ("tr", "Translate (reply to a message)"),
            ("afk", "Set yourself away"),
            ("report", "Alert admins (reply to a message)"),
            # Admin
            ("adminhelp", "Moderation commands (admin)"),
            ("connect", "Manage group from DM (admin)"),
            ("connection", "See your active connection (admin)"),
            ("give", "Reward someone (admin, reply)"),
            ("autopost", "Daily auto-posts (admin)"),
            ("autotrivia", "Scheduled trivia (admin)"),
            ("autoleaderboard", "Weekly leaderboard post (admin)"),
            ("settings", "Group settings (admin)"),
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
    register_buy(app)
    register_points(app)
    register_protection(app)
    register_powerpack(app)
    register_fun(app)
    register_social(app)
    register_federation(app)
    register_watcher(app)
    app.add_error_handler(_on_error)
    total = sum(len(v) for v in app.handlers.values())
    logger.info("⚡ ZAPP369bot v2 (single file) running — %d handlers", total)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
