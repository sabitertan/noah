import json
import re
import urllib.parse
import urllib.request


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=8) as r:
        return json.load(r)


def extract_rule(message: str) -> dict:
    m = re.search(r"\b(?:in|for|at)\s+([A-Za-z][A-Za-z .'-]+?)(?:\s+(?:today|tomorrow|now|right now))?\s*[?.!]*$", message.strip())
    return {"city": m.group(1).strip()} if m else {}


def run(args: dict) -> str:
    geo = _get("https://geocoding-api.open-meteo.com/v1/search?count=1&name=" + urllib.parse.quote(args["city"]))
    if not geo.get("results"):
        return f"Could not find a place called {args['city']}."
    place = geo["results"][0]
    w = _get(
        "https://api.open-meteo.com/v1/forecast?current=temperature_2m,precipitation,wind_speed_10m"
        f"&latitude={place['latitude']}&longitude={place['longitude']}"
    )["current"]
    return (f"{place['name']}, {place.get('country', '')}: {w['temperature_2m']}°C, "
            f"wind {w['wind_speed_10m']} km/h, precipitation {w['precipitation']} mm")
