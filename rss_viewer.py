"""Standalone RSS News Intelligence — professional news filter with SQLite persistence.

Usage:
    python rss_viewer.py [--port 37378] [--db data/rss_news.db]

Features:
    - SQLite persistence with composite indexes
    - Multi-dimensional classification: Industry / Region / Source
    - Conditional HTTP requests (ETag / If-Modified-Since)
    - WebSocket live updates with connection limits
    - Security headers, GZip compression
    - HTML stripping from RSS descriptions
"""

import argparse
import asyncio
import html as html_mod
import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import feedparser
from starlette.applications import Starlette
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect
import uvicorn

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rss_intel")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_WS_CONNECTIONS: int = 100
MAX_LIMIT: int = 100
MAX_FEED_SIZE: int = 5 * 1024 * 1024
DEFAULT_REFRESH: int = 300
ADMIN_TOKEN: str = os.environ.get("ADMIN_TOKEN", "markstackai2026")

# ---------------------------------------------------------------------------
# RSS Sources (category -> URLs)
# ---------------------------------------------------------------------------
FEEDS: dict[str, list[str]] = {
    "US Politics": [
        "https://rss.politico.com/politics-news.xml",
        "https://thehill.com/news/feed",
        "https://api.axios.com/feed/",
        "https://feeds.npr.org/1001/rss.xml",
        "https://www.pbs.org/newshour/feeds/rss/headlines",
        "https://feeds.abcnews.com/abcnews/topstories",
        "https://www.cbsnews.com/latest/rss/main",
        "https://feeds.nbcnews.com/nbcnews/public/news",
    ],
    "World News": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.theguardian.com/world/rss",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://foreignpolicy.com/feed/",
        "https://www.france24.com/en/rss",
        "https://rss.dw.com/xml/rss-en-all",
        "https://news.un.org/feed/subscribe/en/news/all/rss.xml",
        "https://www.euronews.com/rss?format=xml",
        "https://thediplomat.com/feed/",
        "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml",
    ],
    "Economics": [
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "https://finance.yahoo.com/rss/topstories",
        "https://seekingalpha.com/market_currents.xml",
        "https://www.federalreserve.gov/feeds/press_all.xml",
        "https://www.sec.gov/news/pressreleases.rss",
        "https://feeds.content.dowjones.io/public/rss/RSSUSnews",
    ],
    "Crypto": [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
    ],
    "Technology": [
        "https://techcrunch.com/feed/",
        "https://www.theverge.com/rss/index.xml",
        "https://feeds.arstechnica.com/arstechnica/technology-lab",
        "https://www.technologyreview.com/feed/",
        "https://venturebeat.com/category/ai/feed/",
    ],
    "Defense": [
        "https://www.defenseone.com/rss/all/",
        "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml",
        "https://warontherocks.com/feed/",
    ],
    "Policy": [
        "https://www.atlanticcouncil.org/feed/",
        "https://www.crisisgroup.org/rss",
    ],
    "Energy": [
        "https://oilprice.com/rss/main",
    ],
    "Science": [
        "https://feeds.nature.com/nature/rss/current",
        "https://www.sciencedaily.com/rss/all.xml",
    ],
    "Google News": [
        "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q=(election+polls+OR+%22election+forecast%22)+when:1d",
        "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q=(CPI+OR+inflation+OR+GDP+OR+%22jobs+report%22)+when:2d",
        "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q=(crypto+regulation+OR+stablecoin)+when:3d",
        "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q=(OPEC+OR+%22oil+price%22+OR+%22crude+oil%22)+when:1d",
        "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q=(prediction+market+OR+Polymarket)+when:2d",
        "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q=(OpenAI+OR+Anthropic+OR+ChatGPT)+when:2d",
        "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q=(FIFA+OR+NFL+OR+NBA+OR+%22Premier+League%22)+when:1d",
        "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q=(Fed+OR+FOMC+OR+%22Federal+Reserve%22)+when:2d",
        "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q=(%22Supreme+Court%22+OR+Congress+OR+Senate)+when:2d",
        "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q=(Ukraine+OR+Russia+OR+NATO+OR+Iran+OR+Israel)+when:1d",
    ],
    "Regional": [
        "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml",
        "https://feeds.bbci.co.uk/news/world/asia/rss.xml",
        "https://feeds.bbci.co.uk/news/world/africa/rss.xml",
        "https://feeds.bbci.co.uk/news/world/latin_america/rss.xml",
    ],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_int(value: Optional[str], default: int, lo: int, hi: int) -> int:
    """Safely parse an integer with bounds clamping."""
    if value is None:
        return default
    try:
        return max(lo, min(hi, int(value)))
    except (ValueError, TypeError):
        return default


def _strip_html(text: str) -> str:
    """Remove HTML tags and unescape entities."""
    cleaned = re.sub(r"<[^>]+>", "", text)
    return html_mod.unescape(cleaned).strip()


def _google_news_label(url: str) -> str:
    """Extract a readable label from a Google News search RSS URL.

    Args:
        url: Google News RSS search URL.

    Returns:
        Human-friendly label like 'GNews: OPEC / Oil Price'.
    """
    from urllib.parse import urlparse, parse_qs, unquote
    try:
        q = parse_qs(urlparse(url).query).get("q", [""])[0]
        q = unquote(q)
        # Remove time filter like "when:1d", "when:2d"
        q = re.sub(r"\s*when:\w+", "", q)
        # Remove outer parentheses
        q = q.strip("() ")
        # Extract quoted phrases and bare keywords, drop OR/AND
        tokens = re.findall(r'"([^"]+)"|\b([A-Za-z][A-Za-z0-9]+)\b', q)
        keywords = []
        for quoted, bare in tokens:
            word = quoted or bare
            if word.upper() not in ("OR", "AND"):
                keywords.append(word)
        if keywords:
            return "GNews: " + " / ".join(keywords)
    except Exception:
        pass
    return "Google News"


# ---------------------------------------------------------------------------
# Region Inference
# ---------------------------------------------------------------------------

def infer_region(url: str, category: str) -> str:
    """Infer geographic region from feed URL and category.

    Args:
        url: The RSS feed URL.
        category: The feed category.

    Returns:
        Region string.
    """
    url_lower = url.lower()

    if category in ("US Politics", "Economics", "Defense"):
        return "US"

    if "middle_east" in url_lower or "aljazeera" in url_lower:
        return "Middle East"
    if "asia" in url_lower or "diplomat" in url_lower or "channelnewsasia" in url_lower:
        return "Asia"
    if "africa" in url_lower:
        return "Africa"
    if "latin_america" in url_lower:
        return "Latin America"
    if "france24" in url_lower or "dw.com" in url_lower or "euronews" in url_lower:
        return "Europe"

    if "news.google.com" in url_lower:
        if any(kw in url_lower for kw in ["ukraine", "russia", "nato", "iran", "israel"]):
            return "Conflict Zones"
        if any(kw in url_lower for kw in ["election", "congress", "senate", "supreme+court"]):
            return "US"
        if any(kw in url_lower for kw in ["fed", "fomc", "federal+reserve"]):
            return "US"

    return "Global"


# Build URL -> (category, region) lookup
FEED_META: dict[str, tuple[str, str]] = {}
for _cat, _urls in FEEDS.items():
    for _url in _urls:
        FEED_META[_url] = (_cat, infer_region(_url, _cat))

ALL_FEED_URLS: list[str] = list(FEED_META.keys())

# ---------------------------------------------------------------------------
# SQLite Database
# ---------------------------------------------------------------------------

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    source TEXT NOT NULL,
    category TEXT NOT NULL,
    region TEXT DEFAULT 'Global',
    published_at TEXT DEFAULT '',
    published_ts REAL DEFAULT 0,
    fetched_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cat_pub ON articles(category, published_ts DESC);
CREATE INDEX IF NOT EXISTS idx_region_pub ON articles(region, published_ts DESC);
CREATE INDEX IF NOT EXISTS idx_source_pub ON articles(source, published_ts DESC);
CREATE INDEX IF NOT EXISTS idx_fetched ON articles(fetched_at DESC);

CREATE TABLE IF NOT EXISTS feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    category TEXT NOT NULL DEFAULT 'Other',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feeds_cat ON feeds(category);
"""


class ArticleDB:
    """Thread-safe SQLite wrapper for article storage."""

    def __init__(self, db_path: str = "data/rss_news.db") -> None:
        self._db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection with optimized PRAGMAs."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn.execute("PRAGMA cache_size=-8000")
        return self._local.conn

    def _init_db(self) -> None:
        """Create tables and indexes, seed default feeds if empty."""
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = self._get_conn()
        conn.executescript(_CREATE_SQL)
        conn.commit()
        # Seed default feeds on first run
        count = conn.execute("SELECT COUNT(*) FROM feeds").fetchone()[0]
        if count == 0:
            now = time.time()
            rows = [(url, cat, 1, now) for cat, urls in FEEDS.items() for url in urls]
            conn.executemany(
                "INSERT OR IGNORE INTO feeds (url, category, enabled, created_at) VALUES (?, ?, ?, ?)",
                rows,
            )
            conn.commit()
            logger.info("Seeded %d default feeds into database", len(rows))

    def get_feeds(self) -> list[dict[str, Any]]:
        """Get all feeds from database."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, url, category, enabled, created_at FROM feeds ORDER BY category, id"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_enabled_feeds(self) -> list[dict[str, str]]:
        """Get enabled feeds as list of {url, category}."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT url, category FROM feeds WHERE enabled = 1 ORDER BY category, id"
        ).fetchall()
        return [{"url": r["url"], "category": r["category"]} for r in rows]

    def add_feed(self, url: str, category: str) -> Optional[int]:
        """Add a new feed. Returns feed id or None if duplicate."""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO feeds (url, category, enabled, created_at) VALUES (?, ?, 1, ?)",
                (url, category, time.time()),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def delete_feed(self, feed_id: int) -> bool:
        """Delete a feed by id. Returns True if deleted."""
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
        conn.commit()
        return cur.rowcount > 0

    def toggle_feed(self, feed_id: int) -> Optional[bool]:
        """Toggle feed enabled/disabled. Returns new state or None if not found."""
        conn = self._get_conn()
        row = conn.execute("SELECT enabled FROM feeds WHERE id = ?", (feed_id,)).fetchone()
        if not row:
            return None
        new_state = 0 if row["enabled"] else 1
        conn.execute("UPDATE feeds SET enabled = ? WHERE id = ?", (new_state, feed_id))
        conn.commit()
        return bool(new_state)

    def close(self) -> None:
        """Close thread-local connection."""
        conn = getattr(self._local, "conn", None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def insert_articles(self, articles: list[dict[str, Any]]) -> int:
        """Batch insert articles, ignoring duplicates. Returns count of new rows."""
        if not articles:
            return 0
        conn = self._get_conn()
        before = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        conn.executemany(
            """INSERT OR IGNORE INTO articles
               (url, title, description, source, category, region,
                published_at, published_ts, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (a["url"], a["title"], a["description"], a["source"],
                 a["category"], a["region"], a["published_at"],
                 a["published_ts"], a["fetched_at"])
                for a in articles
            ],
        )
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        return after - before

    def query_articles(
        self,
        category: str = "",
        region: str = "",
        source: str = "",
        sources: str = "",
        q: str = "",
        date: str = "",
        days: int = 0,
        limit: int = 30,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Query articles with multi-dimensional filters. Returns (articles, total)."""
        conn = self._get_conn()
        where_parts: list[str] = []
        params: list[Any] = []

        if category:
            where_parts.append("category = ?")
            params.append(category)
        if region:
            where_parts.append("region = ?")
            params.append(region)
        if source:
            where_parts.append("source = ?")
            params.append(source)
        if sources:
            src_list = [s.strip() for s in sources.split(",") if s.strip()]
            if src_list:
                placeholders = ",".join("?" for _ in src_list)
                where_parts.append(f"source IN ({placeholders})")
                params.extend(src_list)
        if q:
            escaped_q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where_parts.append("(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
            params.extend([f"%{escaped_q}%", f"%{escaped_q}%"])
        if date:
            try:
                dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                start_ts = dt.timestamp()
                end_ts = (dt + timedelta(days=1)).timestamp()
                where_parts.append("(published_ts BETWEEN ? AND ? OR fetched_at BETWEEN ? AND ?)")
                params.extend([start_ts, end_ts, start_ts, end_ts])
            except ValueError:
                pass
        elif days > 0:
            cutoff = time.time() - days * 86400
            where_parts.append("(published_ts >= ? OR fetched_at >= ?)")
            params.extend([cutoff, cutoff])

        where_clause = " AND ".join(where_parts) if where_parts else "1=1"

        total = conn.execute(
            f"SELECT COUNT(*) FROM articles WHERE {where_clause}", params
        ).fetchone()[0]

        rows = conn.execute(
            f"""SELECT id, url AS link, title, description, source, category, region,
                       published_at, published_ts AS published, fetched_at
                FROM articles WHERE {where_clause}
                ORDER BY published_ts DESC, fetched_at DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        return [dict(r) for r in rows], total

    def get_stats(self) -> dict[str, Any]:
        """Get aggregate statistics."""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        today_start = time.time() - 86400
        today_count = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE fetched_at >= ?", (today_start,)
        ).fetchone()[0]
        source_count = conn.execute(
            "SELECT COUNT(DISTINCT source) FROM articles"
        ).fetchone()[0]
        return {
            "total": total,
            "today_count": today_count,
            "sources": source_count,
        }

    def get_filters(self, days: int = 0, date: str = "") -> dict[str, list[dict[str, Any]]]:
        """Get filter options with counts, respecting time context."""
        conn = self._get_conn()
        time_parts: list[str] = []
        time_params: list[Any] = []

        if date:
            try:
                dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                start_ts = dt.timestamp()
                end_ts = (dt + timedelta(days=1)).timestamp()
                time_parts.append("fetched_at BETWEEN ? AND ?")
                time_params.extend([start_ts, end_ts])
            except ValueError:
                pass
        elif days > 0:
            cutoff = time.time() - days * 86400
            time_parts.append("fetched_at >= ?")
            time_params.append(cutoff)

        where = " WHERE " + " AND ".join(time_parts) if time_parts else ""

        categories = [
            {"name": r[0], "count": r[1]}
            for r in conn.execute(
                f"SELECT category, COUNT(*) FROM articles{where} GROUP BY category ORDER BY COUNT(*) DESC",
                time_params,
            ).fetchall()
        ]
        regions = [
            {"name": r[0], "count": r[1]}
            for r in conn.execute(
                f"SELECT region, COUNT(*) FROM articles{where} GROUP BY region ORDER BY COUNT(*) DESC",
                time_params,
            ).fetchall()
        ]
        sources = [
            {"name": r[0], "count": r[1]}
            for r in conn.execute(
                f"SELECT source, COUNT(*) FROM articles{where} GROUP BY source ORDER BY COUNT(*) DESC LIMIT 50",
                time_params,
            ).fetchall()
        ]
        return {"categories": categories, "regions": regions, "sources": sources}


# ---------------------------------------------------------------------------
# RSS Fetcher (background thread with SQLite persistence)
# ---------------------------------------------------------------------------

class RSSStore:
    """Fetches RSS feeds and persists to SQLite with WebSocket notifications."""

    def __init__(self, db: ArticleDB, refresh_interval: int = DEFAULT_REFRESH) -> None:
        self._db = db
        self._refresh_interval = refresh_interval
        self._stop_event = threading.Event()
        self._last_refresh: float = 0
        self._last_new_count: int = 0
        self._ws_clients: set[WebSocket] = set()
        self._ws_lock = asyncio.Lock()
        self._ws_count: int = 0
        self._executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix="rss")
        self._etag_cache: dict[str, tuple[str, str]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._feed_count: int = 0

    @property
    def last_refresh(self) -> float:
        """Timestamp of last successful refresh."""
        return self._last_refresh

    @property
    def feed_count(self) -> int:
        """Current enabled feed count."""
        return self._feed_count

    @property
    def ws_count(self) -> int:
        """Current WebSocket connection count."""
        return self._ws_count

    async def register_ws(self, ws: WebSocket) -> bool:
        """Register a WebSocket client. Returns False if limit reached."""
        async with self._ws_lock:
            if self._ws_count >= MAX_WS_CONNECTIONS:
                return False
            self._ws_clients.add(ws)
            self._ws_count = len(self._ws_clients)
        return True

    async def unregister_ws(self, ws: WebSocket) -> None:
        """Unregister a WebSocket client."""
        async with self._ws_lock:
            self._ws_clients.discard(ws)
            self._ws_count = len(self._ws_clients)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start background refresh thread."""
        self._loop = loop
        t = threading.Thread(target=self._run, daemon=True, name="rss-refresh")
        t.start()
        logger.info("RSS refresh started (interval=%ds, feeds=%d)", self._refresh_interval, len(ALL_FEED_URLS))

    def stop(self) -> None:
        """Stop background refresh and clean up."""
        self._stop_event.set()
        self._executor.shutdown(wait=False)
        self._db.close()
        logger.info("RSS store stopped")

    def _run(self) -> None:
        """Main refresh loop with error protection."""
        while not self._stop_event.is_set():
            try:
                self._refresh()
            except Exception:
                logger.exception("Refresh cycle failed")
            self._stop_event.wait(timeout=self._refresh_interval)

    def _refresh(self) -> None:
        """Fetch all enabled feeds from DB and persist new articles."""
        enabled_feeds = self._db.get_enabled_feeds()
        total_feeds = len(enabled_feeds)
        logger.info("Starting refresh of %d feeds...", total_feeds)
        t0 = time.time()
        all_articles: list[dict[str, Any]] = []
        completed = 0

        futures = {
            self._executor.submit(self._fetch_one, f["url"], f["category"]): f["url"]
            for f in enabled_feeds
        }

        try:
            for fut in as_completed(futures, timeout=45):
                url = futures[fut]
                try:
                    articles = fut.result(timeout=1)
                    all_articles.extend(articles)
                    completed += 1
                except Exception as e:
                    logger.warning("Feed error %s: %s", url[:60], e)
        except Exception as timeout_err:
            logger.warning("Timeout after 45s, got %d/%d: %s", completed, total_feeds, timeout_err)

        now = time.time()
        for a in all_articles:
            a["fetched_at"] = now

        new_count = self._db.insert_articles(all_articles)
        self._last_refresh = now
        self._last_new_count = new_count
        self._feed_count = total_feeds
        elapsed = time.time() - t0
        logger.info(
            "Refresh done: %d fetched, +%d new (%d/%d feeds, %.1fs)",
            len(all_articles), new_count, completed, total_feeds, elapsed,
        )

        if self._loop:
            stats = self._db.get_stats()
            stats["last_refresh"] = self._last_refresh
            stats["total_feeds"] = total_feeds
            stats["new_count"] = new_count
            asyncio.run_coroutine_threadsafe(self._notify_ws(stats), self._loop)

    def _fetch_one(self, url: str, category: str = "Other") -> list[dict[str, Any]]:
        """Fetch and parse a single RSS feed with conditional HTTP."""
        region = infer_region(url, category)

        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; NewsIntel/2.0)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        })

        # Add conditional request headers
        cached = self._etag_cache.get(url)
        if cached:
            etag, last_modified = cached
            if etag:
                req.add_header("If-None-Match", etag)
            if last_modified:
                req.add_header("If-Modified-Since", last_modified)

        try:
            resp = urllib.request.urlopen(req, timeout=12)
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return []
            return []
        except Exception:
            return []

        # Cache response headers
        resp_etag = resp.headers.get("ETag", "")
        resp_lm = resp.headers.get("Last-Modified", "")
        if resp_etag or resp_lm:
            self._etag_cache[url] = (resp_etag, resp_lm)

        raw = resp.read(MAX_FEED_SIZE)
        feed = feedparser.parse(raw)

        source = ""
        if feed.feed:
            source = feed.feed.get("title", "")
        if not source:
            parts = url.split("/")
            source = parts[2] if len(parts) > 2 else url
        # Google News search feeds return query strings as titles — make them readable
        if "news.google.com/rss/search" in url:
            source = _google_news_label(url)

        articles: list[dict[str, Any]] = []
        for entry in feed.entries[:15]:
            entry_url = entry.get("link", "")
            if not entry_url:
                continue

            published_ts: float = 0
            published_str = ""
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    published_ts = dt.timestamp()
                    published_str = dt.strftime("%Y-%m-%d %H:%M UTC")
                except (TypeError, ValueError):
                    pass
            if not published_str:
                published_str = entry.get("published", "")

            raw_desc = entry.get("summary", "") or ""
            description = _strip_html(raw_desc)[:500]

            articles.append({
                "title": entry.get("title", ""),
                "description": description,
                "source": source,
                "url": entry_url,
                "category": category,
                "region": region,
                "published_at": published_str,
                "published_ts": published_ts,
                "fetched_at": 0,
            })

        return articles

    async def _notify_ws(self, stats: dict[str, Any]) -> None:
        """Broadcast update to all WebSocket clients, cleaning dead ones."""
        msg = json.dumps({"type": "update", "stats": stats})
        dead: list[WebSocket] = []
        async with self._ws_lock:
            for ws in self._ws_clients:
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._ws_clients.discard(ws)
            self._ws_count = len(self._ws_clients)
        if dead:
            logger.info("Removed %d dead WebSocket connections", len(dead))


# ---------------------------------------------------------------------------
# Route Handlers
# ---------------------------------------------------------------------------

async def homepage(request: Request) -> HTMLResponse:
    """Serve the main HTML page with cache headers."""
    headers = {"Cache-Control": "public, max-age=300"}
    return HTMLResponse(HTML_PAGE, headers=headers)


async def api_articles(request: Request) -> JSONResponse:
    """Query articles with multi-dimensional filters."""
    db: ArticleDB = request.app.state.db
    params = request.query_params
    articles, total = db.query_articles(
        category=params.get("category", ""),
        region=params.get("region", ""),
        source=params.get("source", ""),
        sources=params.get("sources", ""),
        q=params.get("q", ""),
        date=params.get("date", ""),
        days=_safe_int(params.get("days"), 0, 0, 365),
        limit=_safe_int(params.get("limit"), 30, 1, MAX_LIMIT),
        offset=_safe_int(params.get("offset"), 0, 0, 100000),
    )
    return JSONResponse({"data": articles, "total": total})


async def api_stats(request: Request) -> JSONResponse:
    """Return aggregate statistics."""
    db: ArticleDB = request.app.state.db
    store: RSSStore = request.app.state.store
    stats = db.get_stats()
    stats["last_refresh"] = store.last_refresh
    stats["total_feeds"] = store.feed_count
    return JSONResponse(stats)


async def api_filters(request: Request) -> JSONResponse:
    """Return filter options with counts, respecting time context."""
    db: ArticleDB = request.app.state.db
    params = request.query_params
    filters = db.get_filters(
        days=_safe_int(params.get("days"), 0, 0, 365),
        date=params.get("date", ""),
    )
    return JSONResponse(filters)


async def api_health(request: Request) -> JSONResponse:
    """Health check endpoint."""
    db: ArticleDB = request.app.state.db
    store: RSSStore = request.app.state.store
    stats = db.get_stats()
    return JSONResponse({
        "status": "ok",
        "total_articles": stats["total"],
        "last_refresh": store.last_refresh,
        "feed_count": store.feed_count,
        "ws_connections": store.ws_count,
    })


def _check_admin(request: Request) -> bool:
    """Verify admin token from Authorization header."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:] == ADMIN_TOKEN
    return False


async def api_admin_login(request: Request) -> JSONResponse:
    """Admin login — verify password and return token."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    password = (body.get("password") or "").strip()
    if not password:
        return JSONResponse({"error": "Password required"}, status_code=400)
    if password != ADMIN_TOKEN:
        return JSONResponse({"error": "Wrong password"}, status_code=401)
    return JSONResponse({"ok": True, "token": ADMIN_TOKEN})


async def api_feeds(request: Request) -> JSONResponse:
    """List all feeds or add a new feed."""
    db: ArticleDB = request.app.state.db
    if request.method == "GET":
        if not _check_admin(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        feeds = db.get_feeds()
        return JSONResponse({"feeds": feeds})
    elif request.method == "POST":
        if not _check_admin(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        url = (body.get("url") or "").strip()
        category = (body.get("category") or "Other").strip()
        if not url:
            return JSONResponse({"error": "URL is required"}, status_code=400)
        if not url.startswith(("http://", "https://")):
            return JSONResponse({"error": "Invalid URL"}, status_code=400)
        feed_id = db.add_feed(url, category)
        if feed_id is None:
            return JSONResponse({"error": "Feed already exists"}, status_code=409)
        return JSONResponse({"id": feed_id, "url": url, "category": category})
    return JSONResponse({"error": "Method not allowed"}, status_code=405)


async def api_feed_action(request: Request) -> JSONResponse:
    """Delete or toggle a feed by id."""
    if not _check_admin(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    db: ArticleDB = request.app.state.db
    feed_id = _safe_int(request.path_params.get("id"), 0, 0, 999999999)
    if feed_id == 0:
        return JSONResponse({"error": "Invalid feed id"}, status_code=400)
    if request.method == "DELETE":
        ok = db.delete_feed(feed_id)
        if not ok:
            return JSONResponse({"error": "Feed not found"}, status_code=404)
        return JSONResponse({"ok": True})
    elif request.method == "PUT":
        new_state = db.toggle_feed(feed_id)
        if new_state is None:
            return JSONResponse({"error": "Feed not found"}, status_code=404)
        return JSONResponse({"ok": True, "enabled": new_state})
    return JSONResponse({"error": "Method not allowed"}, status_code=405)


async def ws_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for live updates with connection limiting."""
    store: RSSStore = websocket.app.state.store
    await websocket.accept()
    accepted = await store.register_ws(websocket)
    if not accepted:
        await websocket.send_json({"type": "error", "message": "Too many connections"})
        await websocket.close(code=1013)
        return
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("WebSocket error", exc_info=True)
    finally:
        await store.unregister_ws(websocket)


# ---------------------------------------------------------------------------
# Security Middleware
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware:
    """ASGI middleware adding security headers to all HTTP responses."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            async def send_with_headers(message: dict[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    extra = [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"x-xss-protection", b"1; mode=block"),
                        (b"referrer-policy", b"strict-origin-when-cross-origin"),
                        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    ]
                    message["headers"] = list(message.get("headers", [])) + extra
                await send(message)
            await self.app(scope, receive, send_with_headers)
        else:
            await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Application Factory
# ---------------------------------------------------------------------------

def create_app(db_path: str = "data/rss_news.db") -> Starlette:
    """Create the Starlette application with all middleware."""
    db = ArticleDB(db_path)
    store = RSSStore(db, refresh_interval=DEFAULT_REFRESH)

    async def on_startup() -> None:
        loop = asyncio.get_event_loop()
        store.start(loop)

    async def on_shutdown() -> None:
        store.stop()

    app = Starlette(
        routes=[
            Route("/", homepage),
            Route("/api/articles", api_articles),
            Route("/api/stats", api_stats),
            Route("/api/filters", api_filters),
            Route("/api/admin/login", api_admin_login, methods=["POST"]),
            Route("/api/feeds", api_feeds, methods=["GET", "POST"]),
            Route("/api/feeds/{id:int}", api_feed_action, methods=["DELETE", "PUT"]),
            Route("/health", api_health),
            WebSocketRoute("/ws", ws_endpoint),
        ],
        on_startup=[on_startup],
        on_shutdown=[on_shutdown],
    )
    app.state.db = db
    app.state.store = store
    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.add_middleware(SecurityHeadersMiddleware)
    return app


# ---------------------------------------------------------------------------
# HTML Page (placeholder — will be replaced by assembly script)
# ---------------------------------------------------------------------------
HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>News Intelligence Dashboard</title>
<style>
:root {
  --bg-primary: #0d1117;
  --bg-secondary: #161b22;
  --bg-tertiary: #21262d;
  --bg-card: #161b22;
  --border-color: #30363d;
  --text-primary: #e6edf3;
  --text-secondary: #8b949e;
  --text-muted: #484f58;
  --accent-blue: #58a6ff;
  --accent-green: #3fb950;
  --accent-orange: #d29922;
  --accent-red: #f85149;
  --accent-purple: #bc8cff;
  --hover-bg: rgba(88,166,255,0.08);
  --shadow: 0 1px 3px rgba(0,0,0,0.4);
  --radius: 8px;
  --sidebar-width: 280px;
}
[data-theme="light"] {
  --bg-primary: #ffffff;
  --bg-secondary: #f6f8fa;
  --bg-tertiary: #e1e4e8;
  --bg-card: #ffffff;
  --border-color: #d0d7de;
  --text-primary: #1f2328;
  --text-secondary: #656d76;
  --text-muted: #8b949e;
  --accent-blue: #0969da;
  --accent-green: #1a7f37;
  --accent-orange: #9a6700;
  --accent-red: #cf222e;
  --accent-purple: #8250df;
  --hover-bg: rgba(9,105,218,0.06);
  --shadow: 0 1px 3px rgba(31,35,40,0.12);
}
*,*::before,*::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
  background: var(--bg-primary);
  color: var(--text-primary);
  line-height: 1.5;
  overflow: hidden;
  height: 100vh;
}
a { color: var(--accent-blue); text-decoration: none; }
a:hover { text-decoration: underline; }
button { cursor: pointer; font-family: inherit; }
:focus-visible {
  outline: 2px solid var(--accent-blue);
  outline-offset: 2px;
}
.skip-link {
  position: absolute; top: -100px; left: 8px; z-index: 9999;
  background: var(--accent-blue); color: #fff; padding: 8px 16px;
  border-radius: var(--radius); font-weight: 600;
  transition: top 0.2s;
}
.skip-link:focus { top: 8px; }

/* Layout */
.layout { display: flex; height: 100vh; }
.sidebar {
  width: var(--sidebar-width); min-width: var(--sidebar-width);
  background: var(--bg-secondary); border-right: 1px solid var(--border-color);
  display: flex; flex-direction: column; overflow-y: auto;
  scrollbar-width: thin; scrollbar-color: var(--border-color) transparent;
}
.content {
  flex: 1; display: flex; flex-direction: column; overflow-y: auto;
  scroll-behavior: smooth;
  scrollbar-width: thin; scrollbar-color: var(--border-color) transparent;
}
.header {
  background: var(--bg-secondary); border-bottom: 1px solid var(--border-color);
  padding: 12px 24px; display: flex; align-items: center; gap: 12px;
  position: sticky; top: 0; z-index: 100;
}
.header-title { font-size: 18px; font-weight: 700; white-space: nowrap; }
.header-title span { color: var(--accent-blue); }
.header-spacer { flex: 1; }
.header-stats {
  display: flex; gap: 12px; align-items: center; font-size: 13px; color: var(--text-secondary);
}
.stat-pill {
  display: inline-flex; align-items: center; gap: 4px;
  background: var(--bg-tertiary); padding: 4px 10px; border-radius: 12px;
  font-size: 12px; white-space: nowrap;
}
.stat-pill .num { font-weight: 700; color: var(--accent-blue); }
.refresh-time { font-weight: 500; }
.refresh-time.fresh { color: var(--accent-green); }
.refresh-time.stale { color: var(--accent-orange); }
.refresh-time.old { color: var(--accent-red); }
.header-actions { display: flex; gap: 6px; align-items: center; }

/* Icon buttons */
.icon-btn {
  background: none; border: 1px solid var(--border-color); color: var(--text-secondary);
  width: 32px; height: 32px; border-radius: 6px; display: flex; align-items: center;
  justify-content: center; font-size: 16px; transition: all 0.15s;
}
.icon-btn:hover { background: var(--hover-bg); color: var(--text-primary); border-color: var(--accent-blue); }
.icon-btn[aria-pressed="true"] { background: var(--hover-bg); color: var(--accent-blue); border-color: var(--accent-blue); }

/* Menu button */
.menu-btn { display: none; }

/* Sidebar sections */
.sidebar-header {
  padding: 16px; border-bottom: 1px solid var(--border-color);
  display: flex; align-items: center; justify-content: space-between;
}
.sidebar-header h2 { font-size: 14px; font-weight: 600; }
.sidebar-close {
  display: none; background: none; border: none; color: var(--text-secondary);
  font-size: 20px; cursor: pointer; padding: 4px;
}
.sidebar-close:hover { color: var(--text-primary); }
.sidebar-section { padding: 12px 16px; border-bottom: 1px solid var(--border-color); }
.sidebar-section h3 {
  font-size: 12px; font-weight: 600; text-transform: uppercase;
  color: var(--text-secondary); margin-bottom: 8px; letter-spacing: 0.5px;
}
.filter-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 6px 10px; border-radius: 6px; cursor: pointer; font-size: 13px;
  transition: background 0.15s; user-select: none;
}
.filter-item:hover { background: var(--hover-bg); }
.filter-item.active { background: var(--hover-bg); color: var(--accent-blue); font-weight: 600; }
.filter-item .count {
  background: var(--bg-tertiary); padding: 1px 7px; border-radius: 10px;
  font-size: 11px; color: var(--text-secondary); min-width: 24px; text-align: center;
}
.source-item {
  display: flex; align-items: center; gap: 8px; padding: 5px 10px;
  border-radius: 6px; cursor: pointer; font-size: 13px; transition: background 0.15s;
  user-select: none;
}
.source-item:hover { background: var(--hover-bg); }
.source-cb {
  width: 16px; height: 16px; border: 1.5px solid var(--border-color);
  border-radius: 4px; display: flex; align-items: center; justify-content: center;
  font-size: 10px; transition: all 0.15s; flex-shrink: 0;
}
.source-item.checked .source-cb {
  background: var(--accent-blue); border-color: var(--accent-blue); color: #fff;
}
.source-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.source-cnt {
  font-size: 11px; color: var(--text-muted); background: var(--bg-tertiary);
  padding: 1px 6px; border-radius: 10px;
}
.select-all-row {
  display: flex; align-items: center; gap: 8px; padding: 5px 10px;
  margin-bottom: 4px; border-bottom: 1px solid var(--border-color);
  padding-bottom: 8px; cursor: pointer; font-size: 12px; font-weight: 600;
  color: var(--text-secondary); user-select: none;
}
.select-all-row:hover { color: var(--accent-blue); }

/* Date / Days filter */
.date-input {
  width: 100%; padding: 6px 10px; border: 1px solid var(--border-color);
  background: var(--bg-primary); color: var(--text-primary); border-radius: 6px;
  font-size: 13px; margin-bottom: 8px;
}
.date-input:focus { border-color: var(--accent-blue); outline: none; }
.days-chips { display: flex; gap: 6px; flex-wrap: wrap; }
.day-chip {
  padding: 4px 10px; border: 1px solid var(--border-color); border-radius: 12px;
  font-size: 12px; cursor: pointer; background: none; color: var(--text-secondary);
  transition: all 0.15s;
}
.day-chip:hover { border-color: var(--accent-blue); color: var(--accent-blue); }
.day-chip.active { background: var(--accent-blue); color: #fff; border-color: var(--accent-blue); }

/* Search */
.search-wrapper {
  position: relative; display: flex; align-items: center;
}
.search-input {
  width: 200px; padding: 6px 30px 6px 10px; border: 1px solid var(--border-color);
  background: var(--bg-primary); color: var(--text-primary); border-radius: 6px;
  font-size: 13px; transition: border-color 0.15s, width 0.2s;
}
.search-input:focus { border-color: var(--accent-blue); outline: none; width: 260px; }
.search-clear {
  position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
  background: none; border: none; color: var(--text-muted); font-size: 14px;
  cursor: pointer; display: none; padding: 2px 4px; line-height: 1;
}
.search-clear.show { display: block; }
.search-clear:hover { color: var(--text-primary); }

/* Toolbar */
.toolbar {
  padding: 12px 24px; display: flex; align-items: center; gap: 12px;
  border-bottom: 1px solid var(--border-color); flex-wrap: wrap;
}
.search-info {
  font-size: 13px; color: var(--text-secondary);
}
.search-info strong { color: var(--accent-blue); }

/* Loading bar */
.loading-bar-container {
  height: 2px; background: transparent; position: sticky; top: 0; z-index: 99;
}
.loading-bar {
  height: 100%; width: 0; background: var(--accent-blue);
  transition: width 0.3s ease;
}
.loading-bar.active { width: 70%; }
.loading-bar.done { width: 100%; transition: width 0.15s, opacity 0.4s 0.2s; opacity: 0; }

/* Article list */
.article-list {
  padding: 16px 24px; display: flex; flex-direction: column; gap: 12px;
  flex: 1;
}
.article-card {
  background: var(--bg-card); border: 1px solid var(--border-color);
  border-radius: var(--radius); padding: 16px; transition: border-color 0.15s, box-shadow 0.15s;
}
.article-card:hover { border-color: var(--accent-blue); box-shadow: var(--shadow); }
.article-card.highlighted { border-color: var(--accent-blue); box-shadow: 0 0 0 1px var(--accent-blue); }
.article-title {
  font-size: 15px; font-weight: 600; margin-bottom: 6px; line-height: 1.4;
}
.article-title a { color: var(--text-primary); }
.article-title a:hover { color: var(--accent-blue); text-decoration: none; }
.article-meta {
  display: flex; align-items: center; gap: 8px; font-size: 12px;
  color: var(--text-secondary); margin-bottom: 8px; flex-wrap: wrap;
}
.article-meta .tag {
  background: var(--bg-tertiary); padding: 2px 8px; border-radius: 10px;
  font-size: 11px; white-space: nowrap;
}
.article-meta .tag.cat { color: var(--accent-purple); }
.article-meta .tag.region { color: var(--accent-green); }
.article-meta .tag.source { color: var(--accent-orange); }
.rel-time { color: var(--accent-blue); font-weight: 500; }
.article-desc {
  font-size: 13px; color: var(--text-secondary); line-height: 1.6;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Compact mode */
.compact .article-card { padding: 10px 14px; }
.compact .article-title { font-size: 13px; margin-bottom: 4px; }
.compact .article-desc { display: none; }
.compact .article-meta { margin-bottom: 0; font-size: 11px; }

/* Pagination */
.pagination {
  display: flex; justify-content: center; gap: 4px; padding: 20px 24px;
  flex-wrap: wrap;
}
.page-btn {
  min-width: 32px; height: 32px; border: 1px solid var(--border-color);
  background: var(--bg-card); color: var(--text-secondary); border-radius: 6px;
  font-size: 13px; display: flex; align-items: center; justify-content: center;
  transition: all 0.15s; padding: 0 8px;
}
.page-btn:hover { border-color: var(--accent-blue); color: var(--accent-blue); }
.page-btn.active {
  background: var(--accent-blue); color: #fff; border-color: var(--accent-blue); font-weight: 600;
}
.page-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* Skeleton */
.skeleton-card {
  background: var(--bg-card); border: 1px solid var(--border-color);
  border-radius: var(--radius); padding: 16px;
}
.skeleton-line {
  height: 14px; background: var(--bg-tertiary); border-radius: 4px;
  margin-bottom: 10px; animation: pulse 1.5s infinite;
}
.skeleton-line.short { height: 12px; }
.skeleton-line:last-child { margin-bottom: 0; }
@keyframes pulse { 0%,100% { opacity: 0.4; } 50% { opacity: 0.8; } }

/* Empty state */
.empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 60px 24px; text-align: center;
}
.empty-icon { font-size: 48px; margin-bottom: 16px; opacity: 0.6; }
.empty-text { font-size: 16px; font-weight: 600; margin-bottom: 8px; }
.empty-hint { font-size: 13px; color: var(--text-secondary); }

/* Error state */
.error-banner {
  margin: 16px 24px; padding: 12px 16px; background: rgba(248,81,73,0.1);
  border: 1px solid var(--accent-red); border-radius: var(--radius);
  color: var(--accent-red); font-size: 13px; display: none;
}
.error-banner.show { display: block; }

/* Toast */
.toast {
  position: fixed; top: -60px; left: 50%; transform: translateX(-50%);
  background: var(--accent-blue); color: #fff; padding: 10px 20px;
  border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer;
  z-index: 2000; display: flex; align-items: center; gap: 8px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.3); transition: top 0.4s ease;
  white-space: nowrap;
}
.toast.show { top: 16px; }
.toast-dismiss {
  background: none; border: none; color: rgba(255,255,255,0.7);
  font-size: 16px; cursor: pointer; margin-left: 8px; padding: 0 4px;
}
.toast-dismiss:hover { color: #fff; }

/* Footer */
.app-footer {
  text-align: center; padding: 20px 16px; font-size: 12px; color: var(--text-muted);
  border-top: 1px solid var(--border-color); margin-top: 24px;
}
.app-footer a {
  color: var(--accent-blue); text-decoration: none; font-weight: 600;
}
.app-footer a:hover { text-decoration: underline; }

/* Back to top */
.back-top {
  position: fixed; bottom: 24px; right: 24px; width: 40px; height: 40px;
  border-radius: 50%; background: var(--accent-blue); color: #fff; border: none;
  font-size: 18px; display: flex; align-items: center; justify-content: center;
  box-shadow: 0 2px 8px rgba(0,0,0,0.3); opacity: 0; pointer-events: none;
  transition: opacity 0.3s, transform 0.3s; z-index: 500;
}
.back-top.show { opacity: 1; pointer-events: auto; }
.back-top:hover { transform: scale(1.1); }

/* Shortcuts modal */
.modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 3000;
  display: none; align-items: center; justify-content: center;
}
.modal-overlay.show { display: flex; }
.modal {
  background: var(--bg-secondary); border: 1px solid var(--border-color);
  border-radius: 12px; padding: 24px; max-width: 420px; width: 90%;
  box-shadow: 0 8px 24px rgba(0,0,0,0.4);
}
.modal h2 { font-size: 16px; margin-bottom: 16px; }
.modal-close {
  float: right; background: none; border: none; color: var(--text-secondary);
  font-size: 20px; cursor: pointer;
}
.modal-close:hover { color: var(--text-primary); }
.shortcut-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 6px 0; font-size: 13px; border-bottom: 1px solid var(--border-color);
}
.shortcut-row:last-child { border-bottom: none; }
.shortcut-key {
  background: var(--bg-tertiary); border: 1px solid var(--border-color);
  padding: 2px 8px; border-radius: 4px; font-family: monospace; font-size: 12px;
}

/* Feed manager modal */
.feed-modal { max-width: 680px; width: 95%; max-height: 80vh; display: flex; flex-direction: column; }
.feed-add-form { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.feed-input {
  background: var(--bg-primary); border: 1px solid var(--border-color); border-radius: 6px;
  color: var(--text-primary); padding: 8px 12px; font-size: 13px; outline: none;
}
.feed-input:focus { border-color: var(--accent-blue); }
.feed-input::placeholder { color: var(--text-muted); }
.feed-add-form .feed-input { flex: 1; min-width: 180px; }
.feed-cat-input { max-width: 140px; flex: 0 0 140px !important; min-width: 100px !important; }
.feed-add-btn {
  background: var(--accent-blue); color: #fff; border: none; border-radius: 6px;
  padding: 8px 16px; cursor: pointer; font-size: 13px; font-weight: 600; white-space: nowrap;
}
.feed-add-btn:hover { opacity: 0.85; }
.feed-filter-row { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; }
.feed-search { flex: 1; }
.feed-count { font-size: 12px; color: var(--text-secondary); white-space: nowrap; }
.feed-list { overflow-y: auto; flex: 1; max-height: 50vh; }
.feed-item {
  display: flex; align-items: center; gap: 8px; padding: 8px; font-size: 13px;
  border-bottom: 1px solid var(--border-color); transition: background 0.15s;
}
.feed-item:hover { background: var(--bg-tertiary); }
.feed-item.disabled { opacity: 0.45; }
.feed-item-url { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-primary); }
.feed-item-cat { color: var(--accent-blue); font-size: 11px; background: var(--bg-tertiary); padding: 2px 8px; border-radius: 10px; white-space: nowrap; }
.feed-item-btn {
  background: none; border: none; cursor: pointer; font-size: 14px; padding: 2px 6px;
  border-radius: 4px; color: var(--text-secondary);
}
.feed-item-btn:hover { background: var(--bg-tertiary); color: var(--text-primary); }
.feed-item-btn.delete:hover { color: var(--accent-red, #f85149); }

/* Sidebar overlay (mobile) */
.sidebar-overlay {
  display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5);
  z-index: 999;
}
.sidebar-overlay.show { display: block; }

/* Connection status */
.conn-status {
  display: none; align-items: center; gap: 6px; font-size: 12px;
  padding: 4px 10px; border-radius: 12px; background: rgba(248,81,73,0.15);
  color: var(--accent-red);
}
.conn-status.show { display: inline-flex; }
.conn-dot {
  width: 6px; height: 6px; border-radius: 50%; background: var(--accent-red);
  animation: pulse 1.5s infinite;
}

/* Mobile responsive */
@media (max-width: 768px) {
  .menu-btn { display: flex; }
  .sidebar {
    position: fixed; left: -280px; top: 0; bottom: 0; z-index: 1000;
    width: 280px; transition: left 0.3s ease;
  }
  .sidebar.open { left: 0; }
  .sidebar-close { display: block; }
  .header-stats .stat-pill.desktop-only { display: none; }
  .header-title { font-size: 15px; }
  .search-input { width: 140px; }
  .search-input:focus { width: 180px; }
  .article-list { padding: 12px 12px; }
  .toolbar { padding: 10px 12px; }
  .header { padding: 10px 12px; }
}
@media (max-width: 480px) {
  .header-stats { display: none; }
  .search-input { width: 120px; }
  .search-input:focus { width: 150px; }
}
</style>
</head>
<body>
<a href="#main-content" class="skip-link">Skip to content</a>

<div class="layout">
  <!-- Sidebar overlay -->
  <div class="sidebar-overlay" id="sidebarOverlay" aria-hidden="true"></div>

  <!-- Sidebar -->
  <aside class="sidebar" id="sidebar" role="navigation" aria-label="Filters">
    <div class="sidebar-header">
      <h2 data-i18n="filters">Filters</h2>
      <button class="sidebar-close" id="sidebarClose" aria-label="Close filters">&times;</button>
    </div>

    <!-- Categories -->
    <div class="sidebar-section" id="catSection">
      <h3 data-i18n="categories">Categories</h3>
      <div id="catList"></div>
    </div>

    <!-- Regions -->
    <div class="sidebar-section" id="regionSection">
      <h3 data-i18n="regions">Regions</h3>
      <div id="regionList"></div>
    </div>

    <!-- Sources -->
    <div class="sidebar-section" id="sourceSection">
      <h3 data-i18n="sources">Sources</h3>
      <div id="sourceList"></div>
    </div>

    <!-- Date -->
    <div class="sidebar-section">
      <h3 data-i18n="date_range">Date Range</h3>
      <input type="date" class="date-input" id="dateInput" aria-label="Select date">
      <div class="days-chips" id="daysChips"></div>
    </div>
  </aside>

  <!-- Main content -->
  <div class="content" id="contentArea">
    <!-- Header -->
    <header class="header" role="banner">
      <button class="icon-btn menu-btn" id="menuBtn" aria-label="Open menu">&#9776;</button>
      <div class="header-title"><span>MarkStackAI</span> News Intelligence</div>
      <div class="header-stats" id="headerStats">
        <span class="stat-pill"><span class="num" id="statTotal">-</span> <span data-i18n="articles">articles</span></span>
        <span class="stat-pill desktop-only"><span class="num" id="statToday">-</span> <span data-i18n="today">today</span></span>
        <span class="stat-pill desktop-only"><span class="num" id="statFeeds">-</span> <span data-i18n="feeds_healthy">feeds</span></span>
        <span class="stat-pill"><span class="refresh-time" id="statRefresh">-</span></span>
        <span class="conn-status" id="connStatus"><span class="conn-dot"></span><span id="connText">Offline</span></span>
      </div>
      <div class="header-spacer"></div>
      <div class="header-actions">
        <div class="search-wrapper" role="search">
          <input type="text" class="search-input" id="searchInput" placeholder="Search..." aria-label="Search articles">
          <button class="search-clear" id="searchClear" aria-label="Clear search">&times;</button>
        </div>
        <button class="icon-btn" id="viewToggle" aria-label="Toggle compact view" aria-pressed="false" title="Compact view">&#9776;</button>
        <button class="icon-btn" id="themeToggle" aria-label="Toggle theme" aria-pressed="false" title="Toggle theme">&#9790;</button>
        <button class="icon-btn" id="langToggle" aria-label="Toggle language" aria-pressed="false" title="Language">EN</button>
        <button class="icon-btn" id="feedMgrBtn" aria-label="Manage feeds" title="Manage RSS feeds">&#9881;</button>
        <button class="icon-btn" id="shortcutBtn" aria-label="Keyboard shortcuts" title="Shortcuts (?)">?</button>
      </div>
    </header>

    <!-- Loading bar -->
    <div class="loading-bar-container"><div class="loading-bar" id="loadingBar"></div></div>

    <!-- Toolbar / search info -->
    <div class="toolbar" id="toolbar" style="display:none;">
      <div class="search-info" id="searchInfo"></div>
    </div>

    <!-- Error banner -->
    <div class="error-banner" id="errorBanner" role="alert"></div>

    <!-- Toast -->
    <div class="toast" id="newToast" role="alert" aria-live="polite">
      <span>&#128276;</span>
      <span id="toastCount">0</span>
      <span data-i18n="new_articles">new articles</span>
      <button class="toast-dismiss" aria-label="Dismiss">&times;</button>
    </div>

    <!-- Article list -->
    <div class="article-list" id="articleList" role="main" aria-live="polite" aria-label="Articles">
    </div>

    <!-- Pagination -->
    <div class="pagination" id="pagination"></div>

    <!-- Footer -->
    <footer class="app-footer">
      Powered by <a href="https://markstackai.com" target="_blank" rel="noopener noreferrer">MarkStackAI</a>
    </footer>
  </div>
</div>

<!-- Back to top -->
<button class="back-top" id="backTop" aria-label="Back to top">&uarr;</button>

<!-- Shortcuts modal -->
<div class="modal-overlay" id="shortcutsModal" role="dialog" aria-label="Keyboard shortcuts" aria-modal="true">
  <div class="modal">
    <button class="modal-close" id="modalClose" aria-label="Close">&times;</button>
    <h2 data-i18n="shortcuts">Keyboard Shortcuts</h2>
    <div id="shortcutsList"></div>
  </div>
</div>

<!-- Feed Manager Modal -->
<div class="modal-overlay" id="feedModal" role="dialog" aria-label="Feed management" aria-modal="true">
  <div class="modal feed-modal">
    <button class="modal-close" id="feedModalClose" aria-label="Close">&times;</button>
    <!-- Login panel -->
    <div id="feedLoginPanel">
      <h2>Admin Login</h2>
      <p style="font-size:13px;color:var(--text-secondary);margin-bottom:12px;">Enter admin password to manage feeds<br>输入管理员密码以管理数据源</p>
      <div class="feed-add-form">
        <input type="password" id="adminPwdInput" placeholder="Password" class="feed-input" style="flex:1" aria-label="Admin password">
        <button class="feed-add-btn" id="adminLoginBtn">Login</button>
      </div>
      <div id="loginError" style="color:var(--accent-red,#f85149);font-size:12px;margin-top:8px;display:none;"></div>
    </div>
    <!-- Feed management panel (hidden until login) -->
    <div id="feedMgrPanel" style="display:none;">
      <h2 id="feedModalTitle">Manage RSS Feeds</h2>
      <div class="feed-add-form">
        <input type="text" id="feedUrlInput" placeholder="https://example.com/rss.xml" class="feed-input" aria-label="Feed URL">
        <input type="text" id="feedCatInput" placeholder="Category" class="feed-input feed-cat-input" aria-label="Category">
        <button class="feed-add-btn" id="feedAddBtn">+ Add</button>
      </div>
      <div class="feed-filter-row">
        <input type="text" id="feedSearchInput" placeholder="Search feeds..." class="feed-input feed-search" aria-label="Search feeds">
        <span class="feed-count" id="feedCount">0 feeds</span>
        <button class="feed-item-btn" id="adminLogoutBtn" title="Logout" style="font-size:12px;padding:4px 8px;">Logout</button>
      </div>
      <div class="feed-list" id="feedList"></div>
    </div>
  </div>
</div>

<script>
/* ===== i18n ===== */
const I18N = {
  en: {
    filters: 'Filters', categories: 'Categories', regions: 'Regions', sources: 'Sources',
    date_range: 'Date Range', articles: 'articles', today: 'today', all: 'All',
    no_articles: 'No articles found', search: 'Search...', page: 'Page',
    select_all: 'Select All', loading: 'Loading...', just_now: 'just now',
    min_ago: 'm ago', hr_ago: 'h ago', day_ago: 'd ago',
    new_articles: 'new articles', click_to_load: 'Click to load',
    compact: 'Compact', comfortable: 'Comfortable',
    shortcuts: 'Keyboard Shortcuts', search_results: 'results for',
    back_to_top: 'Back to top', offline: 'Connection lost',
    reconnecting: 'Reconnecting...', no_articles_hint: 'Try broadening your filters or adjusting the date range',
    feeds_healthy: 'feeds', open_article: 'Open article'
  },
  zh: {
    filters: '筛选', categories: '分类', regions: '地区', sources: '来源',
    date_range: '日期范围', articles: '篇文章', today: '今天', all: '全部',
    no_articles: '未找到文章', search: '搜索...', page: '页',
    select_all: '全选', loading: '加载中...', just_now: '刚刚',
    min_ago: '分钟前', hr_ago: '小时前', day_ago: '天前',
    new_articles: '条新文章', click_to_load: '点击加载',
    compact: '紧凑', comfortable: '舒适',
    shortcuts: '键盘快捷键', search_results: '的搜索结果',
    back_to_top: '回到顶部', offline: '连接已断开',
    reconnecting: '正在重连...', no_articles_hint: '尝试扩大筛选范围',
    feeds_healthy: '个源', open_article: '打开文章'
  }
};

const SHORTCUTS = [
  ['j', 'Next article'], ['k', 'Previous article'],
  ['o / Enter', 'Open article'], ['r', 'Refresh'],
  ['/', 'Focus search'], ['Escape', 'Close / Clear'],
  ['?', 'Show shortcuts']
];
const SHORTCUTS_ZH = [
  ['j', '下一篇'], ['k', '上一篇'],
  ['o / Enter', '打开文章'], ['r', '刷新'],
  ['/', '搜索'], ['Escape', '关闭 / 清除'],
  ['?', '快捷键帮助']
];

/* ===== State ===== */
const S = {
  lang: localStorage.getItem('ni_lang') || 'en',
  theme: localStorage.getItem('ni_theme') || '',
  category: '', region: '', q: '', days: '', date: '',
  selectedSources: new Set(), sourceSelectAll: true,
  page: 1, limit: 30, total: 0,
  articles: [], filters: null, stats: null,
  loading: false, compact: localStorage.getItem('ni_compact') === '1',
  highlightIdx: -1, wsConnected: false, prevTotal: 0
};

/* ===== Utility ===== */
function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function t(key) { return (I18N[S.lang] && I18N[S.lang][key]) || I18N.en[key] || key; }

function relTime(ts) {
  if (!ts) return '';
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 0) return '';
  if (diff < 60) return t('just_now');
  if (diff < 3600) return Math.floor(diff / 60) + t('min_ago');
  if (diff < 86400) return Math.floor(diff / 3600) + t('hr_ago');
  if (diff < 604800) return Math.floor(diff / 86400) + t('day_ago');
  return '';
}

function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const pad = n => String(n).padStart(2, '0');
  return d.getUTCFullYear() + '-' + pad(d.getUTCMonth()+1) + '-' + pad(d.getUTCDate()) +
    ' ' + pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + ' UTC';
}

function refreshAgo(ts) {
  if (!ts) return { text: '-', cls: '' };
  const diff = Math.floor(Date.now() / 1000 - ts);
  let text, cls;
  if (diff < 60) { text = '<1m'; cls = 'fresh'; }
  else if (diff < 300) { text = Math.floor(diff/60) + 'm'; cls = 'fresh'; }
  else if (diff < 900) { text = Math.floor(diff/60) + 'm'; cls = 'stale'; }
  else { text = Math.floor(diff/60) + 'm'; cls = 'old'; }
  return { text, cls };
}

async function apiFetch(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ===== URL State ===== */
function syncURL() {
  const p = new URLSearchParams();
  if (S.category) p.set('category', S.category);
  if (S.region) p.set('region', S.region);
  if (S.q) p.set('q', S.q);
  if (S.days) p.set('days', S.days);
  if (S.date) p.set('date', S.date);
  if (S.selectedSources.size > 0 && !S.sourceSelectAll) p.set('sources', [...S.selectedSources].join(','));
  if (S.page > 1) p.set('page', String(S.page));
  const qs = p.toString();
  history.replaceState(null, '', qs ? '?' + qs : location.pathname);
}

function loadURL() {
  const p = new URLSearchParams(location.search);
  S.category = p.get('category') || '';
  S.region = p.get('region') || '';
  S.q = p.get('q') || '';
  S.days = p.get('days') || '';
  S.date = p.get('date') || '';
  S.page = parseInt(p.get('page')) || 1;
  const srcStr = p.get('sources');
  if (srcStr) {
    S.sourceSelectAll = false;
    S.selectedSources = new Set(srcStr.split(','));
  }
}

/* ===== DOM refs ===== */
const $ = id => document.getElementById(id);
const contentArea = $('contentArea');
const articleList = $('articleList');
const pagination = $('pagination');
const loadingBar = $('loadingBar');
const errorBanner = $('errorBanner');
const searchInput = $('searchInput');
const searchClear = $('searchClear');
const toolbar = $('toolbar');
const searchInfo = $('searchInfo');
const sidebar = $('sidebar');
const sidebarOverlay = $('sidebarOverlay');
const newToast = $('newToast');
const backTop = $('backTop');
const shortcutsModal = $('shortcutsModal');
const connStatus = $('connStatus');
const connText = $('connText');
const dateInput = $('dateInput');

/* ===== Loading bar ===== */
function showLoading() {
  S.loading = true;
  loadingBar.className = 'loading-bar active';
}
function hideLoading() {
  S.loading = false;
  loadingBar.className = 'loading-bar done';
  setTimeout(() => { loadingBar.className = 'loading-bar'; loadingBar.style.width = ''; }, 600);
}

function showError(msg) {
  errorBanner.textContent = msg;
  errorBanner.classList.add('show');
  setTimeout(() => errorBanner.classList.remove('show'), 8000);
}

/* ===== Render functions ===== */
function renderSkeleton() {
  let h = '';
  for (let i = 0; i < 5; i++) {
    h += '<div class="skeleton-card">' +
      '<div class="skeleton-line" style="width:80%"></div>' +
      '<div class="skeleton-line short" style="width:40%"></div>' +
      '<div class="skeleton-line" style="width:60%"></div>' +
      '</div>';
  }
  articleList.innerHTML = h;
  pagination.innerHTML = '';
}

function renderFilters() {
  if (!S.filters) return;
  const { categories, regions, sources } = S.filters;

  // Categories
  let ch = '<div class="filter-item' + (!S.category ? ' active' : '') + '" data-filter="category" data-value="" role="button" tabindex="0" aria-label="' + esc(t('all')) + ' ' + esc(t('categories')) + '">' +
    '<span>' + esc(t('all')) + '</span></div>';
  (categories || []).forEach(c => {
    ch += '<div class="filter-item' + (S.category === c.name ? ' active' : '') + '" data-filter="category" data-value="' + esc(c.name) + '" role="button" tabindex="0" aria-label="' + esc(c.name) + ' ' + c.count + ' ' + esc(t('articles')) + '">' +
      '<span>' + esc(c.name) + '</span><span class="count">' + c.count + '</span></div>';
  });
  $('catList').innerHTML = ch;

  // Regions
  let rh = '<div class="filter-item' + (!S.region ? ' active' : '') + '" data-filter="region" data-value="" role="button" tabindex="0" aria-label="' + esc(t('all')) + ' ' + esc(t('regions')) + '">' +
    '<span>' + esc(t('all')) + '</span></div>';
  (regions || []).forEach(r => {
    rh += '<div class="filter-item' + (S.region === r.name ? ' active' : '') + '" data-filter="region" data-value="' + esc(r.name) + '" role="button" tabindex="0" aria-label="' + esc(r.name) + ' ' + r.count + ' ' + esc(t('articles')) + '">' +
      '<span>' + esc(r.name) + '</span><span class="count">' + r.count + '</span></div>';
  });
  $('regionList').innerHTML = rh;

  // Sources
  let sh = '<div class="select-all-row" id="selectAllSrc" role="button" tabindex="0" aria-label="' + esc(t('select_all')) + '">' +
    '<span class="source-cb">' + (S.sourceSelectAll ? '&#10003;' : '') + '</span>' +
    '<span>' + esc(t('select_all')) + '</span></div>';
  (sources || []).forEach(s => {
    const checked = S.sourceSelectAll || S.selectedSources.has(s.name);
    sh += '<label class="source-item' + (checked ? ' checked' : '') + '" data-source="' + esc(s.name) + '" role="button" tabindex="0" aria-label="' + esc(s.name) + ' ' + s.count + ' ' + esc(t('articles')) + '">' +
      '<span class="source-cb">' + (checked ? '&#10003;' : '') + '</span>' +
      '<span class="source-name">' + esc(s.name) + '</span>' +
      '<span class="source-cnt">' + s.count + '</span></label>';
  });
  $('sourceList').innerHTML = sh;

  // Days chips
  const daysOpts = [1, 3, 7, 14, 30];
  let dh = '';
  daysOpts.forEach(d => {
    dh += '<button class="day-chip' + (S.days === String(d) ? ' active' : '') + '" data-days="' + d + '" aria-label="' + d + ' days" aria-pressed="' + (S.days === String(d)) + '">' + d + 'd</button>';
  });
  $('daysChips').innerHTML = dh;

  if (S.date) dateInput.value = S.date;
}

function renderArticles() {
  if (!S.articles || S.articles.length === 0) {
    articleList.innerHTML = '<div class="empty">' +
      '<div class="empty-icon">&#128240;</div>' +
      '<div class="empty-text" data-i18n="no_articles">' + esc(t('no_articles')) + '</div>' +
      '<div class="empty-hint" data-i18n="no_articles_hint">' + esc(t('no_articles_hint')) + '</div></div>';
    pagination.innerHTML = '';
    return;
  }

  let h = '';
  S.articles.forEach((a, i) => {
    const rt = relTime(a.published);
    const at = fmtTime(a.published);
    h += '<div class="article-card' + (i === S.highlightIdx ? ' highlighted' : '') + '" data-idx="' + i + '" data-url="' + esc(a.link) + '">' +
      '<div class="article-title"><a href="' + esc(a.link) + '" target="_blank" rel="noopener noreferrer" aria-label="' + esc(t('open_article')) + ': ' + esc(a.title) + '">' + esc(a.title) + '</a></div>' +
      '<div class="article-meta">';
    if (a.category) h += '<span class="tag cat">' + esc(a.category) + '</span>';
    if (a.region) h += '<span class="tag region">' + esc(a.region) + '</span>';
    if (a.source) h += '<span class="tag source">' + esc(a.source) + '</span>';
    if (rt) h += '<span class="rel-time">' + esc(rt) + '</span><span>&middot;</span>';
    h += '<span>' + esc(at) + '</span></div>';
    if (a.description) h += '<div class="article-desc">' + esc(a.description) + '</div>';
    h += '</div>';
  });
  articleList.innerHTML = h;

  // Search info
  if (S.q) {
    toolbar.style.display = 'flex';
    searchInfo.innerHTML = '<strong>' + S.total + '</strong> ' + esc(t('search_results')) + ' <strong>&#39;' + esc(S.q) + '&#39;</strong>';
  } else {
    toolbar.style.display = 'none';
  }

  renderPagination();

  if (S.compact) articleList.classList.add('compact');
  else articleList.classList.remove('compact');
}

function renderPagination() {
  const pages = Math.ceil(S.total / S.limit);
  if (pages <= 1) { pagination.innerHTML = ''; return; }
  let h = '';
  h += '<button class="page-btn" data-page="' + (S.page - 1) + '"' + (S.page <= 1 ? ' disabled' : '') + ' aria-label="Previous page">&laquo;</button>';
  let start = Math.max(1, S.page - 3);
  let end = Math.min(pages, S.page + 3);
  if (start > 1) {
    h += '<button class="page-btn" data-page="1" aria-label="Page 1">1</button>';
    if (start > 2) h += '<span class="page-btn" style="border:none;cursor:default">&hellip;</span>';
  }
  for (let i = start; i <= end; i++) {
    h += '<button class="page-btn' + (i === S.page ? ' active' : '') + '" data-page="' + i + '" aria-label="Page ' + i + '"' +
      (i === S.page ? ' aria-current="page"' : '') + '>' + i + '</button>';
  }
  if (end < pages) {
    if (end < pages - 1) h += '<span class="page-btn" style="border:none;cursor:default">&hellip;</span>';
    h += '<button class="page-btn" data-page="' + pages + '" aria-label="Page ' + pages + '">' + pages + '</button>';
  }
  h += '<button class="page-btn" data-page="' + (S.page + 1) + '"' + (S.page >= pages ? ' disabled' : '') + ' aria-label="Next page">&raquo;</button>';
  pagination.innerHTML = h;
}

function renderStats() {
  if (!S.stats) return;
  $('statTotal').textContent = S.stats.total || 0;
  $('statToday').textContent = S.stats.today_count || 0;
  $('statFeeds').textContent = S.stats.total_feeds || S.stats.sources || 0;
  const r = refreshAgo(S.stats.last_refresh);
  const el = $('statRefresh');
  el.textContent = r.text;
  el.className = 'refresh-time ' + r.cls;
}

function renderShortcuts() {
  const list = S.lang === 'zh' ? SHORTCUTS_ZH : SHORTCUTS;
  let h = '';
  list.forEach(s => {
    h += '<div class="shortcut-row"><span>' + esc(s[1]) + '</span><span class="shortcut-key">' + esc(s[0]) + '</span></div>';
  });
  $('shortcutsList').innerHTML = h;
}

function applyI18n() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    el.textContent = t(key);
  });
  searchInput.placeholder = t('search');
  $('langToggle').textContent = S.lang === 'en' ? 'EN' : '中';
  renderShortcuts();
}

/* ===== API calls ===== */
async function loadArticles() {
  showLoading();
  S.highlightIdx = -1;
  try {
    const p = new URLSearchParams();
    if (S.category) p.set('category', S.category);
    if (S.region) p.set('region', S.region);
    if (S.q) p.set('q', S.q);
    if (S.days) p.set('days', S.days);
    if (S.date) p.set('date', S.date);
    if (S.selectedSources.size > 0 && !S.sourceSelectAll) p.set('sources', [...S.selectedSources].join(','));
    p.set('limit', String(S.limit));
    p.set('offset', String((S.page - 1) * S.limit));
    const data = await apiFetch('/api/articles?' + p.toString());
    S.articles = data.data || [];
    S.total = data.total || 0;
    renderArticles();
    syncURL();
    contentArea.scrollTop = 0;
  } catch (e) {
    showError('Failed to load articles: ' + e.message);
  } finally {
    hideLoading();
  }
}

async function loadFilters() {
  try {
    const p = new URLSearchParams();
    if (S.days) p.set('days', S.days);
    if (S.date) p.set('date', S.date);
    const data = await apiFetch('/api/filters?' + p.toString());
    S.filters = data;
    renderFilters();
  } catch (e) {
    showError('Failed to load filters: ' + e.message);
  }
}

async function loadStats() {
  try {
    const data = await apiFetch('/api/stats');
    S.stats = data;
    S.prevTotal = data.total || 0;
    renderStats();
  } catch (e) {
    /* silent */
  }
}

/* ===== WebSocket ===== */
let ws = null;
let wsRetry = 0;
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws');
  ws.onopen = () => {
    S.wsConnected = true;
    wsRetry = 0;
    connStatus.classList.remove('show');
  };
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'update' && msg.stats) {
        const newTotal = msg.stats.total || 0;
        const diff = newTotal - S.prevTotal;
        S.stats = msg.stats;
        renderStats();
        if (diff > 0 && (S.page > 1 || contentArea.scrollTop > 300)) {
          showToast(diff);
        } else if (diff > 0 && S.page === 1) {
          loadArticles();
          loadFilters();
        }
        S.prevTotal = newTotal;
      }
    } catch (e) {}
  };
  ws.onclose = () => {
    S.wsConnected = false;
    connStatus.classList.add('show');
    connText.textContent = t('reconnecting');
    const delay = Math.min(30000, 1000 * Math.pow(2, wsRetry));
    wsRetry++;
    setTimeout(connectWS, delay);
  };
  ws.onerror = () => { ws.close(); };
}

/* ===== Toast ===== */
function showToast(count) {
  $('toastCount').textContent = count;
  newToast.classList.add('show');
}
function hideToast() { newToast.classList.remove('show'); }
function goToLatest() {
  hideToast();
  S.page = 1;
  loadArticles();
  loadFilters();
  loadStats();
}

/* ===== Theme ===== */
function setTheme(th) {
  document.documentElement.setAttribute('data-theme', th);
  S.theme = th;
  $('themeToggle').textContent = th === 'dark' ? '\u263E' : '\u2600';
  $('themeToggle').setAttribute('aria-pressed', th === 'dark' ? 'true' : 'false');
}
function toggleTheme() {
  const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  setTheme(next);
  localStorage.setItem('ni_theme', next);
}

function initTheme() {
  const saved = localStorage.getItem('ni_theme');
  if (saved) { setTheme(saved); return; }
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  setTheme(prefersDark ? 'dark' : 'light');
}

window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
  if (!localStorage.getItem('ni_theme')) setTheme(e.matches ? 'dark' : 'light');
});

/* ===== Language ===== */
function toggleLang() {
  S.lang = S.lang === 'en' ? 'zh' : 'en';
  localStorage.setItem('ni_lang', S.lang);
  $('langToggle').setAttribute('aria-pressed', S.lang === 'zh' ? 'true' : 'false');
  applyI18n();
  renderFilters();
  renderArticles();
}

/* ===== Compact ===== */
function toggleCompact() {
  S.compact = !S.compact;
  localStorage.setItem('ni_compact', S.compact ? '1' : '0');
  $('viewToggle').setAttribute('aria-pressed', String(S.compact));
  $('viewToggle').textContent = S.compact ? '\u229E' : '\u2630';
  if (S.compact) articleList.classList.add('compact');
  else articleList.classList.remove('compact');
}

/* ===== Sidebar (mobile) ===== */
function openSidebar() {
  sidebar.classList.add('open');
  sidebarOverlay.classList.add('show');
  sidebarOverlay.setAttribute('aria-hidden', 'false');
}
function closeSidebar() {
  sidebar.classList.remove('open');
  sidebarOverlay.classList.remove('show');
  sidebarOverlay.setAttribute('aria-hidden', 'true');
}

/* ===== Search ===== */
let searchTimer = null;
function onSearchInput() {
  const v = searchInput.value.trim();
  searchClear.classList.toggle('show', v.length > 0);
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    S.q = v;
    S.page = 1;
    loadArticles();
  }, 400);
}
function clearSearch() {
  searchInput.value = '';
  searchClear.classList.remove('show');
  S.q = '';
  S.page = 1;
  loadArticles();
}

/* ===== Event Delegation ===== */

// Sidebar filter clicks (categories & regions)
sidebar.addEventListener('click', (e) => {
  const item = e.target.closest('.filter-item');
  if (item) {
    const type = item.getAttribute('data-filter');
    const val = item.getAttribute('data-value');
    if (type === 'category') S.category = val;
    else if (type === 'region') S.region = val;
    S.page = 1;
    loadArticles();
    loadFilters();
    closeSidebar();
    return;
  }

  const srcItem = e.target.closest('.source-item[data-source]');
  if (srcItem) {
    const name = srcItem.getAttribute('data-source');
    toggleSource(name);
    return;
  }

  const selectAll = e.target.closest('#selectAllSrc, .select-all-row');
  if (selectAll) {
    S.sourceSelectAll = true;
    S.selectedSources.clear();
    S.page = 1;
    renderFilters();
    loadArticles();
    return;
  }

  const dayChip = e.target.closest('.day-chip');
  if (dayChip) {
    const d = dayChip.getAttribute('data-days');
    S.days = S.days === d ? '' : d;
    S.date = '';
    dateInput.value = '';
    S.page = 1;
    loadArticles();
    loadFilters();
    return;
  }
});

// Keyboard for sidebar items
sidebar.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') {
    const item = e.target.closest('.filter-item, .source-item[data-source], .select-all-row, .day-chip');
    if (item) { e.preventDefault(); item.click(); }
  }
});

function toggleSource(name) {
  if (S.sourceSelectAll) {
    S.sourceSelectAll = false;
    S.selectedSources.clear();
    if (S.filters && S.filters.sources) {
      S.filters.sources.forEach(s => S.selectedSources.add(s.name));
    }
  }
  if (S.selectedSources.has(name)) S.selectedSources.delete(name);
  else S.selectedSources.add(name);
  if (S.filters && S.filters.sources && S.selectedSources.size === S.filters.sources.length) {
    S.sourceSelectAll = true;
    S.selectedSources.clear();
  }
  if (S.selectedSources.size === 0 && !S.sourceSelectAll) {
    S.sourceSelectAll = true;
  }
  S.page = 1;
  renderFilters();
  loadArticles();
}

// Date input
dateInput.addEventListener('change', () => {
  S.date = dateInput.value || '';
  S.days = '';
  S.page = 1;
  loadArticles();
  loadFilters();
});

// Pagination
pagination.addEventListener('click', (e) => {
  const btn = e.target.closest('.page-btn[data-page]');
  if (btn && !btn.disabled) {
    S.page = parseInt(btn.getAttribute('data-page'));
    loadArticles();
  }
});

// Header buttons
$('themeToggle').addEventListener('click', toggleTheme);
$('langToggle').addEventListener('click', toggleLang);
$('viewToggle').addEventListener('click', toggleCompact);
$('menuBtn').addEventListener('click', openSidebar);
$('sidebarClose').addEventListener('click', closeSidebar);
sidebarOverlay.addEventListener('click', closeSidebar);
searchInput.addEventListener('input', onSearchInput);
searchClear.addEventListener('click', clearSearch);
$('shortcutBtn').addEventListener('click', () => { shortcutsModal.classList.add('show'); });
$('modalClose').addEventListener('click', () => { shortcutsModal.classList.remove('show'); });
shortcutsModal.addEventListener('click', (e) => { if (e.target === shortcutsModal) shortcutsModal.classList.remove('show'); });

// Toast
newToast.addEventListener('click', (e) => {
  if (e.target.closest('.toast-dismiss')) { hideToast(); return; }
  goToLatest();
});

// Back to top
backTop.addEventListener('click', () => { contentArea.scrollTop = 0; });
contentArea.addEventListener('scroll', () => {
  backTop.classList.toggle('show', contentArea.scrollTop > 500);
});

/* ===== Keyboard Shortcuts ===== */
document.addEventListener('keydown', (e) => {
  // Ignore when typing in input
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) {
    if (e.key === 'Escape') {
      clearSearch();
      searchInput.blur();
      closeSidebar();
    }
    return;
  }

  if (e.key === 'j') {
    e.preventDefault();
    const cards = articleList.querySelectorAll('.article-card');
    if (cards.length === 0) return;
    S.highlightIdx = Math.min(S.highlightIdx + 1, cards.length - 1);
    highlightCard(cards);
  } else if (e.key === 'k') {
    e.preventDefault();
    const cards = articleList.querySelectorAll('.article-card');
    if (cards.length === 0) return;
    S.highlightIdx = Math.max(S.highlightIdx - 1, 0);
    highlightCard(cards);
  } else if (e.key === 'o' || e.key === 'Enter') {
    if (S.highlightIdx >= 0) {
      const card = articleList.querySelector('.article-card[data-idx="' + S.highlightIdx + '"]');
      if (card) {
        const url = card.getAttribute('data-url');
        if (url) window.open(url, '_blank', 'noopener');
      }
    }
  } else if (e.key === 'r') {
    e.preventDefault();
    loadArticles();
    loadFilters();
    loadStats();
  } else if (e.key === '/') {
    e.preventDefault();
    searchInput.focus();
  } else if (e.key === 'Escape') {
    if (shortcutsModal.classList.contains('show')) {
      shortcutsModal.classList.remove('show');
    } else {
      closeSidebar();
      hideToast();
    }
  } else if (e.key === '?') {
    shortcutsModal.classList.add('show');
  }
});

function highlightCard(cards) {
  cards.forEach(c => c.classList.remove('highlighted'));
  if (S.highlightIdx >= 0 && S.highlightIdx < cards.length) {
    cards[S.highlightIdx].classList.add('highlighted');
    cards[S.highlightIdx].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

/* ===== Feed Manager (with admin auth) ===== */
let feedData = [];
const feedModal = $('feedModal');
let adminToken = sessionStorage.getItem('ni_admin_token') || '';

function authHeaders() {
  return { 'Authorization': 'Bearer ' + adminToken, 'Content-Type': 'application/json' };
}

function showFeedPanel(loggedIn) {
  $('feedLoginPanel').style.display = loggedIn ? 'none' : 'block';
  $('feedMgrPanel').style.display = loggedIn ? 'block' : 'none';
  $('loginError').style.display = 'none';
}

async function adminLogin() {
  const pwd = $('adminPwdInput').value.trim();
  if (!pwd) return;
  try {
    const r = await fetch('/api/admin/login', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({password: pwd})
    });
    const data = await r.json();
    if (!r.ok) {
      $('loginError').textContent = data.error || 'Login failed';
      $('loginError').style.display = 'block';
      return;
    }
    adminToken = data.token;
    sessionStorage.setItem('ni_admin_token', adminToken);
    showFeedPanel(true);
    loadFeeds();
  } catch (e) {
    $('loginError').textContent = 'Network error';
    $('loginError').style.display = 'block';
  }
}

function adminLogout() {
  adminToken = '';
  sessionStorage.removeItem('ni_admin_token');
  $('adminPwdInput').value = '';
  showFeedPanel(false);
}

async function loadFeeds() {
  try {
    const r = await fetch('/api/feeds', { headers: authHeaders() });
    if (r.status === 401) { adminLogout(); return; }
    const res = await r.json();
    feedData = res.feeds || [];
    renderFeeds();
  } catch (e) { showError('Failed to load feeds: ' + e.message); }
}

function renderFeeds() {
  const q = ($('feedSearchInput').value || '').toLowerCase();
  const filtered = q ? feedData.filter(f => f.url.toLowerCase().includes(q) || f.category.toLowerCase().includes(q)) : feedData;
  $('feedCount').textContent = feedData.length + ' feeds' + (q ? ' (' + filtered.length + ' shown)' : '');
  if (filtered.length === 0) {
    $('feedList').innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted)">No feeds found</div>';
    return;
  }
  let h = '';
  filtered.forEach(f => {
    const cls = f.enabled ? '' : ' disabled';
    const toggleIcon = f.enabled ? '&#9724;' : '&#9654;';
    const toggleTitle = f.enabled ? 'Disable' : 'Enable';
    h += '<div class="feed-item' + cls + '" data-id="' + f.id + '">' +
      '<span class="feed-item-url" title="' + esc(f.url) + '">' + esc(f.url) + '</span>' +
      '<span class="feed-item-cat">' + esc(f.category) + '</span>' +
      '<button class="feed-item-btn toggle" title="' + toggleTitle + '" data-action="toggle" data-id="' + f.id + '">' + toggleIcon + '</button>' +
      '<button class="feed-item-btn delete" title="Delete" data-action="delete" data-id="' + f.id + '">&times;</button>' +
      '</div>';
  });
  $('feedList').innerHTML = h;
}

async function addFeed() {
  const url = $('feedUrlInput').value.trim();
  const cat = $('feedCatInput').value.trim() || 'Other';
  if (!url) return;
  try {
    const r = await fetch('/api/feeds', {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({url, category: cat})
    });
    const data = await r.json();
    if (r.status === 401) { adminLogout(); return; }
    if (!r.ok) { showError(data.error || 'Failed'); return; }
    $('feedUrlInput').value = '';
    $('feedCatInput').value = '';
    loadFeeds();
  } catch (e) { showError('Failed: ' + e.message); }
}

async function feedAction(action, id) {
  try {
    const method = action === 'delete' ? 'DELETE' : 'PUT';
    const r = await fetch('/api/feeds/' + id, { method, headers: authHeaders() });
    if (r.status === 401) { adminLogout(); return; }
    if (!r.ok) { const d = await r.json(); showError(d.error || 'Failed'); return; }
    loadFeeds();
  } catch (e) { showError('Failed: ' + e.message); }
}

$('feedMgrBtn').addEventListener('click', () => {
  feedModal.classList.add('show');
  if (adminToken) { showFeedPanel(true); loadFeeds(); }
  else { showFeedPanel(false); setTimeout(() => $('adminPwdInput').focus(), 100); }
});
$('feedModalClose').addEventListener('click', () => { feedModal.classList.remove('show'); });
feedModal.addEventListener('click', (e) => { if (e.target === feedModal) feedModal.classList.remove('show'); });
$('adminLoginBtn').addEventListener('click', adminLogin);
$('adminPwdInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') adminLogin(); });
$('adminLogoutBtn').addEventListener('click', adminLogout);
$('feedAddBtn').addEventListener('click', addFeed);
$('feedUrlInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') addFeed(); });
$('feedSearchInput').addEventListener('input', renderFeeds);
$('feedList').addEventListener('click', (e) => {
  const btn = e.target.closest('.feed-item-btn');
  if (!btn) return;
  const action = btn.getAttribute('data-action');
  const id = btn.getAttribute('data-id');
  if (action === 'delete') {
    if (confirm('Delete this feed?')) feedAction('delete', id);
  } else if (action === 'toggle') {
    feedAction('toggle', id);
  }
});

/* ===== Refresh timer ===== */
setInterval(() => { if (S.stats) renderStats(); }, 30000);

/* ===== Init ===== */
function init() {
  initTheme();
  loadURL();

  // Apply loaded state to UI
  if (S.q) { searchInput.value = S.q; searchClear.classList.add('show'); }
  if (S.compact) {
    articleList.classList.add('compact');
    $('viewToggle').textContent = '\u229E';
    $('viewToggle').setAttribute('aria-pressed', 'true');
  }

  applyI18n();
  renderSkeleton();

  // Load data
  Promise.all([loadStats(), loadFilters()]).then(() => {
    loadArticles();
  });

  connectWS();
}

init();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the News Intelligence web server."""
    parser = argparse.ArgumentParser(description="News Intelligence")
    parser.add_argument("--port", type=int, default=37378)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--db", type=str, default="data/rss_news.db")
    args = parser.parse_args()

    app = create_app(args.db)
    logger.info("News Intelligence starting on http://%s:%d", args.host, args.port)
    logger.info("Database: %s", args.db)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
