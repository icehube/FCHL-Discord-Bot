#!/usr/bin/env python3
"""Convert 'Players – FCHL Online.csv' into players.json."""

import csv
import json
import re
from pathlib import Path

CSV_PATH = Path("Players – FCHL Online.csv")
DISCORD_PATH = Path("discord.json")
OUTPUT_PATH = Path("players.json")

NAME_RE = re.compile(r"^(.+?)\s{2,}(\S+)$")


def load_team_code_to_id(discord_path: Path) -> dict[str, int]:
    raw = json.loads(discord_path.read_text(encoding="utf-8"))
    return {code: int(obj["id"]) for code, obj in raw.items()}


def convert(csv_path: Path, team_lookup: dict[str, int]) -> list[dict]:
    players = []
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            if len(row) < 4:
                continue

            raw_name, position, fchl_team, club = row[0], row[1], row[2], row[3]

            if fchl_team.strip() in ("UFA", "RFA"):
                continue

            m = NAME_RE.match(raw_name)
            if not m:
                continue
            name = m.group(1).strip()
            fchlgroup = m.group(2).strip()

            activeteam = team_lookup.get(fchl_team.strip(), 0)

            players.append({
                "name": name,
                "club": club.strip(),
                "position": position.strip(),
                "activeteam": activeteam,
                "fchlgroup": fchlgroup,
            })

    players.sort(key=lambda p: p["name"])
    return players


def main():
    team_lookup = load_team_code_to_id(DISCORD_PATH)
    players = convert(CSV_PATH, team_lookup)
    OUTPUT_PATH.write_text(json.dumps(players, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(players)} players to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
