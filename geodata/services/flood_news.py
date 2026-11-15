"""Floods in Kenya as the news reported them, from Google News search.

A past month's outlook is checked against what happened that month, and
for most counties the only public record of a flood is a news story: the
Daily Nation, The Star, Citizen Digital, Kenya News Agency, People Daily and
others write up every flood that displaces people. Google News search has
them by date, in an RSS feed that needs no key.

A headline counts when it reports damage that has happened (people
displaced, homes submerged, a road cut, someone swept away) and names a
county, or a town that is plainly in one. Stories about getting ready
("Nairobi braces for El Niño", "Met warns of flash floods") are left out.
That is still coarse: a flood the papers missed is not seen, and a headline
that names two counties counts both. The page says so wherever it shows
these.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from xml.etree import ElementTree

from geodata.services.reliefweb import counties_named

SEARCH_URL = "https://news.google.com/rss/search"
REQUEST_TIMEOUT_SECONDS = 30
# Each search returns at most about 100 stories, so a month is searched a
# few ways and the results merged.
QUERIES = (
    "floods kenya",
    "flooding kenya county",
    "floods displaced kenya",
    "heavy rains kenya swept away",
)
PAUSE_SECONDS = 1.0

# Damage that has happened. Any one of these is enough...
_STRONG = re.compile(
    r"\b(displac\w*|submerg\w*|marooned|swept|kill\w*|dead|deaths?|died|drown\w*|"
    r"destroy\w*|stranded|rescu\w*|evacuat\w*|inundat\w*|homeless|collaps\w*|"
    r"cut off|wash\w* away|claim\w* (?:\w+ )?lives?)\b",
    re.IGNORECASE,
)
# ...these only when the headline is not about getting ready.
_WEAK = re.compile(
    r"\b(hit|hits|flooded|affected|damage\w*|overflow\w*|burst\w*|havoc|ravag\w*|"
    r"wreak\w*|landslides?|mudslides?|floods? (?:in|across|at)|cause\w* floods?)\b",
    re.IGNORECASE,
)
_READINESS = re.compile(
    r"\b(brac\w*|ahead of|prepar\w*|warn\w*|expect\w*|fears?|risks?|forecast\w*|alert\w*|"
    r"could|may|likely|predict\w*|anticipat\w*|drills?|plans?|readiness|outlook|"
    r"to be affected|tips|how to)\b",
    re.IGNORECASE,
)
_FLOOD = re.compile(
    r"\b(flood\w*|rains?|rainfall|landslides?|mudslides?|overflow\w*|swept)\b", re.IGNORECASE
)

# Places a headline names instead of the county they are in.
TOWNS = {
    "Ahero": "Kisumu",
    "Nyando": "Kisumu",
    "Kano Plains": "Kisumu",
    "Budalangi": "Busia",
    "Eldoret": "Uasin Gishu",
    "Kitale": "Trans Nzoia",
    "Thika": "Kiambu",
    "Malindi": "Kilifi",
    "Garsen": "Tana River",
    "Hola": "Tana River",
    "Naivasha": "Nakuru",
    "Mai Mahiu": "Nakuru",
    "Kibera": "Nairobi",
    "Mathare": "Nairobi",
    "Mukuru": "Nairobi",
    "Eastleigh": "Nairobi",
    "Voi": "Taita-Taveta",
    "Mwea": "Kirinyaga",
    "Kapenguria": "West Pokot",
    "Lodwar": "Turkana",
    "Moyale": "Marsabit",
    "Mpeketoni": "Lamu",
    "Dadaab": "Garissa",
}


class FloodNewsError(Exception):
    """Google News could not be read."""


@dataclass(frozen=True)
class Story:
    title: str
    publisher: str
    url: str
    published: dt.date


def reports_damage(title: str) -> bool:
    """Whether a headline reports a flood that has happened, not one feared."""
    if not _FLOOD.search(title):
        return False
    if _STRONG.search(title):
        return True
    return bool(_WEAK.search(title)) and not _READINESS.search(title)


def counties_in(title: str, counties: list[str]) -> list[str]:
    """The counties a headline names, directly or through a town in one."""
    named = set(counties_named(title, counties))
    named |= {
        county
        for town, county in TOWNS.items()
        if county in counties and counties_named(title, [town])
    }
    return sorted(named)


def _search_url(query: str, first: dt.date, last: dt.date) -> str:
    # `after:` and `before:` are exclusive at Google's end, so widen by a day
    # each side; stories outside the month are dropped after parsing.
    window = f"{query} after:{first - dt.timedelta(days=1)} before:{last + dt.timedelta(days=1)}"
    params = {"q": window, "hl": "en-KE", "gl": "KE", "ceid": "KE:en"}
    return f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"


def _get(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (FloodScout flood record)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise FloodNewsError(f"Google News answered {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise FloodNewsError(f"Google News could not be read: {exc}") from exc


def parse(feed: bytes) -> list[Story]:
    """The stories in one Google News RSS feed."""
    try:
        root = ElementTree.fromstring(feed)
    except ElementTree.ParseError as exc:
        raise FloodNewsError(f"Google News sent something that is not RSS: {exc}") from exc
    stories = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        publisher = (item.findtext("source") or "").strip()
        # Google appends " - Publisher" to every title.
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3].strip()
        try:
            published = email.utils.parsedate_to_datetime(item.findtext("pubDate") or "").date()
        except (TypeError, ValueError):
            continue
        url = (item.findtext("link") or "").strip()
        if title and url:
            stories.append(Story(title=title, publisher=publisher, url=url, published=published))
    return stories


def fetch_month(first: dt.date, last: dt.date) -> list[Story]:
    """Every story found for floods in Kenya published from `first` to `last`, once each."""
    seen: dict[str, Story] = {}
    for n, query in enumerate(QUERIES):
        if n:
            time.sleep(PAUSE_SECONDS)
        for story in parse(_get(_search_url(query, first, last))):
            if first <= story.published <= last:
                seen.setdefault(story.title.casefold(), story)
    return sorted(seen.values(), key=lambda story: story.published)
