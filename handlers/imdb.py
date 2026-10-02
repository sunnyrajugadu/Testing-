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


print("✅ handlers/imdb.py imported", flush=True)

TMDB_KEY = "7f43669a428c09611a0518fa9c0bbddb"


# ================= FETCH SUGGESTION TITLES ================= #

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
                        imdb_image = item.get("i", {}).get("imageUrl") if item.get("i") else None

                        if title:
                            results.append({
                                "id": item_id,
                                "title": title,
                                "year": str(year),
                                "imdb_poster": imdb_image
                            })

                        if len(results) >= limit:
                            break
                    return results
    except Exception as e:
        print(f"IMDb Search Error: {e}", flush=True)
    return []


# ================= FETCH DETAILED MOVIE INFO ================= #

async def fetch_full_movie_details(imdb_id: str, source: str = "imdb"):
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
        "imdb_poster": None,
        "tmdb_poster": None,
        "imdb_url": f"https://www.imdb.com/title/{imdb_id}",
        "trailer_url": None
    }

    # 1. Fetch OMDB Details
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
                        if poster and poster != "N/A":
                            data["imdb_poster"] = poster
    except Exception as e:
        print(f"OMDB Detailed Fetch Error: {e}", flush=True)

    # 2. Fetch TMDB Details for TMDB image, AKA and Trailer
    tmdb_find_url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={TMDB_KEY}&external_source=imdb_id"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(tmdb_find_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    t_find = await resp.json()
                    movie_res = t_find.get("movie_results") or t_find.get("tv_results") or []
                    if movie_res:
                        item = movie_res[0]
                        tmdb_id = item.get("id")
                        media_type = "movie" if t_find.get("movie_results") else "tv"

                        poster_path = item.get("poster_path") or item.get("backdrop_path")
                        if poster_path:
                            data["tmdb_poster"] = f"https://image.tmdb.org/t/p/w780{poster_path}"

                        ext_url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}?api_key={TMDB_KEY}&append_to_response=alternative_titles,videos"
                        async with session.get(ext_url, timeout=aiohttp.ClientTimeout(total=4)) as ext_resp:
                            if ext_resp.status == 200:
                                ext_data = await ext_resp.json()
                                
                                alt_titles = (ext_data.get("alternative_titles", {}).get("titles") or 
                                              ext_data.get("alternative_titles", {}).get("results") or [])
                                for alt in alt_titles:
                                    alt_title = alt.get("title")
                                    if alt_title and alt_title.lower() != data["title"].lower():
                                        data["aka"] = alt_title
                                        break

                                videos = ext_data.get("videos", {}).get("results", [])
                                for vid in videos:
                                    if vid.get("site") == "YouTube" and vid.get("type") in ["Trailer", "Teaser"]:
                                        data["trailer_url"] = f"https://www.youtube.com/watch?v={vid.get('key')}"
                                        break
    except Exception as e:
        print(f"TMDB Extended Fetch Error: {e}", flush=True)

    # Trailer fallback
    if not data["trailer_url"]:
        clean_name = data["title"].replace(" ", "+")
        data["trailer_url"] = f"https://www.youtube.com/results?search_query={clean_name}+official+trailer"

    # Command source base meeda correct poster pick cheyyadam
    if source == "tmdb":
        data["poster"] = data["tmdb_poster"] or data["imdb_poster"]
    else:
        data["poster"] = data["imdb_poster"] or data["tmdb_poster"]

    return data


# ================= /imdb AND /tmdb COMMAND HANDLER ================= #

@app.on_message(filters.command(["imdb", "tmdb"]))
async def imdb_search_command(client, message: Message):
    try:
        cmd = message.command[0].lower()
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            return await message.reply_text(
                f"💡 <b>Usage Guide :</b>\n"
                f"» <code>/{cmd} &lt;movie_name&gt;</code>\n"
                f"» <i>Example :</i> <code>/{cmd} Salaar</code>",
                quote=True
            )

        query = parts[1].strip()
        search_msg = await message.reply_text(f"⚡ <b>Searching {cmd.upper()} database...</b>", quote=True)

        results = await fetch_imdb_results(query, limit=10)
        if not results:
            return await search_msg.edit_text(f"🥀 <b>No matching results found for :</b> <code>{html.escape(query)}</code>")

        # Buttons WITHOUT EMOJIS (Pure clean text format)
        buttons = []
        for item in results:
            btn_text = f"{item['title'][:40]} - {item['year']}"
            buttons.append([
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=f"imdb_view:{cmd}:{item['id']}"
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
        print(f"IMDb/TMDB Command Error: {e}", flush=True)
        try:
            await message.reply_text("⚠️ Something went wrong while searching.", quote=True)
        except Exception:
            pass


# ================= CALLBACK FOR MOVIE CARD ================= #

@app.on_callback_query(filters.regex(r"^imdb_view:(imdb|tmdb):(.*)"))
async def imdb_view_callback(client, query: CallbackQuery):
    try:
        _, source, imdb_id = query.data.split(":", 2)
        await query.answer(f"Fetching from {source.upper()} 🍿...")

        try:
            await query.message.delete()
        except Exception:
            pass

        info = await fetch_full_movie_details(imdb_id, source=source)

        # Dynamic Bot Mention Link
        me = await client.get_me()
        bot_user = me.username or "CinemaVetaBot"
        bot_mention = f'<a href="https://t.me/{bot_user}"><b>@{bot_user}</b></a>'

        # Hashtags
        genre_str = " ".join([f"#{g.replace(' ', '_')}" for g in info["genres"]]) if info["genres"] else "#N/A"
        lang_str = " ".join([f"#{l.replace(' ', '_')}" for l in info["languages"]]) if info["languages"] else "#N/A"
        country_str = " ".join([f"#{c.replace(' ', '_')}" for c in info["countries"]]) if info["countries"] else "#N/A"

        rating_disp = f"{info['rating']} / 10"

        # Clickable Hyperlink Title (Opens direct IMDb link)
        title_link = f'<a href="{info["imdb_url"]}"><b>{html.escape(info["title"])} [{html.escape(str(info["year"]))}]</b></a>'

        caption_lines = [
            f"🎬 {title_link}\n"
        ]

        if info["aka"]:
            caption_lines.append(f"📝 <b>Also Known As :</b> <i>{html.escape(info['aka'])}</i>")

        caption_lines.extend([
            f"⭐ <b>IMDb Rating :</b> <code>{rating_disp}</code>",
            f"🗓️ <b>Release Date :</b> <code>{info['release_date']}</code>",
            f"⏳ <b>Duration :</b> <code>{info['runtime']}</code>",
            f"🎥 <b>Directed By :</b> <code>{info['director']}</code>",
            f"🎭 <b>Genre :</b> {genre_str}",
            f"🌐 <b>Language :</b> {lang_str}",
            f"🌍 <b>Country Of Origin :</b> {country_str}\n",
            "📖 <b>Storyline :</b>",
            f"<blockquote>{html.escape(info['storyline'][:750])}</blockquote>\n",
            f"✨ <b>Powered By : {bot_mention}</b>"
        ])

        final_caption = "\n".join(caption_lines)

        # Exact title in button: "🔗 View {Title} on IMDb"
        clean_btn_title = info["title"][:28]
        buttons = [
            [
                InlineKeyboardButton(
                    f"🔗 {clean_btn_title} on IMDb",
                    url=info["imdb_url"]
                )
            ],
            [
                InlineKeyboardButton(
                    "🎦 Watch Trailer",
                    url=info["trailer_url"]
                )
            ],
            [
                InlineKeyboardButton("✘ Close ✘", callback_data="close")
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
        print(f"IMDb/TMDB View Callback Error: {e}", flush=True)
        try:
            await query.answer("❌ Failed to fetch movie details.", show_alert=True)
        except Exception:
            pass
