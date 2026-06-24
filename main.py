import discord
from discord.ext import commands, tasks
import json
import os
import re
import datetime
import aiohttp
from aiohttp import web

TOKEN = os.environ["DISCORD_TOKEN"]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ── Config ─────────────────────────────────────────────────────────────────────

POINTS = {
    "warn": 1,
    "mute": 2,
    "softban": 2,
    "temp ban": 4,
    "ban": 4
}

PUNISHER_ROLES = {
    "[administration team]",
    "[internal affairs team]",
    "[management team]",
    "[directorship team]"
}

VOID_ROLES = {
    "[management team]",
    "[directorship team]"
}

POINT_THRESHOLD  = 10
REMINDER_CHANNEL = "awaiting-bans"

LOGO_PATH   = os.path.join(os.path.dirname(__file__), "logo.png")
EMBED_COLOR = 0x01D3FF

POINTS_FILE = os.path.join(os.path.dirname(__file__), "points.json")
CASES_FILE  = os.path.join(os.path.dirname(__file__), "cases.json")

last_command_channel: dict[str, int] = {}
processed_cases: set[str] = set()
# Track message IDs already handled to prevent double-processing
processed_message_ids: set[int] = set()

# ── Data helpers ───────────────────────────────────────────────────────────────

def load_json(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=4)


points_db = load_json(POINTS_FILE)
cases_db  = load_json(CASES_FILE)


def has_any_role(member: discord.Member, role_names: set) -> bool:
    return any(r.name.lower() in role_names for r in member.roles)


def find_user_id_in_embed(embed: discord.Embed) -> str | None:
    texts = []
    if embed.title:
        texts.append(embed.title)
    for field in embed.fields:
        texts.append(field.name or "")
        texts.append(field.value or "")
    if embed.footer and embed.footer.text:
        texts.append(embed.footer.text)

    combined = " ".join(texts)

    mention_match = re.search(r"<@!?(\d{17,20})>", combined)
    if mention_match:
        return mention_match.group(1)

    id_match = re.search(r"\b(\d{17,20})\b", combined)
    if id_match:
        return id_match.group(1)

    return None


def parse_case_number(title: str) -> str | None:
    match = re.search(r"case\s*#?(\d+)", title, re.IGNORECASE)
    return match.group(1) if match else None


def resolve_user_id(raw: str) -> str:
    match = re.match(r"<@!?(\d{17,20})>", raw.strip())
    return match.group(1) if match else raw.strip()


def extract_roblox_username(member: discord.Member) -> str:
    nick = member.nick or member.display_name
    if "|" in nick:
        return nick.split("|")[-1].strip()
    return nick.strip()


def get_latest_case(user_id: str) -> dict | None:
    user_cases = {k: v for k, v in cases_db.items() if v.get("user_id") == user_id}
    if not user_cases:
        return None
    latest_key = max(user_cases.keys(), key=lambda x: int(x))
    return {**user_cases[latest_key], "case_number": latest_key}


async def get_roblox_info(username: str) -> tuple[str, str]:
    """Returns (roblox_id, roblox_url). Falls back to ('Unknown', 'N/A') on failure."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [username], "excludeBannedUsers": False},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if data.get("data"):
                        roblox_id = str(data["data"][0]["id"])
                        roblox_url = f"https://www.roblox.com/users/{roblox_id}/profile"
                        return roblox_id, roblox_url
    except Exception as e:
        print(f"[ROBLOX] Lookup failed for {username}: {e}")
    return "Unknown", "N/A"


# ── Embed builder ──────────────────────────────────────────────────────────────

def build_alert_embed(
    discord_username: str,
    discord_id: str,
    roblox_username: str,
    roblox_id: str,
    roblox_url: str,
    total_points: int,
    latest_case: dict | None,
    avatar_url: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        description="**__Ban Threshold Reached__**",
        color=EMBED_COLOR,
    )
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="Discord Username", value=discord_username, inline=True)
    embed.add_field(name="Discord ID",       value=discord_id,       inline=True)
    embed.add_field(name="\u200b",           value="\u200b",         inline=True)  # spacer

    embed.add_field(name="Roblox Username",  value=roblox_username,      inline=True)
    embed.add_field(name="Roblox ID",        value=roblox_id,            inline=True)
    embed.add_field(name="Roblox URL",       value=roblox_url,           inline=True)

    embed.add_field(name="Total Points",     value=str(total_points),    inline=False)

    if latest_case:
        action = (
            f"{latest_case['punishment'].title()} "
            f"(Case #{latest_case['case_number']}, "
            f"+{latest_case['points']} pts)"
        )
        embed.add_field(name="Last Moderation Action", value=action, inline=False)

    embed.set_footer(text="This user has reached or exceeded the ban threshold.")
    return embed


# ── Daily reminder — runs at midnight UTC, NOT on startup ─────────────────────

@tasks.loop(time=datetime.time(hour=0, minute=0, tzinfo=datetime.timezone.utc))
async def daily_reminder():
    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name=REMINDER_CHANNEL)
        if not channel:
            print(f"[REMINDER] Channel '{REMINDER_CHANNEL}' not found in {guild.name}")
            continue

        flagged = [
            (member_id, pts)
            for member_id, pts in points_db.items()
            if pts >= POINT_THRESHOLD
        ]

        if not flagged:
            continue

        for member_id, pts in flagged:
            member = guild.get_member(int(member_id))
            if not member:
                continue

            roblox_username        = extract_roblox_username(member)
            roblox_id, roblox_url  = await get_roblox_info(roblox_username)
            latest_case            = get_latest_case(member_id)

            embed = build_alert_embed(
                discord_username=member.name,
                discord_id=str(member.id),
                roblox_username=roblox_username,
                roblox_id=roblox_id,
                roblox_url=roblox_url,
                total_points=pts,
                latest_case=latest_case,
                avatar_url=str(member.display_avatar.url),
            )
            await channel.send(embed=embed)


@daily_reminder.before_loop
async def before_reminder():
    await bot.wait_until_ready()


# ── Events ─────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Moderation points bot is ready!")
    daily_reminder.start()


@bot.event
async def on_message(message):
    # Ignore our own messages entirely
    if message.author.id == bot.user.id:
        return

    # Deduplicate — Discord can fire on_message more than once for the same message
    if message.id in processed_message_ids:
        return
    processed_message_ids.add(message.id)
    # Keep the set from growing unbounded
    if len(processed_message_ids) > 1000:
        processed_message_ids.clear()

    if not message.guild:
        await bot.process_commands(message)
        return

    guild_id = str(message.guild.id)

    # Track which channel the last punishment command was issued in
    if not message.author.bot and message.content and message.content.startswith("!"):
        cmd = message.content.lstrip("!").lower().split()[0]
        if any(cmd.startswith(p.replace(" ", "")) for p in POINTS):
            if has_any_role(message.author, PUNISHER_ROLES):
                last_command_channel[guild_id] = message.channel.id

    # React to Circle bot punishment embeds
    if message.author.bot and message.embeds:
        embed = message.embeds[0]
        title = (embed.title or "").lower()

        matched_punishment = None
        for punishment, value in POINTS.items():
            if punishment in title:
                matched_punishment = (punishment, value)
                break

        if matched_punishment:
            case_number = parse_case_number(embed.title or "")

            if case_number and case_number in processed_cases:
                await bot.process_commands(message)
                return

            user_id = find_user_id_in_embed(embed)

            if user_id:
                punished_user = message.guild.get_member(int(user_id))
                if punished_user:
                    current_points = points_db.get(str(punished_user.id), 0)
                    current_points += matched_punishment[1]
                    points_db[str(punished_user.id)] = current_points
                    save_json(POINTS_FILE, points_db)

                    if case_number:
                        processed_cases.add(case_number)
                        cases_db[case_number] = {
                            "user_id": str(punished_user.id),
                            "punishment": matched_punishment[0],
                            "points": matched_punishment[1],
                            "guild_id": guild_id
                        }
                        save_json(CASES_FILE, cases_db)

                    point_word = "point" if current_points == 1 else "points"

                    channel_id = last_command_channel.get(guild_id)
                    target_channel = (
                        message.guild.get_channel(channel_id)
                        if channel_id else message.channel
                    )

                    await target_channel.send(
                        f"{punished_user.mention} now has **{current_points} {point_word}**."
                    )

    await bot.process_commands(message)


# ── Commands ───────────────────────────────────────────────────────────────────

@bot.command()
async def points(ctx, member: discord.Member = None):
    member = member or ctx.author
    total = points_db.get(str(member.id), 0)
    point_word = "point" if total == 1 else "points"
    await ctx.send(f"{member.mention} has **{total} {point_word}**.")


@bot.command()
async def void(ctx, raw_user: str, *, raw_case: str):
    """Remove points for a case. Usage: !void <@user or user_id> <case number>"""
    if not has_any_role(ctx.author, VOID_ROLES):
        return await ctx.send(
            "You don't have permission. Requires **[Management Team]** or **[Directorship Team]**."
        )

    user_id     = resolve_user_id(raw_user)
    case_number = re.sub(r"(?i)case\s*", "", raw_case).strip().lstrip("#").strip()
    case        = cases_db.get(case_number)

    if not case or case["guild_id"] != str(ctx.guild.id):
        return await ctx.send(f"No record found for Case #{case_number}.")

    if case["user_id"] != user_id:
        return await ctx.send(f"Case #{case_number} does not belong to that user.")

    pts_to_remove = case["points"]
    punishment    = case["punishment"].title()
    current       = points_db.get(user_id, 0)
    new_total     = max(0, current - pts_to_remove)

    points_db[user_id] = new_total
    save_json(POINTS_FILE, points_db)

    del cases_db[case_number]
    save_json(CASES_FILE, cases_db)
    processed_cases.add(case_number)

    member     = ctx.guild.get_member(int(user_id))
    mention    = member.mention if member else f"<@{user_id}>"
    point_word = "point" if new_total == 1 else "points"

    await ctx.send(
        f"Case #{case_number} ({punishment}) voided. {mention} now has **{new_total} {point_word}**."
    )


@bot.command()
@commands.has_permissions(manage_guild=True)
async def remindnow(ctx):
    """Manually trigger the daily ban-threshold reminder. Admin only."""
    await daily_reminder()
    await ctx.send("Reminder sent.")


@bot.command()
@commands.has_permissions(manage_guild=True)
async def sampleremind(ctx):
    """Send a sample reminder embed to preview the format."""
    roblox_id, roblox_url = await get_roblox_info("vgxbak")
    member = ctx.guild.get_member(624840758188441620)
    avatar = str(member.display_avatar.url) if member else None
    embed = build_alert_embed(
        discord_username="vgxbak",
        discord_id="624840758188441620",
        roblox_username="vgxbak",
        roblox_id=roblox_id,
        roblox_url=roblox_url,
        total_points=10,
        latest_case={"punishment": "warn", "case_number": "7", "points": 1},
        avatar_url=avatar,
    )
    await ctx.send(embed=embed)


async def healthz(request):
    return web.Response(text="ok")


async def start_health_server():
    app = web.Application()
    app.router.add_get("/healthz", healthz)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[HEALTH] Listening on :{port}/healthz")


async def main():
    async with bot:
        # Only start the HTTP health server in production (PORT is injected by Replit)
        if "PORT" in os.environ:
            await start_health_server()
        await bot.start(TOKEN)


import asyncio
asyncio.run(main())