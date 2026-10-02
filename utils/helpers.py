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
    Fetches IMDb Poster Image, Title, Year, Rating, Genres, and Runtime.
    """
    clean_q = normalize_text(query)
    if not clean_q:
        return None

    first_char = clean_q[0]
    sugg_url = f"https://v3.sg.media-imdb.com/suggestion/{first_char}/{clean_q}.json"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(sugg_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                
                movie_item = None
                for item in data.get("d", []):
                    item_id = str(item.get("id", ""))
                    if item_id.startswith("tt"):
                        movie_item = item
                        break
                
                if not movie_item:
                    return None

                movie_id = movie_item.get("id")
                title = movie_item.get("l")
                year = movie_item.get("y")
                image = movie_item.get("i", {}).get("imageUrl") if movie_item.get("i") else None

                details = {
                    "title": title,
                    "year": year,
                    "image": image,
                    "rating": "N/A",
                    "genres": "N/A",
                    "runtime": "N/A"
                }

                # OMDB API dwaara Rating, Genres, and Runtime thechukovadam
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

                return details

    except Exception as e:
        print(f"IMDb Details Error: {e}")
    return None
