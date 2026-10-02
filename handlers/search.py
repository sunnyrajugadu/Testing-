import asyncio
from datetime import datetime
import uuid

from pyrogram import filters
from pyrogram.types import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from bot import app
from config import LOG_CHANNEL_ID
from filters.fsub import enforce_fsub
from database.models import (
    search_files,
    increase_search_count,
    save_search_cache,
    update_search_state
)


print("✅ search.py imported", flush=True)


# ================= SETTINGS ================= #

PAGE_LIMIT = 7

FIXED_LANGUAGES = [
    "All",
    "English",
    "Hindi",
    "Tamil",
    "Telugu",
    "Malayalam",
    "Kannada"
]


# ================= SIZE FORMAT ================= #

def format_size(size):
    try:
        size = int(size or 0)
    except Exception:
        size = 0

    if size >= 1024 ** 3:
        return f"{size / (1024 ** 3):.2f} GB"

    if size >= 1024 ** 2:
        return f"{size / (1024 ** 2):.2f} MB"

    if size >= 1024:
        return f"{size / 1024:.2f} KB"

    return f"{size:.0f} B"


# ================= FORMAT AUDIO ================= #

def format_audio(audio):
    if isinstance(audio, list):
        values = [
            str(item).strip()
            for item in audio
            if item
            and str(item).strip().lower()
            not in {
                "unknown",
                "none",
                "null",
                "n/a",
                "na"
            }
        ]

        if values:
            return ", ".join(dict.fromkeys(values))

        return ""

    if audio is None:
        return ""

    audio = str(audio).strip()

    if not audio:
        return ""

    if audio.lower() in {
        "unknown",
        "none",
        "null",
        "n/a",
        "na"
    }:
        return ""

    return audio


# ================= FILE DISPLAY NAME ================= #

def get_file_display_name(file):
    from utils.rename import clean_file_name

    raw_name = (
        file.get("file_name")
        or file.get("original_file_name")
        or file.get("movie_name")
        or ""
    )

    cleaned = clean_file_name(raw_name)

    if cleaned and cleaned.lower() != "unknown":
        return cleaned

    fallback = clean_file_name(file.get("movie_name") or "")

    if fallback and fallback.lower() != "unknown":
        return fallback

    return "Movie"


# ================= SEARCH LOG ================= #

async def log_search(
    client,
    user,
    search_text,
    result_count=0
):
    try:
        if not LOG_CHANNEL_ID:
            return

        username = (
            f"@{user.username}"
            if user.username
            else "N/A"
        )

        first_name = user.first_name or "N/A"

        if result_count > 0:
            result_status = (
                f"✅ <b>RESULTS FOUND:</b> "
                f"<code>{result_count}</code>"
            )
        else:
            result_status = "❌ <b>NO RESULTS FOUND</b>"

        log_text = (
            "🔍 <b>SEARCH USED</b>\n\n"
            f"👤 <b>Name:</b> {first_name}\n"
            f"📱 <b>Username:</b> {username}\n"
            f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
            f"🔎 <b>Search:</b> <code>{search_text}</code>\n\n"
            f"{result_status}"
        )

        await client.send_message(
            LOG_CHANNEL_ID,
            log_text
        )

    except Exception as e:
        print(f"❌ SEARCH LOG ERROR : {e}", flush=True)


# ================= LANGUAGE BUTTONS ================= #

def language_buttons(
    search_id,
    timestamp,
    selected=None
):
    buttons = []
    row = []

    for lang in FIXED_LANGUAGES:
        text = f"✅ {lang}" if selected == lang else lang

        row.append(
            InlineKeyboardButton(
                text,
                callback_data=(
                    f"lang:"
                    f"{lang}:"
                    f"{search_id}:"
                    f"{timestamp}"
                )
            )
        )

        if len(row) == 3:
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    return buttons


# ================= DIRECT FILE BUTTON ================= #

def build_file_button(
    file,
    user_id,
    timestamp
):
    display_name = get_file_display_name(file)

    file_size = format_size(
        file.get("file_size_bytes", 0)
    )

    file_id = str(file.get("_id"))

    return InlineKeyboardButton(
        text=f"{file_size} | {display_name}",
        callback_data=f"file:{user_id}:{file_id}:{timestamp}"
    )


# ================= PAGINATION ================= #

def pagination_buttons(
    search_id,
    page,
    total,
    timestamp
):
    row = []

    total_pages = (
        (total + PAGE_LIMIT - 1)
        // PAGE_LIMIT
    )

    if total_pages < 1:
        total_pages = 1

    if page > 1:
        row.append(
            InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=(
                    f"page:"
                    f"{search_id}:"
                    f"{page - 1}:"
                    f"{timestamp}"
                )
            )
        )

    row.append(
        InlineKeyboardButton(
            f"📄 {page}/{total_pages}",
            callback_data="none"
        )
    )

    if page < total_pages:
        row.append(
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=(
                    f"page:"
                    f"{search_id}:"
                    f"{page + 1}:"
                    f"{timestamp}"
                )
            )
        )

    return [row]


# ================= CORE SEARCH EXECUTION ================= #

async def execute_search(client, user, chat_id, movie_name, reply_to_message_id=None):
    """
    Executes search and sends the files. Can be called from message handler or callback.
    """
    try:
        user_id = user.id
        print(f"🔍 SEARCH : {movie_name}", flush=True)

        asyncio.create_task(increase_search_count(user_id))

        results = await search_files(movie_name)

        asyncio.create_task(
            log_search(
                client,
                user,
                movie_name,
                len(results) if results else 0
            )
        )

        # ================= NO RESULTS ================= #
        if not results:
            no_result_message = await client.send_message(
                chat_id=chat_id,
                text=f"""
🪄 **Oops! I couldn't find :** `{movie_name}` 📀

🔎 **Try a different spelling or share more details so I can hunt it down!** 🍿
""",
                reply_to_message_id=reply_to_message_id
            )

            try:
                await asyncio.sleep(10)
                await no_result_message.delete()
            except Exception:
                pass

            return

        # ================= SEARCH ID ================= #
        search_id = str(uuid.uuid4())
        menu_timestamp = int(datetime.now().timestamp())

        cache_files = []
        for file in results:
            if "_id" in file:
                file["_id"] = str(file["_id"])
            cache_files.append(file)

        asyncio.create_task(save_search_cache(search_id, cache_files, movie_name))
        asyncio.create_task(update_search_state(search_id, cache_files[:PAGE_LIMIT], "All", 1))

        # ================= BUILD FILE BUTTONS ================= #
        buttons = []
        for file in cache_files[:PAGE_LIMIT]:
            buttons.append(
                [build_file_button(file, user_id, menu_timestamp)]
            )

        # ================= LANGUAGE BUTTONS ================= #
        buttons.extend(
            language_buttons(
                search_id=search_id,
                timestamp=menu_timestamp,
                selected="All"
            )
        )

        # ================= SEND ALL ================= #
        buttons.append(
            [
                InlineKeyboardButton(
                    "📤 Send All",
                    callback_data=(
                        f"all:"
                        f"{user_id}:"
                        f"{search_id}:"
                        f"{menu_timestamp}"
                    )
                )
            ]
        )

        # ================= PAGINATION ================= #
        buttons.extend(
            pagination_buttons(
                search_id,
                1,
                len(cache_files),
                menu_timestamp
            )
        )

        # ================= SEND RESULT (WITH REPLY PREVIEW) ================= #
        await client.send_message(
            chat_id=chat_id,
            text=f"""
🌟✨ **Movie Request:** `{movie_name}` 🪄

🍿 **Here is your files!**
""",
            reply_markup=InlineKeyboardMarkup(buttons),
            reply_to_message_id=reply_to_message_id
        )

        print("✅ SEARCH RESULT SENT", flush=True)

    except Exception as e:
        print(f"❌ SEARCH ERROR : {e}", flush=True)
        try:
            await client.send_message(chat_id, "⚠️ Something went wrong.", reply_to_message_id=reply_to_message_id)
        except Exception:
            pass


# ================= PRIVATE TEXT & SEARCH HANDLER ================= #

@app.on_message(
    filters.private
    & filters.text
    & ~filters.command(
        [
            "start",
            "stats",
            "broadcast",
            "reindex",
            "reload",
            "ping",
            "usage",
            "owner",
            "delete",
            "generate_link"
        ]
    )
)
async def search_movie_handler(
    client,
    message: Message
):
    try:
        if not message.from_user:
            return

        # 1. Ignore messages sent via Inline Query (Prevents Oops error on inline clicks)
        if message.via_bot:
            return

        movie_name = (message.text or "").strip()

        # 2. Ignore messages that contain inline file text / captions
        if (
            "Size :-" in movie_name
            or "Size:" in movie_name
            or "@CinemaVetaBot" in movie_name
            or "@mrDuDeHoLic" in movie_name
            or movie_name.startswith("📁")
            or "Results -" in movie_name
        ):
            return

        # Remove bot username prefix if any
        if movie_name.startswith("@"):
            parts = movie_name.split()
            movie_name = " ".join(parts[1:])

        # Remove /search command prefix if typed in PM
        if movie_name.lower().startswith("/search"):
            movie_name = movie_name[7:].strip()

        if not movie_name:
            return

        # ================= ONE-LINE FSUB ENFORCEMENT ================= #
        if not await enforce_fsub(client, message, payload=movie_name):
            return

        # Run Search
        await execute_search(
            client=client,
            user=message.from_user,
            chat_id=message.chat.id,
            movie_name=movie_name,
            reply_to_message_id=message.id
        )

    except Exception as e:
        print(f"❌ SEARCH HANDLER ERROR : {e}", flush=True)
        try:
            await message.reply_text("⚠️ Something went wrong.", quote=True)
        except Exception:
            pass
