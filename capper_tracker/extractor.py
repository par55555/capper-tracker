"""Turn a capper's post (text + bet-slip images) into structured picks with Claude."""
from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass

import anthropic
import requests

from .config import EXTRACT_MODEL
from .normalize import BET_TYPES, PERIODS, SIDES, STATS, clean_pick

log = logging.getLogger(__name__)

TOOL_NAME = "record_picks"
TWEET_TYPES = ["new_pick", "result_recap", "promo", "other"]
_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _nullable(t: str, **extra) -> dict:
    return {"type": [t, "null"], **extra}


TOOL = {
    "name": TOOL_NAME,
    "description": "Record the post's classification and every wager it recommends.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tweet_type": {"type": "string", "enum": TWEET_TYPES},
            "picks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "league": {"type": "string", "description": "NFL, NBA, NCAAF, MLB, ..."},
                        "bet_type": {"type": "string", "enum": BET_TYPES},
                        "period": {"type": "string", "enum": PERIODS},
                        "player": _nullable("string"),
                        "team": _nullable("string", description="Player's team, or the team being backed"),
                        "opponent": _nullable("string"),
                        "away_team": _nullable("string"),
                        "home_team": _nullable("string"),
                        "stat": {"type": ["string", "null"], "enum": STATS + [None]},
                        "side": {"type": ["string", "null"], "enum": SIDES + [None]},
                        "line": _nullable("number"),
                        "odds": _nullable("integer", description="American odds, e.g. -115 or 240"),
                        "units": _nullable("number"),
                        "parlay_group": _nullable("string"),
                        "snippet": {"type": "string"},
                    },
                    "required": ["league", "bet_type", "period", "player", "team", "stat",
                                 "side", "line", "odds", "units", "parlay_group", "snippet"],
                },
            },
        },
        "required": ["tweet_type", "picks"],
    },
}

SYSTEM_PROMPT = """You extract sports betting picks from posts by sports bettors ("cappers") on X/Twitter. You get the author, the post time, the post text, and any attached images (often bet-slip screenshots). Report your answer with the record_picks tool.

First classify the post (tweet_type):
- new_pick: the author recommends or announces a bet on a game that has not been settled.
- result_recap: reports outcomes of settled bets ("cashed", "✅", "went 4-1 yesterday"). Record no picks.
- promo: discount codes, Discord/subscription ads, giveaways with no specific bet. Record no picks.
- other: commentary, analysis with no stated bet, memes. Record no picks.

Rules for picks:
- Record every distinct wager, including each one in a list-style "card".
- Parlays and same-game parlays: record each leg as its own pick, all legs sharing one parlay_group label ("p1", "p2", ...). Straight bets use parlay_group null.
- "Tailing @someone" or quoting another capper's pick counts as the author's pick.
- NFL teams use these abbreviations only: ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LV LAC LAR MIA MIN NE NO NYG NYJ PHI PIT SF SEA TB TEN WAS.
- team = the player's team for props, or the backed team for spread / moneyline / team total. For game totals set away_team and home_team and leave team null.
- Player names in full with normal capitalization ("Ja'Marr Chase"). Expand last names or nicknames only when you are confident who is meant.
- line is a number. "o24.5 rec yds" -> side "over", line 24.5, stat "receiving_yards". For spreads the line is the backed team's number (e.g. -3.5). Moneylines have line null.
- "ATD"/"anytime TD"/"to score" -> stat "anytime_td", side "yes". "Rush + rec yds" -> "rush_rec_yards". "Pass + rush yds" -> "pass_rush_yards". Use "other" for any prop stat not in the list.
- odds: American odds as an integer only if shown. units: stake in units only if stated ("2u", "1.5U").
- Never invent games, lines, odds or players. Use null for anything not stated or confidently inferable.
- snippet: the shortest verbatim piece of the post or slip that states the bet."""


@dataclass
class Extraction:
    tweet_type: str
    picks: list[dict]  # cleaned via normalize.clean_pick


def parse_response(resp) -> Extraction:
    """Pull the forced tool call out of a Messages API response."""
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
            data = block.input or {}
            tweet_type = data.get("tweet_type") if data.get("tweet_type") in TWEET_TYPES else "other"
            raw_picks = (data.get("picks") or []) if tweet_type == "new_pick" else []
            return Extraction(tweet_type, [clean_pick(p) for p in raw_picks if isinstance(p, dict)])
    raise ValueError(f"No {TOOL_NAME} call in response (stop_reason={getattr(resp, 'stop_reason', '?')})")


class PickExtractor:
    def __init__(self, model: str = EXTRACT_MODEL, client: anthropic.Anthropic | None = None):
        self.model = model
        if client is None and not os.getenv("ANTHROPIC_API_KEY", "").strip():
            raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to .env (no quotes, no inline "
                               "comment) and re-run `python -m capper_tracker extract`.")
        self.client = client or anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.http = requests.Session()
        # Some models reject forced tool use (tool_choice "tool"/"any"). We try it first and,
        # on that specific 400, switch to "auto" plus an explicit instruction for the rest of the run.
        self.forced_tool_choice = True

    def _image_block(self, url: str) -> dict | None:
        try:
            r = self.http.get(url, timeout=20)
            r.raise_for_status()
        except requests.RequestException as e:
            log.warning("Could not fetch image %s: %s", url, e)
            return None
        media_type = r.headers.get("content-type", "").split(";")[0].strip()
        if media_type not in _IMAGE_TYPES or len(r.content) > _MAX_IMAGE_BYTES:
            return None
        data = base64.standard_b64encode(r.content).decode()
        return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}

    def extract(self, *, handle: str, created_at: str, week: int, text: str,
                media_urls: list[str]) -> Extraction:
        content: list[dict] = [b for b in (self._image_block(u) for u in media_urls[:4]) if b]
        content.append({
            "type": "text",
            "text": f"Author: @{handle}\nPosted: {created_at} (NFL week {week})\n\n"
                    f"Post text:\n{text or '(no text; see images)'}",
        })
        messages = [{"role": "user", "content": content}]
        resp = self._create(messages)
        try:
            return parse_response(resp)
        except ValueError:
            if self.forced_tool_choice:
                raise
            # In auto mode the model occasionally answers in prose; ask once more for the tool call.
            messages += [{"role": "assistant", "content": resp.content},
                         {"role": "user", "content": f"Record your answer by calling {TOOL_NAME}."}]
            return parse_response(self._create(messages))

    def _create(self, messages: list[dict]):
        system = SYSTEM_PROMPT if self.forced_tool_choice else (
            SYSTEM_PROMPT + f"\n\nAlways respond by calling the {TOOL_NAME} tool exactly once, "
                            "even when the post has no picks. Never answer in plain text.")
        tool_choice = ({"type": "tool", "name": TOOL_NAME} if self.forced_tool_choice
                       else {"type": "auto"})
        try:
            return self.client.messages.create(
                model=self.model, max_tokens=4096, system=system, tools=[TOOL],
                tool_choice=tool_choice, messages=messages,
            )
        except anthropic.BadRequestError as e:
            if self.forced_tool_choice and "tool_choice" in str(e):
                log.info("%s doesn't support forced tool use; switching to tool_choice=auto", self.model)
                self.forced_tool_choice = False
                return self._create(messages)
            raise
