"""Private memory per user, kept across chats.

Every answered turn is saved with the wells it was about. Later, in a new chat, a phrase like
"the three wells we discussed yesterday" is turned back into well ids from that user's own earlier turns.

Nothing here ever reads another user's turns: every query is filtered by user_id.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_COUNT = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r"|\d+)\s+(?:of\s+the\s+|of\s+those\s+)?(?:\w+\s+)?wells?\b", re.I)
_WHEN = re.compile(r"\b(yesterday|today|last week|last time|earlier|before|previously|previous (?:chat|conversation|session)|last (?:chat|conversation|session))\b", re.I)
_VERB = r"(?:discussed|talked about|spoke about|looked at|covered|reviewed|checked|went over|worked on|asked about|mentioned)"
_REFERENCE = re.compile(
    r"\b(?:wells?|ones|those|them)\b[^?.]{0,60}\b(?:we|i|you)\s+" + _VERB + r"\b"
    r"|\b" + _VERB + r"\b[^?.]{0,40}\b(?:yesterday|earlier|before|previously|last time|last week)\b[^?.]{0,40}\bwells?\b"
    r"|\b(?:same|those|these|previous|earlier)\s+(?:\w+\s+)?wells\b",
    re.I,
)


def ensure_tables(store: Any) -> None:
    store.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_turns (
            turn_id VARCHAR PRIMARY KEY,
            user_id VARCHAR,
            thread_id VARCHAR,
            question VARCHAR,
            tool VARCHAR,
            asset_ids VARCHAR,
            discussed_on DATE,
            created_at TIMESTAMP
        )
        """
    )


def wells_in(payload: dict) -> list[str]:
    """The wells an answer was about, in the order it showed them."""
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    found: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and re.fullmatch(r"WELL-\d+", value, re.I) and value.upper() not in found:
            found.append(value.upper())

    add(result.get("asset_id"))
    for key in ("wells", "pins", "suggestions", "rows"):
        if isinstance(result.get(key), list):
            for item in result[key]:
                if isinstance(item, dict):
                    add(item.get("asset_id"))
    return found


def remember(store: Any, user_id: str, thread_id: str, question: str, answer: str, payload: dict, today: date) -> None:
    """Save the turn. A failure here must never break the chat."""
    try:
        now = datetime.now()
        store.execute("INSERT INTO threads VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING", [thread_id, user_id, question[:60], now])
        for role, text in (("user", question), ("assistant", answer)):
            store.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)", [uuid.uuid4().hex, thread_id, user_id, role, text, now])
        assets = wells_in(payload)
        if assets:
            store.execute(
                "INSERT INTO memory_turns VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [uuid.uuid4().hex, user_id, thread_id, question[:300], payload.get("tool"), json.dumps(assets), today, now],
            )
            for asset in assets:
                store.execute("INSERT INTO discussed_entities VALUES (?, ?, ?, ?, ?)", [uuid.uuid4().hex, user_id, thread_id, asset, today])
    except Exception:
        pass


def refers_to_earlier_wells(question: str) -> bool:
    return bool(_REFERENCE.search(question))


def resolve(store: Any, user_id: str, thread_id: str, question: str, today: date) -> dict | None:
    """Which wells does 'the three wells we discussed yesterday' mean for THIS user? None if it is not that kind of question."""
    if not refers_to_earlier_wells(question):
        return None
    count_match = _COUNT.search(question)
    count = None
    if count_match:
        word = count_match.group(1).lower()
        count = _NUMBER_WORDS.get(word) or (int(word) if word.isdigit() else None)
    when_match = _WHEN.search(question)
    when = when_match.group(1).lower() if when_match else None

    rows = store.query(
        "SELECT thread_id, asset_ids, discussed_on, created_at FROM memory_turns WHERE user_id = ? ORDER BY created_at DESC",
        [user_id],
    )
    turns = [{"thread": r["thread_id"], "assets": json.loads(r["asset_ids"]), "on": r["discussed_on"], "made": r["created_at"]} for r in rows]
    if not turns:
        return {"asset_ids": [], "when": when}

    other = [t for t in turns if t["thread"] != thread_id]
    wall_yesterday = date.today() - timedelta(days=1)
    chosen: list[dict] = []
    fallback = False
    if when == "today":
        chosen = [t for t in turns if t["on"] == today]
    elif when == "yesterday":
        chosen = [t for t in other if t["on"] == today - timedelta(days=1)] or [t for t in other if t["made"].date() == wall_yesterday]
    elif when == "last week":
        chosen = [t for t in other if (today - t["on"]).days >= 2]
    if not chosen:
        # no turn on the exact day (or no day was named): the user's most recent other chat, then this chat
        chosen = other or turns
        fallback = when in ("yesterday", "today", "last week")
    if not chosen:
        return {"asset_ids": [], "when": when}

    lists = [t["assets"] for t in chosen]
    picked: list[str] = []
    if count:
        exact = next((l for l in lists if len(l) == count), None)
        if exact:
            picked = list(exact)
    if not picked:
        for l in lists:
            for asset in l:
                if asset not in picked:
                    picked.append(asset)
            if count and len(picked) >= count:
                break
        picked = picked[: count or 8]
    return {
        "asset_ids": picked,
        "count_asked": count,
        "when": when,
        "discussed_on": str(chosen[0]["on"]),
        "fallback": fallback,
    }
