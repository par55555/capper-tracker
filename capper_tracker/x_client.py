"""Minimal X API v2 client for the two endpoints we need (app-only bearer auth)."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)


class XAPIError(RuntimeError):
    pass


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class XClient:
    BASE = "https://api.x.com/2"
    TWEET_FIELDS = "created_at,note_tweet,in_reply_to_user_id,attachments,referenced_tweets"

    def __init__(self, bearer_token: str, session: requests.Session | None = None):
        if not bearer_token:
            raise ValueError("X_BEARER_TOKEN is not set (see .env.example)")
        self.session = session or requests.Session()
        self.session.headers["Authorization"] = f"Bearer {bearer_token}"
        self.posts_read = 0
        self.users_read = 0

    def _get(self, path: str, params: dict) -> dict:
        for attempt in range(6):
            r = self.session.get(f"{self.BASE}{path}", params=params, timeout=30)
            if r.status_code == 429:
                reset = int(r.headers.get("x-rate-limit-reset", time.time() + 60))
                wait = min(max(reset - time.time(), 1) + 1, 900)
                log.warning("Rate limited on %s; sleeping %.0fs", path, wait)
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            if not r.ok:
                raise XAPIError(f"{r.status_code} on {path}: {r.text[:300]}")
            return r.json()
        raise XAPIError(f"Gave up on {path} after retries")

    def lookup_users(self, handles: list[str]) -> dict[str, dict]:
        """handle -> {id, name}. Costs one user read per handle found, so cache the IDs."""
        found: dict[str, dict] = {}
        for i in range(0, len(handles), 100):
            data = self._get("/users/by", {"usernames": ",".join(handles[i:i + 100])})
            for u in data.get("data", []):
                found[u["username"].lower()] = {"id": u["id"], "name": u["name"]}
                self.users_read += 1
            for err in data.get("errors", []):
                log.warning("User lookup problem: %s", err.get("detail", err))
        return found

    def user_tweets(
        self,
        user_id: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        since_id: str | None = None,
        max_tweets: int = 15,
    ) -> list[dict]:
        """Original posts and self-replies (threads) from a user's timeline, newest first.

        Retweets are excluded server-side. Replies to *other* users are dropped client-side,
        since cappers often post their card as a thread under their own post.
        """
        params: dict = {
            "max_results": 100,
            "exclude": "retweets",
            "tweet.fields": self.TWEET_FIELDS,
            "expansions": "attachments.media_keys",
            "media.fields": "type,url,preview_image_url",
        }
        if since_id:
            params["since_id"] = since_id
        elif start_time:
            params["start_time"] = _iso(start_time)
        if end_time:
            params["end_time"] = _iso(end_time)

        out: list[dict] = []
        while len(out) < max_tweets:
            # X bills every post returned, so only ask for what's still needed (API allows 5-100).
            params["max_results"] = min(100, max(5, max_tweets - len(out)))
            data = self._get(f"/users/{user_id}/tweets", params)
            media = {m["media_key"]: m for m in data.get("includes", {}).get("media", [])}
            for raw in data.get("data", []):
                self.posts_read += 1
                tweet = _normalize(raw, media)
                reply_to = raw.get("in_reply_to_user_id")
                if reply_to and reply_to != user_id:
                    continue
                out.append(tweet)
            token = data.get("meta", {}).get("next_token")
            if not token:
                break
            params["pagination_token"] = token
        return out[:max_tweets]

    @property
    def estimated_cost(self) -> float:
        from .config import X_COST_PER_POST, X_COST_PER_USER
        return self.posts_read * X_COST_PER_POST + self.users_read * X_COST_PER_USER


def _normalize(raw: dict, media: dict) -> dict:
    # Long posts (>280 chars) only carry their full text in note_tweet.
    text = (raw.get("note_tweet") or {}).get("text") or raw.get("text", "")
    urls = []
    for key in (raw.get("attachments") or {}).get("media_keys", []):
        m = media.get(key)
        if not m:
            continue
        # Photos have `url`; videos/GIFs only have a preview frame, which is still useful for slips.
        url = m.get("url") if m.get("type") == "photo" else m.get("preview_image_url")
        if url:
            urls.append(url)
    return {"id": raw["id"], "created_at": raw["created_at"], "text": text, "media_urls": urls}
