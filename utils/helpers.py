import aiohttp
import re
from filters.fsub import is_subscribed


def format_size(size: int):
    if not size:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    size = float(size)

    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1

    return f"{size:.2f} {units[index]}"


def normalize_text(text: str):
    text = text.lower()
    text = re.sub(r"[_\-.]+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def get_pages(total: int, per_page: int = 7):
    if total == 0:
        return 1
    return (total + per_page - 1) // per_page


def paginate(items: list, page: int, limit: int = 7):
    start = (page - 1) * limit
    end = start + limit
    return items[start:end]


async def get_imdb_suggestions(query: str, limit: int = 10):
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
                    titles = []
                    
                    for item in data.get("d", []):
                        # 1. Cast/Celebrities filter:
                        # Titles id 'tt' tho start avthundi (actors/directors di 'nm' tho untundi)
                        item_id = str(item.get("id", ""))
                        if not item_id.startswith("tt"):
                            continue

                        # 2. Type filter: actor, actress, audio lantivi exclude cheyadam
                        entity_type = str(item.get("q", "")).lower()
                        if entity_type in ["actor", "actress", "soundtrack"]:
                            continue

                        title = item.get("l")
                        year = item.get("y")  # IMDb release year

                        if title:
                            # Year unte "Movie Name (2004)" format lo set avthundi
                            display_title = f"{title} ({year})" if year else title

                            if display_title not in titles:
                                titles.append(display_title)

                        if len(titles) >= limit:
                            break

                    return titles
    except Exception as e:
        print(f"IMDb Error: {e}")
    return []


async def get_imdb_movie_details(query: str):
    """
    Fetches ONLY landscape (horizontal 16:9 banner) images, Title, Year, Rating, Genres, and Runtime.
    Primary: TMDB API for high-resolution landscape backdrops.
    Fallback: OMDB / IMDb if TMDB data is unavailable.
    """
    clean_q = normalize_text(query)
    if not clean_q:
        return None

    # Public TMDB v3 API Key for backdrop banners
    tmdb_key = "1bfb10531a5d5187e42a4019210f63d2"
    tmdb_search_url = f"https://api.themoviedb.org/3/search/multi?api_key={tmdb_key}&query={clean_q}"

    details = {
        "title": query.title(),
        "year": None,
        "image": None,
        "rating": "N/A",
        "genres": "N/A",
        "runtime": "N/A"
    }

    try:
        async with aiohttp.ClientSession() as session:
            # 1. Fetch TMDB results to prioritize horizontal backdrops (Landscape)
            async with session.get(tmdb_search_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    t_data = await resp.json()
                    results = t_data.get("results", [])
                    if results:
                        # Backdrop_path unna movie ni landscape kosam filter chesthundhi
                        landscape_item = next((r for r in results if r.get("backdrop_path")), results[0])

                        title = landscape_item.get("title") or landscape_item.get("name") or query.title()
                        release_date = landscape_item.get("release_date") or landscape_item.get("first_air_date") or ""
                        year = release_date.split("-")[0] if release_date else None
                        
                        backdrop = landscape_item.get("backdrop_path")
                        if backdrop:
                            # 780px standard landscape banner
                            details["image"] = f"https://image.tmdb.org/t/p/w780{backdrop}"
                        
                        details["title"] = title
                        details["year"] = year
                        vote = landscape_item.get("vote_average")
                        if vote:
                            details["rating"] = f"{vote:.1f}"

                        # Fetch Runtime and Genres from TMDB item details
                        media_type = landscape_item.get("media_type", "movie")
                        item_id = landscape_item.get("id")
                        if item_id:
                            info_url = f"https://api.themoviedb.org/3/{media_type}/{item_id}?api_key={tmdb_key}"
                            try:
                                async with session.get(info_url, timeout=aiohttp.ClientTimeout(total=3)) as info_resp:
                                    if info_resp.status == 200:
                                        info_data = await info_resp.json()
                                        genres_list = [g.get("name") for g in info_data.get("genres", []) if g.get("name")]
                                        if genres_list:
                                            details["genres"] = ", ".join(genres_list[:3])
                                        
                                        runtime = info_data.get("runtime") or (info_data.get("episode_run_time") or [0])[0]
                                        if runtime:
                                            hours = runtime // 60
                                            mins = runtime % 60
                                            details["runtime"] = f"{hours}h {mins}m" if hours else f"{mins} min"
                            except Exception:
                                pass

                        return details
    except Exception as e:
        print(f"TMDB Landscape Fetch Warning: {e}")

    # 2. Fallback to IMDb/OMDB metadata if TMDB fails completely
    try:
        first_char = clean_q[0]
        sugg_url = f"https://v3.sg.media-imdb.com/suggestion/{first_char}/{clean_q}.json"

        async with aiohttp.ClientSession() as session:
            async with session.get(sugg_url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    movie_item = None
                    for item in data.get("d", []):
                        item_id = str(item.get("id", ""))
                        if item_id.startswith("tt"):
                            movie_item = item
                            break

                    if movie_item:
                        movie_id = movie_item.get("id")
                        details["title"] = movie_item.get("l") or details["title"]
                        details["year"] = movie_item.get("y") or details["year"]

                        api_url = f"https://www.omdbapi.com/?i={movie_id}&apikey=trilogy"
                        try:
                            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=3)) as o_resp:
                                if o_resp.status == 200:
                                    omdb_data = await o_resp.json()
                                    if omdb_data.get("Response") == "True":
                                        details["rating"] = omdb_data.get("imdbRating", "N/A")
                                        details["genres"] = omdb_data.get("Genre", "N/A")
                                        details["runtime"] = omdb_data.get("Runtime", "N/A")
                        except Exception:
                            pass
    except Exception as e:
        print(f"IMDb Details Fallback Warning: {e}")

    return details
