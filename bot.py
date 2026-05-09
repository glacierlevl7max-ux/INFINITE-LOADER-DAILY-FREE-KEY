import os
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "6180067276"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1002798221648"))
CHANNEL_JOIN_URL = os.getenv("CHANNEL_JOIN_URL", "https://t.me/BGMIxSAFExHACKS").strip()
DB_PATH = os.getenv("DB_PATH", "bot.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class AdminFlow(StatesGroup):
    waiting_template = State()
    waiting_add_single = State()
    waiting_add_bulk = State()
    waiting_broadcast = State()
    waiting_cooldown = State()
    waiting_default_limit = State()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value)


def fmt_remaining(delta: timedelta) -> str:
    total = max(0, int(delta.total_seconds()))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h}h {m}m {s}s"


def join_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Join Channel", url=CHANNEL_JOIN_URL)
    kb.button(text="✅ Verify Now", callback_data="user:verify")
    kb.adjust(1)
    return kb.as_markup()


def user_home_kb(unlocked: bool):
    kb = InlineKeyboardBuilder()
    if unlocked:
        kb.button(text="🎁 Claim Gift", callback_data="user:claim")
        kb.button(text="🔄 Recheck Channel", callback_data="user:verify")
    else:
        kb.button(text="➕ Join Channel", url=CHANNEL_JOIN_URL)
        kb.button(text="✅ Verify Now", callback_data="user:verify")
    kb.adjust(1)
    return kb.as_markup()


def owner_panel_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 Set Template", callback_data="adm:template")
    kb.button(text="➕ Add Single Key", callback_data="adm:add_single")
    kb.button(text="🧾 Add Bulk Keys", callback_data="adm:add_bulk")
    kb.button(text="⏳ Set Cooldown", callback_data="adm:cooldown")
    kb.button(text="🔢 Set Limit", callback_data="adm:limit")
    kb.button(text="📊 Stats", callback_data="adm:stats")
    kb.button(text="📦 Active Keys", callback_data="adm:active_keys")
    kb.button(text="📣 Broadcast", callback_data="adm:broadcast")
    kb.button(text="❌ Close", callback_data="adm:close")
    kb.adjust(2, 2, 2, 2, 1)
    return kb.as_markup()


async def send_owner_panel(chat_id: int):
    await bot.send_message(chat_id, "👑 Owner Panel", reply_markup=owner_panel_kb())


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                joined INTEGER DEFAULT 0,
                unlocked INTEGER DEFAULT 0,
                last_claim_at TEXT,
                next_claim_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS template (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                text TEXT DEFAULT '',
                media_type TEXT DEFAULT '',
                file_id TEXT DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_text TEXT UNIQUE,
                claim_limit INTEGER DEFAULT 1,
                claim_count INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                added_at TEXT DEFAULT '',
                used_at TEXT DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                name TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.execute("INSERT OR IGNORE INTO settings(name, value) VALUES('cooldown_hours', '24')")
        await db.execute("INSERT OR IGNORE INTO settings(name, value) VALUES('default_limit', '1')")
        await db.execute("INSERT OR IGNORE INTO template(id, text, media_type, file_id) VALUES(1, '', '', '')")
        await db.commit()


async def ensure_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
        await db.commit()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, joined, unlocked, last_claim_at, next_claim_at FROM users WHERE user_id=?",
            (user_id,),
        )
        return await cur.fetchone()


async def update_user(user_id: int, joined=None, unlocked=None, last_claim_at=None, next_claim_at=None):
    parts = []
    vals = []
    if joined is not None:
        parts.append("joined=?")
        vals.append(1 if joined else 0)
    if unlocked is not None:
        parts.append("unlocked=?")
        vals.append(1 if unlocked else 0)
    if last_claim_at is not None:
        parts.append("last_claim_at=?")
        vals.append(last_claim_at.isoformat())
    if next_claim_at is not None:
        parts.append("next_claim_at=?")
        vals.append(next_claim_at.isoformat())

    if not parts:
        return

    vals.append(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {', '.join(parts)} WHERE user_id=?", vals)
        await db.commit()


async def get_setting(name: str, default: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE name=?", (name,))
        row = await cur.fetchone()
        return row[0] if row else default


async def set_setting(name: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings(name, value) VALUES(?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            (name, value),
        )
        await db.commit()


async def get_template():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT text, media_type, file_id FROM template WHERE id=1")
        row = await cur.fetchone()
        if not row:
            return {"text": "", "media_type": "", "file_id": ""}
        return {"text": row[0] or "", "media_type": row[1] or "", "file_id": row[2] or ""}


async def set_template(text: str, media_type: str = "", file_id: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE template SET text=?, media_type=?, file_id=? WHERE id=1",
            (text, media_type, file_id),
        )
        await db.commit()


async def add_key(key_text: str, claim_limit: int = 1):
    key_text = key_text.strip()
    claim_limit = max(1, int(claim_limit))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO keys(key_text, claim_limit, claim_count, active, added_at, used_at) "
            "VALUES(?, ?, 0, 1, ?, '')",
            (key_text, claim_limit, utcnow().isoformat()),
        )
        await db.commit()


async def add_keys_bulk(items):
    async with aiosqlite.connect(DB_PATH) as db:
        for key_text, claim_limit in items:
            key_text = key_text.strip()
            if not key_text:
                continue
            claim_limit = max(1, int(claim_limit))
            await db.execute(
                "INSERT OR REPLACE INTO keys(key_text, claim_limit, claim_count, active, added_at, used_at) "
                "VALUES(?, ?, 0, 1, ?, '')",
                (key_text, claim_limit, utcnow().isoformat()),
            )
        await db.commit()


async def get_active_key():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, key_text, claim_limit, claim_count FROM keys "
            "WHERE active=1 AND claim_count < claim_limit "
            "ORDER BY id ASC LIMIT 1"
        )
        row = await cur.fetchone()
        if not row:
            return None

        key_id, key_text, claim_limit, claim_count = row
        new_count = int(claim_count) + 1
        active = 1 if new_count < int(claim_limit) else 0
        now = utcnow().isoformat()

        await db.execute(
            "UPDATE keys SET claim_count=?, active=?, used_at=? WHERE id=?",
            (new_count, active, now, key_id),
        )
        await db.commit()
        return {
            "key_text": key_text,
            "claim_limit": int(claim_limit),
            "claim_count": new_count,
        }


async def list_active_keys(limit: int = 15):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT key_text, claim_count, claim_limit FROM keys "
            "WHERE claim_count < claim_limit ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return await cur.fetchall()


async def count_users() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        (cnt,) = await cur.fetchone()
        return cnt


async def count_available_keys() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM keys WHERE claim_count < claim_limit")
        (cnt,) = await cur.fetchone()
        return cnt


async def count_total_keys() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM keys")
        (cnt,) = await cur.fetchone()
        return cnt


async def count_used_keys() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM keys WHERE claim_count >= claim_limit")
        (cnt,) = await cur.fetchone()
        return cnt


async def is_member(user_id: int) -> bool:
    member = await bot.get_chat_member(CHANNEL_ID, user_id)
    return member.status in ("member", "administrator", "creator")


def extract_message_payload(message: Message):
    text = ""
    media_type = ""
    file_id = ""

    if message.text:
        text = message.text.strip()
    elif message.caption:
        text = message.caption.strip()

    if message.photo:
        media_type = "photo"
        file_id = message.photo[-1].file_id
    elif message.document:
        media_type = "document"
        file_id = message.document.file_id
    elif message.video:
        media_type = "video"
        file_id = message.video.file_id
    elif message.animation:
        media_type = "animation"
        file_id = message.animation.file_id
    elif message.audio:
        media_type = "audio"
        file_id = message.audio.file_id

    return text, media_type, file_id


async def send_template_to_user(chat_id: int, key_text: str):
    tpl = await get_template()
    text = (tpl["text"] or "").strip()
    media_type = tpl["media_type"]
    file_id = tpl["file_id"]

    caption = ""
    if text:
        caption += text + "\n\n"
    caption += f"🎁 Your Key:\n<code>{key_text}</code>"

    if media_type == "photo" and file_id:
        await bot.send_photo(chat_id, photo=file_id, caption=caption, parse_mode="HTML")
    elif media_type == "document" and file_id:
        await bot.send_document(chat_id, document=file_id, caption=caption, parse_mode="HTML")
    elif media_type == "video" and file_id:
        await bot.send_video(chat_id, video=file_id, caption=caption, parse_mode="HTML")
    elif media_type == "animation" and file_id:
        await bot.send_animation(chat_id, animation=file_id, caption=caption, parse_mode="HTML")
    elif media_type == "audio" and file_id:
        await bot.send_audio(chat_id, audio=file_id, caption=caption, parse_mode="HTML")
    else:
        await bot.send_message(chat_id, caption, parse_mode="HTML")


async def gift_animation(cb: CallbackQuery):
    steps = [
        "🎁 <b>Opening gift...</b>",
        "✨ <b>Unlocking your reward...</b>",
        "✅ <b>Preparing your key...</b>",
    ]
    for txt in steps:
        try:
            await cb.message.edit_text(txt, parse_mode="HTML")
        except Exception:
            pass
        await asyncio.sleep(0.7)


@dp.message(Command("start"))
async def start(message: Message):
    await ensure_user(message.from_user.id)
    try:
        ok = await is_member(message.from_user.id)
    except Exception:
        ok = False

    await update_user(message.from_user.id, joined=ok, unlocked=ok)

    if ok:
        await message.answer(
            "✅ Channel verified.\n\n🎁 Claim your gift from the button below.",
            reply_markup=user_home_kb(True),
        )
    else:
        await message.answer(
            "🔐 First channel follow to get DAILY FREE TRIAL features unlock.\n\nJoin the channel, then tap Verify Now.",
            reply_markup=join_kb(),
        )

    if message.from_user.id == OWNER_ID:
        await send_owner_panel(message.chat.id)


@dp.message(Command("panel"))
async def panel(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    await send_owner_panel(message.chat.id)


@dp.message(Command("menu"))
async def menu(message: Message):
    await ensure_user(message.from_user.id)
    user = await get_user(message.from_user.id)
    ok = bool(user and user[2] == 1)
    try:
        live = await is_member(message.from_user.id)
    except Exception:
        live = False

    if ok and not live:
        await update_user(message.from_user.id, joined=False, unlocked=False)
        ok = False

    if ok:
        await message.answer(
            "✅ Channel verified.\n\n🎁 Claim your gift from the button below.",
            reply_markup=user_home_kb(True),
        )
    else:
        await message.answer(
            "🔐 First channel follow to get DAILY FREE TRIAL features unlock.\n\nJoin the channel, then tap Verify Now.",
            reply_markup=join_kb(),
        )


@dp.callback_query(F.data == "user:verify")
async def verify(callback: CallbackQuery):
    uid = callback.from_user.id
    await ensure_user(uid)

    try:
        ok = await is_member(uid)
    except Exception:
        ok = False

    if ok:
        await update_user(uid, joined=True, unlocked=True)
        await callback.message.edit_text(
            "✅ Channel verified.\n\n🎁 Claim your gift from the button below.",
            reply_markup=user_home_kb(True),
        )
        await callback.answer("Verified")
    else:
        await update_user(uid, joined=False, unlocked=False)
        await callback.message.edit_text(
            "🔐 First channel follow to get DAILY FREE TRIAL features unlock.\n\nJoin the channel, then tap Verify Now.",
            reply_markup=join_kb(),
        )
        await callback.answer("Join the channel first", show_alert=True)


@dp.callback_query(F.data == "user:claim")
async def claim(callback: CallbackQuery):
    uid = callback.from_user.id
    await ensure_user(uid)

    try:
        ok = await is_member(uid)
    except Exception:
        ok = False

    if not ok:
        await update_user(uid, joined=False, unlocked=False)
        await callback.message.edit_text(
            "🔐 First channel follow to get DAILY FREE TRIAL features unlock.\n\nJoin the channel, then tap Verify Now.",
            reply_markup=join_kb(),
        )
        await callback.answer("You left the channel. Locking again.", show_alert=True)
        return

    user = await get_user(uid)
    if not user:
        await callback.answer("User not found", show_alert=True)
        return

    next_claim_at = parse_dt(user[4])
    now = utcnow()

    if next_claim_at and now < next_claim_at:
        await callback.answer(f"Next claim after {fmt_remaining(next_claim_at - now)}", show_alert=True)
        return

    item = await get_active_key()
    if not item:
        await callback.answer("No keys available right now", show_alert=True)
        await callback.message.answer("❌ No active keys are available right now.")
        return

    cooldown_hours = int(await get_setting("cooldown_hours", "24"))
    await update_user(
        uid,
        joined=True,
        unlocked=True,
        last_claim_at=now,
        next_claim_at=now + timedelta(hours=cooldown_hours),
    )

    await gift_animation(callback)
    await send_template_to_user(uid, item["key_text"])
    await callback.message.answer(
        f"⏳ Come back after {cooldown_hours} hours for the next gift.",
        reply_markup=user_home_kb(True),
    )
    await callback.answer("Gift sent")


@dp.callback_query(F.data == "adm:close")
async def adm_close(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Not allowed", show_alert=True)
        return
    await state.clear()
    await callback.message.answer("Panel closed.")
    await callback.answer()


@dp.callback_query(F.data == "adm:template")
async def adm_template(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Not allowed", show_alert=True)
        return
    await state.set_state(AdminFlow.waiting_template)
    await callback.message.answer(
        "Send template now.\n\nYou can send text or photo/document/video/audio/animation with caption.\nThis will be attached with every key."
    )
    await callback.answer()


@dp.callback_query(F.data == "adm:add_single")
async def adm_add_single(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Not allowed", show_alert=True)
        return
    await state.set_state(AdminFlow.waiting_add_single)
    await callback.message.answer(
        "Send one key now.\n\nFormat:\nKEY-123\nor\nKEY-123 1\n\nIf you write a number, it will be used as limit. Default is 1."
    )
    await callback.answer()


@dp.callback_query(F.data == "adm:add_bulk")
async def adm_add_bulk(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Not allowed", show_alert=True)
        return
    await state.set_state(AdminFlow.waiting_add_bulk)
    await callback.message.answer(
        "Send bulk keys now.\n\nOne key per line.\nOptional limit:\nKEY-111\nKEY-222\nKEY-333\n\nOr:\nKEY-111 1\nKEY-222 1\nKEY-333 1\n\nBlank lines are ignored."
    )
    await callback.answer()


@dp.callback_query(F.data == "adm:cooldown")
async def adm_cooldown(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Not allowed", show_alert=True)
        return
    await state.set_state(AdminFlow.waiting_cooldown)
    await callback.message.answer("Send cooldown in hours.\nExample: 24")
    await callback.answer()


@dp.callback_query(F.data == "adm:limit")
async def adm_limit(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Not allowed", show_alert=True)
        return
    await state.set_state(AdminFlow.waiting_default_limit)
    await callback.message.answer("Send default limit for future keys.\nExample: 1")
    await callback.answer()


@dp.callback_query(F.data == "adm:broadcast")
async def adm_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Not allowed", show_alert=True)
        return
    await state.set_state(AdminFlow.waiting_broadcast)
    await callback.message.answer(
        "Send broadcast now.\n\nYou can send text or a file/photo/video with caption.\nIt will go to all users."
    )
    await callback.answer()


@dp.callback_query(F.data == "adm:stats")
async def adm_stats(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Not allowed", show_alert=True)
        return

    users = await count_users()
    total_keys = await count_total_keys()
    active_keys = await count_available_keys()
    used_keys = await count_used_keys()
    cooldown = await get_setting("cooldown_hours", "24")
    default_limit = await get_setting("default_limit", "1")
    tpl = await get_template()

    await callback.message.answer(
        "📊 Stats\n\n"
        f"Users: {users}\n"
        f"Total keys: {total_keys}\n"
        f"Active keys: {active_keys}\n"
        f"Used keys: {used_keys}\n"
        f"Cooldown hours: {cooldown}\n"
        f"Default limit: {default_limit}\n"
        f"Template media: {tpl['media_type'] or 'none'}"
    )
    await callback.answer()


@dp.callback_query(F.data == "adm:active_keys")
async def adm_active_keys(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Not allowed", show_alert=True)
        return

    rows = await list_active_keys(12)
    if not rows:
        await callback.message.answer("No active keys.")
        await callback.answer()
        return

    text = ["📦 Active Keys"]
    for key_text, claim_count, claim_limit in rows:
        text.append(f"• {key_text}  ({claim_count}/{claim_limit})")
    await callback.message.answer("\n".join(text))
    await callback.answer()


@dp.message(AdminFlow.waiting_template)
async def process_template(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return

    text = message.text or message.caption or ""
    media_type = ""
    file_id = ""

    if message.photo:
        media_type = "photo"
        file_id = message.photo[-1].file_id
    elif message.document:
        media_type = "document"
        file_id = message.document.file_id
    elif message.video:
        media_type = "video"
        file_id = message.video.file_id
    elif message.animation:
        media_type = "animation"
        file_id = message.animation.file_id
    elif message.audio:
        media_type = "audio"
        file_id = message.audio.file_id

    if not text and not file_id:
        await message.answer("No content found.")
        return

    await set_template(text=text, media_type=media_type, file_id=file_id)
    await state.clear()
    await message.answer("Template saved.")


@dp.message(AdminFlow.waiting_add_single)
async def process_add_single(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return

    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Send a key.")
        return

    parts = raw.split()
    key_text = parts[0].strip()
    limit = 1
    if len(parts) > 1 and parts[1].isdigit():
        limit = int(parts[1])

    if not key_text:
        await message.answer("Invalid key.")
        return

    default_limit = int(await get_setting("default_limit", "1"))
    limit = max(1, limit or default_limit)
    await add_key(key_text, limit)
    await state.clear()
    available = await count_available_keys()
    await message.answer(f"Saved.\nKey: {key_text}\nLimit: {limit}\nAvailable keys: {available}")


@dp.message(AdminFlow.waiting_add_bulk)
async def process_add_bulk(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return

    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Send bulk keys.")
        return

    default_limit = int(await get_setting("default_limit", "1"))
    items = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue

        key_text = line
        limit = default_limit

        if "|" in line:
            a, b = line.split("|", 1)
            if b.strip().isdigit():
                key_text = a.strip()
                limit = int(b.strip())
        elif ":" in line:
            a, b = line.rsplit(":", 1)
            if b.strip().isdigit():
                key_text = a.strip()
                limit = int(b.strip())
        else:
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                key_text = parts[0].strip()
                limit = int(parts[1])

        if key_text:
            items.append((key_text, max(1, limit)))

    if not items:
        await message.answer("No valid keys found.")
        return

    await add_keys_bulk(items)
    await state.clear()
    available = await count_available_keys()
    await message.answer(f"Bulk saved.\nAdded: {len(items)}\nAvailable keys: {available}")


@dp.message(AdminFlow.waiting_cooldown)
async def process_cooldown(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return

    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Send number only.")
        return

    hours = max(1, min(int(raw), 168))
    await set_setting("cooldown_hours", str(hours))
    await state.clear()
    await message.answer(f"Cooldown set to {hours} hours.")


@dp.message(AdminFlow.waiting_default_limit)
async def process_default_limit(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return

    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Send number only.")
        return

    limit = max(1, min(int(raw), 100000))
    await set_setting("default_limit", str(limit))
    await state.clear()
    await message.answer(f"Default limit set to {limit}.")


@dp.message(AdminFlow.waiting_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return

    users_sent = 0
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        users = await cur.fetchall()

    text = message.text or message.caption or ""
    media_type = ""
    file_id = ""

    if message.photo:
        media_type = "photo"
        file_id = message.photo[-1].file_id
    elif message.document:
        media_type = "document"
        file_id = message.document.file_id
    elif message.video:
        media_type = "video"
        file_id = message.video.file_id
    elif message.animation:
        media_type = "animation"
        file_id = message.animation.file_id
    elif message.audio:
        media_type = "audio"
        file_id = message.audio.file_id

    for (uid,) in users:
        try:
            if media_type == "photo" and file_id:
                await bot.send_photo(uid, photo=file_id, caption=text if text else None)
            elif media_type == "document" and file_id:
                await bot.send_document(uid, document=file_id, caption=text if text else None)
            elif media_type == "video" and file_id:
                await bot.send_video(uid, video=file_id, caption=text if text else None)
            elif media_type == "animation" and file_id:
                await bot.send_animation(uid, animation=file_id, caption=text if text else None)
            elif media_type == "audio" and file_id:
                await bot.send_audio(uid, audio=file_id, caption=text if text else None)
            else:
                await bot.send_message(uid, text or "Broadcast")
            users_sent += 1
            await asyncio.sleep(0.03)
        except Exception:
            pass

    await state.clear()
    await message.answer(f"Broadcast sent: {users_sent}")


async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
