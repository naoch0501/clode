"""楽天競馬（keiba.rakuten.co.jp）の公開ページから出馬表・オッズを取得する。

RACEID は 18 桁: YYYYMMDD + 競馬場コード(2) + 開催情報(6) + レース番号(2)。
レース番号 00 は競馬場のレース一覧、競馬場コード以下が全て 0 は当日の開催一覧。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup, Tag

from ..models import JST, Entry, Race, RaceCard

log = logging.getLogger(__name__)

BASE = "https://keiba.rakuten.co.jp"
RACEID_RE = re.compile(r"RACEID/(\d{18})")


def _text(el: Tag | None) -> str:
    return el.get_text(" ", strip=True).replace("\u3000", " ") if el else ""


def _num(s: str) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", s.replace(",", ""))
    return float(m.group()) if m else None


def _int(s: str) -> int | None:
    v = _num(s)
    return int(v) if v is not None else None


def _track_from_title(soup: BeautifulSoup) -> str:
    title = soup.title.get_text() if soup.title else ""
    m = re.match(r"\s*(\S+?)競馬場", title)
    return m.group(1) if m else ""


def _post_time(day: date, hhmm: str) -> datetime | None:
    m = re.search(r"(\d{1,2}):(\d{2})", hhmm)
    if not m:
        return None
    return datetime(day.year, day.month, day.day, int(m.group(1)), int(m.group(2)), tzinfo=JST)


def date_from_race_id(race_id: str) -> date:
    return date(int(race_id[:4]), int(race_id[4:6]), int(race_id[6:8]))


# ---------------------------------------------------------------- パーサ（HTML → モデル）


def parse_day_index(html: str) -> list[str]:
    """当日開催一覧ページ → 各競馬場のレース一覧 RACEID（末尾 00）。"""
    soup = BeautifulSoup(html, "lxml")
    ids: list[str] = []
    for a in soup.select("a[href*='race_card/list/RACEID/']"):
        parent = a.parent
        if not parent or not any(c.startswith("track") for c in parent.get("class", [])):
            continue
        m = RACEID_RE.search(a["href"])
        if m and m.group(1).endswith("00") and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def parse_venue_races(html: str) -> list[Race]:
    """競馬場のレース一覧ページ → Race のリスト。"""
    soup = BeautifulSoup(html, "lxml")
    track = _track_from_title(soup)
    table = soup.find("table", class_="dataTable")
    if table is None or table.thead is None or table.tbody is None:
        return []
    cols = [(th.get("class") or [""])[0] for th in table.thead.find_all("th")]
    races: list[Race] = []
    for tr in table.tbody.find_all("tr", recursive=False):
        cells = tr.find_all(["th", "td"], recursive=False)
        row = dict(zip(cols, cells))
        link = row.get("name") and row["name"].find("a", href=RACEID_RE)
        if not link:
            continue
        race_id = RACEID_RE.search(link["href"]).group(1)
        day = date_from_race_id(race_id)
        dist_cell = row.get("distance")
        surface = _text(dist_cell.find("span")) if dist_cell else ""
        races.append(
            Race(
                race_id=race_id,
                date=day,
                track=track,
                race_no=int(race_id[-2:]),
                name=_text(link),
                post_time=_post_time(day, _text(row.get("time"))),
                surface=surface,
                distance=_int(_text(dist_cell).replace(surface, "", 1)) if dist_cell else None,
                runners=_int(_text(row.get("head"))),
                status=_text(row.get("betting")),
            )
        )
    return races


def _parse_race_note(soup: BeautifulSoup, race: Race) -> None:
    note = soup.find(class_="raceNote")
    if not note:
        return
    dist = note.find(class_="distance")
    if dist:
        t = _text(dist)
        race.surface = t[:1] if t[:1] in ("ダ", "芝") else race.surface
        race.distance = _int(t) or race.distance
    dl = note.find("dl")
    if dl:
        for dt in dl.find_all("dt"):
            key = _text(dt).rstrip("：:")
            val = _text(dt.find_next_sibling("dd"))
            if key == "天候":
                race.weather = val
            elif key in ("ダ", "芝"):
                race.going = val
            elif key == "発走時刻":
                race.post_time = _post_time(race.date, val) or race.post_time
    h2 = note.find("h2")
    if h2:
        race.name = _text(h2).replace("　", " ")


def _parse_entry(tr: Tag) -> Entry | None:
    num_cell = tr.find("td", class_="number")
    horse_cell = tr.find("td", class_="horse")
    if not num_cell or not horse_cell:
        return None
    horse_a = horse_cell.find("a")
    horse_id = ""
    if horse_a and (m := re.search(r"HORSEID/(\d+)", horse_a.get("href", ""))):
        horse_id = m.group(1)
    state = list(tr.find("td", class_="state").stripped_strings) if tr.find("td", class_="state") else []
    jockey_strings = list(tr.find("td", class_="jockey").stripped_strings) if tr.find("td", class_="jockey") else []
    jockey = jockey_strings[0] if jockey_strings else ""
    belong = next((s.strip("()（）").replace("\u3000", "") for s in jockey_strings[1:] if s.startswith(("(", "（"))), "")
    trainer = next((s for s in jockey_strings[1:] if not s.startswith(("(", "（"))), "")

    win_text = _text(tr.find("td", class_="oddsWin"))
    place_vals = [_num(s) for s in tr.find("td", class_="oddsPlace").stripped_strings] if tr.find(
        "td", class_="oddsPlace"
    ) else []
    place_vals = [v for v in place_vals if v is not None]
    win = _num(win_text)
    scratched = any(w in win_text for w in ("取消", "除外", "中止")) or (
        win is None and _text(tr.find("td", class_="rank")) == ""
    )
    sex_age = state[0].split("/")[0].replace("馬", "") if state else ""
    return Entry(
        frame=_int(_text(tr.find(class_="position"))) or 0,
        number=_int(_text(num_cell)) or 0,
        horse=_text(horse_a or horse_cell),
        horse_id=horse_id,
        sex_age=sex_age,
        burden=_num(state[1]) if len(state) > 1 else None,
        jockey=jockey.lstrip("☆▲△◇★"),
        jockey_belong=belong,
        trainer=trainer,
        win_odds=None if scratched else win,
        place_odds_min=place_vals[0] if place_vals and not scratched else None,
        place_odds_max=place_vals[-1] if place_vals and not scratched else None,
        popularity=None if scratched else _int(_text(tr.find("td", class_="rank"))),
        scratched=scratched,
    )


def parse_odds_tanfuku(html: str, race_id: str) -> RaceCard:
    """単勝・複勝オッズページ → RaceCard（レース情報＋出走馬＋オッズ）。"""
    soup = BeautifulSoup(html, "lxml")
    day = date_from_race_id(race_id)
    race = Race(race_id=race_id, date=day, track=_track_from_title(soup), race_no=int(race_id[-2:]))
    _parse_race_note(soup, race)
    table = soup.find("table", summary=re.compile("単勝")) or soup.find("table", class_="dataTable")
    entries: list[Entry] = []
    if table is not None:
        for tr in table.find_all("tr"):
            if any(c.startswith("box") for c in tr.get("class", [])):
                e = _parse_entry(tr)
                if e:
                    entries.append(e)
    race.runners = len([e for e in entries if not e.scratched]) or race.runners
    return RaceCard(race=race, entries=entries, fetched_at=datetime.now(JST))


# ---------------------------------------------------------------- 取得


class RakutenDataSource:
    def __init__(self, min_interval: float = 1.0, timeout: float = 15.0, user_agent: str | None = None):
        self.min_interval = min_interval
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) keiba-autobet/0.1"
        )
        self._last = 0.0
        self._cache: dict[str, tuple[float, str]] = {}

    def _get(self, path: str, cache_sec: float = 0) -> str:
        url = BASE + path
        now = time.monotonic()
        if cache_sec and url in self._cache and now - self._cache[url][0] < cache_sec:
            return self._cache[url][1]
        wait = self.min_interval - (now - self._last)
        if wait > 0:
            time.sleep(wait)
        log.debug("GET %s", url)
        res = self.session.get(url, timeout=self.timeout)
        self._last = time.monotonic()
        res.raise_for_status()
        res.encoding = res.apparent_encoding if not res.encoding else res.encoding
        self._cache[url] = (self._last, res.text)
        return res.text

    def list_races(self, day: date) -> list[Race]:
        index = self._get(f"/race_card/list/RACEID/{day:%Y%m%d}0000000000", cache_sec=600)
        races: list[Race] = []
        for venue_id in parse_day_index(index):
            races.extend(parse_venue_races(self._get(f"/race_card/list/RACEID/{venue_id}", cache_sec=300)))
        races.sort(key=lambda r: (r.post_time or datetime.max.replace(tzinfo=JST), r.track))
        return races

    def get_card(self, race_id: str) -> RaceCard:
        return parse_odds_tanfuku(self._get(f"/odds/tanfuku/RACEID/{race_id}"), race_id)
