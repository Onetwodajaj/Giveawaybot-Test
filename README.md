# Giveaway Bot

A Discord bot that hosts giveaways the same way the DonutSMP giveaway bot does:
`/giveaway` opens a popup form for **Duration**, **Number of Winners**, **Prize**,
and an optional **Description**, then posts a live embed with a 🎉 **Enter**
button. When the timer runs out, the bot automatically picks winner(s) and
announces them.

## Setup

1. **Create the bot application**
   - Go to https://discord.com/developers/applications → **New Application**.
   - Go to the **Bot** tab → **Add Bot**.
   - Under **Privileged Gateway Intents**, turn on **Server Members Intent**.
   - Click **Reset Token** and copy the token somewhere safe.

2. **Invite the bot to your server**
   - In the **OAuth2 → URL Generator** tab, check scopes `bot` and
     `applications.commands`.
   - Under **Bot Permissions**, check at least: `Send Messages`,
     `Embed Links`, `Read Message History`.
   - Open the generated URL and add the bot to your server.

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set your token and run**
   ```bash
   export DISCORD_BOT_TOKEN="your-token-here"   # Windows: set DISCORD_BOT_TOKEN=...
   python bot.py
   ```
   Slash commands sync automatically on startup (can take up to an hour to
   appear globally the very first time — restart Discord or wait if they
   don't show immediately).

## Commands

All commands require the **Manage Server** permission.

| Command | Description |
|---|---|
| `/giveaway [channel]` | Opens the creation form (Duration, Winners, Prize, Description) and posts the giveaway in the given channel, or the current one. |
| `/gend <message_id>` | Ends a giveaway early and picks winner(s) immediately. |
| `/greroll <message_id>` | Rerolls winner(s) for a giveaway that has already ended. |

**Duration format** accepts combinations of days/hours/minutes/seconds, e.g.
`3d`, `1h30m`, `45m`, `2d12h`.

## How it works

- Giveaway state is stored in `giveaways.json` next to the script, so active
  giveaways survive a bot restart.
- A background loop checks every 15 seconds for giveaways whose time is up
  and ends them automatically, choosing winners at random from everyone who
  clicked 🎉 **Enter**.
- The **Enter** button is a persistent view, so it keeps working even after
  the bot restarts.

## Notes / possible tweaks

- Winners are picked with `random.sample`, so duplicates aren't possible and
  it's capped to however many people actually entered.
- If you want to restrict who can host giveaways to a specific role instead
  of "Manage Server", swap out the `@app_commands.checks.has_permissions(...)`
  decorators for a role check.
- If you want the bot to require a minimum server tenure or role before
  entering, add that check inside `GiveawayView.enter_button`.
