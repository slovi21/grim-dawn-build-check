#!/usr/bin/env python3
"""Patch guard: find the live Grim Dawn version in two public sources (no key needed): the Steam news feed and the
patch-notes list of the Crate forum (Steam does not announce every patch: 1.3.1.2 was only in the forum).

    python web/live_version.py --out _site/data/live.json [--previous <live.json published so far>]
                               [--feed-file <saved Steam answer>] [--forum-file <saved forum answer>]   (the files: tests)
    python web/live_version.py --compare site/data/current.json _site/data/live.json
    python web/live_version.py --silent _site/data/live.json

The first form writes {"format": 1, "live_version": "1.3.1.2" or null, "checked": "YYYY-MM-DD" (UTC) or null,
"source": "steam-news+crate-forum", "seen": {"steam-news": {"version": "1.3.1.1" or null, "answered": "YYYY-MM-DD" or null},
"crate-forum": {...}}}. "source" names the sources that were asked; "seen" holds, for each of them, the last version it
named and the day (UTC) of its last answer (Entscheidung 66 a: a source that stays silent for good must not stay unseen).
It is fail-soft on purpose: an unreachable source, a time limit (10 s per read, 20 s for the body, 30 s on the wall clock
for the whole fetch), broken JSON or an answer of the wrong shape is not an error, and the exit code is 0, so a failure
here can never stop a deployment.
Each source either names a version, answers without naming one, or does not answer (no answer, or one of the wrong shape).
The rule of Entscheidung 53, per source, when at least one source names a version today:
- every source counts with its answer of today (one that answers without a version forgets its last one); the file
  published before has no say, so a wrong hit corrects itself as soon as the source changes;
- a source that does not answer counts with its last answer from --previous ("seen": its version and its day stay as
  they were), so an outage of one source cannot remove a warning that only this source gave;
- the highest believable version of these wins, with today's date.
No source names a version: the content of --previous again, with its old date of the last good check; a source that
answered today (without a version) gets today as the day of its last answer, one that did not keeps its old day.
A source counts as silent when it did not answer for more than MAX_SILENT_DAYS days, or never did (no day at all): the page
then says it has not checked against that source, and the third form lists these sources, one per line, "<source> <day>"
or "<source> never", for the workflow, which opens one issue for each (exit code 0 always).
Without a valid --previous a run writes what is known (live_version null and checked null when nothing was named), unless
--require-previous is given and a source did not answer: then nothing is written and the exit code is 3 (the workflow
uses it when the earlier file could not be read, so that an outage of a source AND of the earlier file cannot remove a
visible warning; the old publication stays).
--previous comes from the network (the live.json published so far), so it is untrusted: it must be a small JSON object
with a plausible version and a date YYYY-MM-DD, and "seen", if present, may only hold known sources, each exactly
{"version": plausible version or null, "answered": date or null}, whose highest believable version is live_version (or
none at all): exactly what this script writes; else it counts as not present. A day of "answered" after today is not
believed (the source then has no day at all).
A version counts only from a title that starts with the game (maybe one dash or colon) and the version: "Grim Dawn Version 1.3.1.2",
"Grim Dawn v1.1.2.5", "Grim Dawn Version v1.3.0.4 + v1.3.0.5" (a list right after the game: its highest, 1.3.0.5). A text
never counts, a later number of a title never counts ("Grim Dawn Version 1.3.1.2 - next up v1.3.2.0" is 1.3.1.2), and no
title counts with "preview", "beta", "test", "PTR", "experimental", "upcoming", "coming", "April" or "fool(s)" (word forms
included): both sources announce versions before they are live, the forum every 1 April a joke version such as 4.1.2.2
(Entscheidung 64; a dev update on Steam named the coming V1.3.2.0 in its text).
Steam: only announcements of the game itself (feedname "steam_community_announcements"), and only a title that also says
the version is out (Entscheidung 66 e: a list of phrases that count, not only a list of words that do not: "is now
available", "is now live", "hotfix", "has been released", "and MOAR"); a Steam title with a version but without such a
phrase names no version, and the block list above still applies on top. Forum: only topics of category 28 ("Patch
Notes"), whose titles name the version always, so the block list alone is enough there. Every version must be plausible
(the first number 1 to 9, the other three at most 99) and believable: the highest of a list counts only when its first
number equals that of the next lower version of the list and its second number is at most one higher (a joke "4.1.2.3" or
a typo "1.31.1.1" next to 1.3.1.2 is not believed, a real 1.4.0.0 is; a list of one version is believed). The same holds
when the answers of both sources are put together.
Both sources are asked with a generic client name (the forum refuses Python's default one) and follow a redirect only to
https. With --feed-file or --forum-file nothing is fetched: a source without a saved answer is not asked at all. The run
prints one line for the log: what each source named and the version written (checked versions only, never a title).

The second form prints "<live> <data>" when the live version is newer than the version in the data pointer, and nothing
otherwise (exit code 0 either way). Both values are checked to be four numbers, so only digits and dots reach a shell.
The third form prints one line "<source> <YYYY-MM-DD>" or "<source> never" for each silent source of the file (known
sources and real dates only, so nothing else reaches a shell) and nothing when no source is silent (exit code 0 either way).

Only the standard library is used."""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

FEED_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v0002/?appid=219990&count=20&maxlength=300&format=json"
FORUM_URL = "https://forums.crateentertainment.com/c/grimdawn/patch-notes/28.json"
FORUM_CATEGORY = 28                 # "Patch Notes" of Grim Dawn; topics of other categories do not count
STEAM, FORUM = "steam-news", "crate-forum"    # the names of the sources in live.json ("source", "seen")
USER_AGENT = "live-version-check/1"  # a generic client name: the forum refuses Python's default one (HTTP 403)
TIMEOUT_SECONDS = 10                # per read of the network
TOTAL_SECONDS = 20                  # for the body of one answer (a source that trickles is given up)
HARD_LIMIT_SECONDS = TOTAL_SECONDS + TIMEOUT_SECONDS    # wall clock for one source as a whole, headers and redirects included
CHUNK_BYTES = 65536
ANNOUNCEMENTS = "steam_community_announcements"
MAX_FEED_BYTES = 1_000_000          # an answer is read up to this size; a longer one counts as "no answer"
MAX_SILENT_DAYS = 3                 # a source whose last answer is older than this many days counts as silent (the page: 3 too)

# four numbers, not part of a longer dotted number: "1.3.1.1." (end of a sentence) is a version, "1.3.1.1.1" is not
# (ASCII digits only: \d would also match digits of other scripts)
_FOUR = r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?![0-9]|\.[0-9])"
_VERSION = re.compile(r"(?<![0-9.])(" + _FOUR + ")")
_STRICT = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")
# the title of a release starts with the game (maybe followed by one dash or colon) and the version, or a list of versions
# joined by "+": "Grim Dawn Version 1.3.1.2", "Grim Dawn v1.1.2.5", "Grim Dawn - Version 1.3.2.0", "Grim Dawn Version v1.3.0.4
# + v1.3.0.5"
_RELEASE_TITLE = re.compile(r"grim dawn(?:\s*[-:–—])?\s+(?:version\s+)?v?(" + _FOUR + r"(?:\s*\+\s*v?" + _FOUR + ")*)", re.I)
# a title with one of these words (any word form) names no live version: the preview, teaser or announcement of a coming
# version, a beta, test, playtest, PTR/PTS, dev or opt-in branch, early access, a release candidate or a draft, a time to
# come ("arrives December 4th", "releasing on", "launches", "goes live", "soon", "tomorrow", "next week", "on its way"),
# a patch that is only planned ("will be", "incoming", "planned", "pending", "delayed", "postponed": Entscheidung 66 e, a
# "Hotfix 1 incoming" carries the word that counts for Steam), or the yearly joke ("(April Fools!) Grim Dawn Version
# 4.1.2.2"). A block list fails safe: a live release with such a word is missed by one source (the other names it), a
# coming one never warns too early (M14 security review and its check). The past tense counts ("has arrived", "has
# launched"); "latest" and "contest" are not "test".
_NOT_RELEASED = re.compile(r"\b(?:preview\w*|beta\w*|(?:play|re)?test\w*|ptr|pts|experimental\w*|upcoming|coming|soon|"
                           r"tomorrow|next\s+week|pre[-\s]?release\w*|release[-\s]+candidate|rc[0-9]*|"
                           r"arriv(?:es|ing)|launch(?:es|ing)|releas(?:es?|ing)\s+(?:on|in)|releasing|release\s+date|"
                           r"goes\s+live|on\s+its\s+way|sneak\s+peek\w*|teaser\w*|announc\w*|branch\w*|"
                           r"early\s+access|draft\w*|will\s+be|incoming|planned|pending|delayed|postponed|"
                           r"april|fools?)\b", re.I)
# Steam only (Entscheidung 66 e): its announcements are free text (dev updates, podcasts, pins), so a title counts only when
# it also SAYS the version is out. The phrases are those of the real titles of released versions: "is now available"
# (1.3.1.1, 1.3.0.5), "is now live" and "Hotfix" (1.3.0.1), "and MOAR!" (1.3.0.8); plus their plain variants ("now
# available", "is live", "is out", "has been released"). A title with such a phrase and a word of _NOT_RELEASED ("Beta is
# now available") does not count. A live release without any of these phrases is missed on Steam (the forum names it).
_RELEASE_PHRASE = re.compile(r"\b(?:(?:(?:is\s+)?now\s+|is\s+)(?:available|live|out)|"
                             r"has\s+(?:been\s+)?(?:released|launched|arrived)|hotfix(?:es)?|and\s+moar)\b", re.I)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
MAX_PREVIOUS_BYTES = 65536


def plausible(key) -> bool:
    """True for the four numbers of a Grim Dawn version: the first 1 to 9, the other three at most 99."""
    return key is not None and 1 <= key[0] <= 9 and all(0 <= n <= 99 for n in key[1:])


def parse_release_title(title) -> str | None:
    """The version a release title names: the highest believable one of the list right after "Grim Dawn [Version] [v]"
    ("Grim Dawn Version v1.3.0.4 + v1.3.0.5": 1.3.0.5), or None when the title does not start so or names a preview, a
    beta, a test branch or a joke."""
    if not isinstance(title, str) or _NOT_RELEASED.search(title):
        return None
    found = _RELEASE_TITLE.match(title)
    return _highest(v.group(1) for v in _VERSION.finditer(found.group(1))) if found else None


def parse_steam_title(title) -> str | None:
    """The version a Steam announcement title names, or None: what parse_release_title finds, but only when the title also
    says that the version is out (a phrase of _RELEASE_PHRASE, Entscheidung 66 e). Steam's titles are free text; a bare
    "Grim Dawn v1.3.2.0" names no live version there."""
    if not isinstance(title, str) or not _RELEASE_PHRASE.search(title):
        return None
    return parse_release_title(title)


def _key(version) -> tuple[int, int, int, int] | None:
    """The four numbers of a version for comparing, or None when `version` is not exactly four numbers."""
    if not isinstance(version, str) or not _STRICT.fullmatch(version):
        return None
    a, b, c, d = (int(part) for part in version.split("."))
    return (a, b, c, d)


def _version_of(item) -> str | None:
    """The version the title of an announcement names (never its text, never a preview, a beta or a joke; and only a title
    that says the version is out), or None."""
    if not isinstance(item, dict) or item.get("feedname") != ANNOUNCEMENTS:
        return None
    return parse_steam_title(item.get("title"))


def _highest(versions) -> str | None:
    """The highest believable version among `versions` (None and anything not plausible are skipped), or None. Going down
    the sorted list, a version is believed when it is the last one, or when its first number equals that of the next lower
    version and its second number is at most one higher: a joke "4.1.2.3" or a typo "1.31.1.1" above 1.3.1.2 is skipped,
    a real 1.4.0.0 is not, and a real jump counts as soon as a second version of the new line is there. The version is
    given in its plain form ("01.3.2.0" and "1.03.2.0" are "1.3.2.0"), so live.json, the page and the issue title name one
    patch the same way."""
    found = {}
    for version in versions:
        key = _key(version)
        if plausible(key):
            found[key] = ".".join(str(n) for n in key)
    ordered = sorted(found, reverse=True)
    for top, lower in zip(ordered, ordered[1:] + [None]):
        if lower is None or (top[0] == lower[0] and top[1] <= lower[1] + 1):
            return found[top]
    return None


def pick_latest(newsitems) -> str | None:
    """The highest believable version named by the announcements in `newsitems`, or None."""
    return _highest(_version_of(item) for item in newsitems) if isinstance(newsitems, list) else None


def _forum_version_of(topic) -> str | None:
    """The version a topic of the patch-notes category names, or None (another category, or no category at all)."""
    if not isinstance(topic, dict) or type(topic.get("category_id")) is not int or topic["category_id"] != FORUM_CATEGORY:
        return None
    return parse_release_title(topic.get("title"))


def pick_latest_topic(topics) -> str | None:
    """The highest believable version named by the patch-notes topics in `topics`, or None."""
    return _highest(_forum_version_of(topic) for topic in topics) if isinstance(topics, list) else None


def topics_of(answer) -> list | None:
    """The list of topics in a forum answer {"topic_list": {"topics": [...]}}, or None when the shape is wrong."""
    topic_list = answer.get("topic_list") if isinstance(answer, dict) else None
    topics = topic_list.get("topics") if isinstance(topic_list, dict) else None
    return topics if isinstance(topics, list) else None


def is_newer(live, data) -> bool:
    """True when `live` is a higher version than `data` (numbers, not text); anything invalid gives False."""
    a, b = _key(live), _key(data)
    return a is not None and b is not None and a > b


def newsitems_of(feed) -> list | None:
    """The list of news items in a feed answer {"appnews": {"newsitems": [...]}}, or None when the shape is wrong."""
    appnews = feed.get("appnews") if isinstance(feed, dict) else None
    items = appnews.get("newsitems") if isinstance(appnews, dict) else None
    return items if isinstance(items, list) else None


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to another https address; any other one ends the request (it counts as no answer)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not (isinstance(newurl, str) and newurl.lower().startswith("https://")):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _request(url: str) -> urllib.request.Request:
    """The request for `url`: a generic client name (the forum answers Python's default one with HTTP 403), JSON wanted."""
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})


def _open(url: str, timeout: int):
    """The real opener: https only, also after a redirect."""
    return urllib.request.build_opener(_HttpsOnlyRedirects).open(_request(url), timeout=timeout)


def load_feed(url: str = FEED_URL, timeout: int = TIMEOUT_SECONDS, opener=_open, clock=time.monotonic):
    """The decoded answer of `url` (the Steam feed or the forum list), or None on any failure (network, time-out, status,
    redirect away from https, more than MAX_FEED_BYTES, slower than TOTAL_SECONDS in all, text, JSON). Never raises. The
    answer is read piece by piece (one network read each), so that the time limit holds for the whole answer."""
    try:
        deadline = clock() + TOTAL_SECONDS
        body = bytearray()
        with opener(url, timeout=timeout) as reply:
            while chunk := reply.read1(CHUNK_BYTES):
                body += chunk
                if len(body) > MAX_FEED_BYTES or clock() > deadline:
                    return None
        return json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001 - fail-soft by design: whatever goes wrong here means "not checked"
        return None


def today_utc() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


def _entry(version: str | None = None, answered: str | None = None) -> dict:
    """What live.json knows of one source: the last version it named and the day (YYYY-MM-DD, UTC) of its last answer."""
    return {"version": version, "answered": answered}


def build_result(live_version: str | None, day: datetime.date | None, seen: dict) -> dict:
    """The content of live.json. `seen`: the sources that were asked, each as _entry(): the last version it named (or None)
    and the day of its last answer (or None)."""
    return {"format": 1, "live_version": live_version, "checked": day.isoformat() if day else None,
            "source": "+".join(seen), "seen": {name: _entry(e["version"], e["answered"]) for name, e in seen.items()}}


def valid_date(text) -> bool:
    """True for a real calendar date written YYYY-MM-DD (ASCII digits)."""
    if not isinstance(text, str) or not _DATE.fullmatch(text):
        return False
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _valid_entry(entry) -> bool:
    """True for exactly {"version": null or a plausible version, "answered": null or a real day YYYY-MM-DD}."""
    return (isinstance(entry, dict) and set(entry) == {"version", "answered"}
            and (entry["version"] is None or plausible(_key(entry["version"])))
            and (entry["answered"] is None or valid_date(entry["answered"])))


def _valid_seen(seen, live_version) -> bool:
    """True for the "seen" of a live.json: an object of known sources, each a valid entry, whose highest believable
    version is `live_version` (or which names none: a file without any version, or "nothing known"). That is exactly what
    this script writes; a file that contradicts itself is not used."""
    return (isinstance(seen, dict) and set(seen) <= set(SOURCES) and all(_valid_entry(e) for e in seen.values())
            and _highest(e["version"] for e in seen.values()) in (None, live_version))


def read_previous(path) -> dict | None:
    """{"live_version", "checked", "seen"} from the live.json published so far, or None. The file is untrusted (the
    network delivered it): at most 64 KB, a JSON object, a version of four numbers and a real date (or both null: "nothing known
    yet, kept as it is", so that an outage does not stop deployments of a site that never had a version), and "seen", if
    present, of known sources, each with a plausible version (or null) not above the version and a real day (or null) of
    its last answer (a file without "seen" knows nothing per source); anything else is "not present"."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_PREVIOUS_BYTES + 1)
        if len(raw) > MAX_PREVIOUS_BYTES:
            return None
        data = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001 - no file, not text, not JSON, too deep: all mean "not present"
        return None
    if not isinstance(data, dict) or not _valid_seen(data.get("seen", {}), data.get("live_version")):
        return None
    version, checked, seen = data.get("live_version"), data.get("checked"), data.get("seen", {})
    if "live_version" in data and "checked" in data and version is None and checked is None:
        return {"live_version": None, "checked": None, "seen": seen}   # "nothing known yet" is valid: kept as it is
    if not plausible(_key(version)) or not valid_date(checked):
        return None
    plain = ".".join(str(n) for n in _key(version))      # "01.3.2.0" is 1.3.2.0, as everything the script writes
    return {"live_version": plain, "checked": checked, "seen": seen}


def _remembered(previous: dict | None, name: str, day: datetime.date) -> dict:
    """What the earlier file knows of source `name` (a copy; nothing known: no version, no day). A day after today is not
    believed: it would hide the silence of the source for good."""
    entry = previous["seen"].get(name) if previous is not None else None
    if entry is None:
        return _entry()
    answered = entry["answered"]
    if answered is not None and datetime.date.fromisoformat(answered) > day:
        answered = None
    return _entry(entry["version"], answered)


def resolve(today: dict, previous: dict | None, day: datetime.date, require_previous: bool = False) -> dict | None:
    """The result to publish from what each asked source said today (`today`: source -> its version; "" when it answered
    without naming one; None when it did not answer).
    Some source named a version: every source counts with its answer of today ("" forgets the last one; its day of the last
    answer is today), a source that did not answer with its last answer from the earlier file ("seen": version and day as
    they were); the highest believable of these, checked today. A source that did not answer, without a usable earlier
    file, gives None (write nothing) when `require_previous` is set, else it counts as unknown (no version, no day).
    No source named a version: the earlier content with its old date, except that a source that answered today (naming
    nothing) gets today as the day of its last answer; without an earlier file: null and null (the same days), or None
    with `require_previous`."""
    today_text = day.isoformat()
    if any(today.values()):
        if previous is None and require_previous and None in today.values():
            return None
        seen = {name: _entry(version or None, today_text) if version is not None else _remembered(previous, name, day)
                for name, version in today.items()}
        return build_result(_highest(e["version"] for e in seen.values()), day, seen)
    if previous is not None:
        checked = previous["checked"]
        seen = {name: _entry(_remembered(previous, name, day)["version"], today_text) if version is not None
                else _remembered(previous, name, day) for name, version in today.items()}
        return build_result(previous["live_version"], datetime.date.fromisoformat(checked) if checked else None, seen)
    return None if require_previous else build_result(
        None, None, {name: _entry(None, today_text if version is not None else None) for name, version in today.items()})


def silent_sources(live, today: datetime.date) -> list[tuple[str, str | None]]:
    """The sources of a live.json (parsed JSON, anything) that count as silent on day `today`: (name, day of the last answer
    or None). Silent is: the last answer is more than MAX_SILENT_DAYS days old, lies after today, is missing or is not a real
    day (Entscheidung 66 a). Only known sources are named, in the order of SOURCES; a file without "seen" says nothing."""
    seen = live.get("seen") if isinstance(live, dict) else None
    if not isinstance(seen, dict):
        return []
    out = []
    for name in SOURCES:
        if name not in seen:
            continue
        entry = seen[name]
        answered = entry.get("answered") if isinstance(entry, dict) else None
        age = (today - datetime.date.fromisoformat(answered)).days if valid_date(answered) else None
        if age is None or age < 0 or age > MAX_SILENT_DAYS:
            out.append((name, answered if age is not None and age >= 0 else None))
    return out


def _feed_from_file(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _fetch(url: str):
    """load_feed(url) with a hard limit on the wall clock (HARD_LIMIT_SECONDS): the time limits of load_feed do not cover a
    server that trickles its status line, headers or chunk sizes. The fetch runs in a daemon thread; when it is not done
    in time, the source counts as not answering and the thread is left to end with the process."""
    box = []

    def run():
        try:
            box.append(load_feed(url))
        except Exception:  # noqa: BLE001 - load_feed never raises; this only keeps a stray error out of the log
            pass

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(HARD_LIMIT_SECONDS)
    return box[0] if box else None


# each source: its address, the list in its answer (None: wrong shape) and the version named by that list
SOURCES = {
    STEAM: (FEED_URL, newsitems_of, pick_latest),
    FORUM: (FORUM_URL, topics_of, pick_latest_topic),
}


def _ask(name: str, saved: str | None) -> str | None:
    """What source `name` says today (from the saved answer `saved`, else from the network): its version, "" when it
    answered without naming one, None when it did not answer (no answer, or one of the wrong shape). Never raises: whatever
    goes wrong with one source costs nothing of the other."""
    url, entries_of, pick = SOURCES[name]
    try:
        entries = entries_of(_feed_from_file(saved) if saved is not None else _fetch(url))
        return None if entries is None else (pick(entries) or "")
    except Exception:  # noqa: BLE001 - fail-soft by design: an error here means "this source did not answer"
        return None


def _write(out: str, result: dict) -> None:
    try:
        target = Path(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    except OSError as ex:                      # fail-soft to the end: no file means no notice, never a stopped deployment
        print(f"live_version: could not write {out}: {ex}", file=sys.stderr)


def _json_of(path: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - no file, not text, not JSON, too deep: all mean "no version"
        return None


def compare(current_path: str, live_path: str) -> str:
    """"<live> <data>" when the live version is newer than the one in the data pointer, else ""."""
    current, live = _json_of(current_path), _json_of(live_path)
    data_v = current.get("game_version") if isinstance(current, dict) else None
    live_v = live.get("live_version") if isinstance(live, dict) else None
    return f"{live_v} {data_v}" if is_newer(live_v, data_v) else ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python web/live_version.py", description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="write the result (JSON) here")
    ap.add_argument("--previous", help="the live.json published so far (untrusted); it stands in for a source that gives no version")
    ap.add_argument("--require-previous", action="store_true",
                    help="when a source gives no version and --previous is not usable: write nothing, exit 3")
    ap.add_argument("--feed-file", help="read this saved Steam answer instead of asking (tests; nothing is fetched then)")
    ap.add_argument("--forum-file", help="read this saved forum answer instead of asking (tests; nothing is fetched then)")
    ap.add_argument("--compare", nargs=2, metavar=("CURRENT_JSON", "LIVE_JSON"),
                    help="print '<live> <data>' when the live version is newer than the game data, else nothing")
    ap.add_argument("--silent", metavar="LIVE_JSON",
                    help="print '<source> <day of its last answer>' or '<source> never' per silent source, else nothing")
    a = ap.parse_args(argv)
    if a.compare:
        line = compare(*a.compare)
        if line:
            print(line)
        return 0
    if a.silent:
        for name, answered in silent_sources(_json_of(a.silent), today_utc()):
            print(f"{name} {answered or 'never'}")
        return 0
    if not a.out:
        ap.error("--out, --compare or --silent is required")
    saved = {STEAM: a.feed_file, FORUM: a.forum_file}
    offline = any(path is not None for path in saved.values())    # a saved answer means a test: nothing is fetched
    today = {name: _ask(name, saved[name]) for name in SOURCES if saved[name] is not None or not offline}
    try:
        previous = read_previous(a.previous) if a.previous else None
        result = resolve(today, previous, today_utc(), a.require_previous)
    except Exception:  # noqa: BLE001 - never an exception out of this script
        result = None if a.require_previous else build_result(None, None, dict.fromkeys(today))
    # checked versions only (four numbers): no text of a source reaches the log of the workflow
    said = " ".join(f"{name}={version or ('no-version' if version == '' else 'no-answer')}" for name, version in today.items())
    print(f"live_version: {said} -> {(result['live_version'] or 'none') if result else 'not written'}")
    if result is None:
        print("live_version: a source gave no version and the earlier live.json is not usable: nothing written", file=sys.stderr)
        return 3
    _write(a.out, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
