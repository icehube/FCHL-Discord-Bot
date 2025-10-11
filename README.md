# FCHL NHL News Discord Bot

Posts NHL **player news** to your league’s channel and pings the fantasy team(s) that own the player.
Also provides two slash-commands:

* `/who-has <player>` — shows who owns a player and pings them
* `/test-ping [team]` — pings a team by code or name (for verification)

The bot:

* Pulls free NHL news via RSS (ESPN, FOX Sports, RotoWire — configurable)
* Matches article titles/summaries to player names on your rosters
* Pings by **role ID** or **user ID** (preferred), with safe fallbacks
* De-dupes posts via a local SQLite DB (`seen.db`)

---

## Requirements

* Python 3.11+
* A Discord **Bot** (token) invited to your server with:

  * Scopes: `bot`, `applications.commands`
  * Permissions: Send Messages, Embed Links, Read Message History
    *(If you’ll ping roles, enable “Mention @everyone, @here, and All Roles” or set the role to “Allow anyone to @mention this role.”)*
* Channel ID for where the bot will post
* Two data files in the working directory (or set env paths):

  * `players.json` — exported players with `name` and `activeteam` (team ID)
  * `Discord.json` — maps each fantasy team to Discord IDs

---

## Data Files

### `players.json` (array)

```json
[
  { "name": "Connor McDavid", "activeteam": 3 },
  { "name": "Cale Makar", "activeteam": 7 },
  { "name": "Free Agent Guy", "activeteam": 0 }
]
```

* `activeteam` = numeric team ID (0 means unowned)

### `Discord.json` (object keyed by team code)

```json
{
  "LGN": {
    "id": 9,
    "name": "Lessard Gnomes",
    "discord_user_id": 123456789012345678,
    "discord_role_id": 234567890123456789
  },
  "GRO": {
    "id": 3,
    "name": "Grovenor Drew",
    "discord_user_id": 345678901234567890
  }
}
```

* **Preferred**: provide `discord_role_id` (role mention) or `discord_user_id` (user mention)
* Optional legacy field `discord` (username) is supported as a last-resort fallback

---

## Install

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt`

```
discord.py>=2.3
feedparser>=6.0
```

---

## Configure

Set environment variables (locally you can use a `.env` or your shell):

```bash
export DISCORD_BOT_TOKEN=xxxx.your.bot.token.xxxx
export DISCORD_CHANNEL_ID=123456789012345678
# optional (defaults shown)
export PLAYERS_JSON=players.json
export DISCORD_JSON=Discord.json
export POLL_EVERY_SECONDS=600
export POST_LOOKBACK_DAYS=2
export RSS_FEEDS="https://www.espn.com/espn/rss/nhl/news,https://api.foxsports.com/v2/content/optimized-rss?partnerKey=MB0Wehpmuj2lUhuRhQaafhBjAJqaPU244mlTDK1i&size=50&tags=fs/nhl,https://www.rotowire.com/rss/news.php?sport=NHL"
```

> **Bot token vs Webhook:** this project uses a **Bot** (not a channel webhook) so slash-commands work.

---

## Run

```bash
python bot.py
```

On first start, slash-commands are synced globally. In Discord, type `/who-has` or `/test-ping`.

---

## Slash-Commands

* **`/who-has <player>`**

  * Case-insensitive; also matches partial names if exact isn’t found
  * Example: `/who-has Connor McDavid`

* **`/test-ping [team]`**

  * `team` can be a team **code** (e.g., `LGN`) or full/partial **name**
  * No argument shows usage and some sample codes

---

## How it Works

1. Every `POLL_EVERY_SECONDS`, the bot fetches the configured RSS feeds.
2. It compiles word-boundary regexes for each **owned** player name.
3. If a feed item mentions one or more players, it posts a Discord embed with a ping to the owning team(s).
4. Each `(player, link)` is recorded into `seen.db` to avoid duplicates (older than `POST_LOOKBACK_DAYS` are ignored).

---

## Deployment (Kinsta Apps)

* Create an **App** (or Background Worker) with:

  * Start command: `python bot.py`
  * Add the env vars from above
  * Mount `players.json` and `Discord.json` in the app directory (or bake them into the repo)
* Make sure the role IDs you use are **mentionable** in your server settings.

*(You can also run this on any VM, Docker, or your own server.)*

---

## Customization

* **Feeds:** change `RSS_FEEDS` to add/remove sources.
* **Name matching:** add alt-names/nicknames by extending `players.json` and the code (e.g., a `nicknames` array) — easy to add.
* **Posting rules:** adjust the lookback window or summary truncation in `bot.py`.

---

## Troubleshooting

* **Slash-commands not showing:**

  * Kick and re-invite the bot with `applications.commands` scope; restart the bot once to re-sync.
* **Mentions not pinging:**

  * Prefer numeric `discord_role_id`/`discord_user_id`.
  * For role mentions, enable “Allow anyone to @mention this role.”
* **No posts:**

  * Check `DISCORD_CHANNEL_ID` (Developer Mode → right-click channel → Copy ID).
  * Ensure feeds return items and that your players are actually mentioned.
  * Delete `seen.db` to clear de-dupe history during testing.

---

## Repo Structure

```
.
├── bot.py
├── players.json
├── Discord.json
├── requirements.txt
└── README.md
```

---