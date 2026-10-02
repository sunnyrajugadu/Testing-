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


print("✅ handlers/imdb.py imported (Runtime Hours & Clean Photo Fix)", flush=True)


# ================= FORMAT RUNTIME (Minutes to Hours & Mins) ================= #

def format_runtime(runtime_str):
    if not runtime_str or runtime_str == "N/A":
        return "N/A"
    
    # If it's already seasons (e.g. series)
    if "Season" in runtime_str or "Seasons" in runtime_str:
        return runtime_str

    try:
        # Extract digits from string like "175 min" or "175"
        import re
        numbers = re.findall(r'\d+', runtime_str)
        if numbers:
            total_minutes = int(numbers[0])
            hours = total_minutes // 60
            minutes = total_minutes % 60
            if hours > 0 and minutes > 0:
                return f"{hours} hrs {minutes} mins"
            elif hours > 0:
                return f"{hours} hrs"
            else:
                return f"{minutes} mins"
    except Exception:
        pass
    return runtime_str


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
                        poster_img = item.get("i", {}).get("imageUrl")

                        if title:
                            results.append({
                                "id": item_id,
                                "title": title,
                                "year": str(year),
                                "poster": poster_img
                            })

                        if len(results) >= limit:
                            break
                    return results
    except Exception as e:
        print(f"IMDb Search Error: {e}", flush=True)
    return []


# ================= FETCH DETAILED MOVIE/SERIES INFO ================= #

async def fetch_full_movie_details(imdb_id: str, fallback_title: str = None, fallback_year: str = None, fallback_poster: str = None):
    data = {
        "title": fallback_title or "N/A",
        "year": fallback_year or "N/A",
        "aka": None,
        "rating": "N/A",
        "release_date": "N/A",
        "runtime": "N/A",
        "director": "N/A",
        "genres": [],
        "languages": [],
        "countries": [],
        "storyline": "No storyline available.",
        "poster": fallback_poster,
        "imdb_url": f"https://www.imdb.com/title/{imdb_id}",
        "trailer_url": None
    }

    # 1. Fetch from IMDb Suggestion API for robust fallback
    try:
        sugg_url = f"https://v3.sg.media-imdb.com/suggestion/t/{imdb_id}.json"
        async with aiohttp.ClientSession() as session:
            async with session.get(sugg_url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status == 200:
                    j_data = await resp.json()
                    for it in j_data.get("d", []):
                        if str(it.get("id")) == imdb_id:
                            if it.get("l"):
                                data["title"] = it.get("l")
                            if it.get("y"):
                                data["year"] = str(it.get("y"))
                                data["release_date"] = str(it.get("y"))
                            if it.get("s"):
                                data["director"] = it.get("s")
                            if it.get("i", {}).get("imageUrl"):
                                data["poster"] = it.get("i", {}).get("imageUrl")
                            break
    except Exception as e:
        print(f"IMDb Suggestion API Error: {e}", flush=True)

    # 2. Fetch detailed metadata from OMDB
    omdb_url = f"https://www.omdbapi.com/?i={imdb_id}&plot=full&apikey=trilogy"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(omdb_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    o_data = await resp.json()
                    if o_data.get("Response") == "True":
                        if o_data.get("Title") and o_data.get("Title") != "N/A":
                            data["title"] = o_data.get("Title")
                        if o_data.get("Year") and o_data.get("Year") != "N/A":
                            data["year"] = o_data.get("Year")
                        if o_data.get("imdbRating") and o_data.get("imdbRating") != "N/A":
                            data["rating"] = o_data.get("imdbRating")
                        if o_data.get("Released") and o_data.get("Released") != "N/A":
                            data["release_date"] = o_data.get("Released")
                        if o_data.get("Runtime") and o_data.get("Runtime") != "N/A":
                            data["runtime"] = format_runtime(o_data.get("Runtime"))
                        if o_data.get("Director") and o_data.get("Director") != "N/A":
                            data["director"] = o_data.get("Director")

                        total_seasons = o_data.get("totalSeasons")
                        if total_seasons and total_seasons != "N/A":
                            data["runtime"] = f"{total_seasons} Seasons"

                        g = o_data.get("Genre", "")
                        data["genres"] = [x.strip() for x in g.split(",") if x.strip() and x.strip() != "N/A"]

                        l = o_data.get("Language", "")
                        data["languages"] = [x.strip() for x in l.split(",") if x.strip() and x.strip() != "N/A"]

                        c = o_data.get("Country", "")
                        data["countries"] = [x.strip() for x in c.split(",") if x.strip() and x.strip() != "N/A"]

                        plot = o_data.get("Plot")
                        if plot and plot != "N/A" and plot != "N/A.":
                            data["storyline"] = plot

                        poster = o_data.get("Poster")
                        if poster and poster != "N/A":
                            data["poster"] = poster
    except Exception as e:
        print(f"OMDB Fetch Error: {e}", flush=True)

    # High resolution poster fix for clean Telegram photo rendering
    if data["poster"]:
        try:
            if "_V1_" in data["poster"]:
                base_url = data["poster"].split("_V1_")[0]
                data["poster"] = f"{base_url}_V1_UY780_CR0,0,526,780_AL_.jpg"
        except Exception:
            pass

    if not data["poster"]:
        data["poster"] = "https://m.media-amazon.com/images/M/MV5BMDFkYTc0MGEtZmNhMC00ZDIzLWFmNTEtODM1ZmRlYWMwMWFmXkEyXkFqcGdeQXVyMTMxODk2OTU@._V1_UY780_CR0,0,526,780_AL_.jpg"

    clean_name = data["title"].replace(" ", "+")
    data["trailer_url"] = f"https://www.youtube.com/results?search_query={clean_name}+{data['year']}+official+trailer"

    return data


# ================= /imdb COMMAND HANDLER WITH REPLY ================= #

@app.on_message(filters.private & filters.command(["imdb"]))
async def imdb_search_command(client, message: Message):
    try:
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            return await message.reply_text(
                "💡 <b>Usage Guide :</b>\n"
                "» <code>/imdb &lt;movie_name&gt;</code>\n"
                "» <i>Example :</i> <code>/imdb Salaar</code>",
                quote=True
            )

        query = parts[1].strip()
        search_msg = await message.reply_text("⚡ <b>Searching IMDb database...</b>", quote=True)

        results = await fetch_imdb_results(query, limit=10)
        if not results:
            return await search_msg.edit_text(f"🥀 <b>No matching results found for :</b> <code>{html.escape(query)}</code>")

        buttons = []
        for item in results:
            btn_text = f"{item['title']} - {item['year']}"
            poster_pass = item["poster"] if item["poster"] else "none"
            callback_payload = f"imdb_view:{item['id']}:{item['year']}:{poster_pass}:{item['title']}"
            buttons.append([
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=callback_payload[:64]
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

    except Exception as e:
        print(f"IMDb Command Error: {e}", flush=True)
        try:
            await message.reply_text("⚠️ Something went wrong while searching IMDb.", quote=True)
        except Exception:
            pass


# ================= CALLBACK FOR MOVIE CARD ================= #

@app.on_callback_query(filters.regex(r"^imdb_view:(.*)"))
async def imdb_view_callback(client, query: CallbackQuery):
    try:
        parts = query.data.split(":")
        imdb_id = parts[1].strip()
        fallback_year = parts[2].strip() if len(parts) > 2 and parts[2] != "N/A" else None
        fallback_poster = parts[3].strip() if len(parts) > 3 and parts[3] != "none" else None
        fallback_title = parts[4].strip() if len(parts) > 4 else None

        await query.answer("Fetching from IMDb...")

        orig_message = query.message.reply_to_message or query.message

        try:
            await query.message.delete()
        except Exception:
            pass

        info = await fetch_full_movie_details(
            imdb_id, 
            fallback_title=fallback_title, 
            fallback_year=fallback_year, 
            fallback_poster=fallback_poster
        )

        me = await client.get_me()
        bot_user = me.username or "CinemaVetaBot"
        bot_mention = f'<a href="https://t.me/{bot_user}"><b>@{bot_user}</b></a>'

        genre_str = " ".join([f"#{g.replace(' ', '_')}" for g in info["genres"]]) if info["genres"] else "N/A"
        lang_str = " ".join([f"#{l.replace(' ', '_')}" for l in info["languages"]]) if info["languages"] else "N/A"
        country_str = " ".join([f"#{c.replace(' ', '_')}" for c in info["countries"]]) if info["countries"] else "N/A"

        rating_disp = f"{info['rating']} / 10" if info['rating'] != "N/A" else "N/A / 10"

        title_link = f'<a href="{info["imdb_url"]}"><b>{html.escape(info["title"])} [{html.escape(str(info["year"]))}]</b></a>'
        release_info = info["release_date"] if info["release_date"] != "N/A" else info["year"]

        caption_lines = [
            f"🎬 {title_link}\n"
        ]

        if info["aka"]:
            caption_lines.append(f"📝 <b>Also Known As:</b> {html.escape(info['aka'])}")

        caption_lines.extend([
            f"⭐ <b>IMDb Rating :</b> {rating_disp}",
            f"🗓 <b>Release Info :</b> {release_info}",
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
                    chat_id=orig_message.chat.id,
                    photo=info["poster"],
                    caption=final_caption,
                    reply_markup=reply_markup,
                    reply_to_message_id=orig_message.id
                )
                sent = True
            except Exception as pe:
                print(f"Photo send error: {pe}", flush=True)

        if not sent:
            await client.send_message(
                chat_id=orig_message.chat.id,
                text=final_caption,
                reply_markup=reply_markup,
                reply_to_message_id=orig_message.id
            )

    except Exception as e:
        print(f"IMDb View Callback Error: {e}", flush=True)
        try:
            await query.answer("❌ Failed to fetch movie details.", show_alert=True)
        except Exception:
            pass
