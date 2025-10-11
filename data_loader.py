# data_loader.py
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

# ----- file locations (env/args-friendly) -----
PLAYERS_PATH = Path("players.json")   # your uploaded JSON
DISCORD_PATH = Path("discord.json")   # your uploaded JSON (mapping teams -> discord)

def load_players(players_path: Path = PLAYERS_PATH) -> List[dict]:
    data = json.loads(players_path.read_text(encoding="utf-8"))
    # File is a big array of player dicts
    return data

def load_discord_map(discord_path: Path = DISCORD_PATH) -> Dict[int, dict]:
    """
    Returns { team_id(int): {code, name, discord_handle} }
    The raw file is keyed by team code; each value has: id, name, discord
    """
    raw = json.loads(discord_path.read_text(encoding="utf-8"))
    by_id = {}
    for code, obj in raw.items():
        tid = int(obj["id"])
        by_id[tid] = {"code": code, "name": obj["name"], "discord_handle": obj.get("discord", "")}
    return by_id

def compile_player_names(players: List[dict]) -> Dict[str, int]:
    """
    Useful if you want quick lookup by normalized name -> index/id (optional).
    """
    out = {}
    for p in players:
        nm = p.get("name", "").strip()
        if nm:
            out[nm.lower()] = p.get("activeteam", 0)
    return out

def build_roster(players: List[dict], teams_by_id: Dict[int, dict]):
    """
    Builds:
      players_info = {
        player_name: {
          "teams": [ { "team_id": int, "team_name": str, "code": str, "discord_handle": str } ],
        }, ...
      }
    Only includes players with activeteam > 0 and that team exists in Discord.json.
    """
    players_info: Dict[str, dict] = {}
    for p in players:
        name = (p.get("name") or "").strip()
        team_id = p.get("activeteam")  # numeric (0 means no fantasy owner)
        if not name or not isinstance(team_id, int) or team_id <= 0:
            continue
        tinfo = teams_by_id.get(team_id)
        if not tinfo:
            # team id in players.json that isn't in Discord.json — skip gracefully
            continue
        players_info.setdefault(name, {"teams": []})
        players_info[name]["teams"].append({
            "team_id": team_id,
            "team_name": tinfo["name"],
            "code": tinfo["code"],
            "discord_handle": tinfo.get("discord_handle") or ""
        })
    return players_info

# -------------- Discord helpers --------------
def sanitize_username(u: str) -> str:
    # You store discord usernames like "grovenordrew_10615" or "shafty19."
    # Strip spaces; keep punctuation so we can try to find an exact match.
    return (u or "").strip()

async def best_effort_member_ping(guild, username: str) -> Optional[str]:
    """
    Try to resolve a username to a Member mention string.
    Requires the bot to have members intent and the guild to be chunked (default for small guilds).
    If not found, return None and let the caller fall back to plain text.
    """
    if not username:
        return None
    username = sanitize_username(username)
    if not username:
        return None

    # Try exact name match first
    for m in guild.members:
        if m.name == username or (getattr(m, "global_name", None) == username):
            return m.mention
        # Also try case-insensitive startswith to be forgiving
        if m.name.lower().startswith(username.lower()):
            return m.mention
        if getattr(m, "global_name", "") and m.global_name.lower().startswith(username.lower()):
            return m.mention
    return None

async def build_discord_targets_for_player(guild, player_name: str, roster_map) -> List[str]:
    """
    Returns a list of mention strings (role/user) or readable fallbacks for a given player_name.
    """
    info = roster_map.get(player_name)
    if not info:
        return []
    targets = []
    for t in info["teams"]:
        handle = t.get("discord_handle") or ""
        mention = await best_effort_member_ping(guild, handle)
        if mention:
            targets.append(mention)
        else:
            # readable fallback (won't ping reliably without IDs)
            targets.append(f"**{t['team_name']}** (@{handle})" if handle else f"**{t['team_name']}**")
    # dedupe while preserving order
    seen = set()
    out = []
    for x in targets:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
