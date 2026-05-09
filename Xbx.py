"""
Telegram Bot - To'liq funksional
Til: O'zbek
Kutubxona: python-telegram-bot v20+
Ma'lumotlar bazasi: SQLite
"""

import asyncio
import logging
import os
import shutil
import sqlite3
import time
import json
import random
import datetime
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from functools import wraps
from logging.handlers import RotatingFileHandler

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError, Forbidden

# ============================================================
# SOZLAMALAR
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

SUPER_ADMIN_ID = 1885056636

REQUIRED_CHANNELS = [
    {"id": "@tarjimaseriallar77", "name": "Tarjima Seriallar", "url": "https://t.me/tarjimaseriallar77"},
]

ANNOUNCE_CHANNEL = "@tarjimaseriallar77"

BOT_NAME = "Tarjima Seriallar"
BOT_VERSION = "2.0"
BOT_ABOUT = (
    "🎬 <b>Tarjima Seriallar Bot</b>\n\n"
    "Bu yerda siz eng yangi va sifatli tarjima seriallar, "
    "kinolar va boshqa kontentlarni topishingiz mumkin!\n\n"
    "📌 Versiya: 2.0\n"
    "📣 Kanal: @tarjimaseriallar77\n"
    "🌐 Til: O'zbek"
)

# Yangi foydalanuvchiga necha kun erkin kirish beriladi (majburiy obunasiz)
GRACE_DAYS = 3

# ============================================================
# NAV DEKORATOR
# ============================================================

def nav(func):
    @wraps(func)
    async def wrapper(update, ctx):
        q = update.callback_query
        if q:
            try:
                await q.answer()
            except Exception:
                pass
            logger.info("nav() callback: %s uid=%s", q.data, q.from_user.id if q.from_user else "?")
        try:
            await func(update, ctx)
        except Exception as e:
            logger.error("nav() handler %s xatosi: %s", func.__name__, e, exc_info=True)
        raise ApplicationHandlerStop
    return wrapper


SPAM_LIMIT = 5
SPAM_WINDOW = 3

BACKUP_DIR = "backups"
DB_FILE = "bot_database.db"
LOG_FILE = "bot.log"

# ============================================================
# LOGGING
# ============================================================

os.makedirs(BACKUP_DIR, exist_ok=True)

handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[handler, logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# ============================================================
# MA'LUMOTLAR BAZASI
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-8000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY,
            user_id       INTEGER UNIQUE NOT NULL,
            username      TEXT,
            full_name     TEXT,
            referral_by   INTEGER DEFAULT NULL,
            referral_count INTEGER DEFAULT 0,
            is_blocked    INTEGER DEFAULT 0,
            is_admin      INTEGER DEFAULT 0,
            is_vip        INTEGER DEFAULT 0,
            vip_until     TEXT DEFAULT NULL,
            lang          TEXT DEFAULT 'uz',
            media_watched INTEGER DEFAULT 0,
            join_date     TEXT NOT NULL,
            last_seen     TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS media (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT UNIQUE NOT NULL,
            title       TEXT NOT NULL,
            description TEXT,
            category    TEXT NOT NULL,
            file_id     TEXT,
            file_type   TEXT DEFAULT 'video',
            is_vip      INTEGER DEFAULT 0,
            added_by    INTEGER NOT NULL,
            add_date    TEXT NOT NULL,
            view_count  INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            media_id INTEGER NOT NULL,
            UNIQUE(user_id, media_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS vip_requests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            days        INTEGER NOT NULL,
            status      TEXT DEFAULT 'pending',
            request_date TEXT NOT NULL,
            confirm_date TEXT DEFAULT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id    INTEGER NOT NULL,
            episode_num INTEGER NOT NULL,
            file_id     TEXT,
            file_type   TEXT DEFAULT 'video',
            add_date    TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            media_id INTEGER NOT NULL,
            rating   INTEGER NOT NULL,
            UNIQUE(user_id, media_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action  TEXT NOT NULL,
            detail  TEXT,
            ts      TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('next_media_code', '1001')")

    conn.commit()
    conn.close()
    logger.info("Ma'lumotlar bazasi tayyor.")


# ============================================================
# SPAM HIMOYA
# ============================================================

_spam_store: dict[int, list[float]] = {}


def is_spamming(user_id: int) -> bool:
    now = time.time()
    history = _spam_store.get(user_id, [])
    history = [t for t in history if now - t < SPAM_WINDOW]
    history.append(now)
    _spam_store[user_id] = history
    # Xotirani tozalash: 500+ foydalanuvchi bo'lsa eski yozuvlarni o'chiramiz
    if len(_spam_store) > 500:
        cutoff = now - SPAM_WINDOW * 3
        dead = [uid for uid, ts in _spam_store.items() if not ts or max(ts) < cutoff]
        for uid in dead:
            del _spam_store[uid]
    return len(history) > SPAM_LIMIT


# ============================================================
# YORDAMCHI FUNKSIYALAR
# ============================================================

def now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def add_log(user_id: int | None, action: str, detail: str = ""):
    conn = get_db()
    conn.execute(
        "INSERT INTO logs (user_id, action, detail, ts) VALUES (?, ?, ?, ?)",
        (user_id, action, detail, now_str()),
    )
    conn.commit()
    conn.close()


def get_or_create_user(user) -> sqlite3.Row:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO users
               (user_id, username, full_name, join_date, last_seen)
               VALUES (?, ?, ?, ?, ?)""",
            (user.id, user.username, user.full_name, now_str(), now_str()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
        add_log(user.id, "REGISTER", f"@{user.username}")
    else:
        conn.execute(
            "UPDATE users SET last_seen=?, username=?, full_name=? WHERE user_id=?",
            (now_str(), user.username, user.full_name, user.id),
        )
        conn.commit()
    conn.close()
    return row


def is_admin(user_id: int) -> bool:
    if user_id == SUPER_ADMIN_ID:
        return True
    conn = get_db()
    row = conn.execute("SELECT is_admin FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return bool(row and row["is_admin"])


def is_vip(user_id: int) -> bool:
    conn = get_db()
    row = conn.execute("SELECT is_vip, vip_until FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    if not row or not row["is_vip"]:
        return False
    if row["vip_until"]:
        if datetime.datetime.strptime(row["vip_until"], "%Y-%m-%d %H:%M:%S") < datetime.datetime.now():
            _revoke_vip(user_id)
            return False
    return True


def _revoke_vip(user_id: int):
    conn = get_db()
    conn.execute("UPDATE users SET is_vip=0, vip_until=NULL WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    add_log(user_id, "VIP_EXPIRED")


def is_in_grace_period(user_id: int) -> bool:
    """Foydalanuvchi GRACE_DAYS kunlik muddat ichidami?"""
    conn = get_db()
    row = conn.execute("SELECT join_date FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return True
    try:
        join_dt = datetime.datetime.strptime(row["join_date"], "%Y-%m-%d %H:%M:%S")
        return (datetime.datetime.now() - join_dt).days < GRACE_DAYS
    except Exception:
        return False


def next_media_code() -> str:
    conn = get_db()
    cur = conn.execute("SELECT value FROM settings WHERE key='next_media_code'")
    val = int(cur.fetchone()["value"])
    conn.execute("UPDATE settings SET value=? WHERE key='next_media_code'", (str(val + 1),))
    conn.commit()
    conn.close()
    return str(val)


CATEGORY_EMOJI = {
    "serial": "📺",
    "kino": "🎬",
    "musiqa": "🎵",
    "boshqa": "📁",
}


async def announce_new_media(bot, code: str, title: str, category: str, is_vip_content: int = 0):
    cat_emoji = CATEGORY_EMOJI.get(category, "🎬")
    vip_line = "\n💎 <b>VIP kontent</b>" if is_vip_content else ""
    text = (
        f"🆕 <b>Yangi {cat_emoji} qo'shildi!</b>{vip_line}\n\n"
        f"{cat_emoji} <b>{title}</b>\n\n"
        f"📌 Kod: <code>{code}</code>\n\n"
        f"👇 Botda ko'rish uchun kodni yuboring:\n"
        f"➡️ @SceneMix1_Bot ga <code>{code}</code> yozing"
    )
    try:
        await bot.send_message(chat_id=ANNOUNCE_CHANNEL, text=text, parse_mode=ParseMode.HTML)
    except TelegramError as e:
        logger.warning(f"Kanal e'loni yuborilmadi: {e}")


# ============================================================
# DEKORATORLAR
# ============================================================

def spam_guard(func):
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user and is_spamming(user.id):
            if update.callback_query:
                try:
                    await update.callback_query.answer("⚠️ Juda tez bosyapsiz. Kuting.")
                except Exception:
                    pass
            else:
                await update.effective_message.reply_text(
                    "⚠️ Juda tez so'rovlar yuboryapsiz. Iltimos, biroz kuting."
                )
            return
        return await func(update, ctx)
    return wrapper


def require_subscription(func):
    """
    Kanalga obuna tekshiruvi.
    Yangi foydalanuvchi GRACE_DAYS kun ichida tekshirilmaydi.
    """
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
        if is_admin(user.id):
            return await func(update, ctx)
        # 3 kunlik muddat tekshiruvi
        if is_in_grace_period(user.id):
            return await func(update, ctx)
        # Telegram kanal obunasini tekshirish
        not_subscribed = await get_not_subscribed_channels(ctx.bot, user.id)
        if not_subscribed:
            await send_subscription_prompt(update, not_subscribed)
            return
        return await func(update, ctx)
    return wrapper


def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not is_admin(user.id):
            await update.effective_message.reply_text("🚫 Bu buyruq faqat adminlar uchun.")
            return
        return await func(update, ctx)
    return wrapper


def not_blocked(func):
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
        conn = get_db()
        row = conn.execute("SELECT is_blocked FROM users WHERE user_id=?", (user.id,)).fetchone()
        conn.close()
        if row and row["is_blocked"]:
            await update.effective_message.reply_text(
                "🚫 Sizning hisobingiz bloklangan. Admin bilan bog'laning."
            )
            return
        return await func(update, ctx)
    return wrapper


# ============================================================
# OBUNA TEKSHIRISH
# ============================================================

async def get_not_subscribed_channels(bot, user_id: int) -> list:
    not_subbed = []
    for ch in REQUIRED_CHANNELS:
        try:
            member = await asyncio.wait_for(
                bot.get_chat_member(ch["id"], user_id),
                timeout=5.0
            )
            if member.status in ("left", "kicked"):
                not_subbed.append(ch)
        except (TelegramError, asyncio.TimeoutError):
            not_subbed.append(ch)
    return not_subbed


async def send_subscription_prompt(update: Update, channels: list):
    text = (
        "🎬 <b>Tarjima Seriallar Bot</b>\n\n"
        "⚠️ Botdan foydalanish uchun avval quyidagi kanalga obuna bo'ling:\n\n"
    )
    buttons = []
    for ch in channels:
        text += f"📣 {ch['name']}\n"
        buttons.append([InlineKeyboardButton(f"✅ {ch['name']} — Obuna bo'lish", url=ch["url"])])
    buttons.append([InlineKeyboardButton("🔄 Obuna bo'ldim — Tekshirish", callback_data="check_sub")])
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons)
    )


async def check_sub_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    not_subbed = await get_not_subscribed_channels(ctx.bot, query.from_user.id)
    if not_subbed:
        await safe_edit_text(
            query,
            "❌ Siz hali barcha kanallarga obuna bo'lmadingiz.\nIltimos, obuna bo'lib qayta tekshiring.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(f"📣 {ch['name']}", url=ch["url"])] for ch in not_subbed]
                + [[InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub")]]
            ),
        )
    else:
        await safe_edit_text(query, "✅ Rahmat! Endi botdan foydalanishingiz mumkin.\n\n/start buyrug'ini yuboring.")


# ============================================================
# ASOSIY MENYU
# ============================================================

def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("🎬 Kino", callback_data="cat_kino"),
            InlineKeyboardButton("📺 Serial", callback_data="cat_serial"),
        ],
        [InlineKeyboardButton("🏆 Top 10", callback_data="top10_menu")],
    ]
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton("👮 Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)


# ============================================================
# /start BUYRUG'I
# ============================================================

@spam_guard
@not_blocked
async def start_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_or_create_user(user)

    args = ctx.args
    if args and args[0].startswith("ref"):
        ref_id_str = args[0].replace("ref", "")
        if ref_id_str.isdigit():
            ref_id = int(ref_id_str)
            if ref_id != user.id:
                conn = get_db()
                existing = conn.execute(
                    "SELECT referral_by FROM users WHERE user_id=?", (user.id,)
                ).fetchone()
                if existing and existing["referral_by"] is None:
                    conn.execute(
                        "UPDATE users SET referral_by=? WHERE user_id=?", (ref_id, user.id)
                    )
                    conn.execute(
                        "UPDATE users SET referral_count = referral_count + 1 WHERE user_id=?",
                        (ref_id,),
                    )
                    conn.commit()
                    add_log(user.id, "REFERRAL", f"by {ref_id}")
                conn.close()

    # 3 kunlik muddat — obunasiz kirishi mumkin
    if not is_admin(user.id) and not is_in_grace_period(user.id):
        not_subbed = await get_not_subscribed_channels(ctx.bot, user.id)
        if not_subbed:
            await send_subscription_prompt(update, not_subbed)
            return

    # Grace period ichida bo'lsa — qolgan kunlarni ko'rsatamiz
    grace_msg = ""
    if not is_admin(user.id) and is_in_grace_period(user.id):
        conn = get_db()
        row = conn.execute("SELECT join_date FROM users WHERE user_id=?", (user.id,)).fetchone()
        conn.close()
        if row:
            join_dt = datetime.datetime.strptime(row["join_date"], "%Y-%m-%d %H:%M:%S")
            days_left = GRACE_DAYS - (datetime.datetime.now() - join_dt).days
            if days_left > 0:
                grace_msg = f"\n\n⏳ <i>Sizda {days_left} kunlik bepul kirish bor.\nKeyin kanalga obuna bo'lish shart bo'ladi.</i>"

    welcome = (
        f"👋 Xush kelibsiz, <b>{user.first_name}</b>!\n\n"
        f"🎬 <b>Tarjima Seriallar Bot</b>\n\n"
        "📌 Serial yoki kino <b>kodini</b> yozing — darhol yuboriladi!\n\n"
        f"Quyidan kategoriyani tanlang:{grace_msg}"
    )
    await update.message.reply_text(
        welcome, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard(user.id)
    )


# ============================================================
# BEKOR QILISH
# ============================================================

async def cancel_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in media_add_data:
        del media_add_data[uid]
    quick_group_buffer.pop(uid, None)
    t = quick_group_tasks.pop(uid, None)
    if t and not t.done():
        t.cancel()
    ctx.user_data.clear()
    await update.message.reply_text("❌ Bekor qilindi. /start yozing.")
    return ConversationHandler.END


async def conv_timeout_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else None
    if uid:
        quick_group_buffer.pop(uid, None)
        t = quick_group_tasks.pop(uid, None)
        if t and not t.done():
            t.cancel()
        if uid in media_add_data:
            del media_add_data[uid]
    ctx.user_data.clear()
    try:
        if update.effective_message:
            await update.effective_message.reply_text("⏱ Vaqt tugadi (5 daqiqa). /start yozing.")
    except Exception:
        pass
    return ConversationHandler.END


# ============================================================
# PROFIL
# ============================================================

@spam_guard
@not_blocked
@require_subscription
async def profile_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        user = query.from_user
        message = query.message
    else:
        user = update.effective_user
        message = update.message

    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user.id,)).fetchone()
    conn.close()

    if not row:
        await message.reply_text("❌ Profil topilmadi.")
        return

    vip_status = "✅ Aktiv" if is_vip(user.id) else "❌ Yo'q"
    vip_until = row["vip_until"] if row["vip_until"] else "—"
    admin_status = "👮 Ha" if is_admin(user.id) else "❌ Yo'q"

    ref_link = f"https://t.me/{ctx.bot.username}?start=ref{user.id}"

    text = (
        f"👤 <b>Profil ma'lumotlari</b>\n\n"
        f"🆔 ID: <code>{row['user_id']}</code>\n"
        f"👤 Ism: {row['full_name'] or '—'}\n"
        f"📛 Username: @{row['username'] if row['username'] else '—'}\n"
        f"💎 VIP: {vip_status}\n"
        f"📅 VIP tugash: {vip_until}\n"
        f"👮 Admin: {admin_status}\n"
        f"🎬 Ko'rilgan media: {row['media_watched']}\n"
        f"👥 Referrallar: {row['referral_count']}\n"
        f"📅 Ro'yxatdan: {row['join_date']}\n"
        f"🕐 Oxirgi kirish: {row['last_seen']}\n\n"
        f"🔗 Referal havolangiz:\n<code>{ref_link}</code>"
    )
    buttons = [[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]
    if query:
        await safe_edit_text(query, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


# ============================================================
# MEDIA KATEGORIYA
# ============================================================

CATEGORIES = {"kino": "🎬 Kino", "serial": "📺 Serial", "musiqa": "🎵 Musiqa", "boshqa": "📁 Boshqa"}


async def category_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat = query.data.replace("cat_", "")
    cat_name = CATEGORIES.get(cat, cat)

    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM media WHERE category=?", (cat,)).fetchone()[0]
    medias = conn.execute(
        "SELECT * FROM media WHERE category=? ORDER BY add_date DESC LIMIT 20", (cat,)
    ).fetchall()
    conn.close()

    if not medias:
        await safe_edit_text(
            query,
            f"{cat_name} kategoriyasida hozircha media yo'q.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]),
        )
        return

    conn2 = get_db()
    ep_counts = {}
    for m in medias:
        cnt = conn2.execute(
            "SELECT COUNT(*) FROM episodes WHERE media_id=?", (m["id"],)
        ).fetchone()[0]
        ep_counts[m["id"]] = cnt
    conn2.close()

    text = f"{cat_name} — jami <b>{total}</b> ta (so'nggi {len(medias)} ta):\n\n📌 Kodni yozing yoki quyidan tanlang:"
    buttons = []
    buttons.append([InlineKeyboardButton("🔍 Nom bo'yicha qidirish", callback_data=f"cat_search_{cat}")])
    for m in medias:
        vip_icon = "💎 " if m["is_vip"] else ""
        ep_cnt = ep_counts.get(m["id"], 0)
        ep_label = f" ({ep_cnt + 1} qism)" if ep_cnt > 0 else ""
        buttons.append([InlineKeyboardButton(
            f"{vip_icon}{m['code']} | {m['title']}{ep_label}",
            callback_data=f"media_{m['id']}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")])
    await safe_edit_text(query, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


# ============================================================
# MEDIA KO'RISH
# ============================================================

def get_media_rating(media_id: int) -> tuple:
    conn = get_db()
    row = conn.execute(
        "SELECT AVG(rating) as avg, COUNT(*) as cnt FROM ratings WHERE media_id=?", (media_id,)
    ).fetchone()
    conn.close()
    avg = round(row["avg"] or 0, 1)
    cnt = row["cnt"] or 0
    return avg, cnt


def get_user_rating(user_id: int, media_id: int) -> int:
    conn = get_db()
    row = conn.execute("SELECT rating FROM ratings WHERE user_id=? AND media_id=?", (user_id, media_id)).fetchone()
    conn.close()
    return row["rating"] if row else 0


def media_buttons(media_id: int, user_id: int, user_rating: int = 0) -> InlineKeyboardMarkup:
    fav_check = get_db()
    is_fav = fav_check.execute(
        "SELECT id FROM favorites WHERE user_id=? AND media_id=?", (user_id, media_id)
    ).fetchone()
    fav_check.close()

    avg, cnt = get_media_rating(media_id)
    stars = "⭐" * round(avg) if avg else "☆☆☆☆☆"
    rating_label = f"{stars} {avg}/5 ({cnt} ta)" if cnt else "☆ Baholang"

    fav_btn = (
        InlineKeyboardButton("💔 Sevimlilardan", callback_data=f"fav_remove_{media_id}")
        if is_fav else
        InlineKeyboardButton("❤️ Sevimli", callback_data=f"fav_add_{media_id}")
    )

    buttons = [
        [fav_btn, InlineKeyboardButton("📤 Ulashish", switch_inline_query=str(media_id))],
        [InlineKeyboardButton(f"⭐ {rating_label}", callback_data=f"rate_open_{media_id}")],
        [InlineKeyboardButton("🔙 Bosh menyu", callback_data="main_menu")],
    ]
    return InlineKeyboardMarkup(buttons)


def rate_keyboard(media_id: int) -> InlineKeyboardMarkup:
    stars = ["1⭐", "2⭐", "3⭐", "4⭐", "5⭐"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(s, callback_data=f"rate_{i+1}_{media_id}") for i, s in enumerate(stars)],
        [InlineKeyboardButton("🔙 Orqaga", callback_data=f"media_{media_id}")],
    ])


async def rate_open_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    media_id = int(query.data.replace("rate_open_", ""))
    user_rating = get_user_rating(query.from_user.id, media_id)
    text = "⭐ <b>Bahoning qo'yish</b>\n\nBu serialga necha yulduz berasiz?"
    if user_rating:
        text += f"\n\n✅ Sizning bahoyingiz: {'⭐' * user_rating}"
    await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=rate_keyboard(media_id))


async def rate_submit_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    rating = int(parts[1])
    media_id = int(parts[2])
    user_id = query.from_user.id

    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO ratings (user_id, media_id, rating) VALUES (?, ?, ?)",
        (user_id, media_id, rating)
    )
    conn.commit()
    conn.close()

    avg, cnt = get_media_rating(media_id)
    await safe_edit_text(
        query,
        f"✅ Bahoyingiz qabul qilindi: {'⭐' * rating}\n\n"
        f"📊 O'rtacha reyting: {avg}/5 ({cnt} ta ovoz)",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data=f"media_{media_id}")]]),
    )


def get_episodes(media_id: int):
    conn = get_db()
    eps = conn.execute(
        "SELECT * FROM episodes WHERE media_id=? ORDER BY episode_num", (media_id,)
    ).fetchall()
    conn.close()
    return eps


async def safe_edit_text(query, text: str, parse_mode=None, reply_markup=None):
    msg = query.message
    try:
        await msg.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except TelegramError:
        try:
            await msg.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        except TelegramError:
            pass


def episode_list_keyboard(media_id: int, episodes, back_callback="main_menu"):
    buttons = []
    row = []
    for ep in episodes:
        row.append(InlineKeyboardButton(f"{ep['episode_num']}-qism", callback_data=f"ep_{ep['id']}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data=back_callback)])
    return InlineKeyboardMarkup(buttons)


async def show_episode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ep_id = int(query.data.replace("ep_", ""))
    user = query.from_user

    conn = get_db()
    ep = conn.execute("SELECT * FROM episodes WHERE id=?", (ep_id,)).fetchone()
    if not ep:
        await safe_edit_text(query, "❌ Qism topilmadi.")
        conn.close()
        return
    m = conn.execute("SELECT * FROM media WHERE id=?", (ep["media_id"],)).fetchone()
    conn.close()

    if not m:
        await safe_edit_text(query, "❌ Media topilmadi.")
        return

    # Har bir qismda nechanchi qism ekanligi ko'rsatiladi
    caption = (
        f"📺 <b>{m['title']}</b>\n"
        f"🎞 <b>{ep['episode_num']}-qism</b>\n"
        f"📌 Kod: <code>{m['code']}</code>\n"
    )
    back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Barcha qismlar", callback_data=f"media_{m['id']}")]])

    try:
        if ep["file_type"] == "video":
            await ctx.bot.send_video(query.message.chat_id, ep["file_id"], caption=caption, parse_mode=ParseMode.HTML, reply_markup=back_kb)
        elif ep["file_type"] == "document":
            await ctx.bot.send_document(query.message.chat_id, ep["file_id"], caption=caption, parse_mode=ParseMode.HTML, reply_markup=back_kb)
        elif ep["file_type"] == "audio":
            await ctx.bot.send_audio(query.message.chat_id, ep["file_id"], caption=caption, parse_mode=ParseMode.HTML, reply_markup=back_kb)
        else:
            await safe_edit_text(query, caption, parse_mode=ParseMode.HTML, reply_markup=back_kb)
    except TelegramError:
        await safe_edit_text(query, caption, parse_mode=ParseMode.HTML, reply_markup=back_kb)


async def show_episode_1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    media_id = int(query.data.replace("ep1_", ""))

    conn = get_db()
    m = conn.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
    conn.close()

    if not m or not m["file_id"]:
        await safe_edit_text(query, "❌ 1-qism topilmadi.")
        return

    # 1-qism caption
    caption = (
        f"📺 <b>{m['title']}</b>\n"
        f"🎞 <b>1-qism</b>\n"
        f"📌 Kod: <code>{m['code']}</code>\n"
    )
    back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Barcha qismlar", callback_data=f"media_{m['id']}")]])

    try:
        if m["file_type"] == "video":
            await ctx.bot.send_video(query.message.chat_id, m["file_id"], caption=caption, parse_mode=ParseMode.HTML, reply_markup=back_kb)
        elif m["file_type"] == "document":
            await ctx.bot.send_document(query.message.chat_id, m["file_id"], caption=caption, parse_mode=ParseMode.HTML, reply_markup=back_kb)
        elif m["file_type"] == "audio":
            await ctx.bot.send_audio(query.message.chat_id, m["file_id"], caption=caption, parse_mode=ParseMode.HTML, reply_markup=back_kb)
        else:
            await safe_edit_text(query, caption, parse_mode=ParseMode.HTML, reply_markup=back_kb)
    except TelegramError:
        await safe_edit_text(query, caption, parse_mode=ParseMode.HTML, reply_markup=back_kb)


async def show_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    media_id = int(query.data.replace("media_", ""))
    user = query.from_user

    conn = get_db()
    m = conn.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
    conn.close()

    if not m:
        await safe_edit_text(query, "❌ Media topilmadi.")
        return

    if m["is_vip"] and not is_vip(user.id) and not is_admin(user.id):
        await safe_edit_text(
            query,
            "💎 Bu kontent faqat VIP foydalanuvchilar uchun!\n\nVIP olish uchun /vip buyrug'ini yuboring.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 VIP olish", callback_data="vip_buy")],
                [InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")],
            ]),
        )
        return

    episodes = get_episodes(media_id)
    if episodes:
        vip_tag = "💎 [VIP] " if m["is_vip"] else ""
        avg, cnt = get_media_rating(media_id)
        rating_str = f"{'⭐' * round(avg)} {avg}/5 ({cnt} ta)" if cnt else "Hali baholanmagan"
        total_eps = len(episodes) + 1
        text = (
            f"{vip_tag}📺 <b>{m['title']}</b>\n\n"
            f"📌 Kod: <code>{m['code']}</code>\n"
            f"🎞 Jami qismlar: <b>{total_eps} ta</b>\n"
            f"⭐ Reyting: {rating_str}\n\n"
            f"Quyidan qismni tanlang:"
        )
        buttons = [[InlineKeyboardButton("▶️ 1-qism", callback_data=f"ep1_{media_id}")]]
        row = []
        for ep in episodes:
            row.append(InlineKeyboardButton(f"{ep['episode_num']}-qism", callback_data=f"ep_{ep['id']}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")])
        await safe_edit_text(query, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
        return

    conn = get_db()
    conn.execute("UPDATE media SET view_count=view_count+1 WHERE id=?", (media_id,))
    conn.execute("UPDATE users SET media_watched=media_watched+1 WHERE user_id=?", (user.id,))
    conn.commit()
    conn.close()
    add_log(user.id, "MEDIA_VIEW", f"code={m['code']}")

    vip_tag = "💎 [VIP] " if m["is_vip"] else ""
    cat_name = CATEGORIES.get(m["category"], m["category"])
    avg, cnt = get_media_rating(media_id)
    rating_str = f"{'⭐' * round(avg)} {avg}/5 ({cnt} ta)" if cnt else "Hali baholanmagan"
    text = (
        f"{vip_tag}📺 <b>{m['title']}</b>\n\n"
        f"📌 Kod: <code>{m['code']}</code>\n"
        f"📂 Kategoriya: {cat_name}\n"
        f"👀 Ko'rishlar: {m['view_count'] + 1}\n"
        f"⭐ Reyting: {rating_str}\n\n"
        f"📝 {m['description'] or '—'}"
    )
    kb = media_buttons(media_id, user.id)

    if m["file_id"]:
        try:
            if m["file_type"] == "video":
                await ctx.bot.send_video(query.message.chat_id, m["file_id"], caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
            elif m["file_type"] == "photo":
                await ctx.bot.send_photo(query.message.chat_id, m["file_id"], caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
            elif m["file_type"] == "audio":
                await ctx.bot.send_audio(query.message.chat_id, m["file_id"], caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
            elif m["file_type"] == "document":
                await ctx.bot.send_document(query.message.chat_id, m["file_id"], caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
            else:
                await safe_edit_text(query, text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except TelegramError:
            await safe_edit_text(query, text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await safe_edit_text(query, text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ============================================================
# KOD ORQALI MEDIA QIDIRISH
# ============================================================

@spam_guard
@not_blocked
@require_subscription
async def code_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        return

    user = update.effective_user
    conn = get_db()
    m = conn.execute("SELECT * FROM media WHERE code=?", (text,)).fetchone()
    conn.close()

    if not m:
        await update.message.reply_text(
            f"❌ <code>{text}</code> kodli media topilmadi.",
            parse_mode=ParseMode.HTML
        )
        return

    if m["is_vip"] and not is_vip(user.id) and not is_admin(user.id):
        await update.message.reply_text(
            "💎 Bu kontent faqat VIP foydalanuvchilar uchun!\n\n"
            "VIP olish uchun /vip buyrug'ini yuboring."
        )
        return

    episodes = get_episodes(m["id"])
    if episodes:
        vip_tag = "💎 [VIP] " if m["is_vip"] else ""
        avg, cnt = get_media_rating(m["id"])
        rating_str = f"{'⭐' * round(avg)} {avg}/5 ({cnt} ta)" if cnt else "Hali baholanmagan"
        total_eps = len(episodes) + 1
        caption = (
            f"{vip_tag}📺 <b>{m['title']}</b>\n\n"
            f"📌 Kod: <code>{m['code']}</code>\n"
            f"🎞 Jami qismlar: <b>{total_eps} ta</b>\n"
            f"⭐ Reyting: {rating_str}\n\n"
            f"Quyidan qismni tanlang:"
        )
        buttons = [[InlineKeyboardButton("▶️ 1-qism", callback_data=f"ep1_{m['id']}")]]
        row = []
        for ep in episodes:
            row.append(InlineKeyboardButton(f"{ep['episode_num']}-qism", callback_data=f"ep_{ep['id']}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        await update.message.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
        return

    conn = get_db()
    conn.execute("UPDATE media SET view_count=view_count+1 WHERE id=?", (m["id"],))
    conn.execute("UPDATE users SET media_watched=media_watched+1 WHERE user_id=?", (user.id,))
    conn.commit()
    conn.close()
    add_log(user.id, "MEDIA_VIEW", f"code={m['code']}")

    vip_tag = "💎 [VIP] " if m["is_vip"] else ""
    cat_name = CATEGORIES.get(m["category"], m["category"])
    avg, cnt = get_media_rating(m["id"])
    rating_str = f"{'⭐' * round(avg)} {avg}/5 ({cnt} ta)" if cnt else "Hali baholanmagan"
    caption = (
        f"{vip_tag}📺 <b>{m['title']}</b>\n\n"
        f"📌 Kod: <code>{m['code']}</code>\n"
        f"📂 Kategoriya: {cat_name}\n"
        f"👀 Ko'rishlar: {m['view_count'] + 1}\n"
        f"⭐ Reyting: {rating_str}\n\n"
        f"📝 {m['description'] or '—'}"
    )
    kb = media_buttons(m["id"], user.id)

    if m["file_id"]:
        try:
            if m["file_type"] == "video":
                await update.message.reply_video(m["file_id"], caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
            elif m["file_type"] == "photo":
                await update.message.reply_photo(m["file_id"], caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
            elif m["file_type"] == "audio":
                await update.message.reply_audio(m["file_id"], caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
            elif m["file_type"] == "document":
                await update.message.reply_document(m["file_id"], caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
            else:
                await update.message.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)
        except TelegramError:
            await update.message.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.message.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)


# ============================================================
# QIDIRUV
# ============================================================

SEARCH_STATE = 1


async def search_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_text(
        query,
        "🔍 Qidirish uchun media nomi yoki kodini yuboring:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Bekor qilish", callback_data="main_menu")]]),
    )
    return SEARCH_STATE


@spam_guard
async def search_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.message.text.strip()
    conn = get_db()
    results = conn.execute(
        "SELECT * FROM media WHERE title LIKE ? OR code=? ORDER BY add_date DESC LIMIT 15",
        (f"%{q}%", q),
    ).fetchall()
    conn.close()

    if not results:
        await update.message.reply_text(
            f"❌ «{q}» bo'yicha hech narsa topilmadi.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]),
        )
        return ConversationHandler.END

    buttons = []
    for m in results:
        vip_icon = "💎 " if m["is_vip"] else ""
        buttons.append([InlineKeyboardButton(f"{vip_icon}{m['code']} | {m['title']}", callback_data=f"media_{m['id']}")])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")])
    await update.message.reply_text(
        f"🔍 «{q}» bo'yicha {len(results)} ta natija:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return ConversationHandler.END


CAT_SEARCH_STATE = 300

async def cat_search_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat = query.data.replace("cat_search_", "")
    cat_name = CATEGORIES.get(cat, cat)
    ctx.user_data["search_cat"] = cat
    ctx.user_data["search_cat_name"] = cat_name
    await safe_edit_text(
        query,
        f"🔍 <b>{cat_name}</b> bo'yicha qidirish\n\nNomini yozing:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data=f"cat_{cat}")]]),
    )
    return CAT_SEARCH_STATE


@spam_guard
async def cat_search_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.message.text.strip()
    cat = ctx.user_data.get("search_cat", "")
    cat_name = ctx.user_data.get("search_cat_name", "")
    conn = get_db()
    if cat:
        results = conn.execute(
            "SELECT * FROM media WHERE category=? AND (title LIKE ? OR code=?) ORDER BY add_date DESC LIMIT 20",
            (cat, f"%{q}%", q),
        ).fetchall()
    else:
        results = conn.execute(
            "SELECT * FROM media WHERE title LIKE ? OR code=? ORDER BY add_date DESC LIMIT 20",
            (f"%{q}%", q),
        ).fetchall()
    conn.close()

    if not results:
        await update.message.reply_text(
            f"❌ <b>{cat_name}</b> da «{q}» topilmadi.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data=f"cat_{cat}")]]),
        )
        return ConversationHandler.END

    buttons = []
    for m in results:
        vip_icon = "💎 " if m["is_vip"] else ""
        buttons.append([InlineKeyboardButton(f"{vip_icon}{m['code']} | {m['title']}", callback_data=f"media_{m['id']}")])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data=f"cat_{cat}")])
    await update.message.reply_text(
        f"🔍 «{q}» — {len(results)} ta natija:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return ConversationHandler.END


# ============================================================
# TASODIFIY MEDIA
# ============================================================

async def random_media_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    conn = get_db()
    if is_vip(user.id) or is_admin(user.id):
        m = conn.execute("SELECT * FROM media ORDER BY RANDOM() LIMIT 1").fetchone()
    else:
        m = conn.execute("SELECT * FROM media WHERE is_vip=0 ORDER BY RANDOM() LIMIT 1").fetchone()
    conn.close()

    if not m:
        await safe_edit_text(query, "❌ Hozircha media yo'q.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]))
        return

    cat_name = CATEGORIES.get(m["category"], m["category"])
    text = (
        f"🎲 <b>Tasodifiy media:</b>\n\n"
        f"🎬 <b>{m['title']}</b>\n"
        f"📌 Kod: <code>{m['code']}</code>\n"
        f"📂 Kategoriya: {cat_name}\n"
        f"📝 {m['description'] or '—'}"
    )
    buttons = [
        [InlineKeyboardButton("👁 Ko'rish", callback_data=f"media_{m['id']}")],
        [InlineKeyboardButton("🎲 Yana tasodifiy", callback_data="random_media")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")],
    ]
    await safe_edit_text(query, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


# ============================================================
# YANGI QO'SHILGANLAR
# ============================================================

async def latest_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    conn = get_db()
    if is_vip(user.id) or is_admin(user.id):
        medias = conn.execute("SELECT * FROM media ORDER BY add_date DESC LIMIT 10").fetchall()
    else:
        medias = conn.execute("SELECT * FROM media WHERE is_vip=0 ORDER BY add_date DESC LIMIT 10").fetchall()
    conn.close()

    if not medias:
        await safe_edit_text(query, "❌ Hozircha media yo'q.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]))
        return

    buttons = []
    for m in medias:
        vip_icon = "💎 " if m["is_vip"] else ""
        buttons.append([InlineKeyboardButton(f"{vip_icon}{m['code']} | {m['title']}", callback_data=f"media_{m['id']}")])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")])
    await safe_edit_text(query, "🆕 Eng so'nggi qo'shilgan medialar:", reply_markup=InlineKeyboardMarkup(buttons))


# ============================================================
# TOP 10
# ============================================================

async def top10_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_text(
        query,
        "🏆 <b>Top 10</b>\n\nQuyidagi ro'yxatdan birini tanlang:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔥 Eng ko'p ko'rilgan",    callback_data="top10_views")],
            [InlineKeyboardButton("🎞 Eng ko'p qismli serial", callback_data="top10_episodes")],
            [InlineKeyboardButton("🆕 Eng so'nggi qo'shilgan", callback_data="latest")],
            [InlineKeyboardButton("🔙 Orqaga",                callback_data="main_menu")],
        ]),
    )


async def top10_views(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    conn = get_db()
    if is_vip(user.id) or is_admin(user.id):
        medias = conn.execute("SELECT * FROM media ORDER BY view_count DESC LIMIT 10").fetchall()
    else:
        medias = conn.execute("SELECT * FROM media WHERE is_vip=0 ORDER BY view_count DESC LIMIT 10").fetchall()
    conn.close()

    if not medias:
        await safe_edit_text(query, "❌ Hozircha media yo'q.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="top10_menu")]]))
        return

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    buttons = []
    for i, m in enumerate(medias, 1):
        vip_icon = "💎 " if m["is_vip"] else ""
        medal = medals.get(i, f"{i}.")
        buttons.append([InlineKeyboardButton(
            f"{medal} {vip_icon}{m['code']} | {m['title']} ({m['view_count']} 👁)",
            callback_data=f"media_{m['id']}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="top10_menu")])
    await safe_edit_text(query, "🔥 <b>Eng ko'p ko'rilgan Top 10:</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def top10_episodes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    conn = get_db()
    rows = conn.execute("""
        SELECT m.id, m.code, m.title, m.is_vip,
               (COUNT(e.id) + 1) AS total_eps
        FROM media m
        LEFT JOIN episodes e ON e.media_id = m.id
        WHERE m.category = 'serial'
        GROUP BY m.id
        ORDER BY total_eps DESC
        LIMIT 10
    """).fetchall()
    conn.close()

    if not rows:
        await safe_edit_text(query, "❌ Hozircha serial yo'q.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="top10_menu")]]))
        return

    buttons = []
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(rows, 1):
        medal = medals[i-1] if i <= 3 else f"{i}."
        vip_icon = "💎 " if r["is_vip"] else ""
        buttons.append([InlineKeyboardButton(
            f"{medal} {vip_icon}{r['code']} | {r['title']} ({r['total_eps']} qism)",
            callback_data=f"media_{r['id']}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="top10_menu")])
    await safe_edit_text(query, "🎞 <b>Eng ko'p qismli seriallar Top 10:</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


# ============================================================
# SEVIMLILAR
# ============================================================

async def favorites_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    conn = get_db()
    favs = conn.execute(
        """SELECT m.* FROM media m
           INNER JOIN favorites f ON m.id = f.media_id
           WHERE f.user_id=?""",
        (user.id,),
    ).fetchall()
    conn.close()

    if not favs:
        await safe_edit_text(
            query,
            "❤️ Sizning sevimlilar ro'yxatingiz bo'sh.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]),
        )
        return

    buttons = []
    for m in favs:
        buttons.append([InlineKeyboardButton(f"🗑 {m['code']} | {m['title']}", callback_data=f"fav_remove_{m['id']}")])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")])
    await safe_edit_text(
        query,
        f"❤️ Sevimlilar ({len(favs)} ta):\n_(o'chirish uchun bosing)_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def fav_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    media_id = int(query.data.replace("fav_add_", ""))
    user = query.from_user

    conn = get_db()
    try:
        conn.execute("INSERT INTO favorites (user_id, media_id) VALUES (?, ?)", (user.id, media_id))
        conn.commit()
        await query.answer("❤️ Sevimlilarga qo'shildi!", show_alert=True)
    except sqlite3.IntegrityError:
        await query.answer("⚠️ Allaqachon sevimlilarda bor.", show_alert=True)
    finally:
        conn.close()


async def fav_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    media_id = int(query.data.replace("fav_remove_", ""))
    user = query.from_user

    conn = get_db()
    conn.execute("DELETE FROM favorites WHERE user_id=? AND media_id=?", (user.id, media_id))
    conn.commit()
    conn.close()
    await query.answer("🗑 Sevimlilardan o'chirildi!", show_alert=True)
    await favorites_handler(update, ctx)


# ============================================================
# VIP TIZIMI
# ============================================================

async def vip_info_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        user = query.from_user
    else:
        user = update.effective_user

    if is_vip(user.id):
        conn = get_db()
        row = conn.execute("SELECT vip_until FROM users WHERE user_id=?", (user.id,)).fetchone()
        conn.close()
        text = (
            f"💎 Sizda aktiv VIP mavjud!\n\n"
            f"📅 Tugash sanasi: {row['vip_until'] or '—'}"
        )
        buttons = [[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]
    else:
        text = (
            "💎 <b>VIP Obuna</b>\n\n"
            "VIP bo'lsangiz quyidagi imkoniyatlarga ega bo'lasiz:\n"
            "✅ Barcha VIP kontentlarni ko'rish\n"
            "✅ Kino va seriallarning yangi qismlarini birinchi tomosha qilish\n"
            "✅ Reklama ko'rsatilmaydi\n\n"
            "💰 <b>Narxlar:</b>\n"
            "• 30 kun — 20,000 so'm\n"
            "• 60 kun — 35,000 so'm\n"
            "• 90 kun — 50,000 so'm\n\n"
            "VIP olish uchun quyidagi tugmani bosing va to'lov qiling, admin tasdiqlaydi."
        )
        buttons = [
            [InlineKeyboardButton("💎 30 kun", callback_data="vip_req_30")],
            [InlineKeyboardButton("💎 60 kun", callback_data="vip_req_60")],
            [InlineKeyboardButton("💎 90 kun", callback_data="vip_req_90")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")],
        ]

    if query:
        await safe_edit_text(query, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def vip_request_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    days = int(query.data.replace("vip_req_", ""))
    user = query.from_user

    conn = get_db()
    conn.execute(
        "INSERT INTO vip_requests (user_id, days, request_date) VALUES (?, ?, ?)",
        (user.id, days, now_str()),
    )
    conn.commit()
    req_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    add_log(user.id, "VIP_REQUEST", f"{days} kun")

    for admin_id in get_all_admin_ids():
        try:
            await ctx.bot.send_message(
                admin_id,
                f"💎 <b>Yangi VIP so'rovi!</b>\n\n"
                f"👤 Foydalanuvchi: {user.full_name} (@{user.username})\n"
                f"🆔 ID: <code>{user.id}</code>\n"
                f"📅 Muddat: {days} kun\n"
                f"🔢 So'rov ID: {req_id}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"vip_confirm_{req_id}_{user.id}_{days}")],
                    [InlineKeyboardButton("❌ Rad etish", callback_data=f"vip_reject_{req_id}_{user.id}")],
                ]),
            )
        except TelegramError:
            pass

    await safe_edit_text(
        query,
        f"✅ <b>{days} kunlik VIP so'rovingiz yuborildi!</b>\n\n"
        "Admin tez orada ko'rib chiqadi. To'lov ma'lumotlari uchun admin bilan bog'laning.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]),
    )


def get_all_admin_ids() -> list[int]:
    conn = get_db()
    rows = conn.execute("SELECT user_id FROM users WHERE is_admin=1").fetchall()
    conn.close()
    ids = [r["user_id"] for r in rows]
    if SUPER_ADMIN_ID not in ids:
        ids.append(SUPER_ADMIN_ID)
    return ids


async def vip_confirm_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Ruxsat yo'q.", show_alert=True)
        return
    await query.answer()
    parts = query.data.split("_")
    req_id = int(parts[2])
    target_id = int(parts[3])
    days = int(parts[4])

    until = (datetime.datetime.now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    conn.execute("UPDATE vip_requests SET status='confirmed', confirm_date=? WHERE id=?", (now_str(), req_id))
    conn.execute("UPDATE users SET is_vip=1, vip_until=? WHERE user_id=?", (until, target_id))
    conn.commit()
    conn.close()
    add_log(target_id, "VIP_GRANTED", f"{days} kun, by {query.from_user.id}")

    try:
        await ctx.bot.send_message(
            target_id,
            f"🎉 Tabriklaymiz! <b>{days} kunlik VIP</b> hisobingizga qo'shildi!\n\n"
            f"📅 Tugash sanasi: {until}",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass

    await safe_edit_text(query, f"✅ VIP tasdiqlandi — {target_id} uchun {days} kun.")


async def vip_reject_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Ruxsat yo'q.", show_alert=True)
        return
    await query.answer()
    parts = query.data.split("_")
    req_id = int(parts[2])
    target_id = int(parts[3])

    conn = get_db()
    conn.execute("UPDATE vip_requests SET status='rejected' WHERE id=?", (req_id,))
    conn.commit()
    conn.close()
    add_log(target_id, "VIP_REJECTED", f"by {query.from_user.id}")

    try:
        await ctx.bot.send_message(target_id, "❌ VIP so'rovingiz rad etildi. Batafsil ma'lumot uchun admin bilan bog'laning.")
    except TelegramError:
        pass

    await safe_edit_text(query, "❌ VIP so'rovi rad etildi.")


# ============================================================
# ADMIN PANEL
# ============================================================

ADMIN_ADD_MEDIA = range(10, 20)

async def admin_panel_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        user = query.from_user
    else:
        user = update.effective_user

    if not is_admin(user.id):
        return

    conn = get_db()
    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    media_count = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    vip_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_vip=1").fetchone()[0]
    blocked_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_blocked=1").fetchone()[0]
    conn.close()

    text = (
        f"👮 <b>Admin Panel</b>\n\n"
        f"👥 Foydalanuvchilar: {user_count}\n"
        f"🎬 Medialar: {media_count}\n"
        f"💎 VIP: {vip_count}\n"
        f"🚫 Bloklangan: {blocked_count}"
    )
    buttons = [
        [InlineKeyboardButton("➕ Media qo'shish", callback_data="admin_add_media")],
        [
            InlineKeyboardButton("🗑 Media o'chirish", callback_data="admin_del_media"),
            InlineKeyboardButton("✏️ Media tahrirlash", callback_data="admin_edit_media"),
        ],
        [InlineKeyboardButton("✂️ Qism o'chirish", callback_data="admin_del_ep")],
        [InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin_users")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("💎 VIP ro'yxati", callback_data="admin_vip_list")],
        [
            InlineKeyboardButton("➕ Admin qo'shish", callback_data="admin_add_admin"),
            InlineKeyboardButton("➖ Admin o'chirish", callback_data="admin_remove_admin"),
        ],
        [
            InlineKeyboardButton("💾 Backup", callback_data="admin_backup"),
            InlineKeyboardButton("📊 Statistika", callback_data="admin_stats"),
        ],
        [InlineKeyboardButton("📋 Loglar", callback_data="admin_logs")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")],
    ]
    if query:
        await safe_edit_text(query, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


# ============================================================
# MEDIA QO'SHISH (ConversationHandler)
# ============================================================

MEDIA_TITLE, MEDIA_CAT, MEDIA_DESC, MEDIA_VIP, MEDIA_FILE = range(5)
# MEDIA_DESC va MEDIA_VIP endi ishlatilmaydi — kategoriyadan keyin to'g'ri fayl qabul

media_add_data: dict = {}

# Media group buffering
quick_group_buffer: dict = {}
quick_group_tasks: dict = {}

# Admin add media group buffer (oddiy admin add flow uchun)
admin_group_buffer: dict = {}
admin_group_tasks: dict = {}


async def admin_add_media_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    media_add_data[query.from_user.id] = {}
    await safe_edit_text(
        query,
        "🎬 Media sarlavhasini kiriting:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="admin_panel")]]),
    )
    return MEDIA_TITLE


async def media_get_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    media_add_data[uid]["title"] = update.message.text.strip()
    buttons = [
        [InlineKeyboardButton("🎬 Kino", callback_data="mcat_kino"), InlineKeyboardButton("📺 Serial", callback_data="mcat_serial")],
        [InlineKeyboardButton("🎵 Musiqa", callback_data="mcat_musiqa"), InlineKeyboardButton("📁 Boshqa", callback_data="mcat_boshqa")],
    ]
    await update.message.reply_text("Kategoriyani tanlang:", reply_markup=InlineKeyboardMarkup(buttons))
    return MEDIA_CAT


async def media_get_cat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    cat = query.data.replace("mcat_", "")
    media_add_data[uid]["category"] = cat
    media_add_data[uid]["description"] = ""
    media_add_data[uid]["is_vip"] = 0
    media_add_data[uid]["awaiting_file"] = True
    media_add_data[uid]["chat_id"] = query.message.chat_id
    await safe_edit_text(
        query,
        "📤 <b>Videolarni yuboring!</b>\n\n"
        "💡 Bir nechta qism uchun barcha videolarni <b>belgilab</b> bir vaqtda yuboring.\n"
        "Bot hammasini avtomatik saqlaydi.",
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


async def _admin_flush_job(ctx: ContextTypes.DEFAULT_TYPE):
    """Job queue orqali ishlaydi — barcha bufferdagi fayllarni saqlaydi"""
    job_data = ctx.job.data
    uid = job_data["uid"]
    chat_id = job_data["chat_id"]

    pending = admin_group_buffer.pop(uid, [])
    if not pending:
        return

    d = media_add_data.get(uid, {})
    if not d:
        return

    title = d.get("title", "Nomsiz")
    category = d.get("category", "boshqa")
    description = d.get("description", "")
    is_vip_content = d.get("is_vip", 0)

    conn = get_db()
    existing = conn.execute(
        "SELECT * FROM media WHERE LOWER(title)=LOWER(?) AND category=?", (title, category)
    ).fetchone()

    if existing and category == "serial":
        media_id = existing["id"]
        code = existing["code"]
        last_ep = conn.execute(
            "SELECT MAX(episode_num) as mx FROM episodes WHERE media_id=?", (media_id,)
        ).fetchone()["mx"]
        start_ep = 2 if last_ep is None else last_ep + 1
        for i, f in enumerate(pending):
            conn.execute(
                "INSERT INTO episodes (media_id, episode_num, file_id, file_type, add_date) VALUES (?,?,?,?,?)",
                (media_id, start_ep + i, f["file_id"], f["file_type"], now_str()),
            )
        conn.commit()
        total = start_ep - 1 + len(pending)
        conn.close()
        add_log(uid, "GROUP_EPISODE_ADD", f"media_id={media_id} eps={len(pending)}")
        if uid in media_add_data:
            del media_add_data[uid]
        await ctx.bot.send_message(
            chat_id,
            f"✅ <b>{len(pending)} ta qism qo'shildi!</b>\n\n"
            f"📌 Kod: <code>{code}</code>\n"
            f"📺 {title}\n"
            f"🎞 Jami: <b>{total} qism</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]),
        )
        return

    first = pending[0]
    code = next_media_code()
    conn.execute(
        """INSERT INTO media (code, title, description, category, file_id, file_type, is_vip, added_by, add_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (code, title, description, category, first["file_id"], first["file_type"], is_vip_content, uid, now_str()),
    )
    conn.commit()
    media_id = conn.execute("SELECT id FROM media WHERE code=?", (code,)).fetchone()[0]

    for i, f in enumerate(pending[1:], start=2):
        conn.execute(
            "INSERT INTO episodes (media_id, episode_num, file_id, file_type, add_date) VALUES (?,?,?,?,?)",
            (media_id, i, f["file_id"], f["file_type"], now_str()),
        )
    conn.commit()
    conn.close()

    add_log(uid, "GROUP_MEDIA_ADD", f"code={code} title={title} count={len(pending)}")
    if uid in media_add_data:
        del media_add_data[uid]

    await announce_new_media(ctx.bot, code, title, category, is_vip_content)
    await ctx.bot.send_message(
        chat_id,
        f"✅ <b>Media muvaffaqiyatli qo'shildi!</b>\n\n"
        f"📌 Kod: <code>{code}</code>\n"
        f"🎬 Sarlavha: {title}\n"
        f"🎞 Jami: <b>{len(pending)} qism</b>\n"
        f"📣 Kanal e'loni yuborildi!",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]),
    )


def _get_file_from_message(message):
    """Xabardan file_id va file_type ni aniqlaydi"""
    if message.video:
        return message.video.file_id, "video"
    if message.audio:
        return message.audio.file_id, "audio"
    if message.photo:
        return message.photo[-1].file_id, "photo"
    if message.document:
        return message.document.file_id, "document"
    return None, None


async def admin_file_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    ConversationHandler dan TASHQARIDA ishlaydigan global fayl handler.
    Admin media_add_data["awaiting_file"]=True bo'lsa barcha fayllarni qabul qiladi.
    Media group xabarlarini ham to'liq qabul qiladi.
    """
    uid = update.effective_user.id
    if not is_admin(uid):
        return

    d = media_add_data.get(uid, {})

    # /skip buyrug'i
    if update.message.text:
        if update.message.text.strip() == "/skip" and d.get("awaiting_file"):
            title = d.get("title", "Nomsiz")
            category = d.get("category", "boshqa")
            description = d.get("description", "")
            is_vip_content = d.get("is_vip", 0)
            code = next_media_code()
            conn = get_db()
            conn.execute(
                """INSERT INTO media (code, title, description, category, file_id, file_type, is_vip, added_by, add_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (code, title, description, category, None, "video", is_vip_content, uid, now_str()),
            )
            conn.commit()
            conn.close()
            add_log(uid, "MEDIA_ADD_SKIP", f"code={code} title={title}")
            if uid in media_add_data:
                del media_add_data[uid]
            await update.message.reply_text(
                f"✅ Media qo'shildi (fayl keyinroq)!\n\n📌 Kod: <code>{code}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]),
            )
        return  # Boshqa matn xabarlari bu handlerga tegishli emas

    # Fayl kelgan — awaiting_file tekshirish
    if not d.get("awaiting_file"):
        return

    file_id, file_type = _get_file_from_message(update.message)
    if not file_id:
        return

    chat_id = update.message.chat_id

    # BARCHA fayllarni bufferga qo'shamiz — bitta bo'lsa ham, ko'p bo'lsa ham
    # media_group_id ni tekshirmaymiz chunki ba'zan birinchi xabar None keladi
    admin_group_buffer.setdefault(uid, []).append({"file_id": file_id, "file_type": file_type})

    # Eski jobni bekor qilib, yangisini boshlaymiz (har fayl kelganda 3s kechiktiramiz)
    old_jobs = ctx.application.job_queue.get_jobs_by_name(f"admin_flush_{uid}")
    for job in old_jobs:
        job.schedule_removal()

    ctx.application.job_queue.run_once(
        _admin_flush_job,
        when=3,
        data={"uid": uid, "chat_id": chat_id},
        name=f"admin_flush_{uid}",
    )
    logger.info(
        f"admin_file_handler: uid={uid} "
        f"buffer={len(admin_group_buffer.get(uid, []))} "
        f"group={update.message.media_group_id}"
    )


# ============================================================
# TEZ YUKLASH REJIMI (/tezqoshish)
# ============================================================

QUICK_TITLE = 200


async def quick_add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "⚡ <b>Tez yuklash rejimi</b>\n\n"
        "Barcha videolarni belgilab bir vaqtda yuboring.\n"
        "Bot ularni avtomatik serial qismlari sifatida saqlaydi.\n\n"
        "Kategoriya: 📺 Serial | Hammaga ochiq\n\n"
        "Chiqish uchun /done yozing.",
        parse_mode=ParseMode.HTML,
    )
    return QUICK_TITLE


async def _flush_group_prompt(chat_id: int, uid: int, bot):
    """Eskirgan — faqat moslik uchun qoldirilgan"""
    pass


async def _quick_flush_job(ctx: ContextTypes.DEFAULT_TYPE):
    """Job queue orqali quick_add media group ni flush qiladi"""
    job_data = ctx.job.data
    uid = job_data["uid"]
    chat_id = job_data["chat_id"]
    pending = quick_group_buffer.get(uid, [])
    if pending:
        await ctx.bot.send_message(
            chat_id,
            f"📦 <b>{len(pending)} ta video</b> qabul qilindi!\n\n📝 Serial nomini yozing:",
            parse_mode=ParseMode.HTML,
        )


async def _save_group_as_serial(uid: int, title: str, files: list, update, ctx):
    """Bir nechta faylni serial qismlari sifatida saqlaydi"""
    conn = get_db()
    existing = conn.execute(
        "SELECT * FROM media WHERE LOWER(title)=LOWER(?) AND category='serial'", (title,)
    ).fetchone()

    if existing:
        media_id = existing["id"]
        code = existing["code"]
        last_ep = conn.execute(
            "SELECT MAX(episode_num) as mx FROM episodes WHERE media_id=?", (media_id,)
        ).fetchone()["mx"]
        start_ep = 2 if last_ep is None else last_ep + 1
        for i, f in enumerate(files):
            conn.execute(
                "INSERT INTO episodes (media_id, episode_num, file_id, file_type, add_date) VALUES (?,?,?,?,?)",
                (media_id, start_ep + i, f["file_id"], f["file_type"], now_str()),
            )
        conn.commit()
        total = start_ep - 1 + len(files)
        conn.close()
    else:
        code = next_media_code()
        first = files[0]
        conn.execute(
            """INSERT INTO media (code, title, description, category, file_id, file_type, is_vip, added_by, add_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, title, "", "serial", first["file_id"], first["file_type"], 0, uid, now_str()),
        )
        conn.commit()
        media_id = conn.execute("SELECT id FROM media WHERE code=?", (code,)).fetchone()[0]
        for i, f in enumerate(files[1:], start=2):
            conn.execute(
                "INSERT INTO episodes (media_id, episode_num, file_id, file_type, add_date) VALUES (?,?,?,?,?)",
                (media_id, i, f["file_id"], f["file_type"], now_str()),
            )
        conn.commit()
        conn.close()
        total = len(files)
        await announce_new_media(ctx.bot, code, title, "serial", 0)

    add_log(uid, "GROUP_ADD", f"code={code} title={title} eps={len(files)}")
    await update.message.reply_text(
        f"✅ <b>{len(files)} ta qism qo'shildi!</b>\n"
        f"📌 Kod: <code>{code}</code> | 📺 {title}\n"
        f"🎞 Jami: <b>{total} qism</b>\n\n"
        "Keyingi videolarni yuboring yoki /done yozing.",
        parse_mode=ParseMode.HTML,
    )


async def quick_add_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return

    if update.message.text:
        text = update.message.text.strip()
        if text in ("/done", "/stop", "/tugat"):
            quick_group_buffer.pop(uid, None)
            quick_group_tasks.pop(uid, None)
            old_jobs = ctx.application.job_queue.get_jobs_by_name(f"quick_flush_{uid}")
            for job in old_jobs:
                job.schedule_removal()
            await update.message.reply_text("✅ Tez yuklash rejimi tugatildi.\n\n/admin — Admin panel")
            return ConversationHandler.END

        pending = quick_group_buffer.pop(uid, [])
        t = quick_group_tasks.pop(uid, None)
        if t:
            t.cancel()
        if pending:
            await _save_group_as_serial(uid, text, pending, update, ctx)
            return QUICK_TITLE

        await update.message.reply_text("⚠️ Faqat video/fayl yuboring yoki /done yozing.")
        return QUICK_TITLE

    # Fayl
    file_id = None
    file_type = "video"
    if update.message.video:
        file_id = update.message.video.file_id
        file_type = "video"
    elif update.message.document:
        file_id = update.message.document.file_id
        file_type = "document"
    elif update.message.audio:
        file_id = update.message.audio.file_id
        file_type = "audio"
    else:
        await update.message.reply_text("⚠️ Faqat video/fayl yuboring yoki /done yozing.")
        return QUICK_TITLE

    media_group_id = update.message.media_group_id

    if media_group_id:
        quick_group_buffer.setdefault(uid, []).append({"file_id": file_id, "file_type": file_type})
        # Eski job ni bekor qilamiz
        old_jobs = ctx.application.job_queue.get_jobs_by_name(f"quick_flush_{uid}")
        for job in old_jobs:
            job.schedule_removal()
        # 3 soniyadan keyin prompt yuboramiz
        ctx.application.job_queue.run_once(
            _quick_flush_job,
            when=3,
            data={"uid": uid, "chat_id": update.message.chat_id},
            name=f"quick_flush_{uid}",
        )
        logger.info(f"quick_add_video: uid={uid} buffer={len(quick_group_buffer.get(uid, []))} group={media_group_id}")
        return QUICK_TITLE
    else:
        ctx.user_data["quick_file_id"] = file_id
        ctx.user_data["quick_file_type"] = file_type
        await update.message.reply_text("📝 Serial nomini yozing:")
        return QUICK_TITLE + 1


async def quick_add_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return

    title = update.message.text.strip()
    file_id = ctx.user_data.get("quick_file_id")
    file_type = ctx.user_data.get("quick_file_type", "video")

    conn = get_db()
    existing = conn.execute(
        "SELECT * FROM media WHERE LOWER(title)=LOWER(?) AND category='serial'", (title,)
    ).fetchone()

    if existing:
        last_ep = conn.execute(
            "SELECT MAX(episode_num) as mx FROM episodes WHERE media_id=?", (existing["id"],)
        ).fetchone()["mx"]
        new_ep_num = 2 if last_ep is None else last_ep + 1
        conn.execute(
            "INSERT INTO episodes (media_id, episode_num, file_id, file_type, add_date) VALUES (?,?,?,?,?)",
            (existing["id"], new_ep_num, file_id, file_type, now_str()),
        )
        conn.commit()
        conn.close()
        add_log(uid, "EPISODE_ADD", f"media_id={existing['id']} ep={new_ep_num} title={title}")
        await update.message.reply_text(
            f"✅ <b>{new_ep_num}-qism qo'shildi!</b>\n"
            f"📌 Kod: <code>{existing['code']}</code> | 📺 {title}\n"
            f"🎞 Jami: <b>{new_ep_num} qism</b>\n\n"
            "Keyingi videoni yuboring yoki /done yozing.",
            parse_mode=ParseMode.HTML,
        )
    else:
        code = next_media_code()
        conn.execute(
            """INSERT INTO media (code, title, description, category, file_id, file_type, is_vip, added_by, add_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, title, "", "serial", file_id, file_type, 0, uid, now_str()),
        )
        conn.commit()
        conn.close()
        add_log(uid, "QUICK_ADD", f"code={code} title={title}")
        await announce_new_media(ctx.bot, code, title, "serial", 0)
        await update.message.reply_text(
            f"✅ <b>Yangi serial qo'shildi!</b>\n"
            f"📌 Kod: <code>{code}</code> | 📺 {title}\n"
            f"📣 Kanal e'loni yuborildi!\n\n"
            "Keyingi videoni yuboring yoki /done yozing.",
            parse_mode=ParseMode.HTML,
        )
    return QUICK_TITLE


# ============================================================
# MEDIA O'CHIRISH
# ============================================================

DEL_MEDIA_STATE = 100

async def admin_del_media_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_text(
        query,
        "🗑 O'chirish uchun media kodini kiriting:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="admin_panel")]]),
    )
    return DEL_MEDIA_STATE


async def admin_del_media_do(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    conn = get_db()
    row = conn.execute("SELECT * FROM media WHERE code=?", (code,)).fetchone()
    if not row:
        await update.message.reply_text(f"❌ Kod '{code}' topilmadi.")
        conn.close()
        return DEL_MEDIA_STATE
    conn.execute("DELETE FROM media WHERE code=?", (code,))
    conn.execute("DELETE FROM episodes WHERE media_id=?", (row["id"],))
    conn.commit()
    conn.close()
    add_log(update.effective_user.id, "MEDIA_DELETE", f"code={code}")
    await update.message.reply_text(
        f"✅ «{row['title']}» (kod: {code}) o'chirildi.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]),
    )
    return ConversationHandler.END


# ============================================================
# QISM O'CHIRISH
# ============================================================

DEL_EP_CODE = 500

async def admin_del_ep_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_text(
        query,
        "✂️ <b>Qism o'chirish</b>\n\nSerial <b>kodini</b> kiriting:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="admin_panel")]]),
    )
    return DEL_EP_CODE


async def admin_del_ep_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    conn = get_db()
    m = conn.execute("SELECT * FROM media WHERE code=?", (code,)).fetchone()
    if not m:
        conn.close()
        await update.message.reply_text(
            f"❌ Kod '{code}' topilmadi. Qaytadan kiriting:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="admin_panel")]]),
        )
        return DEL_EP_CODE

    eps = conn.execute(
        "SELECT * FROM episodes WHERE media_id=? ORDER BY episode_num", (m["id"],)
    ).fetchall()
    conn.close()

    if not eps:
        await update.message.reply_text(
            f"⚠️ «{m['title']}» serialining qo'shimcha qismlari yo'q.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]),
        )
        return ConversationHandler.END

    ctx.user_data["del_ep_media_title"] = m["title"]
    buttons = []
    row = []
    for ep in eps:
        row.append(InlineKeyboardButton(
            f"🗑 {ep['episode_num']}-qism",
            callback_data=f"ep_del_{ep['id']}"
        ))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("❌ Bekor", callback_data="admin_panel")])

    await update.message.reply_text(
        f"✂️ <b>{m['title']}</b>\n\nO'chirmoqchi bo'lgan qismni tanlang:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return ConversationHandler.END


async def admin_del_ep_do(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ep_id = int(query.data.replace("ep_del_", ""))
    conn = get_db()
    ep = conn.execute(
        "SELECT e.*, m.title FROM episodes e JOIN media m ON e.media_id=m.id WHERE e.id=?",
        (ep_id,)
    ).fetchone()
    if not ep:
        conn.close()
        await safe_edit_text(query, "❌ Qism topilmadi.")
        return
    conn.execute("DELETE FROM episodes WHERE id=?", (ep_id,))
    conn.commit()
    conn.close()
    add_log(query.from_user.id, "EP_DELETE", f"ep_id={ep_id} serial={ep['title']}")
    await safe_edit_text(
        query,
        f"✅ «{ep['title']}» — {ep['episode_num']}-qism o'chirildi.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]),
    )


# ============================================================
# MEDIA TAHRIRLASH
# ============================================================

EDIT_CODE, EDIT_VALUE = 400, 401

async def admin_edit_media_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_text(
        query,
        "✏️ <b>Media tahrirlash</b>\n\nTahrirlamoqchi bo'lgan media <b>kodini</b> kiriting:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="admin_panel")]]),
    )
    return EDIT_CODE


async def admin_edit_code_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    conn = get_db()
    m = conn.execute("SELECT * FROM media WHERE code=?", (code,)).fetchone()
    conn.close()
    if not m:
        await update.message.reply_text(
            f"❌ Kod '{code}' topilmadi. Qaytadan kiriting:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="admin_panel")]]),
        )
        return EDIT_CODE
    ctx.user_data["edit_media_id"] = m["id"]
    vip_label = "✅ Ha" if m["is_vip"] else "❌ Yo'q"
    text = (
        f"✏️ <b>{m['title']}</b> (kod: {m['code']})\n\n"
        f"📝 Tavsif: {m['description'] or '—'}\n"
        f"💎 VIP: {vip_label}\n\n"
        f"Nimani tahrirlash kerak?"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📌 Nomini o'zgartir",       callback_data="ef_title")],
            [InlineKeyboardButton("📝 Tavsifini o'zgartir",    callback_data="ef_desc")],
            [InlineKeyboardButton("💎 VIP holatini almashtir", callback_data="ef_vip")],
            [InlineKeyboardButton("❌ Bekor",                  callback_data="admin_panel")],
        ]),
    )
    return EDIT_VALUE


async def admin_edit_field_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.replace("ef_", "")
    media_id = ctx.user_data.get("edit_media_id")

    if field == "vip":
        conn = get_db()
        m = conn.execute("SELECT is_vip, title FROM media WHERE id=?", (media_id,)).fetchone()
        new_vip = 0 if m["is_vip"] else 1
        conn.execute("UPDATE media SET is_vip=? WHERE id=?", (new_vip, media_id))
        conn.commit()
        conn.close()
        label = "💎 VIP qilindi" if new_vip else "🔓 VIP olib tashlandi"
        await safe_edit_text(
            query,
            f"✅ «{m['title']}» — {label}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]),
        )
        return ConversationHandler.END

    prompts = {"title": "Yangi nomni kiriting:", "desc": "Yangi tavsifni kiriting:"}
    ctx.user_data["edit_field"] = field
    await safe_edit_text(
        query,
        prompts.get(field, "Yangi qiymatni kiriting:"),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="admin_panel")]]),
    )
    return EDIT_VALUE


async def admin_edit_value_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    media_id = ctx.user_data.get("edit_media_id")
    field = ctx.user_data.get("edit_field")

    col_map = {"title": "title", "desc": "description"}
    col = col_map.get(field)
    if not col or not media_id:
        await update.message.reply_text("❌ Xatolik. Qaytadan urinib ko'ring.")
        return ConversationHandler.END

    conn = get_db()
    conn.execute(f"UPDATE media SET {col}=? WHERE id=?", (value, media_id))
    conn.commit()
    m = conn.execute("SELECT title, code FROM media WHERE id=?", (media_id,)).fetchone()
    conn.close()
    add_log(update.effective_user.id, "MEDIA_EDIT", f"id={media_id} field={field}")
    await update.message.reply_text(
        f"✅ «{m['title']}» (kod: {m['code']}) yangilandi!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]),
    )
    return ConversationHandler.END


# ============================================================
# BROADCAST
# ============================================================

BROADCAST_STATE = 201

async def admin_broadcast_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    await safe_edit_text(
        query,
        "📢 Barcha foydalanuvchilarga yuboriladigan xabarni kiriting:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="admin_panel")]]),
    )
    return BROADCAST_STATE


async def admin_broadcast_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return ConversationHandler.END

    status_msg = await update.message.reply_text("📢 Broadcast boshlandi...")

    BATCH = 25
    offset = 0
    total = 0
    failed = 0

    while True:
        conn = get_db()
        batch = conn.execute(
            "SELECT user_id FROM users WHERE is_blocked=0 LIMIT ? OFFSET ?",
            (BATCH, offset),
        ).fetchall()
        conn.close()

        if not batch:
            break

        for row in batch:
            try:
                await ctx.bot.copy_message(
                    chat_id=row["user_id"],
                    from_chat_id=update.message.chat_id,
                    message_id=update.message.message_id,
                )
                total += 1
            except (TelegramError, Forbidden):
                failed += 1
            await asyncio.sleep(0.05)

        offset += BATCH

        if offset % 100 == 0:
            try:
                await status_msg.edit_text(f"📢 Yuborilmoqda... ✅ {total} / ❌ {failed}")
            except TelegramError:
                pass

        await asyncio.sleep(0.3)

    add_log(uid, "BROADCAST", f"sent={total} failed={failed}")
    try:
        await status_msg.edit_text(
            f"📢 Broadcast tugadi!\n✅ Yuborildi: {total}\n❌ Yuborilmadi: {failed}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]),
        )
    except TelegramError:
        await update.message.reply_text(
            f"📢 Broadcast tugadi!\n✅ Yuborildi: {total}\n❌ Yuborilmadi: {failed}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]),
        )
    return ConversationHandler.END


# ============================================================
# FOYDALANUVCHILAR
# ============================================================

async def admin_users_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    conn = get_db()
    users = conn.execute("SELECT * FROM users ORDER BY join_date DESC LIMIT 20").fetchall()
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()

    text = f"👥 <b>Foydalanuvchilar ({total} ta)</b>\n\nSo'nggi 20 ta:\n\n"
    buttons = []
    for u in users:
        block = "🚫" if u["is_blocked"] else "✅"
        vip = "💎" if u["is_vip"] else ""
        text_btn = f"{block}{vip} {u['full_name'] or u['user_id']}"
        buttons.append([InlineKeyboardButton(text_btn, callback_data=f"user_info_{u['user_id']}")])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")])
    await safe_edit_text(query, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def user_info_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    target_id = int(query.data.replace("user_info_", ""))
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE user_id=?", (target_id,)).fetchone()
    conn.close()

    if not u:
        await query.answer("Topilmadi.", show_alert=True)
        return

    text = (
        f"👤 <b>Foydalanuvchi ma'lumotlari</b>\n\n"
        f"🆔 ID: <code>{u['user_id']}</code>\n"
        f"👤 Ism: {u['full_name']}\n"
        f"@{u['username'] or '—'}\n"
        f"💎 VIP: {'Ha' if u['is_vip'] else 'Yoq'}\n"
        f"👮 Admin: {'Ha' if u['is_admin'] else 'Yoq'}\n"
        f"🚫 Bloklangan: {'Ha' if u['is_blocked'] else 'Yoq'}\n"
        f"🎬 Ko'rgan: {u['media_watched']}\n"
        f"👥 Referal: {u['referral_count']}\n"
        f"📅 Qo'shilgan: {u['join_date']}"
    )
    block_btn = "✅ Unblock" if u["is_blocked"] else "🚫 Block"
    block_cb = f"unblock_{target_id}" if u["is_blocked"] else f"block_{target_id}"
    vip_btn = "❌ VIP olish" if u["is_vip"] else "💎 VIP berish"
    vip_cb = f"admin_revoke_vip_{target_id}" if u["is_vip"] else f"admin_give_vip_{target_id}"

    buttons = [
        [InlineKeyboardButton(block_btn, callback_data=block_cb)],
        [InlineKeyboardButton(vip_btn, callback_data=vip_cb)],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="admin_users")],
    ]
    await safe_edit_text(query, text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def block_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    target_id = int(query.data.replace("block_", ""))
    conn = get_db()
    conn.execute("UPDATE users SET is_blocked=1 WHERE user_id=?", (target_id,))
    conn.commit()
    conn.close()
    add_log(query.from_user.id, "BLOCK", str(target_id))
    await query.answer(f"✅ {target_id} bloklandi.", show_alert=True)
    await user_info_handler(update, ctx)


async def unblock_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    target_id = int(query.data.replace("unblock_", ""))
    conn = get_db()
    conn.execute("UPDATE users SET is_blocked=0 WHERE user_id=?", (target_id,))
    conn.commit()
    conn.close()
    add_log(query.from_user.id, "UNBLOCK", str(target_id))
    await query.answer(f"✅ {target_id} blokdan chiqarildi.", show_alert=True)
    await user_info_handler(update, ctx)


async def admin_give_vip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    target_id = int(query.data.replace("admin_give_vip_", ""))
    until = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    conn.execute("UPDATE users SET is_vip=1, vip_until=? WHERE user_id=?", (until, target_id))
    conn.commit()
    conn.close()
    add_log(query.from_user.id, "VIP_GIVE", str(target_id))
    try:
        await ctx.bot.send_message(target_id, "💎 Sizga 30 kunlik VIP berildi!")
    except TelegramError:
        pass
    await query.answer("✅ VIP berildi!", show_alert=True)


async def admin_revoke_vip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    target_id = int(query.data.replace("admin_revoke_vip_", ""))
    conn = get_db()
    conn.execute("UPDATE users SET is_vip=0, vip_until=NULL WHERE user_id=?", (target_id,))
    conn.commit()
    conn.close()
    add_log(query.from_user.id, "VIP_REVOKE", str(target_id))
    try:
        await ctx.bot.send_message(target_id, "❌ VIP statusingiz bekor qilindi.")
    except TelegramError:
        pass
    await query.answer("✅ VIP olib tashlandi!", show_alert=True)


async def admin_vip_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    conn = get_db()
    users = conn.execute("SELECT * FROM users WHERE is_vip=1").fetchall()
    conn.close()

    if not users:
        await safe_edit_text(
            query,
            "💎 Hozircha VIP foydalanuvchilar yo'q.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]]),
        )
        return

    text = f"💎 <b>VIP foydalanuvchilar ({len(users)} ta):</b>\n\n"
    for u in users:
        text += f"• {u['full_name']} (ID: {u['user_id']}) — {u['vip_until']}\n"
    await safe_edit_text(
        query,
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]]),
    )


# ============================================================
# ADMIN QO'SHISH / O'CHIRISH
# ============================================================

ADD_ADMIN_STATE = 302
REMOVE_ADMIN_STATE = 303


async def admin_add_admin_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != SUPER_ADMIN_ID:
        await query.answer("Faqat superadmin!", show_alert=True)
        return
    await safe_edit_text(
        query,
        "➕ Admin qilmoqchi bo'lgan foydalanuvchi ID sini kiriting:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="admin_panel")]]),
    )
    return ADD_ADMIN_STATE


async def admin_add_admin_do(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPER_ADMIN_ID:
        return ConversationHandler.END
    uid_str = update.message.text.strip()
    if not uid_str.isdigit():
        await update.message.reply_text("❌ Noto'g'ri ID.")
        return ADD_ADMIN_STATE
    target_id = int(uid_str)
    conn = get_db()
    conn.execute("UPDATE users SET is_admin=1 WHERE user_id=?", (target_id,))
    conn.commit()
    conn.close()
    add_log(update.effective_user.id, "ADD_ADMIN", str(target_id))
    await update.message.reply_text(
        f"✅ {target_id} admin qilindi.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]),
    )
    return ConversationHandler.END


async def admin_remove_admin_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != SUPER_ADMIN_ID:
        await query.answer("Faqat superadmin!", show_alert=True)
        return
    await safe_edit_text(
        query,
        "➖ Adminlikdan olib tashlamoqchi bo'lgan foydalanuvchi ID sini kiriting:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="admin_panel")]]),
    )
    return REMOVE_ADMIN_STATE


async def admin_remove_admin_do(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPER_ADMIN_ID:
        return ConversationHandler.END
    uid_str = update.message.text.strip()
    if not uid_str.isdigit():
        await update.message.reply_text("❌ Noto'g'ri ID.")
        return REMOVE_ADMIN_STATE
    target_id = int(uid_str)
    conn = get_db()
    conn.execute("UPDATE users SET is_admin=0 WHERE user_id=?", (target_id,))
    conn.commit()
    conn.close()
    add_log(update.effective_user.id, "REMOVE_ADMIN", str(target_id))
    await update.message.reply_text(
        f"✅ {target_id} adminlikdan olindi.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]]),
    )
    return ConversationHandler.END


# ============================================================
# STATISTIKA
# ============================================================

async def admin_stats_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    vip_users = conn.execute("SELECT COUNT(*) FROM users WHERE is_vip=1").fetchone()[0]
    blocked = conn.execute("SELECT COUNT(*) FROM users WHERE is_blocked=1").fetchone()[0]
    total_media = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    total_views = conn.execute("SELECT SUM(view_count) FROM media").fetchone()[0] or 0
    vip_pending = conn.execute("SELECT COUNT(*) FROM vip_requests WHERE status='pending'").fetchone()[0]
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    today_users = conn.execute("SELECT COUNT(*) FROM users WHERE join_date LIKE ?", (f"{today}%",)).fetchone()[0]
    conn.close()

    text = (
        f"📊 <b>Statistika</b>\n\n"
        f"👥 Jami foydalanuvchilar: <b>{total_users}</b>\n"
        f"🆕 Bugungi yangi: <b>{today_users}</b>\n"
        f"💎 VIP: <b>{vip_users}</b>\n"
        f"🚫 Bloklangan: <b>{blocked}</b>\n\n"
        f"🎬 Jami medialar: <b>{total_media}</b>\n"
        f"👁 Jami ko'rishlar: <b>{total_views}</b>\n\n"
        f"⏳ Kutilayotgan VIP so'rovlar: <b>{vip_pending}</b>"
    )
    await safe_edit_text(
        query,
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]]),
    )


# ============================================================
# BACKUP
# ============================================================

async def admin_backup_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"backup_{timestamp}.db")
    shutil.copy2(DB_FILE, backup_path)
    add_log(query.from_user.id, "BACKUP", backup_path)

    await safe_edit_text(
        query,
        f"💾 Backup yaratildi:\n<code>{backup_path}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]]),
    )

    try:
        with open(backup_path, "rb") as f:
            await ctx.bot.send_document(
                query.message.chat_id,
                f,
                filename=f"backup_{timestamp}.db",
                caption="💾 Ma'lumotlar bazasi backup fayli",
            )
    except TelegramError:
        pass


# ============================================================
# LOGLAR
# ============================================================

async def admin_logs_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    conn = get_db()
    logs = conn.execute("SELECT * FROM logs ORDER BY ts DESC LIMIT 30").fetchall()
    conn.close()

    if not logs:
        text = "📋 Loglar bo'sh."
    else:
        text = "📋 <b>So'nggi 30 ta log:</b>\n\n"
        for l in logs:
            text += f"[{l['ts']}] {l['action']} | ID:{l['user_id']} | {l['detail']}\n"

    await safe_edit_text(
        query,
        text[:4096],
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]]),
    )


# ============================================================
# BOT HAQIDA, YORDAM, QOIDALAR
# ============================================================

async def about_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_text(
        query,
        f"ℹ️ <b>Bot haqida</b>\n\n{BOT_ABOUT}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]),
    )


async def help_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    text = (
        "❓ <b>Yordam</b>\n\n"
        "📌 <b>Medialarni topish:</b>\n"
        "• Media kodini (masalan: 1001) to'g'ridan-to'g'ri yuboring\n"
        "• Yoki menyudan kategoriya tanlang\n"
        "• 🔍 Qidiruv orqali nom bo'yicha izlang\n\n"
        "💎 <b>VIP:</b>\n"
        "• /vip buyrug'ini yuboring\n"
        "• Admin tasdiqlaydi\n\n"
        "👥 <b>Referal:</b>\n"
        "• Profilimdan havolangizni oling\n"
        "• Do'stlaringizni taklif qiling\n\n"
        "🆘 Muammo bo'lsa admin bilan bog'laning."
    )
    buttons = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]])
    if query:
        await safe_edit_text(query, text, parse_mode=ParseMode.HTML, reply_markup=buttons)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=buttons)


async def rules_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📜 <b>Bot qoidalari</b>\n\n"
        "1. Bot faqat qonuniy maqsadlarda ishlatiladi.\n"
        "2. Spam yuborish taqiqlanadi.\n"
        "3. Mualliflik huquqlarini hurmat qiling.\n"
        "4. Taqiqlangan kontentni tarqatish man etiladi.\n"
        "5. Qoidalarni buzganlar bloklanadi.\n\n"
        "Qoidalarni buzganlar ogohlantirilmasdan bloklanadi!"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]),
    )


# ============================================================
# ASOSIY MENYU CALLBACK
# ============================================================

async def main_menu_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    await safe_edit_text(
        query,
        f"👋 Xush kelibsiz, <b>{user.first_name}</b>!\n\nQuyidagi menyudan foydalaning:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(user.id),
    )


# ============================================================
# XATO USHLASH
# ============================================================

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error("Xato yuz berdi", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Xato yuz berdi. Iltimos, qayta urinib ko'ring.")
        except TelegramError:
            pass


# ============================================================
# VIP MUDDAT TEKSHIRUV (Avtomatik)
# ============================================================

async def check_vip_expiry(ctx: ContextTypes.DEFAULT_TYPE):
    now = now_str()
    conn = get_db()
    expired = conn.execute(
        "SELECT user_id FROM users WHERE is_vip=1 AND vip_until IS NOT NULL AND vip_until < ?",
        (now,),
    ).fetchall()
    for row in expired:
        uid = row["user_id"]
        conn.execute("UPDATE users SET is_vip=0, vip_until=NULL WHERE user_id=?", (uid,))
        add_log(uid, "VIP_AUTO_EXPIRED")
        try:
            await ctx.bot.send_message(
                uid,
                "⚠️ Sizning VIP muddatingiz tugadi. Yangilash uchun /vip buyrug'ini yuboring."
            )
        except TelegramError:
            pass
    conn.commit()
    conn.close()


# ============================================================
# AVTOMATIK BACKUP (kunlik)
# ============================================================

async def auto_backup(ctx: ContextTypes.DEFAULT_TYPE):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"auto_backup_{timestamp}.db")
    shutil.copy2(DB_FILE, backup_path)
    add_log(None, "AUTO_BACKUP", backup_path)
    logger.info(f"Avtomatik backup: {backup_path}")

    backups = sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.endswith(".db")],
        reverse=True,
    )
    for old in backups[10:]:
        os.remove(os.path.join(BACKUP_DIR, old))


# ============================================================
# KUNLIK STATISTIKA HISOBOTI (Avtomatik)
# ============================================================

async def daily_stats_report(ctx: ContextTypes.DEFAULT_TYPE):
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    conn = get_db()
    total_users   = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    new_today     = conn.execute("SELECT COUNT(*) FROM users WHERE join_date LIKE ?", (f"{today}%",)).fetchone()[0]
    vip_count     = conn.execute("SELECT COUNT(*) FROM users WHERE is_vip=1").fetchone()[0]
    blocked_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_blocked=1").fetchone()[0]
    total_media   = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    total_views   = conn.execute("SELECT SUM(view_count) FROM media").fetchone()[0] or 0
    top3 = conn.execute(
        "SELECT title, code, view_count FROM media ORDER BY view_count DESC LIMIT 3"
    ).fetchall()
    conn.close()

    medals = ["🥇", "🥈", "🥉"]
    top_text = ""
    for i, m in enumerate(top3):
        top_text += f"{medals[i]} {m['code']} | {m['title']} — {m['view_count']} 👁\n"
    if not top_text:
        top_text = "Hozircha ma'lumot yo'q\n"

    now_time = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    text = (
        f"📊 <b>Kunlik hisobot</b> — {now_time}\n\n"
        f"👥 Jami foydalanuvchi: <b>{total_users}</b>\n"
        f"🆕 Bugun qo'shildi: <b>{new_today}</b>\n"
        f"💎 VIP: <b>{vip_count}</b>\n"
        f"🚫 Bloklangan: <b>{blocked_count}</b>\n\n"
        f"🎬 Jami medialar: <b>{total_media}</b>\n"
        f"👁 Jami ko'rishlar: <b>{total_views}</b>\n\n"
        f"🔥 <b>Eng ko'p ko'rilgan Top 3:</b>\n{top_text}"
    )

    for admin_id in get_all_admin_ids():
        try:
            await ctx.bot.send_message(admin_id, text, parse_mode=ParseMode.HTML)
        except TelegramError:
            pass

    add_log(None, "DAILY_STATS_SENT", f"admins={len(get_all_admin_ids())}")
    logger.info("Kunlik statistika hisoboti yuborildi.")


# ============================================================
# ASOSIY BOT ISHGA TUSHIRISH
# ============================================================

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN topilmadi! Iltimos, BOT_TOKEN environment variable o'rnating.")
        return

    init_db()

    conn = get_db()
    conn.execute(
        """INSERT OR IGNORE INTO users
           (user_id, username, full_name, join_date, last_seen, is_admin)
           VALUES (?, 'superadmin', 'Super Admin', ?, ?, 1)""",
        (SUPER_ADMIN_ID, now_str(), now_str()),
    )
    conn.execute("UPDATE users SET is_admin=1 WHERE user_id=?", (SUPER_ADMIN_ID,))
    conn.commit()
    conn.close()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(30)
        .write_timeout(60)
        .connect_timeout(30)
        .pool_timeout(15)
        .concurrent_updates(8)
        .build()
    )

    _cancel = CommandHandler("cancel", cancel_command)
    _timeout_state = {ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, conv_timeout_handler)]}

    _universal_fallbacks = [
        _cancel,
        CommandHandler("start", start_command),
        CallbackQueryHandler(main_menu_callback,   pattern="^main_menu$"),
        CallbackQueryHandler(admin_panel_handler,  pattern="^admin_panel$"),
        CallbackQueryHandler(top10_menu,           pattern="^top10_menu$"),
        CallbackQueryHandler(top10_views,          pattern="^top10_views$"),
        CallbackQueryHandler(top10_episodes,       pattern="^top10_episodes$"),
        CallbackQueryHandler(category_handler,     pattern="^cat_"),
        CallbackQueryHandler(show_media,           pattern="^media_"),
        CallbackQueryHandler(favorites_handler,    pattern="^favorites$"),
        CallbackQueryHandler(latest_handler,       pattern="^latest$"),
        CallbackQueryHandler(random_media_handler, pattern="^random_media$"),
        CallbackQueryHandler(profile_handler,      pattern="^profile$"),
        CallbackQueryHandler(vip_info_handler,     pattern="^vip_info$"),
    ]

    # Media qo'shish: faqat nom + kategoriya, keyin fayl (admin_file_handler)
    add_media_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_media_start, pattern="^admin_add_media$")],
        states={
            MEDIA_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, media_get_title)],
            MEDIA_CAT:   [CallbackQueryHandler(media_get_cat, pattern="^mcat_")],
            **_timeout_state,
        },
        fallbacks=_universal_fallbacks,
        conversation_timeout=300,
    )

    # Media o'chirish
    del_media_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_del_media_start, pattern="^admin_del_media$")],
        states={
            DEL_MEDIA_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_del_media_do)],
            **_timeout_state,
        },
        fallbacks=_universal_fallbacks,
        conversation_timeout=300,
    )

    # Broadcast
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast$")],
        states={
            BROADCAST_STATE: [MessageHandler(filters.ALL, admin_broadcast_send)],
            **_timeout_state,
        },
        fallbacks=_universal_fallbacks,
        conversation_timeout=300,
    )

    # Qidiruv
    search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(search_start, pattern="^search$")],
        states={
            SEARCH_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_query)],
            **_timeout_state,
        },
        fallbacks=_universal_fallbacks,
        conversation_timeout=300,
    )

    # Kategoriya bo'yicha qidiruv
    cat_search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cat_search_start, pattern="^cat_search_")],
        states={
            CAT_SEARCH_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cat_search_query)],
            **_timeout_state,
        },
        fallbacks=_universal_fallbacks,
        conversation_timeout=300,
    )

    # Admin qo'shish
    add_admin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_admin_start, pattern="^admin_add_admin$")],
        states={
            ADD_ADMIN_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_admin_do)],
            **_timeout_state,
        },
        fallbacks=_universal_fallbacks,
        conversation_timeout=300,
    )

    # Admin o'chirish
    remove_admin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_remove_admin_start, pattern="^admin_remove_admin$")],
        states={
            REMOVE_ADMIN_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_remove_admin_do)],
            **_timeout_state,
        },
        fallbacks=_universal_fallbacks,
        conversation_timeout=300,
    )

    # Qism o'chirish
    del_ep_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_del_ep_start, pattern="^admin_del_ep$")],
        states={
            DEL_EP_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_del_ep_code)],
            **_timeout_state,
        },
        fallbacks=_universal_fallbacks,
        conversation_timeout=300,
    )

    # Media tahrirlash
    edit_media_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_edit_media_start, pattern="^admin_edit_media$")],
        states={
            EDIT_CODE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_code_received)],
            EDIT_VALUE: [
                CallbackQueryHandler(admin_edit_field_chosen, pattern="^ef_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_value_received),
            ],
            **_timeout_state,
        },
        fallbacks=_universal_fallbacks,
        conversation_timeout=300,
    )

    # Tez yuklash rejimi
    quick_add_conv = ConversationHandler(
        entry_points=[CommandHandler("tezqoshish", quick_add_start)],
        states={
            QUICK_TITLE: [
                MessageHandler(filters.VIDEO | filters.AUDIO | filters.Document.ALL, quick_add_video),
                MessageHandler(filters.TEXT, quick_add_video),
            ],
            QUICK_TITLE + 1: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, quick_add_title),
            ],
            **_timeout_state,
        },
        fallbacks=_universal_fallbacks,
        allow_reentry=True,
        conversation_timeout=600,
    )

    # ---- HANDLERLAR QO'SHISH ----

    app.add_handler(CommandHandler("start",   start_command))
    app.add_handler(CommandHandler("help",    help_handler))
    app.add_handler(CommandHandler("rules",   rules_command))
    app.add_handler(CommandHandler("profile", profile_handler))
    app.add_handler(CommandHandler("admin",   admin_panel_handler))
    app.add_handler(CommandHandler("vip",     vip_info_handler))
    app.add_handler(CommandHandler("cancel",  cancel_command))

    app.add_handler(quick_add_conv)
    app.add_handler(add_media_conv)
    app.add_handler(del_media_conv)
    app.add_handler(del_ep_conv)
    app.add_handler(edit_media_conv)
    app.add_handler(broadcast_conv)
    app.add_handler(cat_search_conv)
    app.add_handler(search_conv)
    app.add_handler(add_admin_conv)
    app.add_handler(remove_admin_conv)

    # Callback handlers — group=-1
    G = -1

    def h(func, pattern):
        app.add_handler(CallbackQueryHandler(nav(func), pattern=pattern), G)

    h(check_sub_callback,        "^check_sub$")
    h(main_menu_callback,        "^main_menu$")
    h(top10_menu,                "^top10_menu$")
    h(top10_views,               "^top10_views$")
    h(top10_episodes,            "^top10_episodes$")
    h(category_handler,          "^cat_(?!search_)")
    h(show_episode_1,            "^ep1_")
    h(admin_del_ep_do,           "^ep_del_")
    h(show_episode,              "^ep_(?!del_)")
    h(show_media,                "^media_")
    h(random_media_handler,      "^random_media$")
    h(latest_handler,            "^latest$")
    h(favorites_handler,         "^favorites$")
    h(fav_add,                   "^fav_add_")
    h(fav_remove,                "^fav_remove_")
    h(rate_open_callback,        "^rate_open_")
    h(rate_submit_callback,      r"^rate_\d+_\d+$")
    h(profile_handler,           "^profile$")
    h(vip_info_handler,          "^vip_info$")
    h(vip_info_handler,          "^vip_buy$")
    h(vip_request_handler,       "^vip_req_")
    h(vip_confirm_handler,       "^vip_confirm_")
    h(vip_reject_handler,        "^vip_reject_")
    h(about_handler,             "^about$")
    h(help_handler,              "^help$")
    h(admin_panel_handler,       "^admin_panel$")
    h(admin_users_handler,       "^admin_users$")
    h(user_info_handler,         "^user_info_")
    h(block_user,                "^block_")
    h(unblock_user,              "^unblock_")
    h(admin_give_vip,            "^admin_give_vip_")
    h(admin_revoke_vip,          "^admin_revoke_vip_")
    h(admin_vip_list,            "^admin_vip_list$")
    h(admin_stats_handler,       "^admin_stats$")
    h(admin_backup_handler,      "^admin_backup$")
    h(admin_logs_handler,        "^admin_logs$")

    # Admin fayl yuklash — ConversationHandler dan TASHQARIDA, media group uchun
    # Group=2 da ishlaydi, ConversationHandlerlar (group=0) bilan parallel
    _admin_media_filter = (
        filters.VIDEO | filters.AUDIO | filters.PHOTO |
        filters.Document.ALL |
        (filters.TEXT & ~filters.COMMAND)
    )
    app.add_handler(MessageHandler(_admin_media_filter, admin_file_handler), group=2)

    # Raqamli kod orqali media qidiruv
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"^\d+$"), code_search))

    # Catch-all
    async def _catchall_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if query:
            try:
                await query.answer("⚠️ Bu tugma eskirgan. Iltimos, /start bosing.")
            except Exception:
                pass
    app.add_handler(CallbackQueryHandler(_catchall_callback), group=1)

    app.add_error_handler(error_handler)

    # Davriy vazifalar
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(check_vip_expiry, interval=3600, first=60)
        job_queue.run_repeating(auto_backup, interval=86400, first=3600)
        job_queue.run_daily(daily_stats_report, time=datetime.time(hour=9, minute=0, second=0))

    # Keep-alive HTTP server — Replit uxlab qolmaslik uchun
    keep_alive_port = int(os.environ.get("PORT", 8000))

    class _PingHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Bot ishlayapti! ✅".encode("utf-8"))
        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
        def log_message(self, *args):
            pass

    def _run_server():
        for port in [keep_alive_port, keep_alive_port + 1, keep_alive_port + 2]:
            try:
                server = HTTPServer(("0.0.0.0", port), _PingHandler)
                logger.info(f"Keep-alive server port {port} da ishga tushdi.")
                server.serve_forever()
                return
            except OSError:
                continue
        logger.warning("Keep-alive server: hech bir port bo'sh emas, o'tkazib yuborildi.")

    t = threading.Thread(target=_run_server, daemon=True)
    t.start()

    logger.info("Bot ishga tushdi!")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        timeout=30,
    )


if __name__ == "__main__":
    attempt = 0
    while True:
        try:
            attempt += 1
            logger.info(f"Bot ishga tushirilmoqda (urinish #{attempt})...")
            main()
            logger.info("Bot normal to'xtadi.")
            break
        except KeyboardInterrupt:
            logger.info("Bot to'xtatildi (Ctrl+C).")
            break
        except Exception as exc:
            wait = min(10 * attempt, 120)
            logger.error(f"Bot to'xtadi: {exc}. {wait} soniyadan keyin qayta ishga tushadi...")
            time.sleep(wait)
