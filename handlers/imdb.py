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


print("✅ handlers/imdb.py imported (Clean IMDb + TMDB Sync + Reply Format)", flush=True)

TMDB_KEY = "7f43669a428c09611a0518fa9c0bbddb"


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


# ================= FETCH DETAILED MOVIE/SERIES INFO (TMDB + OMDB Sync) ================= #

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

    # 1. Fetch from TMDB using IMDb ID (Ensures 100% accurate Title, Release Date, Poster, Runtime, etc.)
    try:
        find_url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={TMDB_KEY}&external_source=imdb_id"
        async with aiohttp.ClientSession() as session:
            async with session.get(find_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    t_data = await resp.json()
                    movie_res = t_data.get("movie_results", [])
                    tv_res = t_data.get("tv_results", [])
                    
                    item = None
                    media_type = "movie"
                    if movie_res:
                        item = movie_res[0]
                        media_type = "movie"
                    elif tv_res:
                        item = tv_res[0]
                        media_type = "tv"

                    if item:
                        data["title"] = item.get("title") or item.get("name") or "N/A"
                        
                        date_str = item.get("release_date") or item.get("first_air_date") or "N/A"
                        if date_str and date_str != "N/A":
                            data["release_date"] = date_str
                            data["year"] = date_str.split("-")[0]

                        vote_avg = item.get("vote_average")
                        if vote_avg and vote_avg > 0:
                            data["rating"] = round(vote_avg, 1)

                        overview = item.get("overview")
                        if overview:
                            data["storyline"] = overview

                        poster_path = item.get("poster_path") or item.get("backdrop_path")
                        if poster_path:
                            data["poster"] = f"https://image.tmdb.org/t/p/w780{poster_path}"

                        # Fetch detailed TMDB data for director, genres, runtime/seasons, language, country
                        tmdb_id = item.get("id")
                        detail_url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}?api_key={TMDB_KEY}&append_to_response=credits,alternative_titles,videos"
                        async with session.get(detail_url, timeout=aiohttp.ClientTimeout(total=4)) as d_resp:
                            if d_resp.status == 200:
                                d_data = await d_resp.json()
                                
                                # Runtime or Series formatting
                                if media_type == "movie":
                                    rt = d_data.get("runtime")
                                    if rt:
                                        data["runtime"] = f"{rt} min"
                                else:
                                    seasons = d_data.get("seasons", [])
                                    season_parts = []
                                    for s in seasons:
                                        s_num = s.get("season_number")
                                        e_count = s.get("episode_count")
                                        if s_num > 0 and e_count:
                                            season_parts.append(f"S{s_num:02d} E{e_count:02d}")
                                    if season_parts:
                                        data["runtime"] = ", ".join(season_parts)

                                # Genres
                                data["genres"] = [g.get("name") for g in d_data.get("genres", []) if g.get("name")]

                                # Languages
                                data["languages"] = [l.get("english_name") for l in d_data.get("spoken_languages", []) if l.get("english_name")]

                                # Countries
                                data["countries"] = [c.get("name") for c in d_data.get("production_countries", []) if c.get("name")]

                                # Director / Creator
                                if media_type == "movie":
                                    crew = d_data.get("credits", {}).get("crew", [])
                                    directors = [cr.get("name") for cr in crew if cr.get("job") == "Director"]
                                    if directors:
                                        data["director"] = ", ".join(directors)
                                else:
                                    creators = d_data.get("created_by", [])
                                    if creators:
                                        data["director"] = ", ".join([cr.get("name") for cr in creators])

                                # Trailer URL
                                videos = d_data.get("videos", {}).get("results", [])
                                for vid in videos:
                                    if vid.get("site") == "YouTube" and vid.get("type") in ["Trailer", "Teaser"]:
                                        data["trailer_url"] = f"https://www.youtube.com/watch?v={vid.get('key')}"
                                        break
    except Exception as e:
        print(f"TMDB Fetch Error: {e}", flush=True)

    # 2. Fallback to OMDB if any core field is missing
    omdb_url = f"https://www.omdbapi.com/?i={imdb_id}&plot=full&apikey=trilogy"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(omdb_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    o_data = await resp.json()
                    if o_data.get("Response") == "True":
                        if data["title"] == "N/A":
                            data["title"] = o_data.get("Title", "N/A")
                        if data["year"] == "N/A":
                            data["year"] = o_data.get("Year", "N/A")
                        if data["rating"] == "N/A":
                            data["rating"] = o_data.get("imdbRating", "N/A")
                        if data["release_date"] == "N/A":
                            data["release_date"] = o_data.get("Released", "N/A")
                        if data["director"] == "N/A":
                            data["director"] = o_data.get("Director", "N/A")
                        if not data["genres"]:
                            g = o_data.get("Genre", "")
                            data["genres"] = [x.strip() for x in g.split(",") if x.strip() and x.strip() != "N/A"]
                        if not data["languages"]:
                            l = o_data.get("Language", "")
                            data["languages"] = [x.strip() for x in l.split(",") if x.strip() and x.strip() != "N/A"]
                        if not data["countries"]:
                            c = o_data.get("Country", "")
                            data["countries"] = [x.strip() for x in c.split(",") if x.strip() and x.strip() != "N/A"]
                        if data["storyline"] == "No storyline available.":
                            plot = o_data.get("Plot")
                            if plot and plot != "N/A":
                                data["storyline"] = plot
                        if not data["poster"]:
                            poster = o_data.get("Poster")
                            if poster and poster != "N/A":
                                data["poster"] = poster
    except Exception as e:
        print(f"OMDB Fallback Error: {e}", flush=True)

    # Trailer fallback
    if not data["trailer_url"]:
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

    except Exception as e:
        print(f"IMDb Command Error: {e}", flush=True)
        try:
            await message.reply_text("⚠️ Something went wrong while searching IMDb.", quote=True)
        except Exception:
            pass


# ================= CALLBACK FOR MOVIE CARD (Reply to User Message) ================= #

@app.on_callback_query(filters.regex(r"^imdb_view:(.*)"))
async def imdb_view_callback(client, query: CallbackQuery):
    try:
        imdb_id = query.data.split(":", 1)[1].strip()
        await query.answer("Fetching from IMDb...")

        # Store the original user message object to reply to it
        orig_message = query.message.reply_to_message or query.message

        try:
            await query.message.delete()
        except Exception:
            pass

        info = await fetch_full_movie_details(imdb_id)

        # Dynamic Bot Mention Link
        me = await client.get_me()
        bot_user = me.username or "CinemaVetaBot"
        bot_mention = f'<a href="https://t.me/{bot_user}"><b>@{bot_user}</b></a>'

        # Hashtags formatting
        genre_str = " ".join([f"#{g.replace(' ', '_')}" for g in info["genres"]]) if info["genres"] else "N/A"
        lang_str = " ".join([f"#{l.replace(' ', '_')}" for l in info["languages"]]) if info["languages"] else "N/A"
        country_str = " ".join([f"#{c.replace(' ', '_')}" for c in info["countries"]]) if info["countries"] else "N/A"

        rating_disp = f"{info['rating']} / 10" if info['rating'] != "N/A" else "N/A / 10"

        # Clickable Hyperlink Title (Direct IMDb Link)
        title_link = f'<a href="{info["imdb_url"]}"><b>{html.escape(info["title"])} [{html.escape(str(info["year"]))}]</b></a>'

        caption_lines = [
            f"🎬 {title_link}\n"
        ]

        if info["aka"]:
            caption_lines.append(f"📝 <b>Also Known As:</b> {html.escape(info['aka'])}")

        caption_lines.extend([
            f"⭐ <b>IMDb Rating :</b> {rating_disp}",
            f"🗓 <b>Release Info :</b> {info['release_date'] if info['release_date'] != 'N/A' else info['year']}",
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
