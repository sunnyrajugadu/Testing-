import html
import aiohttp
from pyrogram import filters
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from bot import app
from utils.helpers import normalize_text


print("✅ handlers/imdb.py imported (Interactive Pure IMDb Flow)", flush=True)

# State tracking for users waiting to type the movie name after /imdb command
IMDB_AWAITING_INPUT = {}


# ================= FETCH SUGGESTION TITLES (IMDb) ================= #

async def fetch_imdb_results(query: str, limit: int = 10):
    clean_q = normalize_text(query)
    if not clean_q:
        return []

    first_char = clean_q[0]
    url = f"https://v3.sg.media-imdb.com/suggestion/{first_char}/{clean_q}.json"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = []
                    for item in data.get("d", []):
                        item_id = str(item.get("id", ""))
                        if not item_id.startswith("tt"):
                            continue

                        entity_type = str(item.get("q", "")).lower()
                        if entity_type in ["actor", "actress", "soundtrack"]:
                            continue

                        title = item.get("l")
                        year = item.get("y", "N/A")

                        if title:
                            results.append({
                                "id": item_id,
                                "title": title,
                                "year": str(year)
                            })

                        if len(results) >= limit:
                            break
                    return results
    except Exception as e:
        print(f"IMDb Search Error: {e}", flush=True)
    return []


# ================= FETCH DETAILED MOVIE INFO (Pure IMDb / OMDB) ================= #

async def fetch_full_movie_details(imdb_id: str):
    data = {
        "title": "N/A",
        "year": "N/A",
        "aka": None,
        "rating": "N/A",
        "release_date": "N/A",
        "runtime": "N/A",
        "director": "N/A",
        "genres": [],
        "languages": [],
        "countries": [],
        "storyline": "No storyline available.",
        "poster": None,
        "imdb_url": f"https://www.imdb.com/title/{imdb_id}",
        "trailer_url": None
    }

    # Fetch official poster from IMDb suggestion API
    try:
        prefix = imdb_id[:3] if len(imdb_id) >= 3 else imdb_id
        sugg_url = f"https://v3.sg.media-imdb.com/suggestion/{prefix}/{imdb_id}.json"
        async with aiohttp.ClientSession() as session:
            async with session.get(sugg_url, timeout=aiohttp.ClientTimeout(total=3)) as s_resp:
                if s_resp.status == 200:
                    s_data = await s_resp.json()
                    for it in s_data.get("d", []):
                        if str(it.get("id")) == imdb_id:
                            img = it.get("i", {}).get("imageUrl")
                            if img:
                                data["poster"] = img
                            break
    except Exception:
        pass

    # Fetch full metadata and poster fallback from OMDB
    omdb_url = f"https://www.omdbapi.com/?i={imdb_id}&plot=full&apikey=trilogy"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(omdb_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    o_data = await resp.json()
                    if o_data.get("Response") == "True":
                        data["title"] = o_data.get("Title", "N/A")
                        data["year"] = o_data.get("Year", "N/A")
                        data["rating"] = o_data.get("imdbRating", "N/A")
                        data["release_date"] = o_data.get("Released", "N/A")
                        data["runtime"] = o_data.get("Runtime", "N/A")
                        data["director"] = o_data.get("Director", "N/A")
                        
                        g = o_data.get("Genre", "")
                        data["genres"] = [x.strip() for x in g.split(",") if x.strip() and x.strip() != "N/A"]

                        l = o_data.get("Language", "")
                        data["languages"] = [x.strip() for x in l.split(",") if x.strip() and x.strip() != "N/A"]

                        c = o_data.get("Country", "")
                        data["countries"] = [x.strip() for x in c.split(",") if x.strip() and x.strip() != "N/A"]

                        plot = o_data.get("Plot")
                        if plot and plot != "N/A":
                            data["storyline"] = plot

                        poster = o_data.get("Poster")
                        if not data["poster"] and poster and poster != "N/A":
                            data["poster"] = poster
    except Exception as e:
        print(f"IMDb OMDB Fetch Error: {e}", flush=True)

    # YouTube Trailer search query link
    clean_name = data["title"].replace(" ", "+")
    data["trailer_url"] = f"https://www.youtube.com/results?search_query={clean_name}+{data['year']}+official+trailer"

    return data


# ================= STEP 1: /imdb COMMAND TRIGGER ================= #

@app.on_message(filters.private & filters.command(["imdb"]))
async def imdb_search_command(client, message: Message):
    try:
        user_id = message.from_user.id
        parts = message.text.split(maxsplit=1)

        # Direct argument unte ventane search execute avtundi (e.g., /imdb Salaar)
        if len(parts) > 1:
            query = parts[1].strip()
            return await process_imdb_flow(client, message, query)

        # Argument lekapothe movie name kosam prompt adugutundi
        cancel_button = InlineKeyboardMarkup([
            [InlineKeyboardButton("✘ Cancel ✘", callback_data=f"imdb_cancel:{user_id}")]
        ])

        prompt_msg = await message.reply_text(
            "🎬 <b>Enter the movie or series name to get IMDb details:</b>",
            reply_markup=cancel_button,
            quote=True
        )

        IMDB_AWAITING_INPUT[user_id] = prompt_msg.id

    except Exception as e:
        print(f"IMDb Command Error: {e}", flush=True)
        try:
            await message.reply_text("⚠️ Something went wrong while processing /imdb command.", quote=True)
        except Exception:
            pass


# ================= STEP 2: INTERCEPT USER INPUT ================= #
# group=-1 prioritizes this before normal movie search handler runs

@app.on_message(filters.private & filters.text & ~filters.command(["start", "imdb"]), group=-1)
async def intercept_imdb_text(client, message: Message):
    user_id = message.from_user.id

    if user_id in IMDB_AWAITING_INPUT:
        prompt_msg_id = IMDB_AWAITING_INPUT.pop(user_id, None)

        if prompt_msg_id:
            try:
                await client.delete_messages(chat_id=message.chat.id, message_ids=prompt_msg_id)
            except Exception:
                pass

        query = (message.text or "").strip()
        await process_imdb_flow(client, message, query)

        # Stop propagation to prevent search.py from searching files in database
        message.stop_propagation()


# ================= STEP 3: SEARCH AND DISPLAY MATCHED BUTTONS ================= #

async def process_imdb_flow(client, message: Message, query: str):
    search_msg = await message.reply_text("⚡ <b>Searching IMDb database...</b>", quote=True)

    results = await fetch_imdb_results(query, limit=10)
    if not results:
        return await search_msg.edit_text(f"🥀 <b>No matching results found for :</b> <code>{html.escape(query)}</code>")

    # Clean text buttons matching 3rd picture style (Title - Year)
    buttons = []
    for item in results:
        btn_text = f"{item['title']} - {item['year']}"
        buttons.append([
            InlineKeyboardButton(
                text=btn_text,
                callback_data=f"imdb_view:{item['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton("Close", callback_data="close")
    ])

    header_text = (
        f"🎯 <b>Matched Results For :</b> <code>{html.escape(query.title())}</code>\n"
        f"<i>👇 Choose the exact title below to view full details :</i>"
    )

    await search_msg.edit_text(
        text=header_text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ================= STEP 4: CANCEL BUTTON CALLBACK ================= #

@app.on_callback_query(filters.regex(r"^imdb_cancel:(.*)"))
async def imdb_cancel_callback(client, query: CallbackQuery):
    try:
        target_user_id = int(query.data.split(":", 1)[1])
        if query.from_user.id != target_user_id:
            return await query.answer("⚠️ This button is not for you.", show_alert=True)

        IMDB_AWAITING_INPUT.pop(query.from_user.id, None)
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.answer("IMDb search cancelled.")
    except Exception as e:
        print(f"IMDb Cancel Error: {e}", flush=True)


# ================= STEP 5: CALLBACK FOR MOVIE CARD (Matching 4th Picture Style) ================= #

@app.on_callback_query(filters.regex(r"^imdb_view:(.*)"))
async def imdb_view_callback(client, query: CallbackQuery):
    try:
        imdb_id = query.data.split(":", 1)[1].strip()
        await query.answer("Fetching from IMDb...")

        try:
            await query.message.delete()
        except Exception:
            pass

        info = await fetch_full_movie_details(imdb_id)

        # Dynamic Bot Mention Link
        me = await client.get_me()
        bot_user = me.username or "CinemaVetaBot"
        bot_mention = f'<a href="https://t.me/{bot_user}"><b>@{bot_user}</b></a>'

        # Hashtags formatting matching 4th screenshot style
        genre_str = " ".join([f"#{g.replace(' ', '_')}" for g in info["genres"]]) if info["genres"] else "N/A"
        lang_str = " ".join([f"#{l.replace(' ', '_')}" for l in info["languages"]]) if info["languages"] else "N/A"
        country_str = " ".join([f"#{c.replace(' ', '_')}" for c in info["countries"]]) if info["countries"] else "N/A"

        rating_disp = f"{info['rating']} / 10" if info['rating'] != "N/A" else "N/A / 10"

        # Matching 4th picture caption structure exactly
        caption_lines = [
            f"🎬 <b>{html.escape(info['title'])} [{html.escape(str(info['year']))}]</b>\n"
        ]

        if info["aka"]:
            caption_lines.append(f"📝 <b>Also Known As:</b> {html.escape(info['aka'])}")

        caption_lines.extend([
            f"⭐ <b>IMDb Rating :</b> {rating_disp}",
            f"🗓️️ <b>Release Info :</b> {info['release_date'] if info['release_date'] != 'N/A' else info['year']}",
            f"⏳ <b>Runtime :</b> {info['runtime']}",
            f"🎥 <b>Directed By :</b> {info['director']}",
            f"🎭 <b>Genre :</b> {genre_str}",
            f"🌐 <b>Language :</b> {lang_str}",
            f"🌍 <b>Country Of Origin :</b> {country_str}\n",
            "📖 <b>Storyline :</b>",
            f"{html.escape(info['storyline'])}\n",
            f"✨ <b>Powered By :</b>\n{bot_mention}"
        ])

        final_caption = "\n".join(caption_lines)

        # Matching 4th picture buttons format exactly
        clean_btn_title = info["title"]
        buttons = [
            [
                InlineKeyboardButton(
                    f"🔗 {clean_btn_title} on IMDb",
                    url=info["imdb_url"]
                )
            ],
            [
                InlineKeyboardButton(
                    "▶️ Watch Trailer",
                    url=info["trailer_url"]
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(buttons)

        sent = False
        if info["poster"]:
            try:
                await client.send_photo(
                    chat_id=query.message.chat.id,
                    photo=info["poster"],
                    caption=final_caption,
                    reply_markup=reply_markup
                )
                sent = True
            except Exception as pe:
                print(f"Photo send error: {pe}", flush=True)

        if not sent:
            await client.send_message(
                chat_id=query.message.chat.id,
                text=final_caption,
                reply_markup=reply_markup
            )

    except Exception as e:
        print(f"IMDb View Callback Error: {e}", flush=True)
        try:
            await query.answer("❌ Failed to fetch movie details.", show_alert=True)
        except Exception:
            pass
