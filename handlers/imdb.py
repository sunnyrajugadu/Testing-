import html
import re
from urllib.parse import quote_plus

import aiohttp
from pyrogram import filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot import app
from utils.helpers import normalize_text


print("✅ handlers/imdb.py imported (IMDb GraphQL Metadata Fix)", flush=True)


# ============================================================
# IMDb endpoints
# ============================================================

IMDB_GRAPHQL_URL = "https://api.graphql.imdb.com/"
IMDB_SUGGESTION_BASE = "https://v3.sg.media-imdb.com/suggestion/"
IMDB_TITLE_URL = "https://www.imdb.com/title/{}"

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=12, connect=5, sock_read=10)


# ============================================================
# Helpers
# ============================================================

def clean_text(value, default="N/A"):
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def unique_strings(values):
    result = []
    seen = set()
    for value in values or []:
        value = clean_text(value, "")
        if not value:
            continue
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def format_runtime(runtime_str=None, seconds=None):
    """Prefer IMDb's own displayable runtime; otherwise format seconds."""
    if runtime_str and runtime_str != "N/A":
        return str(runtime_str).strip()

    try:
        total_seconds = int(seconds or 0)
        if total_seconds <= 0:
            return "N/A"

        total_minutes = total_seconds // 60
        hours = total_minutes // 60
        minutes = total_minutes % 60

        if hours and minutes:
            return f"{hours} hrs {minutes} mins"
        if hours:
            return f"{hours} hrs"
        return f"{minutes} mins"
    except Exception:
        return "N/A"


def format_release_date(release_date, fallback_year=None):
    if not release_date:
        return clean_text(fallback_year)

    day = release_date.get("day")
    month = release_date.get("month")
    year = release_date.get("year")

    if not year:
        return clean_text(fallback_year)

    try:
        months = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"
        ]
        month_name = months[int(month) - 1] if month else None
        if month_name and day:
            return f"{month_name} {int(day)}, {year}"
        if month_name:
            return f"{month_name} {year}"
        return str(year)
    except Exception:
        return str(year)


def poster_high_res(url):
    if not url:
        return None

    try:
        # IMDb image URLs normally contain a resize suffix such as _V1_...
        # Remove it and request a larger portrait image.
        if "_V1_" in url:
            base = url.split("_V1_", 1)[0]
            return f"{base}_V1_UY1200_CR0,0,800,1200_AL_.jpg"
    except Exception:
        pass

    return url


def make_hashtags(values):
    tags = []
    for value in values or []:
        value = clean_text(value, "")
        if value:
            tag = re.sub(r"[^\w]+", "_", value, flags=re.UNICODE).strip("_")
            if tag:
                tags.append(f"#{tag}")
    return " ".join(tags) if tags else "N/A"


def get_credit_names(principal_credits, category_ids):
    names = []
    wanted = {str(x).lower() for x in category_ids}

    for group in principal_credits or []:
        category = group.get("category") or {}
        category_id = str(category.get("id", "")).lower()
        category_text = str(category.get("text", "")).lower()

        if category_id not in wanted and not any(x in category_text for x in wanted):
            continue

        for credit in group.get("credits") or []:
            name = ((credit.get("name") or {}).get("nameText") or {}).get("text")
            if name:
                names.append(name)

    return unique_strings(names)


def extract_akas(akas_data, original_title):
    result = []
    original_key = (original_title or "").casefold().strip()

    for edge in (akas_data or {}).get("edges", []) or []:
        node = edge.get("node") or {}
        text = clean_text(node.get("text"), "")
        if not text:
            continue
        if text.casefold().strip() == original_key:
            continue
        result.append(text)

    return unique_strings(result)[:5]


def extract_graphql_errors(payload):
    errors = payload.get("errors") or []
    messages = []
    for error in errors:
        message = error.get("message") if isinstance(error, dict) else str(error)
        if message:
            messages.append(str(message))
    return "; ".join(messages)


# ============================================================
# IMDb GraphQL request
# ============================================================

async def imdb_graphql(query, variables=None):
    payload = {
        "query": query,
        "variables": variables or {},
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; CinemaVeta/1.0)",
        "Origin": "https://www.imdb.com",
        "Referer": "https://www.imdb.com/",
    }

    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT, headers=headers) as session:
        async with session.post(IMDB_GRAPHQL_URL, json=payload) as response:
            if response.status != 200:
                body = await response.text()
                raise RuntimeError(f"IMDb GraphQL HTTP {response.status}: {body[:300]}")

            data = await response.json(content_type=None)

            if data.get("errors") and not data.get("data"):
                raise RuntimeError(extract_graphql_errors(data) or "IMDb GraphQL returned an error")

            return data


# ============================================================
# Search titles - IMDb GraphQL first, suggestion API fallback
# ============================================================

IMDB_SEARCH_QUERY = r'''
query SearchTitles($searchTerm: String!, $first: Int!) {
  mainSearch(
    first: $first
    options: {
      searchTerm: $searchTerm
      type: TITLE
    }
  ) {
    edges {
      node {
        entity {
          ... on Title {
            id
            titleText { text }
            releaseYear { year }
            titleType { id text isSeries isEpisode }
            ratingsSummary { aggregateRating }
            primaryImage { url }
            titleGenres {
              genres { genre { text } }
            }
          }
        }
      }
    }
  }
}
'''


async def fetch_imdb_results_graphql(query, limit=10):
    payload = await imdb_graphql(
        IMDB_SEARCH_QUERY,
        {"searchTerm": query, "first": max(1, min(int(limit), 25))},
    )

    results = []
    edges = (((payload.get("data") or {}).get("mainSearch") or {}).get("edges") or [])

    for edge in edges:
        entity = ((edge.get("node") or {}).get("entity") or {})
        if not entity.get("id", "").startswith("tt"):
            continue

        title = ((entity.get("titleText") or {}).get("text"))
        if not title:
            continue

        release_year = (entity.get("releaseYear") or {}).get("year")
        rating = (entity.get("ratingsSummary") or {}).get("aggregateRating")
        poster = (entity.get("primaryImage") or {}).get("url")
        title_type = entity.get("titleType") or {}

        genres = []
        for genre_item in ((entity.get("titleGenres") or {}).get("genres") or []):
            genre = ((genre_item.get("genre") or {}).get("text"))
            if genre:
                genres.append(genre)

        results.append({
            "id": entity["id"],
            "title": title,
            "year": str(release_year) if release_year else "N/A",
            "poster": poster,
            "rating": str(rating) if rating is not None else "N/A",
            "genres": unique_strings(genres),
            "type": clean_text(title_type.get("text"), "N/A"),
        })

        if len(results) >= limit:
            break

    return results


async def fetch_imdb_results_suggestion(query: str, limit: int = 10):
    clean_q = normalize_text(query)
    if not clean_q:
        return []

    first_char = quote_plus(clean_q[0])
    encoded_query = quote_plus(clean_q)
    url = f"{IMDB_SUGGESTION_BASE}{first_char}/{encoded_query}.json"

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return []

                data = await response.json(content_type=None)
                results = []

                for item in data.get("d", []):
                    item_id = str(item.get("id", ""))
                    if not item_id.startswith("tt"):
                        continue

                    entity_type = str(item.get("q", "")).lower()
                    if entity_type in {"actor", "actress", "soundtrack"}:
                        continue

                    title = item.get("l")
                    if not title:
                        continue

                    results.append({
                        "id": item_id,
                        "title": title,
                        "year": str(item.get("y")) if item.get("y") else "N/A",
                        "poster": (item.get("i") or {}).get("imageUrl"),
                        "rating": str(item.get("r")) if item.get("r") is not None else "N/A",
                        "genres": unique_strings(item.get("gen") or []),
                        "type": clean_text(item.get("q"), "N/A"),
                    })

                    if len(results) >= limit:
                        break

                return results
    except Exception as exc:
        print(f"IMDb Suggestion Search Error: {exc}", flush=True)
        return []


async def fetch_imdb_results(query: str, limit: int = 10):
    clean_q = normalize_text(query)
    if not clean_q:
        return []

    try:
        results = await fetch_imdb_results_graphql(clean_q, limit=limit)
        if results:
            return results
    except Exception as exc:
        print(f"IMDb GraphQL Search Error: {exc}", flush=True)

    # Keep the old suggestion endpoint as a search fallback.
    return await fetch_imdb_results_suggestion(clean_q, limit=limit)


# ============================================================
# Full IMDb title details
# ============================================================

IMDB_DETAILS_QUERY = r'''
query GetTitleDetails($id: ID!) {
  title(id: $id) {
    id
    titleText {
      text
      isOriginalTitle
      country { text }
    }
    originalTitleText { text }
    releaseYear {
      year
      endYear
    }
    titleType {
      id
      text
      isSeries
      isEpisode
    }
    releaseDate {
      day
      month
      year
      country { text }
    }
    runtime {
      seconds
      displayableProperty {
        value { plainText }
      }
    }
    titleGenres {
      genres { genre { text } }
    }
    ratingsSummary {
      aggregateRating
      voteCount
    }
    primaryImage {
      url
      width
      height
    }
    plot {
      plotText { plainText }
    }
    countriesOfOrigin {
      countries { id text }
    }
    spokenLanguages(limit: 20) {
      spokenLanguages { id text }
    }
    principalCredits {
      category { id text }
      credits {
        name {
          id
          nameText { text }
        }
      }
    }
    akas(first: 10) {
      edges {
        node {
          text
          country { text }
          language { text }
        }
      }
    }
    certificate {
      rating
    }
  }
}
'''


async def fetch_full_movie_details(
    imdb_id: str,
    fallback_title: str = None,
    fallback_year: str = None,
    fallback_poster: str = None,
    fallback_rating: str = None,
    fallback_genres: list = None,
    fallback_director: str = None,
):
    # IMPORTANT: Never invent language/country data. If IMDb does not return it,
    # the card displays N/A instead of pretending it is English/Telugu/India.
    data = {
        "id": imdb_id,
        "title": fallback_title or "N/A",
        "original_title": None,
        "year": fallback_year or "N/A",
        "year_end": None,
        "title_type": "N/A",
        "aka": None,
        "rating": fallback_rating or "N/A",
        "vote_count": None,
        "release_date": fallback_year or "N/A",
        "release_country": None,
        "runtime": "N/A",
        "director": fallback_director if fallback_director and fallback_director != "N/A" else "N/A",
        "writers": [],
        "genres": fallback_genres or [],
        "languages": [],
        "countries": [],
        "certificate": None,
        "storyline": "No storyline available.",
        "poster": fallback_poster,
        "imdb_url": IMDB_TITLE_URL.format(imdb_id),
        "trailer_url": None,
    }

    try:
        payload = await imdb_graphql(IMDB_DETAILS_QUERY, {"id": imdb_id})
        title = ((payload.get("data") or {}).get("title"))

        if not title:
            raise RuntimeError("IMDb returned no title data")

        title_text = title.get("titleText") or {}
        original_title = (title.get("originalTitleText") or {}).get("text")
        release_year = title.get("releaseYear") or {}
        release_date = title.get("releaseDate") or {}
        runtime = title.get("runtime") or {}
        rating = title.get("ratingsSummary") or {}
        title_type = title.get("titleType") or {}
        plot = title.get("plot") or {}

        data["title"] = clean_text(title_text.get("text"), data["title"])
        data["original_title"] = clean_text(original_title, "") or None
        data["year"] = str(release_year.get("year")) if release_year.get("year") else data["year"]
        data["year_end"] = release_year.get("endYear")
        data["title_type"] = clean_text(title_type.get("text"), "N/A")

        data["rating"] = (
            str(rating.get("aggregateRating"))
            if rating.get("aggregateRating") is not None
            else data["rating"]
        )
        data["vote_count"] = rating.get("voteCount")

        data["release_date"] = format_release_date(release_date, data["year"])
        data["release_country"] = ((release_date.get("country") or {}).get("text"))

        display_runtime = (((runtime.get("displayableProperty") or {}).get("value") or {}).get("plainText"))
        data["runtime"] = format_runtime(display_runtime, runtime.get("seconds"))

        genres = []
        for item in ((title.get("titleGenres") or {}).get("genres") or []):
            genre = ((item.get("genre") or {}).get("text"))
            if genre:
                genres.append(genre)
        data["genres"] = unique_strings(genres)

        poster = (title.get("primaryImage") or {}).get("url")
        if poster:
            data["poster"] = poster_high_res(poster)

        plot_text = (plot.get("plotText") or {}).get("plainText")
        if plot_text:
            data["storyline"] = plot_text.strip()

        countries = []
        for item in ((title.get("countriesOfOrigin") or {}).get("countries") or []):
            if item.get("text"):
                countries.append(item["text"])
        data["countries"] = unique_strings(countries)

        languages = []
        for item in ((title.get("spokenLanguages") or {}).get("spokenLanguages") or []):
            if item.get("text"):
                languages.append(item["text"])
        data["languages"] = unique_strings(languages)

        data["certificate"] = ((title.get("certificate") or {}).get("rating")) or None

        directors = get_credit_names(
            title.get("principalCredits"),
            {"director"},
        )
        writers = get_credit_names(
            title.get("principalCredits"),
            {"writer", "writers"},
        )

        if directors:
            data["director"] = ", ".join(directors)
        data["writers"] = writers

        akas = extract_akas(title.get("akas"), original_title)
        if akas:
            data["aka"] = ", ".join(akas)

    except Exception as exc:
        print(f"IMDb GraphQL Details Error [{imdb_id}]: {exc}", flush=True)

        # If the public GraphQL endpoint is temporarily unavailable, try the
        # exact-ID suggestion endpoint before returning the search fallback data.
        try:
            sugg_url = f"{IMDB_SUGGESTION_BASE}t/{quote_plus(imdb_id)}.json"
            async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
                async with session.get(sugg_url) as response:
                    if response.status == 200:
                        payload = await response.json(content_type=None)
                        for item in payload.get("d", []):
                            if str(item.get("id")) != imdb_id:
                                continue
                            data["title"] = clean_text(item.get("l"), data["title"])
                            if item.get("y"):
                                data["year"] = str(item["y"])
                                data["release_date"] = str(item["y"])
                            if item.get("r") is not None:
                                data["rating"] = str(item["r"])
                            if item.get("gen"):
                                data["genres"] = unique_strings(item["gen"])
                            poster = (item.get("i") or {}).get("imageUrl")
                            if poster:
                                data["poster"] = poster_high_res(poster)
                            break
        except Exception as fallback_exc:
            print(f"IMDb ID Fallback Error [{imdb_id}]: {fallback_exc}", flush=True)

    if not data["poster"]:
        # Do not use a random/movie-specific fake poster. If IMDb has no image,
        # Telegram will simply send the metadata as text.
        data["poster"] = None

    trailer_query = quote_plus(f"{data['title']} {data['year']} official trailer")
    data["trailer_url"] = f"https://www.youtube.com/results?search_query={trailer_query}"

    return data


# ============================================================
# /imdb COMMAND
# ============================================================

@app.on_message(filters.private & filters.command(["imdb"]))
async def imdb_search_command(client, message: Message):
    try:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            return await message.reply_text(
                "💡 <b>Usage Guide :</b>\n"
                "» <code>/imdb &lt;movie_name&gt;</code>\n"
                "» <i>Example :</i> <code>/imdb Salaar</code>",
                quote=True,
            )

        query = parts[1].strip()
        search_msg = await message.reply_text(
            "⚡ <b>Searching IMDb database...</b>",
            quote=True,
        )

        results = await fetch_imdb_results(query, limit=10)
        if not results:
            return await search_msg.edit_text(
                f"🥀 <b>No matching results found for :</b> <code>{html.escape(query)}</code>"
            )

        buttons = []
        for item in results:
            btn_text = f"{item['title']} - {item['year']}"

            # ONLY pass the IMDb ID. Telegram callback_data has a 64-byte limit;
            # the old implementation packed poster/title/genres/director into it
            # and then truncated the string, which could destroy the metadata.
            callback_payload = f"imdb_view:{item['id']}"

            buttons.append([
                InlineKeyboardButton(
                    text=btn_text[:64],
                    callback_data=callback_payload,
                )
            ])

        buttons.append([
            InlineKeyboardButton("Close", callback_data="close")
        ])

        header_text = (
            f"🎯 <b>Matched Results For :</b> <code>{html.escape(query.title())}</code>\n"
            f"<i>👇 Choose the exact title below to view full IMDb details :</i>"
        )

        await search_msg.edit_text(
            text=header_text,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    except Exception as exc:
        print(f"IMDb Command Error: {exc}", flush=True)
        try:
            await message.reply_text(
                "⚠️ Something went wrong while searching IMDb.",
                quote=True,
            )
        except Exception:
            pass


# ============================================================
# IMDb DETAIL CALLBACK
# ============================================================

@app.on_callback_query(filters.regex(r"^imdb_view:(tt\d+)$"))
async def imdb_view_callback(client, query: CallbackQuery):
    try:
        imdb_id = query.matches[0].group(1)

        await query.answer("Fetching exact IMDb details...")

        # Keep the original user's message as the reply target.
        orig_message = query.message.reply_to_message or query.message

        try:
            await query.message.delete()
        except Exception:
            pass

        info = await fetch_full_movie_details(imdb_id)

        me = await client.get_me()
        bot_user = me.username or "CinemaVetaBot"
        bot_mention = f'<a href="https://t.me/{bot_user}"><b>@{html.escape(bot_user)}</b></a>'

        genre_str = make_hashtags(info["genres"])
        lang_str = make_hashtags(info["languages"])
        country_str = make_hashtags(info["countries"])

        rating_disp = (
            f"{html.escape(str(info['rating']))} / 10"
            if info["rating"] != "N/A"
            else "N/A / 10"
        )

        vote_count = info.get("vote_count")
        if isinstance(vote_count, int):
            vote_disp = f" ({vote_count:,} votes)"
        else:
            vote_disp = ""

        title_display = html.escape(info["title"])
        year_display = html.escape(str(info["year"]))
        title_link = (
            f'<a href="{info["imdb_url"]}">'
            f'<b>{title_display} [{year_display}]</b>'
            f'</a>'
        )

        caption_lines = [f"🎬 {title_link}\n"]

        if info.get("original_title") and info["original_title"] != info["title"]:
            caption_lines.append(
                f"📝 <b>Original Title :</b> {html.escape(info['original_title'])}"
            )

        if info.get("aka"):
            caption_lines.append(
                f"🏷 <b>Also Known As :</b> {html.escape(info['aka'])}"
            )

        if info.get("title_type") and info["title_type"] != "N/A":
            caption_lines.append(
                f"🎞 <b>Type :</b> {html.escape(info['title_type'])}"
            )

        caption_lines.extend([
            f"⭐ <b>IMDb Rating :</b> {rating_disp}{vote_disp}",
            f"🗓 <b>Release Info :</b> {html.escape(str(info['release_date']))}",
            f"⏳ <b>Runtime :</b> {html.escape(str(info['runtime']))}",
            f"🎥 <b>Directed By :</b> {html.escape(str(info['director']))}",
            f"✍️ <b>Written By :</b> {html.escape(', '.join(info['writers']) if info['writers'] else 'N/A')}",
            f"🎭 <b>Genre :</b> {genre_str}",
            f"🌐 <b>Language :</b> {lang_str}",
            f"🌍 <b>Country Of Origin :</b> {country_str}",
        ])

        if info.get("certificate"):
            caption_lines.append(
                f"🔞 <b>Certificate :</b> {html.escape(str(info['certificate']))}"
            )

        if info.get("release_country"):
            caption_lines.append(
                f"📍 <b>First Release Country :</b> {html.escape(str(info['release_country']))}"
            )

        caption_lines.extend([
            "",
            "📖 <b>Storyline :</b>",
            html.escape(info["storyline"]),
            "",
            f"✨ <b>Powered By :</b>\n{bot_mention}",
        ])

        final_caption = "\n".join(caption_lines)

        buttons = [
            [
                InlineKeyboardButton(
                    f"🔗 {info['title'][:40]} on IMDb",
                    url=info["imdb_url"],
                )
            ],
            [
                InlineKeyboardButton(
                    "▶️ Watch Trailer",
                    url=info["trailer_url"],
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(buttons)

        sent = False

        if info.get("poster"):
            try:
                await client.send_photo(
                    chat_id=orig_message.chat.id,
                    photo=info["poster"],
                    caption=final_caption,
                    reply_markup=reply_markup,
                    reply_to_message_id=orig_message.id,
                )
                sent = True
            except Exception as photo_error:
                print(f"IMDb Photo Send Error: {photo_error}", flush=True)

        if not sent:
            await client.send_message(
                chat_id=orig_message.chat.id,
                text=final_caption,
                reply_markup=reply_markup,
                reply_to_message_id=orig_message.id,
                disable_web_page_preview=False,
            )

    except Exception as exc:
        print(f"IMDb View Callback Error: {exc}", flush=True)
        try:
            await query.answer(
                "❌ Failed to fetch IMDb details.",
                show_alert=True,
            )
        except Exception:
            pass
