"""
Discord Giveaway Bot
---------------------
Slash command `/giveaway` opens a popup form (Duration, Number of Winners,
Prize, Description) just like the DonutSMP giveaway bot. It then posts a
live embed with an "Enter" button, tracks entries, and automatically picks
winner(s) when the timer runs out.

Commands:
    /giveaway            - open the creation form and post a giveaway
    /gend <message_id>   - end a giveaway early
    /greroll <message_id> - reroll winner(s) for an ended giveaway

Requires: discord.py >= 2.4, Python 3.10+
"""

import json
import os
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

# --------------------------------------------------------------------------
# Config / storage
# --------------------------------------------------------------------------

DATA_FILE = Path(__file__).parent / "giveaways.json"
CHECK_INTERVAL_SECONDS = 15

intents = discord.Intents.default()
intents.members = True  # enable "Server Members Intent" in the dev portal too

bot = commands.Bot(command_prefix="!", intents=intents)


def load_data() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# message_id (str) -> giveaway record
giveaways: dict = load_data()


# --------------------------------------------------------------------------
# Duration parsing, e.g. "3d", "1h30m", "45m", "2d12h"
# --------------------------------------------------------------------------

DURATION_PATTERN = re.compile(
    r"^\s*(?:(?P<days>\d+)\s*d)?\s*(?:(?P<hours>\d+)\s*h)?"
    r"\s*(?:(?P<minutes>\d+)\s*m)?\s*(?:(?P<seconds>\d+)\s*s)?\s*$",
    re.IGNORECASE,
)


def parse_duration(text: str) -> Optional[timedelta]:
    match = DURATION_PATTERN.match(text.strip())
    if not match:
        return None
    parts = {k: int(v) for k, v in match.groupdict().items() if v}
    if not parts:
        return None
    return timedelta(
        days=parts.get("days", 0),
        hours=parts.get("hours", 0),
        minutes=parts.get("minutes", 0),
        seconds=parts.get("seconds", 0),
    )


# --------------------------------------------------------------------------
# Embed builder
# --------------------------------------------------------------------------

def build_giveaway_embed(
    *,
    prize: str,
    host_mention: str,
    end_time: datetime,
    winner_count: int,
    entries: int,
    description: Optional[str] = None,
    ended: bool = False,
    winners_mentions: Optional[list] = None,
) -> discord.Embed:
    unix_ts = int(end_time.timestamp())
    embed = discord.Embed(
        title=f"🎉 {prize}",
        description=description if description else None,
        color=discord.Color.dark_grey() if ended else discord.Color.blurple(),
    )

    if ended:
        embed.add_field(name="Ended", value=f"<t:{unix_ts}:R>", inline=False)
        if winners_mentions:
            embed.add_field(name="Winner(s)", value=", ".join(winners_mentions), inline=False)
        else:
            embed.add_field(name="Winner(s)", value="No valid entries — no winner could be chosen.", inline=False)
    else:
        embed.add_field(name="Ends", value=f"<t:{unix_ts}:R> ({datetime.strftime(end_time, '%d/%m/%Y')})", inline=False)

    embed.add_field(name="Hosted by", value=host_mention, inline=True)
    embed.add_field(name="Entries", value=str(entries), inline=True)
    embed.add_field(name="Winners", value=str(winner_count), inline=True)
    embed.set_footer(text="Giveaway has ended." if ended else "Click 🎉 Enter below to join!")
    return embed


# --------------------------------------------------------------------------
# UI: the creation form (modal)
# --------------------------------------------------------------------------

class GiveawayModal(discord.ui.Modal, title="Create a Giveaway"):
    duration = discord.ui.TextInput(
        label="Duration", placeholder="e.g. 3d, 1h30m, 45m", max_length=20
    )
    winners = discord.ui.TextInput(
        label="Number of Winners", placeholder="1", max_length=3, default="1"
    )
    prize = discord.ui.TextInput(
        label="Prize", placeholder="e.g. 50m", max_length=200
    )
    description = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    def __init__(self, channel: discord.abc.Messageable):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        delta = parse_duration(str(self.duration.value))
        if delta is None or delta.total_seconds() <= 0:
            await interaction.response.send_message(
                "Couldn't understand that duration. Try formats like `3d`, `1h30m`, or `45m`.",
                ephemeral=True,
            )
            return

        try:
            winner_count = int(str(self.winners.value).strip())
            if winner_count < 1:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "Number of winners must be a positive whole number.", ephemeral=True
            )
            return

        end_time = datetime.now(timezone.utc) + delta
        host_mention = f"<@{interaction.user.id}>"
        desc_value = str(self.description.value) if self.description.value else None

        embed = build_giveaway_embed(
            prize=str(self.prize.value),
            host_mention=host_mention,
            end_time=end_time,
            winner_count=winner_count,
            entries=0,
            description=desc_value,
        )

        message = await self.channel.send(embed=embed, view=GiveawayView())

        giveaways[str(message.id)] = {
            "channel_id": self.channel.id,
            "guild_id": interaction.guild_id,
            "host_id": interaction.user.id,
            "prize": str(self.prize.value),
            "winner_count": winner_count,
            "description": desc_value,
            "end_time": end_time.isoformat(),
            "entries": [],
            "ended": False,
        }
        save_data(giveaways)

        await interaction.response.send_message(
            f"Giveaway posted in {self.channel.mention}!", ephemeral=True
        )


# --------------------------------------------------------------------------
# UI: persistent "Enter" button
# --------------------------------------------------------------------------

class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # persistent across restarts

    @discord.ui.button(label="Enter", emoji="🎉", style=discord.ButtonStyle.blurple, custom_id="giveaway_enter")
    async def enter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        message_id = str(interaction.message.id)
        entry = giveaways.get(message_id)

        if entry is None or entry.get("ended"):
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return

        user_id = interaction.user.id
        entries = entry["entries"]
        if user_id in entries:
            entries.remove(user_id)
            joined = False
        else:
            entries.append(user_id)
            joined = True
        save_data(giveaways)

        embed = build_giveaway_embed(
            prize=entry["prize"],
            host_mention=f"<@{entry['host_id']}>",
            end_time=datetime.fromisoformat(entry["end_time"]),
            winner_count=entry["winner_count"],
            entries=len(entries),
            description=entry.get("description"),
        )
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message(
            "🎉 You're entered! Good luck." if joined else "You left the giveaway.",
            ephemeral=True,
        )


# --------------------------------------------------------------------------
# Ending / rerolling giveaways
# --------------------------------------------------------------------------

async def end_giveaway(message_id: str, reroll: bool = False) -> Optional[str]:
    """Ends (or rerolls) a giveaway. Returns an error string, or None on success."""
    entry = giveaways.get(message_id)
    if entry is None:
        return "No giveaway found with that message ID."
    if not reroll and entry.get("ended"):
        return "That giveaway has already ended."
    if reroll and not entry.get("ended"):
        return "That giveaway hasn't ended yet."

    channel = bot.get_channel(entry["channel_id"])
    if channel is None:
        try:
            channel = await bot.fetch_channel(entry["channel_id"])
        except discord.HTTPException:
            entry["ended"] = True
            save_data(giveaways)
            return "Couldn't find the original channel."

    try:
        message = await channel.fetch_message(int(message_id))
    except discord.NotFound:
        entry["ended"] = True
        save_data(giveaways)
        return "Couldn't find the original giveaway message."

    entrants = entry["entries"]
    winner_count = entry["winner_count"]
    chosen = random.sample(entrants, k=min(winner_count, len(entrants))) if entrants else []
    winners_mentions = [f"<@{uid}>" for uid in chosen]

    embed = build_giveaway_embed(
        prize=entry["prize"],
        host_mention=f"<@{entry['host_id']}>",
        end_time=datetime.fromisoformat(entry["end_time"]),
        winner_count=winner_count,
        entries=len(entrants),
        description=entry.get("description"),
        ended=True,
        winners_mentions=winners_mentions,
    )
    await message.edit(embed=embed, view=None)

    if winners_mentions:
        verb = "New winner(s)" if reroll else "Congratulations"
        await channel.send(
            f"🎉 {verb}: {', '.join(winners_mentions)}! You won **{entry['prize']}**!",
            reference=message,
        )
    else:
        await channel.send(f"No one entered the giveaway for **{entry['prize']}**, so no winner could be chosen.")

    entry["ended"] = True
    entry["last_winners"] = chosen
    save_data(giveaways)
    return None


@tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
async def giveaway_checker():
    now = datetime.now(timezone.utc)
    for message_id, entry in list(giveaways.items()):
        if entry.get("ended"):
            continue
        end_time = datetime.fromisoformat(entry["end_time"])
        if end_time <= now:
            await end_giveaway(message_id)


@giveaway_checker.before_loop
async def before_checker():
    await bot.wait_until_ready()


# --------------------------------------------------------------------------
# Slash commands
# --------------------------------------------------------------------------

@bot.tree.command(name="giveaway", description="Create a new giveaway")
@app_commands.describe(channel="Channel to post the giveaway in (defaults to this channel)")
@app_commands.checks.has_permissions(manage_guild=True)
async def giveaway_cmd(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    target_channel = channel or interaction.channel
    await interaction.response.send_modal(GiveawayModal(target_channel))


@bot.tree.command(name="gend", description="End a giveaway early and pick winner(s) now")
@app_commands.describe(message_id="The ID of the giveaway message")
@app_commands.checks.has_permissions(manage_guild=True)
async def gend_cmd(interaction: discord.Interaction, message_id: str):
    await interaction.response.defer(ephemeral=True)
    error = await end_giveaway(message_id)
    await interaction.followup.send(error or "Giveaway ended.", ephemeral=True)


@bot.tree.command(name="greroll", description="Reroll winner(s) for an already-ended giveaway")
@app_commands.describe(message_id="The ID of the giveaway message")
@app_commands.checks.has_permissions(manage_guild=True)
async def greroll_cmd(interaction: discord.Interaction, message_id: str):
    await interaction.response.defer(ephemeral=True)
    error = await end_giveaway(message_id, reroll=True)
    await interaction.followup.send(error or "Rerolled!", ephemeral=True)


async def _perm_error_handler(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "You need the **Manage Server** permission to do that.", ephemeral=True
        )
    else:
        raise error


giveaway_cmd.error(_perm_error_handler)
gend_cmd.error(_perm_error_handler)
greroll_cmd.error(_perm_error_handler)


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------

@bot.event
async def on_ready():
    bot.add_view(GiveawayView())  # re-register persistent button after restarts
    if not giveaway_checker.is_running():
        giveaway_checker.start()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("Set the DISCORD_BOT_TOKEN environment variable before running the bot.")
    bot.run(token)
