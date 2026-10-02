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
                        title = item.get("l")
                        if title and title not in titles:
                            titles.append(title)
                        if len(titles) >= limit:
                            break
                    return titles
    except Exception as e:
        print(f"IMDb Error: {e}")
    return []
