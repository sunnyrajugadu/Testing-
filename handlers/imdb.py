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


print("✅ handlers/imdb.py imported (Pure IMDb Robust Fix)", flush=True)


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


# ================= FETCH DETAILED MOVIE/SERIES INFO (Pure IMDb via Web & Suggestion) ================= #

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

    # 1. Fetch details from IMDb Suggestion API
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
                            if it.get("l"):
                                data["title"] = it.get("l")
                            if it.get("y"):
                                data["year"] = str(it.get("y"))
                            if it.get("s"):
                                data["director"] = it.get("s")
                            break
    except Exception as e:
        print(f"IMDb Suggestion Fetch Error: {e}", flush=True)

    # 2. Fetch metadata from IMDb Public JSON API (IMDb web data structure)
    try:
        api_url = f"https://v2.sg.media-imdb.com/suggestion/t/{imdb_id}.json"
        # Fallback to alternative mobile json or web scraping headers if needed
        web_api = f"https://www.imdb.com/title/{imdb_id}/"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(web_api, headers=headers, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    html_text = await resp.text()
                    import json, re
                    
                    # Extract JSON-LD data embedded in IMDb page for 100% accurate info
                    json_ld_match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html_text, re.DOTALL)
                    if json_ld_match:
                        ld_data = json.loads(json_ld_match.group(1))
                        
                        if ld_data.get("name"):
                            data["title"] = ld_data.get("name")
                        if ld_data.get("aggregateRating", {}).get("ratingValue"):
                            data["rating"] = str(ld_data.get("aggregateRating", {}).get("ratingValue"))
                        if ld_data.get("datePublished"):
                            data["release_date"] = ld_data.get("datePublished")
                            data["year"] = data["release_date"].split("-")[0]
                        if ld_data.get("description"):
                            data["storyline"] = ld_data.get("description")
                        if ld_data.get("image") and not data["poster"]:
                            data["poster"] = ld_data.get("image")
                        if ld_data.get("duration"):
                            # ISO duration format like PT175M -> parse or keep
                            dur = ld_data.get("duration")
                            m_match = re.search(r'(\d+)M', dur)
                            if m_match:
                                data["runtime"] = f"{m_match.group(1)} min"
                        
                        # Genres
                        genres = ld_data.get("genre", [])
                        if isinstance(genres, str):
                            genres = [genres]
                        if genres:
                            data["genres"] = genres

                        # Director
                        director = ld_data.get("director", [])
                        if isinstance(director, dict):
                            director = [director]
                        directors_list = [d.get("name") for d in director if d.get("name")]
                        if directors_list:
                            data["director"] = ", ".join(directors_list)
    except Exception as e:
        print(f"IMDb Web Scraping Error: {e}", flush=True)

    # High-resolution poster formatting
    if data["poster"] and "_V1_" in data["poster"]:
        try:
            base_url = data["poster"].split("_V1_")[0]
            data["poster"] = f"{base_url}_V1_UY780_CR0,0,526,780_AL_.jpg"
        except Exception:
            pass

    # Default fallbacks if still N/A
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
