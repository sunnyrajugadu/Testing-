import html
import json
import re
from urllib.parse import quote_plus

import aiohttp
from pyrogram import filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot import app
from utils.helpers import normalize_text


print("✅ handlers/imdb.py imported (IMDb Full Metadata Fix)", flush=True)


# ============================================================
# IMDb endpoints / HTTP
# ============================================================

IMDB_GRAPHQL_URL = "https://api.graphql.imdb.com/"
IMDB_SUGGESTION_BASE = "https://v3.sg.media-imdb.com/suggestion/"
IMDB_TITLE_URL = "https://www.imdb.com/title/{}"

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=6, sock_read=12)

IMDB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.imdb.com/",
}


# ============================================================
# Generic helpers
# ============================================================

def clean_text(value, default="N/A"):
    if value is None:
        return default
    if isinstance(value, str):
        value = value.strip()
    else:
        value = str(value).strip()
    return value if value else default


def unique_strings(values):
    result = []
    seen = set()
    for value in values or []:
        if isinstance(value, dict):
            value = value.get("text") or value.get("name") or value.get("value")
        value = clean_text(value, "")
        if not value:
            continue
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def deep_find(obj, wanted_keys):
    """Recursively find the first useful value for any key in wanted_keys."""
    wanted = {str(k).lower() for k in wanted_keys}

    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in wanted and value not in (None, "", [], {}):
                return value
        for value in obj.values():
            found = deep_find(value, wanted)
            if found not in (None, "", [], {}):
                return found

    elif isinstance(obj, list):
        for value in obj:
            found = deep_find(value, wanted)
            if found not in (None, "", [], {}):
                return found

    return None


def deep_find_all(obj, wanted_keys, output=None):
    if output is None:
        output = []
    wanted = {str(k).lower() for k in wanted_keys}

    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in wanted and value not in (None, "", [], {}):
                output.append(value)
            deep_find_all(value, wanted, output)
    elif isinstance(obj, list):
        for value in obj:
            deep_find_all(value, wanted, output)

    return output


def parse_json_ld_scripts(text):
    """Read every JSON-LD block from an IMDb title page."""
    found = []
    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.I | re.S,
    )

    for raw in pattern.findall(text or ""):
        raw = raw.strip()
        if not raw:
            continue
        raw = html.unescape(raw)
        try:
            found.append(json.loads(raw))
        except Exception:
            # Some pages contain malformed/trailing JSON-LD. Ignore only that block.
            continue

    return found


def extract_next_data(text):
    """Extract IMDb's __NEXT_DATA__ object when present."""
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        text or "",
        re.I | re.S,
    )
    if not match:
        return None

    try:
        return json.loads(html.unescape(match.group(1).strip()))
    except Exception:
        return None


def iso_duration_to_text(value):
    if not value:
        return "N/A"

    value = str(value).strip()
    if not value.startswith("P"):
        return value

    hours = re.search(r"(\d+)H", value)
    minutes = re.search(r"(\d+)M", value)
    seconds = re.search(r"(\d+)S", value)

    h = int(hours.group(1)) if hours else 0
    m = int(minutes.group(1)) if minutes else 0
    s = int(seconds.group(1)) if seconds else 0

    if h and m:
        return f"{h} hrs {m} mins"
    if h:
        return f"{h} hrs"
    if m:
        return f"{m} mins"
    if s:
        return f"{s} secs"
    return "N/A"


def format_runtime(runtime_str=None, seconds=None):
    if runtime_str and runtime_str != "N/A":
        runtime_str = str(runtime_str).strip()
        if runtime_str.startswith("PT") or runtime_str.startswith("P"):
            converted = iso_duration_to_text(runtime_str)
            if converted != "N/A":
                return converted
        return runtime_str

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


def format_release_date(value, fallback_year=None):
    if isinstance(value, str):
        value = value.strip()
        if value:
            # 2025-01-02 -> January 2, 2025
            match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value)
            if match:
                y, m, d = match.groups()
                try:
                    months = [
                        "January", "February", "March", "April", "May", "June",
                        "July", "August", "September", "October", "November", "December"
                    ]
                    return f"{months[int(m) - 1]} {int(d)}, {y}"
                except Exception:
                    pass
            return value

    if isinstance(value, dict):
        year = value.get("year")
        month = value.get("month")
        day = value.get("day")
        if year:
            try:
                months = [
                    "January", "February", "March", "April", "May", "June",
                    "July", "August", "September", "October", "November", "December"
                ]
                if month and day:
                    return f"{months[int(month) - 1]} {int(day)}, {year}"
                if month:
                    return f"{months[int(month) - 1]} {year}"
                return str(year)
            except Exception:
                return str(year)

    return clean_text(fallback_year)


def poster_high_res(url):
    if not url:
        return None
    try:
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
        if not value:
            continue
        tag = re.sub(r"[^\w]+", "_", value, flags=re.UNICODE).strip("_")
        if tag:
            tags.append(f"#{tag}")
    return " ".join(tags) if tags else "N/A"


def names_from_json_value(value):
    result = []

    def walk(item):
        if isinstance(item, str):
            # Avoid treating generic labels as people.
            if item.strip():
                result.append(item.strip())
        elif isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                result.append(name.strip())
            else:
                for v in item.values():
                    if isinstance(v, (dict, list)):
                        walk(v)
        elif isinstance(item, list):
            for v in item:
                walk(v)

    walk(value)
    return unique_strings(result)


def country_names(value):
    result = []

    def walk(item):
        if isinstance(item, str):
            if item.strip():
                result.append(item.strip())
        elif isinstance(item, dict):
            # JSON-LD countryOfOrigin may be {name: "India"}.
            for key in ("name", "text", "country"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    result.append(val.strip())
                    return
            for v in item.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(item, list):
            for v in item:
                walk(v)

    walk(value)
    return unique_strings(result)


def language_names(value):
    result = []

    def walk(item):
        if isinstance(item, str):
            if item.strip():
                result.append(item.strip())
        elif isinstance(item, dict):
            for key in ("name", "text", "language"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    result.append(val.strip())
                    return
            for v in item.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(item, list):
            for v in item:
                walk(v)

    walk(value)
    return unique_strings(result)


# ============================================================
# IMDb page metadata
# ============================================================

async def fetch_imdb_title_page(imdb_id):
    url = IMDB_TITLE_URL.format(imdb_id) + "/"
    headers = dict(IMDB_HEADERS)

    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT, headers=headers) as session:
        async with session.get(url, allow_redirects=True) as response:
            if response.status != 200:
                raise RuntimeError(f"IMDb title page HTTP {response.status}")
            return await response.text(errors="ignore")


def extract_title_from_jsonld(blocks):
    for block in blocks:
        candidates = block if isinstance(block, list) else [block]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if item.get("@type") in ("Movie", "TVSeries", "TVEpisode", "TVMovie", "CreativeWork"):
                title = item.get("name")
                if title:
                    return title
    return None


def extract_jsonld_details(blocks):
    """Extract the stable schema.org fields IMDb publishes on title pages."""
    out = {}

    for block in blocks:
        items = block if isinstance(block, list) else [block]
        for item in items:
            if not isinstance(item, dict):
                continue

            item_type = item.get("@type")
            types = item_type if isinstance(item_type, list) else [item_type]
            if not any(t in {"Movie", "TVSeries", "TVEpisode", "TVMovie", "CreativeWork"} for t in types if t):
                continue

            out.setdefault("title", item.get("name"))
            out.setdefault("image", item.get("image"))
            out.setdefault("datePublished", item.get("datePublished"))
            out.setdefault("duration", item.get("duration"))
            out.setdefault("genre", item.get("genre"))
            out.setdefault("inLanguage", item.get("inLanguage"))
            out.setdefault("countryOfOrigin", item.get("countryOfOrigin"))
            out.setdefault("description", item.get("description"))
            out.setdefault("director", item.get("director"))
            out.setdefault("creator", item.get("creator"))
            out.setdefault("aggregateRating", item.get("aggregateRating"))
            out.setdefault("contentRating", item.get("contentRating"))
            out.setdefault("alternateName", item.get("alternateName"))

    return out


# ============================================================
# Small GraphQL calls only for reliable IMDb fields
# ============================================================

async def imdb_graphql(query, variables=None):
    payload = {"query": query, "variables": variables or {}}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": IMDB_HEADERS["User-Agent"],
        "Origin": "https://www.imdb.com",
        "Referer": "https://www.imdb.com/",
    }

    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT, headers=headers) as session:
        async with session.post(IMDB_GRAPHQL_URL, json=payload) as response:
            text = await response.text(errors="ignore")
            if response.status != 200:
                raise RuntimeError(f"IMDb GraphQL HTTP {response.status}: {text[:250]}")
            try:
                return json.loads(text)
            except Exception as exc:
                raise RuntimeError(f"IMDb GraphQL invalid JSON: {text[:250]}") from exc


IMDB_CORE_QUERY = r'''
query GetTitleCore($id: ID!) {
  title(id: $id) {
    id
    titleText { text }
    originalTitleText { text }
    releaseYear { year endYear }
    releaseDate { day month year }
    runtime { seconds }
    ratingsSummary { aggregateRating voteCount }
    titleGenres { genres { genre { text } } }
    primaryImage { url }
    plot { plotText { plainText } }
  }
}
'''


async def fetch_imdb_core_graphql(imdb_id):
    payload = await imdb_graphql(IMDB_CORE_QUERY, {"id": imdb_id})
    title = ((payload.get("data") or {}).get("title"))
    if not title:
        return {}
    return title


# ============================================================
# Search
# ============================================================

IMDB_SEARCH_QUERY = r'''
query SearchTitles($searchTerm: String!, $first: Int!) {
  mainSearch(first: $first, options: { searchTerm: $searchTerm, type: TITLE }) {
    edges {
      node {
        entity {
          ... on Title {
            id
            titleText { text }
            releaseYear { year }
            ratingsSummary { aggregateRating }
            primaryImage { url }
            titleGenres { genres { genre { text } } }
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
        item_id = str(entity.get("id", ""))
        if not item_id.startswith("tt"):
            continue

        title = ((entity.get("titleText") or {}).get("text"))
        if not title:
            continue

        genres = []
        for item in ((entity.get("titleGenres") or {}).get("genres") or []):
            genre = ((item.get("genre") or {}).get("text"))
            if genre:
                genres.append(genre)

        results.append({
            "id": item_id,
            "title": title,
            "year": str((entity.get("releaseYear") or {}).get("year") or "N/A"),
            "poster": (entity.get("primaryImage") or {}).get("url"),
            "rating": str((entity.get("ratingsSummary") or {}).get("aggregateRating") or "N/A"),
            "genres": unique_strings(genres),
        })

        if len(results) >= limit:
            break

    return results


async def fetch_imdb_results_suggestion(query, limit=10):
    clean_q = normalize_text(query)
    if not clean_q:
        return []

    first_char = quote_plus(clean_q[0])
    encoded_query = quote_plus(clean_q)
    url = f"{IMDB_SUGGESTION_BASE}{first_char}/{encoded_query}.json"

    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            async with session.get(url, headers=IMDB_HEADERS) as response:
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
            })

            if len(results) >= limit:
                break

        return results
    except Exception as exc:
        print(f"IMDb Suggestion Search Error: {exc}", flush=True)
        return []


async def fetch_imdb_results(query, limit=10):
    clean_q = normalize_text(query)
    if not clean_q:
        return []

    try:
        results = await fetch_imdb_results_graphql(clean_q, limit)
        if results:
            return results
    except Exception as exc:
        print(f"IMDb GraphQL Search Error: {exc}", flush=True)

    return await fetch_imdb_results_suggestion(clean_q, limit)


# ============================================================
# FULL IMDb DETAILS
# ============================================================

async def fetch_full_movie_details(
    imdb_id: str,
    fallback_title=None,
    fallback_year=None,
    fallback_poster=None,
    fallback_rating=None,
    fallback_genres=None,
    fallback_director=None,
):
    data = {
        "id": imdb_id,
        "title": fallback_title or "N/A",
        "original_title": None,
        "year": fallback_year or "N/A",
        "rating": fallback_rating or "N/A",
        "vote_count": None,
        "release_date": fallback_year or "N/A",
        "runtime": "N/A",
        "director": fallback_director or "N/A",
        "genres": unique_strings(fallback_genres or []),
        "languages": [],
        "countries": [],
        "storyline": "No storyline available.",
        "poster": fallback_poster,
        "aka": None,
        "certificate": None,
        "imdb_url": IMDB_TITLE_URL.format(imdb_id),
        "trailer_url": None,
    }

    # --------------------------------------------------------
    # 1. IMDb title page: structured metadata
    # This is where we get the fields that were previously N/A.
    # --------------------------------------------------------
    try:
        page = await fetch_imdb_title_page(imdb_id)
        jsonld_blocks = parse_json_ld_scripts(page)
        jsonld = extract_jsonld_details(jsonld_blocks)

        if jsonld.get("title"):
            data["title"] = clean_text(jsonld["title"], data["title"])

        if jsonld.get("image"):
            image = jsonld["image"]
            if isinstance(image, list):
                image = image[0] if image else None
            if image:
                data["poster"] = poster_high_res(image)

        if jsonld.get("datePublished"):
            data["release_date"] = format_release_date(jsonld["datePublished"], data["year"])
            match = re.match(r"^(\d{4})", str(jsonld["datePublished"]))
            if match:
                data["year"] = match.group(1)

        if jsonld.get("duration"):
            data["runtime"] = format_runtime(jsonld["duration"])

        if jsonld.get("genre"):
            genres = jsonld["genre"]
            if not isinstance(genres, list):
                genres = [genres]
            data["genres"] = unique_strings(genres) or data["genres"]

        if jsonld.get("inLanguage"):
            data["languages"] = language_names(jsonld["inLanguage"])

        if jsonld.get("countryOfOrigin"):
            data["countries"] = country_names(jsonld["countryOfOrigin"])

        if jsonld.get("description"):
            data["storyline"] = clean_text(jsonld["description"], data["storyline"])

        if jsonld.get("contentRating"):
            data["certificate"] = clean_text(jsonld["contentRating"], "") or None

        aggregate = jsonld.get("aggregateRating") or {}
        if isinstance(aggregate, dict):
            if aggregate.get("ratingValue") is not None:
                data["rating"] = str(aggregate["ratingValue"])
            if aggregate.get("ratingCount") is not None:
                try:
                    data["vote_count"] = int(aggregate["ratingCount"])
                except Exception:
                    data["vote_count"] = aggregate["ratingCount"]

        director_value = jsonld.get("director")
        director_names = names_from_json_value(director_value)
        if director_names:
            data["director"] = ", ".join(director_names)

        alternate = jsonld.get("alternateName")
        if alternate:
            if isinstance(alternate, list):
                alternate = unique_strings(alternate)
                if alternate:
                    data["aka"] = ", ".join(alternate[:5])
            elif str(alternate).strip() and str(alternate).strip() != data["title"]:
                data["aka"] = str(alternate).strip()

        # IMDb's Next.js state can contain additional language/country/credit
        # information not exposed by JSON-LD. Use it only as a supplemental source.
        next_data = extract_next_data(page)
        if next_data:
            if not data["languages"]:
                vals = deep_find_all(next_data, {"spokenLanguages", "languages", "inLanguage"})
                lang_values = []
                for val in vals:
                    lang_values.extend(language_names(val))
                data["languages"] = unique_strings(lang_values)

            if not data["countries"]:
                vals = deep_find_all(next_data, {"countriesOfOrigin", "countryOfOrigin"})
                country_values = []
                for val in vals:
                    country_values.extend(country_names(val))
                data["countries"] = unique_strings(country_values)

            if data["director"] == "N/A":
                director_candidates = deep_find_all(next_data, {"director"})
                director_names = []
                for val in director_candidates:
                    director_names.extend(names_from_json_value(val))
                data["director"] = ", ".join(unique_strings(director_names)) or "N/A"

    except Exception as exc:
        print(f"IMDb Title Page Error [{imdb_id}]: {exc}", flush=True)

    # --------------------------------------------------------
    # 2. Small official IMDb GraphQL query.
    # Only known/stable fields are requested. This prevents one
    # unsupported field from wiping the entire details response.
    # --------------------------------------------------------
    try:
        core = await fetch_imdb_core_graphql(imdb_id)
        if core:
            title_text = ((core.get("titleText") or {}).get("text"))
            original_title = ((core.get("originalTitleText") or {}).get("text"))
            release_year = core.get("releaseYear") or {}
            release_date = core.get("releaseDate") or {}
            runtime = core.get("runtime") or {}
            ratings = core.get("ratingsSummary") or {}

            if title_text:
                data["title"] = title_text
            if original_title and original_title != data["title"]:
                data["original_title"] = original_title

            if release_year.get("year"):
                data["year"] = str(release_year["year"])

            # GraphQL releaseDate is more precise than year-only JSON-LD.
            if release_date:
                data["release_date"] = format_release_date(release_date, data["year"])

            if ratings.get("aggregateRating") is not None:
                data["rating"] = str(ratings["aggregateRating"])
            if ratings.get("voteCount") is not None:
                data["vote_count"] = ratings["voteCount"]

            if runtime.get("seconds"):
                data["runtime"] = format_runtime(seconds=runtime["seconds"])

            genres = []
            for item in ((core.get("titleGenres") or {}).get("genres") or []):
                genre = ((item.get("genre") or {}).get("text"))
                if genre:
                    genres.append(genre)
            if genres:
                data["genres"] = unique_strings(genres)

            image = (core.get("primaryImage") or {}).get("url")
            if image:
                data["poster"] = poster_high_res(image)

            plot = ((core.get("plot") or {}).get("plotText") or {}).get("plainText")
            if plot:
                data["storyline"] = plot.strip()

    except Exception as exc:
        print(f"IMDb Core GraphQL Error [{imdb_id}]: {exc}", flush=True)

    # --------------------------------------------------------
    # 3. Final cleanup / no fake data
    # --------------------------------------------------------
    if not data["poster"]:
        data["poster"] = None

    if data["rating"] in (None, "", "None"):
        data["rating"] = "N/A"
    if data["release_date"] in (None, "", "None"):
        data["release_date"] = data["year"] or "N/A"

    trailer_query = quote_plus(
        f"{data['title']} {data['year']} official trailer"
    )
    data["trailer_url"] = (
        f"https://www.youtube.com/results?search_query={trailer_query}"
    )

    return data


# ============================================================
# /imdb command
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
                f"🥀 <b>No matching results found for :</b> "
                f"<code>{html.escape(query)}</code>"
            )

        buttons = []
        for item in results:
            btn_text = f"{item['title']} - {item['year']}"
            # IMPORTANT: only IMDb ID. No 64-byte truncation.
            buttons.append([
                InlineKeyboardButton(
                    text=btn_text[:64],
                    callback_data=f"imdb_view:{item['id']}",
                )
            ])

        buttons.append([
            InlineKeyboardButton("Close", callback_data="close")
        ])

        header_text = (
            f"🎯 <b>Matched Results For :</b> "
            f"<code>{html.escape(query.title())}</code>\n"
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
# IMDb detail callback
# ============================================================

@app.on_callback_query(filters.regex(r"^imdb_view:(tt\d+)$"))
async def imdb_view_callback(client, query: CallbackQuery):
    try:
        imdb_id = query.matches[0].group(1)
        await query.answer("Fetching exact IMDb details...")

        orig_message = query.message.reply_to_message or query.message

        try:
            await query.message.delete()
        except Exception:
            pass

        info = await fetch_full_movie_details(imdb_id)

        me = await client.get_me()
        bot_user = me.username or "CinemaVetaBot"
        bot_mention = (
            f'<a href="https://t.me/{html.escape(bot_user)}">'
            f'<b>@{html.escape(bot_user)}</b></a>'
        )

        genre_str = make_hashtags(info["genres"])
        lang_str = make_hashtags(info["languages"])
        country_str = make_hashtags(info["countries"])

        if info["rating"] != "N/A":
            rating_disp = f"{html.escape(str(info['rating']))} / 10"
        else:
            rating_disp = "N/A / 10"

        if isinstance(info.get("vote_count"), int):
            vote_disp = f" ({info['vote_count']:,} votes)"
        else:
            vote_disp = ""

        title_link = (
            f'<a href="{info["imdb_url"]}">'
            f'<b>{html.escape(info["title"])} '
            f'[{html.escape(str(info["year"]))}]</b></a>'
        )

        caption_lines = [f"🎬 {title_link}\n"]

        if info.get("original_title"):
            caption_lines.append(
                f"📝 <b>Original Title :</b> "
                f"{html.escape(str(info['original_title']))}"
            )

        if info.get("aka"):
            caption_lines.append(
                f"🏷 <b>Also Known As :</b> {html.escape(str(info['aka']))}"
            )

        caption_lines.extend([
            f"⭐ <b>IMDb Rating :</b> {rating_disp}{vote_disp}",
            f"🗓 <b>Release Info :</b> "
            f"{html.escape(str(info['release_date']))}",
            f"⏳ <b>Runtime :</b> {html.escape(str(info['runtime']))}",
            f"🎥 <b>Directed By :</b> {html.escape(str(info['director']))}",
            f"🎭 <b>Genre :</b> {genre_str}",
            f"🌐 <b>Language :</b> {lang_str}",
            f"🌍 <b>Country Of Origin :</b> {country_str}",
        ])

        if info.get("certificate"):
            caption_lines.append(
                f"🔞 <b>Certificate :</b> "
                f"{html.escape(str(info['certificate']))}"
            )

        caption_lines.extend([
            "",
            "📖 <b>Storyline :</b>",
            html.escape(str(info["storyline"])),
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
