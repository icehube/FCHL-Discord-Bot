# bot.py
import os
import re
import json
import hashlib
import asyncio
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import feedparser
import discord
from discord import app_commands
from discord.ext import tasks

# -----------------------------
# Config via environment vars
# -----------------------------
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

PLAYERS_JSON = Path(os.getenv("PLAYERS_JSON", "players.json"))
DISCORD_JSON = Path(os.getenv("DISCORD_JSON", "Discord.json"))

DEFAULT_FEEDS = [
    "https://www.espn.com/espn/rss/nhl/news",
    "https://api.foxsports.com/v2/content/optimized-rss?partnerKey=MB0Wehpmuj2lUhuRhQaafhBjAJqaPU244mlTDK1i&size=50&tags=fs/nhl",
    "https://www.rotowire.com/rss/news.php?sport=NHL",
]
RSS_FEEDS = [u.strip() for u in os.getenv("RSS_FEEDS", ",".join(DEFAULT_FEEDS)).split(",") if u.strip()]

POLL_EVERY_SECONDS = int(os.getenv("POLL_EVERY_SECONDS", "600"))   # 10 minutes
POST_LOOKBACK_DAYS = int(os.getenv("POST_LOOKBACK_DAYS", "2"))     # ignore older than N days

# -----------------------------
# Discord client
# -----------------------------
intents = discord.Intents.default()
intents.members = False  # keep on for graceful username fallback
client = discord.Client(
    intents=intents,
    allowed_mentions=discord.AllowedMentions(roles=True, users=True, everyone=False)
)
tree = app_commands.CommandTree(client)

# -----------------------------
# Local dedupe (SQLite)
# -----------------------------
SEEN_DB = Path("seen.db")

def seen_db_init():
    con = sqlite3.connect(SEEN_DB)
    with con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS news_seen (
                player_name  TEXT NOT NULL,
                link_hash    TEXT NOT NULL,
                published_at TEXT,
                PRIMARY KEY (player_name, link_hash)
            )
        """)
    con.close()

def link_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()

def is_seen(player_name: str, url: str) -> bool:
    h = link_hash(url)
    con = sqlite3.connect(SEEN_DB)
    cur = con.cursor()
    cur.execute("SELECT 1 FROM news_seen WHERE player_name=? AND link_hash=? LIMIT 1", (player_name, h))
    row = cur.fetchone()
    con.close()
    return row is not None

def mark_seen(player_name: str, url: str, published_at: Optional[datetime]):
    h = link_hash(url)
    con = sqlite3.connect(SEEN_DB)
    with con:
        con.execute(
            "INSERT OR IGNORE INTO news_seen (player_name, link_hash, published_at) VALUES (?,?,?)",
            (player_name, h, published_at.isoformat() if published_at else None)
        )
    con.close()

# -----------------------------
# Data loading (your JSON files)
# -----------------------------
def load_players(path: Path) -> List[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("players.json must be a JSON array.")
    return data

def to_int_or_none(val) -> Optional[int]:
    try:
        if val is None or val == "":
            return None
        return int(val)
    except Exception:
        return None

def load_discord_map(path: Path) -> Dict[int, dict]:
    """
    Discord.json supports:
      {
        "TEAMCODE": {
          "id": 9,
          "name": "Team Name",
          "discord_user_id": 123456789012345678,   # optional
          "discord_role_id": 234567890123456789,   # optional
          "discord": "legacyUsername"              # optional fallback
        }, ...
      }
    Returned as { team_id: {...} } keyed by numeric id, plus a helper index by code and name.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    by_id: Dict[int, dict] = {}
    for code, obj in raw.items():
        tid = int(obj["id"])
        by_id[tid] = {
            "code": code,
            "name": obj.get("name", f"Team {tid}"),
            "discord_user_id": to_int_or_none(obj.get("discord_user_id")),
            "discord_role_id": to_int_or_none(obj.get("discord_role_id")),
            "discord_username": obj.get("discord", ""),  # legacy fallback
        }
    return by_id

def build_roster(players: List[dict], teams_by_id: Dict[int, dict]) -> Dict[str, dict]:
    """
    Returns:
      {
        "Connor McDavid": {
           "teams": [ {team_id, team_name, code, discord_user_id, discord_role_id, discord_username} ]
        },
        ...
      }
    Only includes players with activeteam > 0 that exists in Discord.json.
    """
    roster: Dict[str, dict] = {}
    for p in players:
        name = (p.get("name") or "").strip()
        team_id = p.get("activeteam")
        if not name or not isinstance(team_id, int) or team_id <= 0:
            continue
        tinfo = teams_by_id.get(team_id)
        if not tinfo:
            continue
        roster.setdefault(name, {"teams": []})
        roster[name]["teams"].append({
            "team_id": team_id,
            "team_name": tinfo["name"],
            "code": tinfo["code"],
            "discord_user_id": tinfo.get("discord_user_id"),
            "discord_role_id": tinfo.get("discord_role_id"),
            "discord_username": tinfo.get("discord_username", "")
        })
    return roster

def index_teams_by_code_and_name(teams_by_id: Dict[int, dict]) -> Tuple[Dict[str, int], Dict[str, int]]:
    code_index: Dict[str, int] = {}
    name_index: Dict[str, int] = {}
    for tid, info in teams_by_id.items():
        code_index[info["code"].lower()] = tid
        name_index[info["name"].lower()] = tid
    return code_index, name_index

# -----------------------------
# Name matching
# -----------------------------
def compile_name_patterns(player_names: List[str]) -> Dict[str, re.Pattern]:
    pats = {}
    for nm in player_names:
        pats[nm] = re.compile(rf"\b{re.escape(nm)}\b", re.IGNORECASE)
    return pats

# -----------------------------
# Discord helpers (target building)
# -----------------------------
def sanitize_username(u: str) -> str:
    return (u or "").strip()

async def resolve_member_mention(guild: discord.Guild, username: str) -> Optional[str]:
    """Best-effort username -> mention (only used if IDs are missing)."""
    username = sanitize_username(username)
    if not username or not guild:
        return None

    # exact match
    for m in guild.members:
        if m.name == username or getattr(m, "global_name", None) == username:
            return m.mention
    # loose startswith
    lower = username.lower()
    for m in guild.members:
        if m.name.lower().startswith(lower):
            return m.mention
        gn = getattr(m, "global_name", None)
        if gn and gn.lower().startswith(lower):
            return m.mention
    return None

def mention_for_role(role_id: Optional[int]) -> Optional[str]:
    return f"<@&{int(role_id)}>" if role_id else None

def mention_for_user(user_id: Optional[int]) -> Optional[str]:
    return f"<@{int(user_id)}>" if user_id else None

async def build_targets_for_player(guild: discord.Guild, player_name: str, roster_map: Dict[str, dict]) -> List[str]:
    info = roster_map.get(player_name)
    if not info:
        return []
    targets: List[str] = []

    for t in info["teams"]:
        role_id = t.get("discord_role_id")
        user_id = t.get("discord_user_id")
        legacy_username = t.get("discord_username", "")

        m = mention_for_role(role_id) or mention_for_user(user_id)
        if m:
            targets.append(m)
            continue

        # last resort: try resolving username to a Member
        mention = await resolve_member_mention(guild, legacy_username) if legacy_username else None
        if mention:
            targets.append(mention)
        else:
            # readable, non-pinging fallback
            if legacy_username:
                targets.append(f"**{t['team_name']}** (@{legacy_username})")
            else:
                targets.append(f"**{t['team_name']}**")

    # de-dupe while preserving order
    seen = set()
    deduped = []
    for x in targets:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return deduped

def all_team_mentions(teams_by_id: Dict[int, dict]) -> Dict[int, str]:
    """Map team_id -> mention string or readable fallback."""
    out: Dict[int, str] = {}
    for tid, t in teams_by_id.items():
        m = mention_for_role(t.get("discord_role_id")) or mention_for_user(t.get("discord_user_id"))
        if m:
            out[tid] = m
        else:
            usern = t.get("discord_username") or ""
            out[tid] = f"**{t['name']}**" + (f" (@{usern})" if usern else "")
    return out

# -----------------------------
# Feed helpers
# -----------------------------
def parse_published(entry) -> Optional[datetime]:
    pp = getattr(entry, "published_parsed", None)
    if pp:
        return datetime(*pp[:6], tzinfo=timezone.utc)
    return None

async def post_item(channel: discord.TextChannel,
                    title: str, url: str, summary: str,
                    team_ping: str, player_name: str, source_name: str):
    desc = (summary or "").strip()
    if len(desc) > 400:
        desc = desc[:400] + "…"
    embed = discord.Embed(title=title, url=url, description=desc)
    embed.set_footer(text=f"{source_name} • {player_name}")
    content = f"{team_ping} — news on **{player_name}**"
    await channel.send(content=content, embed=embed)
    await asyncio.sleep(1.0)

# -----------------------------
# Poll loop
# -----------------------------
@tasks.loop(seconds=POLL_EVERY_SECONDS)
async def poll_feeds():
    await client.wait_until_ready()
    channel = client.get_channel(DISCORD_CHANNEL_ID)
    if not channel or not isinstance(channel, discord.TextChannel):
        print("Channel not found or not a text channel. Check DISCORD_CHANNEL_ID.")
        return

    # Load roster each iteration in case files were updated
    try:
        players = load_players(PLAYERS_JSON)
        team_map = load_discord_map(DISCORD_JSON)
        roster = build_roster(players, team_map)
    except Exception as ex:
        print(f"Data load error: {ex}")
        return

    if not roster:
        return

    name_patterns = compile_name_patterns(list(roster.keys()))
    cutoff = datetime.now(timezone.utc) - timedelta(days=POST_LOOKBACK_DAYS)

    entries: List[Tuple[str, object]] = []
    for feed_url in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
            src_name = parsed.feed.get("title", feed_url)
            for e in parsed.entries:
                entries.append((src_name, e))
        except Exception as ex:
            print(f"[Feed error] {feed_url}: {ex}")

    if not entries:
        return

    for src_name, e in entries:
        title = getattr(e, "title", "") or ""
        link = getattr(e, "link", "") or ""
        summary = getattr(e, "summary", "") or getattr(e, "description", "") or ""
        published = parse_published(e) or datetime.now(timezone.utc)

        if not title or not link:
            continue
        if published < cutoff:
            continue

        haystack = f"{title}\n{summary}"

        matched_players = [pname for pname, pat in name_patterns.items() if pat.search(haystack)]
        if not matched_players:
            continue

        for player_name in matched_players:
            if is_seen(player_name, link):
                continue
            targets = await build_targets_for_player(channel.guild, player_name, roster)
            if not targets:
                continue
            team_ping = ", ".join(targets)
            try:
                await post_item(channel, title, link, summary, team_ping, player_name, src_name)
                mark_seen(player_name, link, published)
            except Exception as ex:
                print(f"[Post failed] {ex}")

# -----------------------------
# Slash Commands
# -----------------------------
def load_latest_maps():
    """Helper to load fresh data for commands."""
    players = load_players(PLAYERS_JSON)
    teams_by_id = load_discord_map(DISCORD_JSON)
    roster = build_roster(players, teams_by_id)
    code_idx, name_idx = index_teams_by_code_and_name(teams_by_id)
    return players, teams_by_id, roster, code_idx, name_idx

@tree.command(name="who-has", description="Show which fantasy team(s) own a player and ping them.")
@app_commands.describe(player="Player full name (e.g., Connor McDavid)")
async def who_has(interaction: discord.Interaction, player: str):
    await interaction.response.defer(ephemeral=False, thinking=True)
    try:
        _, teams_by_id, roster, _, _ = load_latest_maps()
    except Exception as ex:
        await interaction.followup.send(f"Data load error: {ex}")
        return

    # Find case-insensitively by exact string; if not found, try contains
    pname_exact = next((p for p in roster.keys() if p.lower() == player.lower()), None)
    if not pname_exact:
        pname_exact = next((p for p in roster.keys() if player.lower() in p.lower()), None)

    if not pname_exact:
        await interaction.followup.send(f"Could not find a rostered player matching “{player}”.")
        return

    # Build pings
    targets = await build_targets_for_player(interaction.guild, pname_exact, roster)
    if not targets:
        await interaction.followup.send(f"**{pname_exact}** is not mapped to any Discord targets.")
        return

    team_names = {t["team_name"] for t in roster[pname_exact]["teams"]}
    pings = ", ".join(targets)
    await interaction.followup.send(f"{pings} — **{pname_exact}** is on: " + ", ".join(sorted(team_names)))

@tree.command(name="test-ping", description="Ping a specific team by code or name; if omitted, show usage.")
@app_commands.describe(team="Team code or name (optional)")
async def test_ping(interaction: discord.Interaction, team: Optional[str] = None):
    # If no team specified, reply ephemerally with usage and sample codes
    try:
        _, teams_by_id, _, code_idx, name_idx = load_latest_maps()
    except Exception as ex:
        await interaction.response.send_message(f"Data load error: {ex}", ephemeral=True)
        return

    if not team:
        # Show first 10 codes as a gentle hint
        codes = ", ".join(list({info['code'] for info in teams_by_id.values()})[:10])
        await interaction.response.send_message(
            f"Usage: `/test-ping team:<code or name>`\nExample codes: {codes}\n"
            f"• Pings use role IDs first, then user IDs. Make sure roles are mentionable.",
            ephemeral=True
        )
        return

    # Resolve team
    key = team.lower().strip()
    tid = None
    if key in code_idx:
        tid = code_idx[key]
    elif key in name_idx:
        tid = name_idx[key]
    else:
        # fuzzy contains
        for nm, t_id in name_idx.items():
            if key in nm:
                tid = t_id
                break
        if not tid:
            for cd, t_id in code_idx.items():
                if key in cd:
                    tid = t_id
                    break

    if not tid or tid not in teams_by_id:
        await interaction.response.send_message(f"Team “{team}” not found.", ephemeral=True)
        return

    info = teams_by_id[tid]
    m = mention_for_role(info.get("discord_role_id")) or mention_for_user(info.get("discord_user_id"))
    if not m:
        # last resort readable
        usern = info.get("discord_username") or ""
        m = f"**{info['name']}**" + (f" (@{usern})" if usern else "")

    await interaction.response.send_message(f"{m} — test ping ✅")

# -----------------------------
# Lifecycle
# -----------------------------
@client.event
async def on_ready():
    print(f"Logged in as {client.user} (guilds: {[g.name for g in client.guilds]})")
    seen_db_init()
    # Sync commands to all guilds the bot is currently in
    try:
        await tree.sync()  # global sync (fine for a single private server)
        print("Slash commands synced.")
    except Exception as ex:
        print(f"Command sync failed: {ex}")

    if not poll_feeds.is_running():
        poll_feeds.start()

if __name__ == "__main__":
    if not DISCORD_TOKEN or not DISCORD_CHANNEL_ID:
        raise SystemExit("Set DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID env vars.")
    client.run(DISCORD_TOKEN)
