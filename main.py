import discord
from discord.ext import commands, tasks
import json
import os
import re
import datetime
import time
import aiohttp
from aiohttp import web
import secrets
import asyncio

TOKEN = os.environ.get("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.synced = False

# â”€â”€ Config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

POINTS = {
    "warn": 1,
    "mute": 2,
    "softban": 2,
    "kick": 2,
    "temp ban": 4,
    "tempban": 4,
    "temp banned": 4,
    "banned": 4,
    "ban": 4
}

MANAGEMENT_ROLE_ID = 1517687053134069911
DIRECTORSHIP_ROLE_ID = 1517686125177737228

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

APPEAL_REVIEW_ROLES = {
    "[management team]",
    "[directorship team]"
}

POINT_THRESHOLD  = 10
FIRST_APPEAL_COOLDOWN_DAYS = 14   # Must wait 2 weeks before first appeal
APPEAL_COOLDOWN_DAYS = 60         # Must wait 2 months after a denial
KICK_REMINDER_WINDOW_MINUTES = 30  # Only remind if user rejoins within 30 min

# Channels
WELCOME_CHANNEL_ID = 1517684680005124136
DASHBOARD_CHANNEL_ID = 1517682110842798192
APPEALS_CHANNEL_ID = 1519408033170460672
INGAME_KICK_CHANNEL_ID = 1521216668402188461  # ERLC webhook channel (kick + join events)
INGAME_REMINDER_CHANNEL_ID = 1519468672849150022
INGAME_MODERATING_ROLE_ID = 1520870451923124415

CIRCLE_MODLOGS_CHANNEL_ID = 1518943358750425328
SECURITY_LOGS_CHANNEL_ID = 1519696216961847366

DIRECTORSHIP_PING_EXEMPT_ROLE = 1517688590442692779

GUILD_ID = 1517672283513294868

BASE_URL_RAW = os.getenv("BASE_URL", "https://osrp-bot-production.up.railway.app")
BASE_URL = ("https://" + BASE_URL_RAW) if not BASE_URL_RAW.startswith("http://") and not BASE_URL_RAW.startswith("https://") else BASE_URL_RAW
BASE_URL = BASE_URL.rstrip("/")

EMBED_COLOR = 0x4FC3F7

POINTS_FILE = os.path.join(os.path.dirname(__file__), "points.json")
CASES_FILE  = os.path.join(os.path.dirname(__file__), "cases.json")
APPEALS_FILE = os.path.join(os.path.dirname(__file__), "appeals.json")
KICKED_FILE = os.path.join(os.path.dirname(__file__), "kicked.json")
APPEAL_TOKENS_FILE = os.path.join(os.path.dirname(__file__), "appeal_tokens.json")
BLACKLIST_FILE = os.path.join(os.path.dirname(__file__), "blacklist.json")
STAFF_BLACKLIST_FILE = os.path.join(os.path.dirname(__file__), "staff_blacklist.json")
STAFF_BLACKLIST_ROLE_ID = 1523615876929028106
MELONLY_API_TOKEN = os.environ.get("MELONLY_TOKEN", "")
MELONLY_FORM_ID = "7474151205980082176"
MELONLY_API_BASE = "https://api.melonly.xyz/api/v1"

processed_cases: set[str] = set()
processed_message_ids: set[int] = set()
recent_punishments: dict[str, float] = {}  # user_id -> timestamp, to avoid duplicate Circle bot responses
recent_ping_cooldown: dict[int, float] = {}  # user_id -> timestamp, anti-ping cooldown
banned_users_pending: dict[int, int] = {}  # user_id -> ban_case_number

# â”€â”€ Data helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
appeals_db = load_json(APPEALS_FILE)
kicked_db = load_json(KICKED_FILE)
appeal_tokens_db = load_json(APPEAL_TOKENS_FILE)
blacklist_db = load_json(BLACKLIST_FILE)
staff_blacklist_db = load_json(STAFF_BLACKLIST_FILE)


async def melonly_ensure_blocked_role():
    """On startup, ensure the staff blacklist role is in Melonly form's blockedDiscordRoles."""
    if not MELONLY_API_TOKEN:
        print("[MELONLY] No API token, skipping blocked role setup")
        return
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {MELONLY_API_TOKEN}"}
            async with session.get(
                f"{MELONLY_API_BASE}/server/applications/{MELONLY_FORM_ID}",
                headers=headers
            ) as resp:
                if resp.status != 200:
                    print(f"[MELONLY] Failed to fetch form: {resp.status}")
                    return
                form = await resp.json()
            
            blocked = form.get("blockedDiscordRoles", [])
            role_str = str(STAFF_BLACKLIST_ROLE_ID)
            if role_str in blocked:
                print("[MELONLY] Blocked role already configured")
                return
            
            blocked.append(role_str)
            async with session.patch(
                f"{MELONLY_API_BASE}/server/applications/{MELONLY_FORM_ID}",
                headers=headers,
                json={"blockedDiscordRoles": blocked}
            ) as resp:
                if resp.status == 200:
                    print("[MELONLY] Successfully added blocked role to form")
                else:
                    print(f"[MELONLY] Failed to update form: {resp.status}")
    except Exception as e:
        print(f"[MELONLY] Error: {e}")


MANAGEMENT_ROLE_IDS = {MANAGEMENT_ROLE_ID, DIRECTORSHIP_ROLE_ID}

def has_any_role(member: discord.Member, role_names: set) -> bool:
    return any(r.name.lower() in role_names for r in member.roles)

def has_staff_role(member: discord.Member) -> bool:
    return any(r.id in MANAGEMENT_ROLE_IDS for r in member.roles)


def find_user_id_in_embed(embed: discord.Embed) -> str | None:
    texts = []
    if embed.title:
        texts.append(embed.title)
    if embed.description:
        texts.append(embed.description)
    for field in embed.fields:
        texts.append(field.name or "")
        texts.append(field.value or "")
    if embed.footer and embed.footer.text:
        texts.append(embed.footer.text)

    combined = " ".join(texts)

    mention_match = re.search(r"<@!?(\d{17,20})>", combined)
    if mention_match:
        return mention_match.group(1)

    id_match = re.search(r"(\d{17,20})", combined)
    if id_match:
        return id_match.group(1)

    return None


def find_roblox_username_in_embed(embed: discord.Embed) -> str | None:
    texts = []
    if embed.title:
        texts.append(embed.title)
    if embed.description:
        texts.append(embed.description)
    for field in embed.fields:
        texts.append(field.name or "")
        texts.append(field.value or "")
    combined = " ".join(texts)
    
    # ERLC webhook format: "Player: Username (123456789)" or "Player: Username was kicked"
    patterns = [
        r"Player:\s*([A-Za-z0-9_]+)",
        r"(?:Roblox|RBX|User)[:\s]+([A-Za-z0-9_]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            return match.group(1)
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


def get_ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f"{n}{suffix}"


def generate_appeal_token() -> str:
    return secrets.token_hex(5).upper()  # 10-char hex code


async def delete_after_delay(msg: discord.Message, seconds: int):
    await asyncio.sleep(seconds)
    try:
        await msg.delete()
    except Exception:
        pass


async def get_roblox_info(username: str) -> tuple[str, str, str]:
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
                        user_data = data["data"][0]
                        roblox_id = str(user_data["id"])
                        roblox_url = f"https://www.roblox.com/users/{roblox_id}/profile"
                        
                        # Get creation date
                        async with session.get(
                            f"https://users.roblox.com/v1/users/{roblox_id}",
                            timeout=aiohttp.ClientTimeout(total=5),
                        ) as user_resp:
                            if user_resp.status == 200:
                                user_info = await user_resp.json()
                                created = user_info.get("created", "Unknown")
                                return roblox_id, roblox_url, created
                        return roblox_id, roblox_url, "Unknown"
    except Exception as e:
        print(f"[ROBLOX] Lookup failed for {username}: {e}")
    return "Unknown", "N/A", "Unknown"


async def get_roblox_avatar_url(roblox_id: str) -> str:
    if roblox_id == "Unknown":
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={roblox_id}&size=150x150&format=Png",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("data"):
                        return data["data"][0]["imageUrl"]
    except Exception as e:
        print(f"[ROBLOX] Avatar fetch failed for ID {roblox_id}: {e}")
    return None


async def check_bloxlink_linked(member: discord.Member) -> dict | None:
    """Check if a member has Discord linked via Bloxlink. Returns Discord info if linked."""
    try:
        guild = member.guild
        # Try via Bloxlink API endpoint if available
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.blox.link/v4/public/discord/{member.id}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("success") and data.get("robloxID"):
                        roblox_id = data["robloxID"]
                        return {"discord_id": str(member.id), "discord_mention": member.mention, "roblox_id": roblox_id}
    except Exception as e:
        print(f"[BLOXLINK] API check failed for {member.id}: {e}")
    
    # Fallback: check for common Bloxlink-linked role names
    bloxlink_role_names = {"bloxlink linked", "verified", "linked", "roblox verified"}
    has_role = any(r.name.lower() in bloxlink_role_names for r in member.roles)
    if has_role:
        return {"discord_id": str(member.id), "discord_mention": member.mention, "roblox_id": "linked"}
    return None


# â”€â”€ Embed builders â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    embed.add_field(name="\u200b",           value="\u200b",         inline=True)

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


def build_appeal_review_embed(
    discord_username: str,
    discord_id: str,
    avatar_url: str | None = None,
    **appeal_data
) -> discord.Embed:
    embed = discord.Embed(
        description="**__Ban Appeal Submitted__**",
        color=EMBED_COLOR,
    )
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    
    embed.add_field(name="Discord Username", value=discord_username, inline=True)
    embed.add_field(name="Discord ID", value=discord_id, inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    
    embed.add_field(name="Why were you banned?", 
                    value=appeal_data.get("why_banned", appeal_data.get("ban_reason", "N/A")), inline=False)
    embed.add_field(name="Why do you deserve to be unbanned?", 
                    value=appeal_data.get("why_unban", "N/A"), inline=False)
    embed.add_field(name="Time Since Ban", value=appeal_data.get("time_since_ban", "N/A"), inline=False)
    
    if appeal_data.get("extra_info"):
        embed.add_field(name="Do you have any extra information to provide?", 
                        value=appeal_data.get("extra_info"), inline=False)
    
    why_banned = appeal_data.get("why_banned", appeal_data.get("ban_reason", ""))
    if why_banned:
        embed.add_field(name="\u200b", value=f"<:alert:1522684494119960586> This user was banned for: {why_banned}", inline=False)
    
    embed.set_footer(text=f"Appeal ID: {appeal_data.get('appeal_id', 'N/A')}")
    return embed


def build_kick_reminder_embed(
    roblox_username: str,
    roblox_id: str,
    roblox_url: str,
    roblox_created: str,
    discord_info: dict | None,
    avatar_url: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        description="**__Ingame Kick - Rejoin Detected__**",
        color=EMBED_COLOR,
    )
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    
    embed.add_field(name="Roblox Username", value=f"**[{roblox_username}]({roblox_url})**", inline=True)
    embed.add_field(name="Roblox ID", value=roblox_id, inline=True)
    embed.add_field(name="Date of Creation", value=roblox_created, inline=True)
    
    if discord_info:
        embed.add_field(name="Discord Account", value=discord_info.get("discord_mention", "N/A"), inline=True)
        embed.add_field(name="Discord ID", value=discord_info.get("discord_id", "N/A"), inline=True)
        embed.add_field(name="Bloxlink Status", value="âœ… Linked", inline=True)
    else:
        embed.add_field(name="Discord Account", value="âŒ No Discord linked", inline=False)
    
    embed.add_field(name="Action Required", 
                    value="This user was kicked ingame and has rejoined within 30 minutes. Please ban them from the server to prevent further issues.", 
                    inline=False)
    
    embed.set_footer(text=f"Kicked at: {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    return embed


# â”€â”€ Daily reminder â€” runs at midnight UTC â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@tasks.loop(time=datetime.time(hour=0, minute=0, tzinfo=datetime.timezone.utc))
async def daily_reminder():
    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name="awaiting-bans")
        if not channel:
            print(f"[REMINDER] Channel 'awaiting-bans' not found in {guild.name}")
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
            roblox_id, roblox_url, _  = await get_roblox_info(roblox_username)
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


# â”€â”€ Welcome message on member join â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    if guild.id != GUILD_ID:
        return
    
    # â”€â”€ Check if user was recently kicked ingame (rejoin detection) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    member_id_str = str(member.id)
    if member_id_str in kicked_db:
        kick_data = kicked_db[member_id_str]
        kicked_time = datetime.datetime.fromisoformat(kick_data["kicked_at"])
        now = datetime.datetime.now(datetime.timezone.utc)
        time_diff = (now - kicked_time).total_seconds() / 60
        
        if time_diff <= KICK_REMINDER_WINDOW_MINUTES:
            roblox_username = kick_data.get("roblox_username", "Unknown")
            roblox_id = kick_data.get("roblox_id", "Unknown")
            roblox_url = kick_data.get("roblox_url", "N/A")
            roblox_created = kick_data.get("roblox_created", "Unknown")
            
            discord_info = await check_bloxlink_linked(member)
            avatar_url = await get_roblox_avatar_url(roblox_id) if roblox_id != "Unknown" else None
            
            embed = build_kick_reminder_embed(
                roblox_username=roblox_username,
                roblox_id=roblox_id,
                roblox_url=roblox_url,
                roblox_created=roblox_created,
                discord_info=discord_info,
                avatar_url=avatar_url or str(member.display_avatar.url),
            )
            
            mod_role = f"<@&{INGAME_MODERATING_ROLE_ID}>"
            reminder_channel = guild.get_channel(INGAME_REMINDER_CHANNEL_ID)
            if reminder_channel:
                await reminder_channel.send(content=mod_role, embed=embed)
            
            del kicked_db[member_id_str]
            save_json(KICKED_FILE, kicked_db)
    
    # —— Welcome message ————————————————————————————————————————————————————
    welcome_channel = guild.get_channel(WELCOME_CHANNEL_ID)
    if not welcome_channel:
        print(f"[WELCOME] Welcome channel {WELCOME_CHANNEL_ID} not found")
        return
    
    try:
        member_count = sum(1 for m in guild.members if not m.bot)
        ordinal = get_ordinal(member_count)
        
        content = (
            f"Welcome to **Oklahoma State Roleplay** {member.mention}.\n"
            f"You are our **{ordinal}** member!"
        )
        
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            emoji=discord.PartialEmoji(name="member", id=1523432455091261500),
            label=f"{member_count}",
            style=discord.ButtonStyle.secondary,
            disabled=True
        ))
        view.add_item(discord.ui.Button(
            emoji=discord.PartialEmoji(name="info", id=1523432618866118767),
            label="Dashboard",
            url="https://discord.com/channels/1517672283513294868/1517682110842798192",
            style=discord.ButtonStyle.link
        ))
        
        await welcome_channel.send(content=content, view=view)
        print(f"[WELCOME] Welcome message sent for {member.name} (ID: {member.id})")
    except Exception as e:
        print(f"[WELCOME] Error sending welcome message for {member.name}: {e}")


# â”€â”€ Appeal Modal â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class BanAppealModal(discord.ui.Modal, title="Ban Appeal Form"):
    def __init__(self, user: discord.User, appeal_token: str):
        self.user = user
        self.appeal_token = appeal_token
        
        self.discord_username = discord.ui.TextInput(
            label="Discord Username",
            default=f"{user.name}",
            required=True,
        )
        self.discord_id = discord.ui.TextInput(
            label="Discord ID",
            default=f"{user.id}",
            required=True,
        )
        self.why_banned = discord.ui.TextInput(
            label="Why were you banned?",
            placeholder="Explain what you were banned for...",
            required=True,
            style=discord.TextStyle.paragraph
        )
        self.why_unban = discord.ui.TextInput(
            label="Why do you deserve to be unbanned?",
            placeholder="Explain why you should be unbanned...",
            required=True,
            style=discord.TextStyle.paragraph
        )
        self.time_since_ban = discord.ui.TextInput(
            label="Time Since Ban",
            placeholder="How long ago were you banned?",
            required=True,
        )
        self.extra_info = discord.ui.TextInput(
            label="Do you have any extra information to provide?",
            placeholder="Any additional information? (Leave blank if none)",
            required=False,
            style=discord.TextStyle.paragraph
        )
        super().__init__(items=[self.discord_username, self.discord_id, self.why_banned, self.why_unban, self.time_since_ban, self.extra_info])

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        user_id = interaction.user.id
        appeal_id = f"{user_id}_{int(datetime.datetime.now().timestamp())}"
        
        # Verify this is the same user who was sent the form
        if interaction.user.id != self.user.id:
            await interaction.followup.send("âŒ This appeal form is not for your account.", ephemeral=True)
            return
        
        appeals_db[appeal_id] = {
            "user_id": str(user_id),
            "discord_username": self.discord_username.value,
            "discord_id": self.discord_id.value,
            "why_banned": self.why_banned.value,
            "why_unban": self.why_unban.value,
            "ban_reason": self.why_banned.value,
            "time_since_ban": self.time_since_ban.value,
            "extra_info": self.extra_info.value,
            "submitted_at": datetime.datetime.now().isoformat(),
            "status": "pending",
            "appeal_token": self.appeal_token
        }
        save_json(APPEALS_FILE, appeals_db)
        
        # Mark token as used
        if self.appeal_token in appeal_tokens_db:
            appeal_tokens_db[self.appeal_token]["used"] = True
            save_json(APPEAL_TOKENS_FILE, appeal_tokens_db)
        
        guild = bot.get_guild(GUILD_ID)
        appeals_channel = guild.get_channel(APPEALS_CHANNEL_ID)
        
        if not appeals_channel:
            await interaction.followup.send("âŒ Appeal channel not found. Please contact an admin.")
            return
        
        embed = build_appeal_review_embed(
            discord_username=self.discord_username.value,
            discord_id=self.discord_id.value,
            avatar_url=str(interaction.user.display_avatar.url),
            appeal_id=appeal_id,
            why_banned=self.why_banned.value,
            why_unban=self.why_unban.value,
            ban_reason=self.why_banned.value,
            time_since_ban=self.time_since_ban.value,
            extra_info=self.extra_info.value,
        )
        
        class AppealReviewView(discord.ui.View):
            def __init__(self):
                super().__init__()
            
            async def send_approve_dm(self, user_id, guild):
                try:
                    user = await bot.fetch_user(int(user_id))
                    invite = None
                    welcome_ch = guild.get_channel(WELCOME_CHANNEL_ID)
                    if welcome_ch:
                        try:
                            invite = await welcome_ch.create_invite(max_uses=1, max_age=86400)
                        except Exception:
                            pass
                    
                    msg = f"Your ban appeal has been reviewed and has been **approved**. You have been unbanned from **{guild.name}**."
                    if invite:
                        msg += f"\n\nHere is an invite to rejoin the server: {invite.url}\n\n*This invite expires in 24 hours.*"
                    await user.send(msg)
                except Exception:
                    pass
            
            async def send_deny_dm(self, user_id, guild):
                try:
                    user = await bot.fetch_user(int(user_id))
                    two_months = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=60)).strftime("%B %d, %Y")
                    await user.send(
                        f"Your ban appeal for **{guild.name}** has been reviewed and unfortunately has been **denied**.\n\n"
                        f"You may submit another appeal after **{two_months}** (2 months from today).\n\n"
                        f"We appreciate your understanding."
                    )
                except Exception:
                    pass
            
            @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
            async def approve_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                if not has_any_role(button_interaction.user, APPEAL_REVIEW_ROLES) and not has_staff_role(button_interaction.user):
                    await button_interaction.response.send_message("You don't have permission to approve appeals.", ephemeral=True)
                    return
                
                appeals_db[appeal_id]["status"] = "approved"
                save_json(APPEALS_FILE, appeals_db)
                
                try:
                    await guild.unban(discord.Object(int(user_id)), reason=f"Ban appeal approved - {button_interaction.user.name}")
                except Exception as e:
                    print(f"[APPEAL] Failed to unban {user_id}: {e}")
                
                await self.send_approve_dm(user_id, guild)
                
                embed.color = discord.Color.green()
                embed.description = "**__Ban Appeal - APPROVED__**"
                embed.add_field(name="Approved By", value=button_interaction.user.mention, inline=False)
                await button_interaction.response.edit_message(embed=embed, view=None)
            
            @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
            async def deny_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                if not has_any_role(button_interaction.user, APPEAL_REVIEW_ROLES) and not has_staff_role(button_interaction.user):
                    await button_interaction.response.send_message("You don't have permission to deny appeals.", ephemeral=True)
                    return
                
                appeals_db[appeal_id]["status"] = "denied"
                save_json(APPEALS_FILE, appeals_db)
                
                await self.send_deny_dm(user_id, guild)
                
                embed.color = discord.Color.red()
                embed.description = "**__Ban Appeal - DENIED__**"
                embed.add_field(name="Denied By", value=button_interaction.user.mention, inline=False)
                await button_interaction.response.edit_message(embed=embed, view=None)
        
        await appeals_channel.send(embed=embed, view=AppealReviewView())
        await interaction.followup.send("âœ… Your ban appeal has been submitted! We will review it and get back to you soon. The appeal ID is: " + appeal_id)


# â”€â”€ Events â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("OSRP Management bot is ready!")
    
    if not bot.synced:
        await bot.tree.sync()
        bot.synced = True
    
    daily_reminder.start()
    
    # Clean up expired kicked entries
    now = datetime.datetime.now(datetime.timezone.utc)
    expired = []
    for user_id_str, kick_data in kicked_db.items():
        kicked_time = datetime.datetime.fromisoformat(kick_data["kicked_at"])
        if (now - kicked_time).total_seconds() > KICK_REMINDER_WINDOW_MINUTES * 60:
            expired.append(user_id_str)
    for uid in expired:
        del kicked_db[uid]
    if expired:
        save_json(KICKED_FILE, kicked_db)
        print(f"[KICK] Cleaned up {len(expired)} expired kick entries")
    
    # Ensure Melonly form has staff blacklist role blocked
    asyncio.create_task(melonly_ensure_blocked_role())



@bot.event
async def on_message(message):
    if message.author.id == bot.user.id:
        return

    if message.id in processed_message_ids:
        return
    processed_message_ids.add(message.id)
    if len(processed_message_ids) > 1000:
        processed_message_ids.clear()

    if not message.guild:
        await bot.process_commands(message)
        return

    guild_id = str(message.guild.id)
    guild = message.guild

    # Directorship ping protection
    if not message.author.bot:
        exempt = message.author.get_role(DIRECTORSHIP_PING_EXEMPT_ROLE)
        if not exempt:
            target_member = None

            # Check direct @mentions
            for mentioned in message.mentions:
                if isinstance(mentioned, discord.Member) and mentioned.get_role(DIRECTORSHIP_ROLE_ID):
                    target_member = mentioned
                    break

            # Check reply pings
            if not target_member and message.reference and message.reference.resolved:
                ref = message.reference.resolved
                if isinstance(ref, discord.Message) and isinstance(ref.author, discord.Member) and ref.author.get_role(DIRECTORSHIP_ROLE_ID):
                    target_member = ref.author

            if target_member:
                now = time.time()
                last_ping = recent_ping_cooldown.get(message.author.id, 0)
                if now - last_ping < 5:
                    pass
                else:
                    recent_ping_cooldown[message.author.id] = now
                    embed = discord.Embed(
                        description="**Do not @ mention members of the Directorship Team.**\n@ mentioning directors is a violation of [rule 4](https://discord.com/channels/1517672283513294868/1517682110842798192).",
                        color=0x01d3ff
                    )
                    embed.set_image(url="https://media.tenor.com/7694799882666584177/discord-ping-off-no-ping-reply-ping.gif")
                    try:
                        reminder = await message.reply(content=message.author.mention, embed=embed)
                        asyncio.create_task(delete_after_delay(reminder, 20))
                    except Exception as e:
                        print(f"[ANTI-PING] Failed: {e}")
    # â”€â”€ React to Circle bot punishment embeds â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if message.author.bot and message.embeds:
        embed = message.embeds[0]
        title = (embed.title or "").lower()
        description = (embed.description or "").lower()

        matched_punishment = None
        for punishment, value in POINTS.items():
            if punishment in title or punishment in description:
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
                user_id_str = str(user_id)
                
                current_points = points_db.get(user_id_str, 0)
                current_points += matched_punishment[1]
                points_db[user_id_str] = current_points
                save_json(POINTS_FILE, points_db)

                if case_number:
                    processed_cases.add(case_number)
                    cases_db[case_number] = {
                        "user_id": user_id_str,
                        "punishment": matched_punishment[0],
                        "points": matched_punishment[1],
                        "guild_id": guild_id
                    }
                    save_json(CASES_FILE, cases_db)

                point_word = "point" if current_points == 1 else "points"
                
                mention = punished_user.mention if punished_user else f"<@{user_id}>"

                # Skip sending duplicate message if this user was just punished by a !command
                now = time.time()
                last_ts = recent_punishments.get(user_id_str)
                if last_ts and (now - last_ts) < 3:
                    recent_punishments.pop(user_id_str, None)
                else:
                    pts_msg = await message.channel.send(
                        f"{mention} now has **{current_points} {point_word}**." 
                        f" ({matched_punishment[0].title()}, +{matched_punishment[1]})"
                    )
                    asyncio.create_task(delete_after_delay(pts_msg, 25))
                
                # If it's a ban/temp ban, generate appeal token and DM
                if matched_punishment[0] in ("ban", "banned", "temp ban", "tempban", "temp banned"):
                    await handle_ban_appeal_dm(user_id, matched_punishment[0], current_points)

    # â”€â”€ Monitor ingame kick channel (ERLC webhooks) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if message.channel.id == INGAME_KICK_CHANNEL_ID and message.embeds:
        embed = message.embeds[0]
        title = (embed.title or "").lower()
        description = (embed.description or "").lower()
        
        roblox_username = find_roblox_username_in_embed(embed)
        if not roblox_username:
            await bot.process_commands(message)
            return
        
        # Check if this is a kick embed
        is_kick = any(w in title or w in description for w in ["kick", "kicked", "removed", "booted"])
        is_join = any(w in title or w in description for w in ["join", "joined", "connect", "connected"])
        
        if is_kick:
            roblox_id, roblox_url, roblox_created = await get_roblox_info(roblox_username)
            
            kicked_discord_user = None
            for member in guild.members:
                if extract_roblox_username(member).lower() == roblox_username.lower():
                    kicked_discord_user = member
                    break
            
            if kicked_discord_user:
                kicked_db[str(kicked_discord_user.id)] = {
                    "roblox_username": roblox_username,
                    "roblox_id": roblox_id,
                    "roblox_url": roblox_url,
                    "roblox_created": roblox_created,
                    "kicked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }
                save_json(KICKED_FILE, kicked_db)
                print(f"[KICK] Tracked {roblox_username} (Discord: {kicked_discord_user.id}) - will remind if they rejoin within {KICK_REMINDER_WINDOW_MINUTES} min")
        
        # Check if this is a join embed from someone recently kicked
        if is_join:
            await handle_erlc_rejoin(guild, roblox_username, embed)

    await bot.process_commands(message)


async def handle_ban_appeal_dm(user_id, punishment_type: str, total_points: int):
    """Generate an appeal token and DM the user with ban appeal instructions.
    user_id can be int or str - will try to fetch user even if not in server."""
    user_id_str = str(user_id)
    
    # Check blacklist - don't send appeal code if blacklisted
    if user_id_str in blacklist_db:
        try:
            user = await bot.fetch_user(int(user_id_str))
            msg = (
                f"You have been {punishment_type} from **Oklahoma State Roleplay**.\n\n"
                f"Your appeal has been **denied** and you are **blacklisted** from submitting an appeal. "
                f"This decision is final."
            )
            await user.send(msg)
            print(f"[APPEAL] Blacklisted user {user_id} - no appeal code sent")
        except Exception:
            pass
        return
    
    existing_token = None
    for token, data in appeal_tokens_db.items():
        if data.get("user_id") == user_id_str and not data.get("used"):
            existing_token = token
            break
    
    if not existing_token:
        token = generate_appeal_token()
        appeal_tokens_db[token] = {
            "user_id": user_id_str,
            "punishment": punishment_type,
            "total_points": total_points,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "used": False
        }
        save_json(APPEAL_TOKENS_FILE, appeal_tokens_db)
    else:
        token = existing_token
    
    dm_sent = False
    dm_error = None
    try:
        user = await bot.fetch_user(int(user_id_str))
        appeal_url = f"{BASE_URL}/appeal"
        msg = (
            f"Your ban appeal code for **Oklahoma State Roleplay:**\n\n"
            f"`{{ {token} }}`\n\n"
            f"Go to {appeal_url} and enter this code to submit your appeal.\n\n"
            f"**This code is unique to you, sharing it can result in a permanent ban\n_ _\nwithout appeal from the server.**"
        )
        await user.send(msg)
        dm_sent = True
        print(f"[APPEAL] DM sent to {user_id} with appeal token {token}")
    except discord.Forbidden:
        dm_error = "DMs closed"
        print(f"[APPEAL] Cannot DM {user_id} - DMs closed")
    except discord.NotFound:
        dm_error = "User not found"
        print(f"[APPEAL] User {user_id} not found")
    except Exception as e:
        dm_error = str(e)
        print(f"[APPEAL] Failed to send DM to {user_id}: {e}")

    # Send status to Circle modlogs channel
    guild = bot.get_guild(GUILD_ID)
    if guild:
        modlog_channel = guild.get_channel(CIRCLE_MODLOGS_CHANNEL_ID)
        if modlog_channel:
            status_text = "DM Sent" if dm_sent else f"DM Failed ({dm_error})"
            status_color = EMBED_COLOR if dm_sent else 0xf85149
            log_embed = discord.Embed(
                description=f"**__Appeal Link DM {'Sent' if dm_sent else 'Failed'}__**",
                color=status_color,
            )
            try:
                u = await bot.fetch_user(int(user_id_str))
                log_embed.set_thumbnail(url=u.display_avatar.url)
                log_embed.add_field(name="User", value=f"{u.mention} ({u})", inline=True)
            except Exception:
                log_embed.add_field(name="User ID", value=user_id_str, inline=True)
            log_embed.add_field(name="Status", value=status_text, inline=True)
            log_embed.add_field(name="Punishment", value=punishment_type.title(), inline=True)
            log_embed.add_field(name="Token", value=f"`{token}`", inline=False)
            try:
                await modlog_channel.send(embed=log_embed)
            except Exception:
                pass


async def handle_erlc_rejoin(guild: discord.Guild, roblox_username: str, embed: discord.Embed):
    """Called when ERLC sends a join webhook - checks if the player was recently kicked."""
    # Find if this roblox user has a Discord account in the server
    rejoin_member = None
    for member in guild.members:
        if extract_roblox_username(member).lower() == roblox_username.lower():
            rejoin_member = member
            break
    
    if not rejoin_member:
        return
    
    member_id_str = str(rejoin_member.id)
    if member_id_str not in kicked_db:
        return
    
    kick_data = kicked_db[member_id_str]
    kicked_time = datetime.datetime.fromisoformat(kick_data["kicked_at"])
    now = datetime.datetime.now(datetime.timezone.utc)
    time_diff = (now - kicked_time).total_seconds() / 60
    
    if time_diff > KICK_REMINDER_WINDOW_MINUTES:
        del kicked_db[member_id_str]
        save_json(KICKED_FILE, kicked_db)
        return
    
    # Send kick reminder
    reminder_channel = guild.get_channel(INGAME_REMINDER_CHANNEL_ID)
    if not reminder_channel:
        return
    
    roblox_id = kick_data.get("roblox_id", "Unknown")
    roblox_url = kick_data.get("roblox_url", "N/A")
    roblox_created = kick_data.get("roblox_created", "Unknown")
    
    discord_info = await check_bloxlink_linked(rejoin_member)
    avatar_url = await get_roblox_avatar_url(roblox_id) if roblox_id != "Unknown" else None
    
    reminder_embed = build_kick_reminder_embed(
        roblox_username=roblox_username,
        roblox_id=roblox_id,
        roblox_url=roblox_url,
        roblox_created=roblox_created,
        discord_info=discord_info,
        avatar_url=avatar_url or str(rejoin_member.display_avatar.url),
    )
    
    mod_role = f"<@&{INGAME_MODERATING_ROLE_ID}>"
    await reminder_channel.send(content=mod_role, embed=reminder_embed)
    print(f"[KICK] Rejoin detected for {roblox_username} - ban reminder sent")
    
    del kicked_db[member_id_str]
    save_json(KICKED_FILE, kicked_db)


# â”€â”€ Web server for ban appeal website â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

APPEAL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OSRP Ban Appeal</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --bg-primary: #0b0d14;
            --bg-secondary: #11131e;
            --bg-card: #161a2b;
            --border: #232841;
            --text-primary: #e2e8f0;
            --text-secondary: #8892a4;
            --accent: #4fc3f7;
            --accent-dim: rgba(79, 195, 247, 0.12);
            --accent-glow: rgba(79, 195, 247, 0.15);
            --green: #4ade80;
            --red: #f87171;
            --orange: #fb923c;
            --radius: 12px;
        }
        body {
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            background-image: radial-gradient(ellipse at 50% 0%, rgba(79, 195, 247, 0.06) 0%, transparent 60%);
        }
        .card {
            max-width: 580px;
            width: 100%;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 40px 36px;
            box-shadow: 0 24px 80px rgba(0,0,0,0.6);
            position: relative;
            overflow: hidden;
        }
        .card::before {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 2px;
            background: linear-gradient(90deg, transparent, var(--accent), transparent);
            opacity: 0.5;
        }
        .logo {
            display: flex;
            align-items: center;
            gap: 14px;
            margin-bottom: 24px;
        }
        .logo-icon {
            width: 44px; height: 44px;
            background: linear-gradient(135deg, var(--accent), #0288d1);
            border-radius: 12px;
            display: flex; align-items: center; justify-content: center;
            font-size: 18px; font-weight: 800; color: #0b0d14;
            flex-shrink: 0;
            box-shadow: 0 0 24px var(--accent-glow);
        }
        .logo-text h1 { font-size: 20px; font-weight: 700; }
        .logo-text .sub { font-size: 12px; color: var(--text-secondary); margin-top: 1px; }
        .subtitle { color: var(--text-secondary); font-size: 13px; margin-bottom: 28px; line-height: 1.5; }

        .steps {
            display: flex;
            gap: 6px;
            margin-bottom: 28px;
            padding: 4px;
            background: var(--bg-primary);
            border-radius: 10px;
            border: 1px solid var(--border);
        }
        .step {
            flex: 1;
            text-align: center;
            padding: 8px 12px;
            font-size: 12px;
            color: var(--text-secondary);
            border-radius: 8px;
            transition: all 0.3s;
            font-weight: 500;
        }
        .step.active {
            background: var(--accent);
            color: #0b0d14;
            font-weight: 700;
        }
        .step.done { color: var(--green); }

        .code-section { text-align: center; padding: 12px 0 4px; }
        .code-section p { color: var(--text-secondary); font-size: 14px; margin-bottom: 20px; }
        .code-input-wrap {
            display: flex;
            gap: 10px;
            max-width: 380px;
            margin: 0 auto;
        }
        .code-input {
            flex: 1;
            text-align: center;
            font-size: 26px;
            letter-spacing: 8px;
            text-transform: uppercase;
            font-weight: 700;
            background: var(--bg-primary);
            border: 2px solid var(--border);
            border-radius: var(--radius);
            padding: 12px 14px;
            color: var(--text-primary);
            font-family: 'Consolas', monospace;
            transition: border-color 0.25s, box-shadow 0.25s;
        }
        .code-input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-dim); }
        .code-input::placeholder { letter-spacing: 4px; font-size: 18px; color: #2a3145; }
        .code-input-wrap button {
            background: linear-gradient(135deg, var(--accent), #0288d1);
            color: #0b0d14;
            font-weight: 700;
            border: none;
            border-radius: var(--radius);
            padding: 12px 22px;
            font-size: 14px;
            cursor: pointer;
            transition: opacity 0.2s, transform 0.1s;
            white-space: nowrap;
        }
        .code-input-wrap button:hover { opacity: 0.9; transform: translateY(-1px); }
        .code-input-wrap button:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }

        .info-grid {
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 16px 20px;
            margin-bottom: 24px;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }
        .info-item .label { font-size: 10px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; }
        .info-item .value { font-size: 14px; font-weight: 600; color: var(--text-primary); margin-top: 3px; }

        form { display: flex; flex-direction: column; gap: 16px; }
        .form-group label { display: block; font-size: 11px; font-weight: 600; color: var(--text-secondary); margin-bottom: 5px; text-transform: uppercase; letter-spacing: 0.5px; }
        .form-group input, .form-group textarea {
            width: 100%;
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 10px 14px;
            color: var(--text-primary);
            font-size: 13px;
            font-family: inherit;
            transition: border-color 0.2s;
        }
        .form-group input:focus, .form-group textarea:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-dim); }
        .form-group input:disabled { opacity: 0.5; cursor: not-allowed; }
        .form-group textarea { resize: vertical; min-height: 90px; }
        .field-note { font-size: 11px; color: var(--text-secondary); margin-top: -4px; }

        .btn-submit {
            background: linear-gradient(135deg, var(--accent), #0288d1);
            color: #0b0d14;
            font-weight: 700;
            border: none;
            border-radius: var(--radius);
            padding: 14px 24px;
            font-size: 15px;
            cursor: pointer;
            transition: opacity 0.2s, transform 0.1s;
            margin-top: 4px;
        }
        .btn-submit:hover { opacity: 0.9; transform: translateY(-1px); }
        .btn-submit:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }

        .alert {
            padding: 14px 18px;
            border-radius: var(--radius);
            font-size: 13px;
            line-height: 1.6;
            display: flex;
            align-items: flex-start;
            gap: 10px;
            margin-bottom: 20px;
        }
        .alert-icon { font-size: 18px; flex-shrink: 0; margin-top: 1px; }
        .alert-error { background: rgba(248, 113, 113, 0.08); border: 1px solid rgba(248, 113, 113, 0.25); color: var(--red); }
        .alert-success { background: rgba(74, 222, 128, 0.08); border: 1px solid rgba(74, 222, 128, 0.25); color: var(--green); text-align: center; }
        .alert-warning { background: rgba(251, 146, 60, 0.08); border: 1px solid rgba(251, 146, 60, 0.25); color: var(--orange); }
        .alert-info { background: var(--accent-dim); border: 1px solid rgba(79, 195, 247, 0.25); color: var(--accent); }
        .hidden { display: none !important; }

        .blacklist-box {
            background: rgba(248, 113, 113, 0.06);
            border: 1px solid rgba(248, 113, 113, 0.2);
            border-radius: var(--radius);
            padding: 28px 24px;
            text-align: center;
        }
        .blacklist-box .bl-icon { font-size: 40px; margin-bottom: 12px; }
        .blacklist-box h3 { font-size: 18px; font-weight: 700; color: var(--red); margin-bottom: 8px; }
        .blacklist-box p { font-size: 13px; line-height: 1.6; color: var(--text-secondary); }
        .blacklist-box .bl-note { margin-top: 12px; padding: 10px 14px; background: var(--bg-primary); border-radius: 8px; font-size: 12px; color: var(--text-secondary); border: 1px solid var(--border); }

        .success-content { text-align: center; padding: 16px 0; }
        .success-content .check { font-size: 44px; margin-bottom: 14px; }
        .success-content h2 { font-size: 20px; font-weight: 700; margin-bottom: 8px; color: var(--green); }
        .success-content p { font-size: 13px; line-height: 1.6; color: var(--text-secondary); }
        .success-content .appeal-id { display: inline-block; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; padding: 6px 14px; font-family: 'Consolas', monospace; font-size: 12px; margin-top: 12px; color: var(--accent); }

        input:-webkit-autofill { -webkit-box-shadow: 0 0 0 1000px var(--bg-primary) inset; -webkit-text-fill-color: var(--text-primary); }
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">
            <div class="logo-icon">OS</div>
            <div class="logo-text">
                <h1>OSRP Ban Appeal</h1>
                <div class="sub">Oklahoma State Roleplay</div>
            </div>
        </div>
        
        <div id="alert-area"></div>
        
        <div class="steps">
            <div class="step active" id="step-1">Enter Code</div>
            <div class="step" id="step-2">Review</div>
            <div class="step" id="step-3">Submit</div>
        </div>
        
        <div id="code-section">
            <div class="code-section">
                <p>Enter your 10-character appeal code from Discord.</p>
                <div class="code-input-wrap">
                    <input type="text" id="code-input" class="code-input" maxlength="10" placeholder="CODE" autocomplete="off" spellcheck="false">
                    <button id="code-submit-btn">Verify</button>
                </div>
            </div>
        </div>
        
        <div id="info-section" class="hidden">
            <div class="info-grid">
                <div class="info-item">
                    <div class="label">Discord</div>
                    <div class="value" id="info-discord">—</div>
                </div>
                <div class="info-item">
                    <div class="label">Punishment</div>
                    <div class="value" id="info-punishment">—</div>
                </div>
                <div class="info-item">
                    <div class="label">Status</div>
                    <div class="value" id="info-status">—</div>
                </div>
                <div class="info-item">
                    <div class="label">Total Points</div>
                    <div class="value" id="info-points">—</div>
                </div>
            </div>
        </div>
        
        <div id="cooldown-box" class="alert alert-warning hidden">
            <span class="alert-icon">&#9888;</span>
            <div>
                <strong>Cooldown Active</strong><br>
                <span id="cooldown-text"></span>
            </div>
        </div>
        
        <div id="blacklist-box" class="hidden">
            <div class="blacklist-box">
                <div class="bl-icon">&#128274;</div>
                <h3>Appeal Access Revoked</h3>
                <p id="bl-message"></p>
                <div id="bl-note-display" class="bl-note hidden"></div>
            </div>
        </div>
        
        <form id="appeal-form" class="hidden">
            <div class="form-group">
                <label>Discord Username</label>
                <input type="text" id="discord_username" disabled>
            </div>
            <div class="form-group">
                <label>Discord ID</label>
                <input type="text" id="discord_id" disabled>
            </div>
            <div class="form-group">
                <label>Why were you banned?</label>
                <textarea id="why_banned" required placeholder="Tell us what happened..."></textarea>
            </div>
            <div class="form-group">
                <label>Why should you be unbanned?</label>
                <textarea id="why_unban" required placeholder="Explain why you deserve a second chance..."></textarea>
            </div>
            <div class="form-group">
                <label>Time Since Ban</label>
                <input type="text" id="time_since_ban" placeholder="e.g. 2 months, 3 weeks" required>
            </div>
            <div class="form-group">
                <label>Extra Information</label>
                <textarea id="extra_info" placeholder="Anything else we should know? (Optional)"></textarea>
                <div class="field-note">Additional context that may help your case</div>
            </div>
            <button type="submit" class="btn-submit" id="submit-btn">Submit Appeal</button>
        </form>
        
        <div id="success-box" class="hidden">
            <div class="success-content">
                <div class="check">&#10003;</div>
                <h2>Appeal Submitted</h2>
                <p>Your appeal has been received. Staff will review it and you will be notified via Discord.</p>
                <div class="appeal-id" id="success-appeal-id"></div>
            </div>
        </div>
    </div>
    
    <script>
        var tokenInput = document.getElementById('code-input');
        var codeSection = document.getElementById('code-section');
        var infoSection = document.getElementById('info-section');
        var appealForm = document.getElementById('appeal-form');
        var alertArea = document.getElementById('alert-area');
        var successBox = document.getElementById('success-box');
        var cooldownBox = document.getElementById('cooldown-box');
        var blacklistBox = document.getElementById('blacklist-box');
        var currentToken = null;
        
        function showError(msg) {
            alertArea.innerHTML = '<div class="alert alert-error"><span class="alert-icon">&#10007;</span><span>' + msg + '</span></div>';
        }
        function clearAlerts() { alertArea.innerHTML = ''; }
        
        function updateSteps(active) {
            for (var i = 1; i <= 3; i++) {
                var s = document.getElementById('step-' + i);
                s.classList.remove('active', 'done');
                if (i < active) s.classList.add('done');
                else if (i === active) s.classList.add('active');
            }
        }
        
        tokenInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                document.getElementById('code-submit-btn').click();
            }
        });
        
        document.getElementById('code-submit-btn').addEventListener('click', function() {
            var token = tokenInput.value.trim().toUpperCase();
            if (token.length !== 10) {
                showError('Please enter a valid 10-character appeal code.');
                return;
            }
            clearAlerts();
            blacklistBox.classList.add('hidden');
            var btn = this;
            btn.disabled = true;
            btn.textContent = 'Checking...';
            
            fetch('/api/appeal/info?token=' + encodeURIComponent(token))
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    btn.disabled = false;
                    btn.textContent = 'Verify';
                    
                    if (data.error === 'blacklisted') {
                        var dur = data.bl_duration || 'permanent';
                        var note = data.bl_note || '';
                        var msgEl = document.getElementById('bl-message');
                        var noteEl = document.getElementById('bl-note-display');
                        if (dur === 'permanent') {
                            msgEl.textContent = 'You have been permanently barred from submitting an appeal on Oklahoma State Roleplay. This decision is final and cannot be overturned.';
                        } else if (dur === '3m') {
                            msgEl.textContent = 'You are barred from submitting an appeal for 3 months. Please wait until the restriction period has passed.';
                        } else if (dur === '6m') {
                            msgEl.textContent = 'You are barred from submitting an appeal for 6 months. Please wait until the restriction period has passed.';
                        }
                        if (note) {
                            noteEl.textContent = 'Reason: ' + note;
                            noteEl.classList.remove('hidden');
                        } else {
                            noteEl.classList.add('hidden');
                        }
                        codeSection.classList.add('hidden');
                        blacklistBox.classList.remove('hidden');
                        tokenInput.disabled = true;
                        btn.disabled = true;
                        return;
                    }
                    if (data.error) {
                        showError(data.error);
                        return;
                    }
                    
                    if (data.used) {
                        showError('This code has already been used. You need a fresh appeal code.');
                        return;
                    }
                    
                    currentToken = token;
                    updateSteps(2);
                    codeSection.classList.add('hidden');
                    
                    document.getElementById('info-discord').textContent = data.discord_username + ' (#' + data.discord_id + ')';
                    document.getElementById('info-punishment').textContent = data.punishment;
                    document.getElementById('info-status').textContent = data.cooldown_active ? 'Cooldown Active' : 'Eligible';
                    document.getElementById('info-points').textContent = data.total_points;
                    infoSection.classList.remove('hidden');
                    
                    document.getElementById('discord_username').value = data.discord_username;
                    document.getElementById('discord_id').value = data.discord_id;
                    
                    if (data.cooldown_active) {
                        cooldownBox.classList.remove('hidden');
                        document.getElementById('cooldown-text').innerHTML = 'You can submit an appeal after <strong>' + data.cooldown_ends + '</strong>.';
                        document.getElementById('submit-btn').disabled = true;
                    } else {
                        updateSteps(3);
                        appealForm.classList.remove('hidden');
                    }
                })
                .catch(function(err) {
                    btn.disabled = false;
                    btn.textContent = 'Verify';
                    showError('Failed to load appeal info. Try again later.');
                });
        });
        
        appealForm.addEventListener('submit', function(e) {
            e.preventDefault();
            clearAlerts();
            var submitBtn = document.getElementById('submit-btn');
            submitBtn.disabled = true;
            submitBtn.textContent = 'Submitting...';
            
            fetch('/api/appeal/submit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    token: currentToken,
                    discord_username: document.getElementById('discord_username').value,
                    discord_id: document.getElementById('discord_id').value,
                    why_banned: document.getElementById('why_banned').value,
                    why_unban: document.getElementById('why_unban').value,
                    ban_reason: document.getElementById('why_banned').value,
                    time_since_ban: document.getElementById('time_since_ban').value,
                    extra_info: document.getElementById('extra_info').value
                })
            })
            .then(function(r) { return r.json(); })
            .then(function(response) {
                if (response.success) {
                    appealForm.classList.add('hidden');
                    infoSection.classList.add('hidden');
                    cooldownBox.classList.add('hidden');
                    document.getElementById('success-appeal-id').textContent = response.appeal_id;
                    successBox.classList.remove('hidden');
                    updateSteps(0);
                } else if (response.error === 'blacklisted') {
                    showError('You cannot submit this appeal. Your access has been restricted.');
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Submit Appeal';
                } else {
                    showError(response.error || 'Submission failed. Try again.');
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Submit Appeal';
                }
            })
            .catch(function(err) {
                showError('Network error. Try again.');
                submitBtn.disabled = false;
                submitBtn.textContent = 'Submit Appeal';
            });
        });
    </script>
</body>
</html>"""


async def handle_appeal_info(request):
    token = request.query.get("token", "")
    token_data = appeal_tokens_db.get(token)
    
    if not token_data:
        return web.json_response({"error": "Invalid or expired appeal code."}, status=404)
    
    user_id = token_data["user_id"]
    
    if user_id in blacklist_db:
        bl = blacklist_db[user_id]
        bl_duration = bl.get("duration", "permanent")
        bl_note = bl.get("note", "")
        bl_barred_until = bl.get("barred_until", None)
        return web.json_response({
            "error": "blacklisted",
            "bl_duration": bl_duration,
            "bl_note": bl_note,
            "bl_barred_until": bl_barred_until,
        }, status=403)
    
    # Look up Discord user info (works even for banned users via fetch_user)
    discord_username = "Unknown"
    try:
        user = await bot.fetch_user(int(user_id))
        discord_username = str(user)
    except Exception:
        guild = bot.get_guild(GUILD_ID)
        if guild:
            member = guild.get_member(int(user_id))
            if member:
                discord_username = str(member)
    
    # Check cooldown
    now = datetime.datetime.now(datetime.timezone.utc)
    user_appeals = {k: v for k, v in appeals_db.items() if v.get("user_id") == user_id}
    denied_appeals = [a for a in user_appeals.values() if a.get("status") == "denied"]
    
    cooldown_active = False
    cooldown_ends = ""
    if denied_appeals:
        # Subsequent appeal: 2 month cooldown from last denial
        latest_denied = max(denied_appeals, key=lambda a: a.get("submitted_at", ""))
        denied_at = datetime.datetime.fromisoformat(latest_denied["submitted_at"])
        days_since_denied = (now - denied_at).days
        cooldown_active = days_since_denied < APPEAL_COOLDOWN_DAYS
        cooldown_ends = (denied_at + datetime.timedelta(days=APPEAL_COOLDOWN_DAYS)).strftime("%Y-%m-%d %H:%M UTC")
    else:
        # First appeal: 2 week cooldown from token creation
        created_at = datetime.datetime.fromisoformat(token_data["created_at"])
        days_since_created = (now - created_at).days
        cooldown_active = days_since_created < FIRST_APPEAL_COOLDOWN_DAYS
        cooldown_ends = (created_at + datetime.timedelta(days=FIRST_APPEAL_COOLDOWN_DAYS)).strftime("%Y-%m-%d %H:%M UTC")
    
    return web.json_response({
        "discord_username": discord_username,
        "discord_id": user_id,
        "punishment": token_data.get("punishment", "Unknown"),
        "total_points": token_data.get("total_points", 0),
        "cooldown_active": cooldown_active,
        "cooldown_ends": cooldown_ends,
        "used": token_data.get("used", False),
        "created_at": token_data["created_at"]
    })


async def handle_appeal_submit(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body."}, status=400)
    
    token = body.get("token", "")
    why_banned = body.get("why_banned", "").strip()
    why_unban = body.get("why_unban", "").strip()
    ban_reason = body.get("ban_reason", why_banned).strip()
    time_since_ban = body.get("time_since_ban", "").strip()
    extra_info = body.get("extra_info", "").strip()
    discord_username = body.get("discord_username", "").strip()
    discord_id = body.get("discord_id", "").strip()
    
    if not token or not why_banned or not time_since_ban:
        return web.json_response({"error": "Required fields missing."}, status=400)
    
    token_data = appeal_tokens_db.get(token)
    if not token_data:
        return web.json_response({"error": "Invalid or expired appeal link."}, status=404)
    
    if token_data.get("used"):
        return web.json_response({"error": "This appeal link has already been used."}, status=400)
    
    user_id = token_data["user_id"]
    
    if user_id in blacklist_db:
        return web.json_response({"error": "blacklisted"}, status=403)
    
    # Verify that the submitted Discord ID matches the token's user
    if discord_id != user_id:
        return web.json_response({"error": "Discord ID mismatch. This appeal link is not for this account."}, status=403)
    
    # Check cooldown
    now = datetime.datetime.now(datetime.timezone.utc)
    user_appeals = {k: v for k, v in appeals_db.items() if v.get("user_id") == user_id}
    denied_appeals = [a for a in user_appeals.values() if a.get("status") == "denied"]
    if denied_appeals:
        latest_denied = max(denied_appeals, key=lambda a: a.get("submitted_at", ""))
        denied_at = datetime.datetime.fromisoformat(latest_denied["submitted_at"])
        days_since_denied = (now - denied_at).days
        if days_since_denied < APPEAL_COOLDOWN_DAYS:
            cooldown_ends = (denied_at + datetime.timedelta(days=APPEAL_COOLDOWN_DAYS)).strftime("%Y-%m-%d %H:%M UTC")
            return web.json_response({
                "error": f"You are still on cooldown. You must wait 2 months between appeals. You can submit after {cooldown_ends}."
            }, status=400)
    else:
        # First appeal: 2 week cooldown from token creation
        created_at = datetime.datetime.fromisoformat(token_data["created_at"])
        days_since_created = (now - created_at).days
        if days_since_created < FIRST_APPEAL_COOLDOWN_DAYS:
            cooldown_ends = (created_at + datetime.timedelta(days=FIRST_APPEAL_COOLDOWN_DAYS)).strftime("%Y-%m-%d %H:%M UTC")
            return web.json_response({
                "error": f"You must wait 2 weeks before your first appeal. You can submit after {cooldown_ends}."
            }, status=400)
    
    # Create the appeal
    appeal_id = f"{user_id}_{int(now.timestamp())}"
    
    appeals_db[appeal_id] = {
        "user_id": user_id,
        "discord_username": discord_username,
        "discord_id": discord_id,
        "why_banned": why_banned,
        "why_unban": why_unban,
        "ban_reason": ban_reason,
        "time_since_ban": time_since_ban,
        "extra_info": extra_info,
        "submitted_at": now.isoformat(),
        "status": "pending",
        "appeal_token": token,
        "source": "web"
    }
    save_json(APPEALS_FILE, appeals_db)
    
    # Mark token as used
    token_data["used"] = True
    save_json(APPEAL_TOKENS_FILE, appeal_tokens_db)
    
    # Send to appeals channel
    guild = bot.get_guild(GUILD_ID)
    if guild:
        appeals_channel = guild.get_channel(APPEALS_CHANNEL_ID)
        if appeals_channel:
            member = guild.get_member(int(user_id))
            avatar_url = str(member.display_avatar.url) if member else None
            
            embed = build_appeal_review_embed(
                discord_username=discord_username,
                discord_id=discord_id,
                avatar_url=avatar_url,
                appeal_id=appeal_id,
                why_banned=why_banned,
                why_unban=why_unban,
                ban_reason=ban_reason,
                time_since_ban=time_since_ban,
                extra_info=extra_info,
            )
            
            class AppealReviewView(discord.ui.View):
                def __init__(self):
                    super().__init__()
                
                async def send_approve_dm(self, user_id, guild_obj):
                    try:
                        user = await bot.fetch_user(int(user_id))
                        invite = None
                        welcome_ch = guild_obj.get_channel(WELCOME_CHANNEL_ID)
                        if welcome_ch:
                            try:
                                invite = await welcome_ch.create_invite(max_uses=1, max_age=86400)
                            except Exception:
                                pass
                        
                        msg = f"Your ban appeal has been reviewed and has been **approved**. You have been unbanned from **{guild_obj.name}**."
                        if invite:
                            msg += f"\n\nHere is an invite to rejoin the server: {invite.url}\n\n*This invite expires in 24 hours.*"
                        await user.send(msg)
                    except Exception:
                        pass
                
                async def send_deny_dm(self, user_id, guild_obj):
                    try:
                        user = await bot.fetch_user(int(user_id))
                        three_months = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=90)).strftime("%B %d, %Y")
                        await user.send(
                            f"Your ban appeal for **{guild_obj.name}** has been reviewed and unfortunately has been **denied**.\n\n"
                            f"You may submit another appeal after **{three_months}** (3 months from today).\n\n"
                            f"We appreciate your understanding."
                        )
                    except Exception:
                        pass
                
                @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
                async def approve_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                    if not has_any_role(button_interaction.user, APPEAL_REVIEW_ROLES) and not has_staff_role(button_interaction.user):
                        await button_interaction.response.send_message("You don't have permission to approve appeals.", ephemeral=True)
                        return
                    
                    appeals_db[appeal_id]["status"] = "approved"
                    save_json(APPEALS_FILE, appeals_db)
                    
                    try:
                        await guild.unban(discord.Object(int(user_id)), reason=f"Ban appeal approved - {button_interaction.user.name}")
                    except Exception as e:
                        print(f"[APPEAL] Failed to unban {user_id}: {e}")
                    
                    await self.send_approve_dm(user_id, guild)
                    
                    embed.color = discord.Color.green()
                    embed.description = "**__Ban Appeal - APPROVED__**"
                    embed.add_field(name="Approved By", value=button_interaction.user.mention, inline=False)
                    await button_interaction.response.edit_message(embed=embed, view=None)
                
                @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
                async def deny_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                    if not has_any_role(button_interaction.user, APPEAL_REVIEW_ROLES) and not has_staff_role(button_interaction.user):
                        await button_interaction.response.send_message("You don't have permission to deny appeals.", ephemeral=True)
                        return
                    
                    appeals_db[appeal_id]["status"] = "denied"
                    save_json(APPEALS_FILE, appeals_db)
                    
                    await self.send_deny_dm(user_id, guild)
                    
                    embed.color = discord.Color.red()
                    embed.description = "**__Ban Appeal - DENIED__**"
                    embed.add_field(name="Denied By", value=button_interaction.user.mention, inline=False)
                    await button_interaction.response.edit_message(embed=embed, view=None)
            
            await appeals_channel.send(embed=embed, view=AppealReviewView())
    
    return web.json_response({"success": True, "appeal_id": appeal_id})


async def handle_appeal_page(request):
    return web.Response(text=APPEAL_HTML, content_type="text/html")


# â”€â”€ Dashboard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OSRP Staff Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --bg-primary: #090b12;
            --bg-secondary: #0f121e;
            --bg-card: #151929;
            --bg-card-hover: #1b2037;
            --border: #222844;
            --text-primary: #e2e8f0;
            --text-secondary: #8892a4;
            --accent: #4fc3f7;
            --accent-dim: rgba(79, 195, 247, 0.1);
            --accent-glow: rgba(79, 195, 247, 0.12);
            --green: #4ade80;
            --red: #f87171;
            --orange: #fb923c;
            --purple: #a78bfa;
            --radius: 10px;
        }
        body {
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            background: linear-gradient(135deg, #0b1120 0%, #0f1923 25%, #0a0f1a 50%, #0d1525 75%, #090e18 100%);
            color: var(--text-primary);
            min-height: 100vh;
        }
        .login-page {
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .login-card {
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 48px 40px;
            width: 100%;
            max-width: 420px;
            text-align: center;
            box-shadow: 0 20px 60px rgba(0,0,0,0.5);
        }
        .login-card .logo {
            width: 64px; height: 64px;
            background: linear-gradient(135deg, var(--accent), #0288d1);
            border-radius: 16px;
            display: flex; align-items: center; justify-content: center;
            font-size: 28px; font-weight: 800; color: #fff;
            margin: 0 auto 20px;
            box-shadow: 0 0 30px var(--accent-glow);
        }
        .login-card h1 { font-size: 22px; font-weight: 700; margin-bottom: 4px; }
        .login-card p { color: var(--text-secondary); font-size: 14px; margin-bottom: 28px; }
        .login-card input {
            width: 100%;
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 12px 16px;
            color: var(--text-primary);
            font-size: 14px;
            margin-bottom: 16px;
            transition: border-color 0.2s;
        }
        .login-card input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }
        .login-card button {
            width: 100%;
            background: linear-gradient(135deg, var(--accent), #0288d1);
            color: #fff;
            border: none;
            border-radius: 8px;
            padding: 12px;
            font-size: 15px;
            font-weight: 700;
            cursor: pointer;
            transition: opacity 0.2s, transform 0.1s;
        }
        .login-card button:hover { opacity: 0.9; }
        .login-card button:active { transform: scale(0.98); }

        .app-layout {
            max-width: 960px;
            margin: 0 auto;
            padding: 20px;
        }
        .topbar {
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 20px 24px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 16px;
        }
        .topbar-info { flex: 1; min-width: 0; }
        .topbar-info h1 { font-size: 20px; font-weight: 700; }
        .topbar-info .sub { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }
        .topbar-actions { display: flex; gap: 8px; flex-shrink: 0; }
        .topbar-actions button {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 8px 16px;
            color: var(--text-primary);
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
            font-family: inherit;
        }
        .topbar-actions button:hover { background: var(--bg-card-hover); }
        .topbar-actions button.danger { color: var(--red); }
        .topbar-actions button.danger:hover { background: rgba(248, 81, 73, 0.1); }

        .nav-bar {
            display: flex;
            gap: 4px;
            margin-bottom: 20px;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 6px;
        }
        .nav-item {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px 18px;
            border-radius: 8px;
            cursor: pointer;
            color: var(--text-secondary);
            font-size: 13px;
            font-weight: 500;
            transition: all 0.15s;
            border: none;
            background: none;
            font-family: inherit;
            white-space: nowrap;
        }
        .nav-item:hover { background: var(--bg-card); color: var(--text-primary); }
        .nav-item.active { background: var(--accent); color: #fff; font-weight: 600; }
        .nav-item .nav-badge {
            margin-left: 4px;
            background: var(--bg-card);
            border-radius: 10px;
            padding: 1px 8px;
            font-size: 11px;
            color: var(--text-secondary);
            font-weight: 600;
        }
        .nav-item.active .nav-badge { background: rgba(255,255,255,0.2); color: #fff; }
        .nav-spacer { flex: 1; }
        .nav-item.danger { color: var(--red); }
        .nav-item.danger:hover { background: rgba(248, 81, 73, 0.1); }

        .content { }

        .panel { display: none; }
        .panel.active { display: block; }

        .card {
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            overflow: hidden;
        }
        .card-header {
            padding: 14px 20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .card-header h2 { font-size: 14px; font-weight: 700; }
        .card-header .badge {
            background: var(--bg-card);
            border-radius: 12px;
            padding: 2px 10px;
            font-size: 11px;
            color: var(--text-secondary);
        }
        .card-body { padding: 20px; }

        .panel-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .panel-full { grid-column: 1 / -1; }

        .form-group { margin-bottom: 14px; }
        .form-group label { display: block; font-size: 11px; font-weight: 600; color: var(--text-secondary); margin-bottom: 5px; text-transform: uppercase; letter-spacing: 0.3px; }
        .form-group input, .form-group textarea, .form-group select {
            width: 100%;
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 10px 14px;
            color: var(--text-primary);
            font-size: 13px;
            font-family: inherit;
            transition: border-color 0.2s;
        }
        .form-group input:focus, .form-group textarea:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }
        .form-group textarea { resize: vertical; min-height: 70px; }
        .form-row { display: flex; gap: 10px; }
        .form-row input, .form-row textarea { flex: 1; }
        .btn {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 10px 20px;
            color: var(--text-primary);
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            white-space: nowrap;
            font-family: inherit;
        }
        .btn:hover { background: var(--bg-card-hover); }
        .btn:active { transform: scale(0.98); }
        .btn-primary {
            background: linear-gradient(135deg, var(--accent), #0288d1);
            color: #fff;
            border: none;
        }
        .btn-primary:hover { opacity: 0.9; box-shadow: 0 0 20px var(--accent-glow); }
        .btn-danger { color: var(--red); }
        .btn-danger:hover { background: rgba(248, 81, 73, 0.1); border-color: rgba(248, 81, 73, 0.3); }
        .btn-sm { padding: 6px 12px; font-size: 12px; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }

        .table-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th { text-align: left; padding: 10px 16px; font-size: 11px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid var(--border); }
        td { padding: 10px 16px; border-bottom: 1px solid var(--border); }
        tr:last-child td { border-bottom: none; }
        tr:hover td { background: rgba(255,255,255,0.02); }
        code { background: var(--bg-primary); padding: 2px 6px; border-radius: 4px; font-size: 12px; }
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }
        .status-badge.pending { background: rgba(240, 136, 62, 0.15); color: var(--orange); }
        .status-badge.approved { background: rgba(63, 185, 80, 0.15); color: var(--green); }
        .status-badge.denied { background: rgba(248, 81, 73, 0.15); color: var(--red); }

        .alert {
            padding: 12px 16px;
            border-radius: 8px;
            font-size: 13px;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .alert-error { background: rgba(248, 81, 73, 0.1); border: 1px solid rgba(248, 81, 73, 0.3); color: var(--red); }
        .alert-success { background: rgba(63, 185, 80, 0.1); border: 1px solid rgba(63, 185, 80, 0.3); color: var(--green); }
        .hidden { display: none !important; }
        .empty-state { text-align: center; padding: 32px 16px; color: var(--text-secondary); font-size: 13px; }

        .user-card {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px;
            background: var(--bg-primary);
            border: 1px solid var(--border);
            border-radius: 10px;
            margin-bottom: 10px;
            transition: border-color 0.2s, background 0.2s;
        }
        .user-card:last-child { margin-bottom: 0; }
        .user-card:hover { border-color: var(--accent); background: var(--accent-dim); }
        .user-avatar {
            width: 40px; height: 40px;
            border-radius: 50%;
            background: var(--bg-card);
            flex-shrink: 0;
            overflow: hidden;
            border: 2px solid var(--border);
        }
        .user-avatar img { width: 100%; height: 100%; object-fit: cover; display: block; }
        .user-avatar .placeholder {
            width: 100%; height: 100%;
            display: flex; align-items: center; justify-content: center;
            font-size: 16px; font-weight: 700; color: var(--text-secondary);
            background: var(--bg-card);
        }
        .user-info { flex: 1; min-width: 0; }
        .user-info .name { font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .user-info .name .tag { color: var(--text-secondary); font-weight: 400; }
        .user-info .uid { font-size: 11px; color: var(--text-secondary); font-family: 'Consolas', monospace; margin-top: 2px; }
        .user-info .note-text { font-size: 12px; color: var(--text-secondary); margin-top: 4px; font-style: italic; }
        .user-meta { text-align: right; flex-shrink: 0; display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
        .user-meta .added-date { font-size: 11px; color: var(--text-secondary); }

        .tabs { display: flex; gap: 4px; margin-bottom: 16px; flex-wrap: wrap; }
        .tab {
            background: transparent;
            border: none;
            padding: 7px 14px;
            color: var(--text-secondary);
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            border-radius: 6px;
            transition: all 0.2s;
            font-family: inherit;
        }
        .tab:hover { background: var(--bg-card); }
        .tab.active { background: var(--accent); color: #fff; }

        .skeleton {
            background: linear-gradient(90deg, var(--bg-card) 25%, var(--bg-card-hover) 50%, var(--bg-card) 75%);
            background-size: 200% 100%;
            animation: shimmer 1.5s infinite;
            border-radius: 6px;
        }
        @keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
        .skeleton-text { height: 14px; margin-bottom: 8px; }
        .skeleton-text:last-child { width: 60%; }

        .blame-text { font-size: 11px; color: var(--text-secondary); margin-top: 2px; }

        @media (max-width: 700px) {
            .app-layout { padding: 12px; }
            .nav-bar { flex-wrap: wrap; }
            .nav-item { padding: 8px 12px; font-size: 12px; }
            .panel-grid { grid-template-columns: 1fr; }
            .mod-quick-grid { grid-template-columns: 1fr; }
            .stats-row { grid-template-columns: repeat(2, 1fr); }
        }
    </style>
</head>
<body>
    <div class="login-page" id="login-page">
        <div class="login-card">
            <div class="logo">OS</div>
            <h1>Staff Dashboard</h1>
            <p>Oklahoma State Roleplay — Ban Appeal Management</p>
            <input type="password" id="login-key" placeholder="Enter dashboard key" autocomplete="off" onkeydown="if(event.key==='Enter')loginClick()">
            <button id="login-btn" onclick="loginClick()">Sign In</button>
            <div id="login-error-fallback" style="display:none;margin-top:16px;padding:12px 16px;border-radius:8px;background:rgba(248,81,73,0.1);border:1px solid rgba(248,81,73,0.3);color:#f85149;font-size:14px;font-weight:500;"></div>
        </div>
    </div>

    <div class="app-layout hidden" id="dashboard-page">
        <div class="topbar">
            <div class="topbar-info">
                <h1 id="guild-name">OSRP Staff Dashboard</h1>
                <div class="sub" id="guild-subtitle">Loading server info...</div>
            </div>
            <div class="topbar-actions">
                <button id="refresh-btn">Refresh</button>
                <button class="danger" id="logout-btn">Sign Out</button>
            </div>
        </div>

        <div class="nav-bar" id="nav-bar">
            <button class="nav-item active" data-panel="all">
                All Appeals
                <span class="nav-badge" id="nav-all">0</span>
            </button>
            <span class="nav-spacer"></span>
            <button class="nav-item" data-panel="blacklist">
                Appeal BL
                <span class="nav-badge" id="nav-blacklist">0</span>
            </button>
            <button class="nav-item" data-panel="staff-blacklist">
                Staff BL
                <span class="nav-badge" id="nav-staff-blacklist">0</span>
            </button>
        </div>

        <div class="content">
                <!-- Panel: All Appeals -->
                <div class="panel active" id="panel-all">
                    <div class="card panel-full">
                        <div class="card-header">
                            <h2>All Appeals</h2>
                            <span class="badge" id="appeals-count">0</span>
                        </div>
                        <div class="card-body">
                            <div class="tabs">
                                <button class="tab active" data-filter="all">All</button>
                                <button class="tab" data-filter="pending">Pending</button>
                                <button class="tab" data-filter="approved">Approved</button>
                                <button class="tab" data-filter="denied">Denied</button>
                            </div>
                            <div class="table-wrap">
                                <table>
                                    <thead><tr><th>User</th><th>Reason</th><th>Status</th><th>Date</th></tr></thead>
                                    <tbody id="appeals-table-body"><tr><td colspan="4"><div class="empty-state">Loading appeals...</div></td></tr></tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Panel: Blacklist -->
                <div class="panel" id="panel-blacklist">
                    <div class="card panel-full">
                        <div class="card-header">
                            <h2>Appeal Blacklist</h2>
                        </div>
                        <div class="card-body">
                            <div id="blacklist-result" class="hidden"></div>
                            <div class="form-group">
                                <label>Discord User ID</label>
                                <input type="text" id="blacklist-user-id" placeholder="Paste Discord user ID here">
                            </div>
                            <div class="form-group">
                                <label>Reason / Note</label>
                                <textarea id="blacklist-note" placeholder="Why is this user being blacklisted?" rows="3"></textarea>
                            </div>
                            <div class="form-group">
                                <label>Duration</label>
                                <select id="blacklist-duration" style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:8px;padding:10px 14px;color:var(--text-primary);font-size:13px;font-family:inherit;">
                                    <option value="3m">3 Months</option>
                                    <option value="6m">6 Months</option>
                                    <option value="permanent" selected>Permanent</option>
                                </select>
                            </div>
                            <button id="blacklist-add-btn" class="btn btn-primary" style="width:100%;">Add to Blacklist</button>
                        </div>
                    </div>
                    <div class="card panel-full" style="margin-top:16px;">
                        <div class="card-header">
                            <h2>Blacklisted Users</h2>
                            <span class="badge" id="blacklist-count">0</span>
                        </div>
                        <div class="card-body" id="blacklist-table">
                            <div class="empty-state">No blacklisted users.</div>
                        </div>
                    </div>
                </div>

                <!-- Panel: Staff Blacklist -->
                <div class="panel" id="panel-staff-blacklist">
                    <div class="card panel-full">
                        <div class="card-header">
                            <h2>Staff Blacklist</h2>
                        </div>
                        <div class="card-body">
                            <div id="staff-blacklist-result" class="hidden"></div>
                            <div class="form-group">
                                <label>Discord User ID</label>
                                <input type="text" id="staff-blacklist-user-id" placeholder="Paste Discord user ID here">
                            </div>
                            <div class="form-group">
                                <label>Reason / Note</label>
                                <textarea id="staff-blacklist-note" placeholder="Why is this user being staff blacklisted?" rows="3"></textarea>
                            </div>
                            <div class="form-group">
                                <label>Duration</label>
                                <select id="staff-blacklist-duration" style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:8px;padding:10px 14px;color:var(--text-primary);font-size:13px;font-family:inherit;">
                                    <option value="3m">3 Months</option>
                                    <option value="6m">6 Months</option>
                                    <option value="permanent" selected>Permanent</option>
                                </select>
                            </div>
                            <button id="staff-blacklist-add-btn" class="btn btn-primary" style="width:100%;">Add to Staff Blacklist</button>
                        </div>
                    </div>
                    <div class="card panel-full" style="margin-top:16px;">
                        <div class="card-header">
                            <h2>Staff Blacklisted Users</h2>
                            <span class="badge" id="staff-blacklist-count">0</span>
                        </div>
                        <div class="card-body" id="staff-blacklist-table">
                            <div class="empty-state">No staff blacklisted users.</div>
                        </div>
                    </div>
                </div>

            </div>
        </div>
    </div>

    <script>
        const API_KEY = () => { try { return localStorage.getItem('dashboard_key') || ''; } catch(e) { return ''; } };
        var currentFilter = 'all';

        function showLoginError(msg) {
            var el = document.getElementById('login-error-fallback');
            if (el) { el.textContent = msg; el.style.display = 'block'; }
        }
        function hideLoginError() {
            var el = document.getElementById('login-error-fallback');
            if (el) el.style.display = 'none';
        }

        function escapeHtml(text) {
            if (!text) return '';
            var d = document.createElement('div');
            d.textContent = text;
            return d.innerHTML;
        }
        function formatDate(iso) {
            if (!iso) return '--';
            var d = new Date(iso);
            return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        }
        function showAlert(id, msg, type) {
            var el = document.getElementById(id);
            if (!el) return;
            el.className = 'alert alert-' + type;
            el.innerHTML = msg;
            el.classList.remove('hidden');
            setTimeout(function() { el.classList.add('hidden'); }, 4000);
        }

        async function apiFetch(path, options) {
            options = options || {};
            var key = API_KEY();
            var headers = { 'Content-Type': 'application/json', 'X-Admin-Key': key };
            try {
                var res = await fetch(path, { ...options, headers: { ...headers, ...(options.headers || {}) } });
                if (res.status === 401) {
                    try { localStorage.removeItem('dashboard_key'); } catch(e) {}
                    showLoginError('Invalid dashboard key.');
                    return null;
                }
                if (res.status === 503) {
                    showLoginError('Dashboard not configured - set DASHBOARD_KEY on Railway.');
                    return null;
                }
                return res.json();
            } catch (e) {
                return { error: 'Network error.' };
            }
        }

        async function doLogin(keyValue) {
            hideLoginError();
            try { localStorage.setItem('dashboard_key', keyValue); } catch(e) {}
            var guild = await apiFetch('/api/dashboard/guild-info');
            var data = await apiFetch('/api/dashboard/data');
            if (!data) return;
            if (data.error) {
                showLoginError(data.error);
                try { localStorage.removeItem('dashboard_key'); } catch(e) {}
                return;
            }
            document.getElementById('login-page').classList.add('hidden');
            document.getElementById('dashboard-page').classList.remove('hidden');
            renderDashboard(guild, data);
        }

        async function renderDashboard(guild, data) {
            if (guild && !guild.error) {
                document.getElementById('guild-name').textContent = (guild.name || 'OSRP') + ' - Staff Dashboard';
                document.getElementById('guild-subtitle').textContent = 'Ban Appeal Management';
            }
            document.getElementById('appeals-count').textContent = data.total_appeals || 0;
            document.getElementById('nav-all').textContent = data.total_appeals || 0;

            await Promise.all([ loadAppeals(), loadBlacklist(), loadStaffBlacklist() ]);
        }

        function loginClick() {
            var input = document.getElementById('login-key');
            if (!input || !input.value.trim()) {
                showLoginError('Enter the dashboard key first.');
                if (input) input.focus();
                return;
            }
            doLogin(input.value.trim());
        }

        (function() {
            var k;
            try { k = localStorage.getItem('dashboard_key'); } catch(e) {}
            if (k) doLogin(k);
        })();

        document.getElementById('logout-btn').addEventListener('click', function() {
            try { localStorage.removeItem('dashboard_key'); } catch(e) {}
            document.getElementById('login-page').classList.remove('hidden');
            document.getElementById('dashboard-page').classList.add('hidden');
        });

        document.getElementById('refresh-btn').addEventListener('click', async function() {
            await doLogin(API_KEY());
        });

        document.querySelectorAll('.nav-item[data-panel]').forEach(function(item) {
            item.addEventListener('click', function() {
                document.querySelectorAll('.nav-item[data-panel]').forEach(function(n) { n.classList.remove('active'); });
                this.classList.add('active');
                document.querySelectorAll('.panel').forEach(function(p) { p.classList.remove('active'); });
                var panel = document.getElementById('panel-' + this.dataset.panel);
                if (panel) panel.classList.add('active');
            });
        });

        async function loadAppeals() {
            var data = await apiFetch('/api/dashboard/appeals');
            if (!data || !data.appeals) return;
            renderAppeals(data.appeals);
        }

        function renderAppeals(appeals) {
            var tbody = document.getElementById('appeals-table-body');
            var filtered = currentFilter === 'all' ? appeals : appeals.filter(function(a) { return a.status === currentFilter; });
            if (filtered.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4"><div class="empty-state">No ' + (currentFilter === 'all' ? '' : currentFilter + ' ') + 'appeals found.</div></td></tr>';
            } else {
                tbody.innerHTML = filtered.map(function(a) {
                    var sc = a.status === 'pending' ? 'pending' : a.status === 'approved' ? 'approved' : 'denied';
                    return '<tr><td><strong>' + escapeHtml(a.discord_username || 'Unknown') + '</strong><br><code>' + escapeHtml(a.discord_id || '') + '</code></td><td>' + escapeHtml((a.why_banned || a.ban_reason || '').substring(0, 60)) + (a.why_banned && a.why_banned.length > 60 ? '...' : '') + '</td><td><span class="status-badge ' + sc + '">' + a.status + '</span></td><td>' + formatDate(a.submitted_at) + '</td></tr>';
                }).join('');
            }

        }

        document.querySelectorAll('.tab').forEach(function(tab) {
            tab.addEventListener('click', function() {
                document.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
                this.classList.add('active');
                currentFilter = this.dataset.filter;
                loadAppeals();
            });
        });

        function renderUserCard(u) {
            var avatarHtml = u.avatar_url ? '<img src="' + escapeHtml(u.avatar_url) + '" alt="" loading="lazy">' : '<div class="placeholder">' + (u.username ? u.username[0].toUpperCase() : '?') + '</div>';
            var nameHtml = u.username && u.username !== u.user_id ? '<span class="name">' + escapeHtml(u.username) + '</span>' : '<span class="name"><code>' + escapeHtml(u.user_id) + '</code></span>';
            var noteHtml = u.note ? '<div class="note-text">' + escapeHtml(u.note) + '</div>' : '';
            var durLabel = { '3m': '3 months', '6m': '6 months', 'permanent': 'Permanent' }[u.duration] || 'Permanent';
            var durColor = u.duration === 'permanent' ? 'var(--red)' : 'var(--orange)';
            var durHtml = '<div style="font-size:11px;color:' + durColor + ';margin-top:4px;">' + durLabel + '</div>';
            return '<div class="user-card"><div class="user-avatar">' + avatarHtml + '</div><div class="user-info">' + nameHtml + '<div class="uid">' + escapeHtml(u.user_id) + '</div>' + noteHtml + durHtml + '</div><div class="user-meta"><button class="btn btn-danger btn-sm" onclick="removeBlacklist(\\'' + escapeHtml(u.user_id) + '\\')">Remove</button><div class="added-date">' + formatDate(u.added_at) + '</div></div></div>';
        }

        async function loadBlacklist() {
            var data = await apiFetch('/api/dashboard/blacklist');
            if (!data) return;
            var table = document.getElementById('blacklist-table');
            var count = data.users ? data.users.length : 0;
            document.getElementById('blacklist-count').textContent = count;
            document.getElementById('nav-blacklist').textContent = count;
            document.getElementById('stat-blacklist').textContent = count;
            if (count === 0) {
                table.innerHTML = '<div class="empty-state">No blacklisted users.</div>';
                return;
            }
            table.innerHTML = data.users.map(function(u) { return renderUserCard(u); }).join('');
        }

        async function removeBlacklist(userId) {
            var data = await apiFetch('/api/dashboard/blacklist/remove', { method: 'POST', body: JSON.stringify({ user_id: userId }) });
            if (data && data.success) loadBlacklist();
        }

        function doBlacklistAdd(userId, note, resultId, btn, duration) {
            if (!userId) return;
            duration = duration || 'permanent';
            if (btn) { btn.disabled = true; btn.textContent = 'Adding...'; }
            apiFetch('/api/dashboard/blacklist/add', { method: 'POST', body: JSON.stringify({ user_id: userId, note: note, duration: duration }) }).then(function(data) {
                if (btn) { btn.disabled = false; btn.textContent = 'Add to Blacklist'; }
                if (data && data.success) {
                    document.getElementById('blacklist-user-id').value = '';
                    document.getElementById('blacklist-note').value = '';
                    document.getElementById('mod-bl-id').value = '';
                    document.getElementById('mod-bl-note').value = '';
                    var durLabel = { '3m': '3 months', '6m': '6 months', 'permanent': 'permanently' }[duration] || 'permanently';
                    showAlert(resultId, '<strong>' + escapeHtml(data.user && data.user.username ? data.user.username : userId) + '</strong> blacklisted (' + durLabel + ').', 'success');
                    loadBlacklist();
                } else if (data && data.error) {
                    showAlert(resultId, data.error, 'error');
                }
            });
        }

        document.getElementById('blacklist-add-btn').addEventListener('click', function() {
            var userId = document.getElementById('blacklist-user-id').value.trim();
            var note = document.getElementById('blacklist-note').value.trim();
            var duration = document.getElementById('blacklist-duration').value;
            doBlacklistAdd(userId, note, 'blacklist-result', this, duration);
        });

        document.getElementById('blacklist-user-id').addEventListener('keydown', function(e) {
            if (e.key === 'Enter') document.getElementById('blacklist-add-btn').click();
        });

        document.getElementById('mod-bl-btn').addEventListener('click', function() {
            var userId = document.getElementById('mod-bl-id').value.trim();
            var note = document.getElementById('mod-bl-note').value.trim();
            doBlacklistAdd(userId, note, 'mod-bl-result', this, 'permanent');
        });

        document.getElementById('mod-bl-id').addEventListener('keydown', function(e) {
            if (e.key === 'Enter') document.getElementById('mod-bl-btn').click();
        });

        /* ---- Staff Blacklist ---- */
        function renderStaffUserCard(u) {
            var avatarHtml = u.avatar_url ? '<img src="' + escapeHtml(u.avatar_url) + '" alt="" loading="lazy">' : '<div class="placeholder">' + (u.username ? u.username[0].toUpperCase() : '?') + '</div>';
            var nameHtml = u.username && u.username !== u.user_id ? '<span class="name">' + escapeHtml(u.username) + '</span>' : '<span class="name"><code>' + escapeHtml(u.user_id) + '</code></span>';
            var noteHtml = u.note ? '<div class="note-text">' + escapeHtml(u.note) + '</div>' : '';
            var durLabel = { '3m': '3 months', '6m': '6 months', 'permanent': 'Permanent' }[u.duration] || 'Permanent';
            var durColor = u.duration === 'permanent' ? 'var(--red)' : 'var(--orange)';
            var durHtml = '<div style="font-size:11px;color:' + durColor + ';margin-top:4px;">' + durLabel + '</div>';
            return '<div class="user-card"><div class="user-avatar">' + avatarHtml + '</div><div class="user-info">' + nameHtml + '<div class="uid">' + escapeHtml(u.user_id) + '</div>' + noteHtml + durHtml + '</div><div class="user-meta"><button class="btn btn-danger btn-sm" onclick="removeStaffBlacklist(\\'' + escapeHtml(u.user_id) + '\\')">Remove</button><div class="added-date">' + formatDate(u.added_at) + '</div></div></div>';
        }

        async function loadStaffBlacklist() {
            var data = await apiFetch('/api/dashboard/staff-blacklist');
            if (!data) return;
            var table = document.getElementById('staff-blacklist-table');
            var count = data.users ? data.users.length : 0;
            document.getElementById('staff-blacklist-count').textContent = count;
            document.getElementById('nav-staff-blacklist').textContent = count;
            if (count === 0) {
                table.innerHTML = '<div class="empty-state">No staff blacklisted users.</div>';
                return;
            }
            table.innerHTML = data.users.map(function(u) { return renderStaffUserCard(u); }).join('');
        }

        async function removeStaffBlacklist(userId) {
            var data = await apiFetch('/api/dashboard/staff-blacklist/remove', { method: 'POST', body: JSON.stringify({ user_id: userId }) });
            if (data && data.success) loadStaffBlacklist();
        }

        function doStaffBlacklistAdd(userId, note, resultId, btn, duration) {
            if (!userId) return;
            duration = duration || 'permanent';
            if (btn) { btn.disabled = true; btn.textContent = 'Adding...'; }
            apiFetch('/api/dashboard/staff-blacklist/add', { method: 'POST', body: JSON.stringify({ user_id: userId, note: note, duration: duration }) }).then(function(data) {
                if (btn) { btn.disabled = false; btn.textContent = 'Add to Staff Blacklist'; }
                if (data && data.success) {
                    document.getElementById('staff-blacklist-user-id').value = '';
                    document.getElementById('staff-blacklist-note').value = '';
                    var durLabel = { '3m': '3 months', '6m': '6 months', 'permanent': 'permanently' }[duration] || 'permanently';
                    showAlert(resultId, '<strong>' + escapeHtml(data.user && data.user.username ? data.user.username : userId) + '</strong> staff blacklisted (' + durLabel + ').', 'success');
                    loadStaffBlacklist();
                } else if (data && data.error) {
                    showAlert(resultId, data.error, 'error');
                }
            });
        }

        document.getElementById('staff-blacklist-add-btn').addEventListener('click', function() {
            var userId = document.getElementById('staff-blacklist-user-id').value.trim();
            var note = document.getElementById('staff-blacklist-note').value.trim();
            var duration = document.getElementById('staff-blacklist-duration').value;
            doStaffBlacklistAdd(userId, note, 'staff-blacklist-result', this, duration);
        });

        document.getElementById('staff-blacklist-user-id').addEventListener('keydown', function(e) {
            if (e.key === 'Enter') document.getElementById('staff-blacklist-add-btn').click();
        });

    </script>
</body>
</html>"""

def require_dashboard_key(request):
    """Check that DASHBOARD_KEY is set and the request provides the correct key."""
    dashboard_key = os.environ.get("DASHBOARD_KEY")
    if not dashboard_key:
        return web.json_response({"error": "Dashboard not configured - set DASHBOARD_KEY environment variable"}, status=503)
    key = request.headers.get("X-Admin-Key", "")
    if key != dashboard_key:
        return web.json_response({"error": "Invalid key"}, status=401)
    return None


async def handle_dashboard_page(request):
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")


async def handle_dashboard_data(request):
    err = require_dashboard_key(request)
    if err:
        return err
    
    total = len(appeals_db)
    pending = len([a for a in appeals_db.values() if a.get("status") == "pending"])
    approved = len([a for a in appeals_db.values() if a.get("status") == "approved"])
    denied = len([a for a in appeals_db.values() if a.get("status") == "denied"])
    blacklist_count = len(blacklist_db)
    staff_blacklist_count = len(staff_blacklist_db)
    
    return web.json_response({
        "total_appeals": total,
        "pending_appeals": pending,
        "approved_appeals": approved,
        "denied_appeals": denied,
        "blacklist_count": blacklist_count,
        "staff_blacklist_count": staff_blacklist_count
    })


async def handle_dashboard_blacklist(request):
    err = require_dashboard_key(request)
    if err:
        return err
    
    async def enrich_user(uid, data):
        entry = {
            "user_id": uid,
            "added_by": data.get("added_by", ""),
            "added_at": data.get("added_at", ""),
            "username": data.get("username", None),
            "avatar_url": data.get("avatar_url", None),
            "note": data.get("note", ""),
            "duration": data.get("duration", "permanent"),
            "barred_until": data.get("barred_until", None),
        }
        # If no cached info, try to fetch it
        if not entry["username"]:
            try:
                user = await bot.fetch_user(int(uid))
                entry["username"] = str(user)
                entry["avatar_url"] = str(user.display_avatar.url)
                # cache it for next time
                blacklist_db[uid]["username"] = entry["username"]
                blacklist_db[uid]["avatar_url"] = entry["avatar_url"]
                save_json(BLACKLIST_FILE, blacklist_db)
            except Exception:
                entry["username"] = uid
        return entry
    
    tasks = [enrich_user(uid, d) for uid, d in blacklist_db.items()]
    users = await asyncio.gather(*tasks) if tasks else []
    return web.json_response({"users": users})


async def handle_dashboard_blacklist_add(request):
    err = require_dashboard_key(request)
    if err:
        return err
    
    try:
        body = await request.json()
        user_id = body.get("user_id", "").strip()
        note = body.get("note", "").strip()
        duration = body.get("duration", "permanent")
        if duration not in ("3m", "6m", "permanent"):
            duration = "permanent"
        if not user_id:
            return web.json_response({"error": "User ID required"}, status=400)
        
        now = datetime.datetime.now(datetime.timezone.utc)
        entry = {
            "added_by": "dashboard",
            "added_at": now.isoformat(),
            "note": note,
            "duration": duration,
        }
        if duration == "3m":
            entry["barred_until"] = (now + datetime.timedelta(days=90)).isoformat()
        elif duration == "6m":
            entry["barred_until"] = (now + datetime.timedelta(days=180)).isoformat()
        else:
            entry["barred_until"] = None
        
        # Fetch Discord user info for a cooler display
        try:
            user = await bot.fetch_user(int(user_id))
            entry["username"] = str(user)
            entry["avatar_url"] = str(user.display_avatar.url)
        except Exception:
            entry["username"] = user_id
        
        blacklist_db[user_id] = entry
        save_json(BLACKLIST_FILE, blacklist_db)
        
        # DM the user about the blacklist
        try:
            dur_label = {"3m": "3 months", "6m": "6 months", "permanent": "permanently"}[duration]
            bl_msg = (
                f"You have been **blacklisted** from **Oklahoma State Roleplay**.\n\n"
                f"You are barred from submitting an appeal for **{dur_label}**."
            )
            if duration == "permanent":
                bl_msg = (
                    f"You have been **blacklisted** and **permanently barred** from appealing "
                    f"on **Oklahoma State Roleplay**.\n\n"
                    f"This decision is final."
                )
            if note:
                bl_msg += f"\n\nReason: {note}"
            await user.send(bl_msg)
        except Exception:
            pass
        
        return web.json_response({"success": True, "user": entry})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)


async def handle_dashboard_blacklist_remove(request):
    err = require_dashboard_key(request)
    if err:
        return err
    
    try:
        body = await request.json()
        user_id = body.get("user_id", "").strip()
        if user_id in blacklist_db:
            del blacklist_db[user_id]
            save_json(BLACKLIST_FILE, blacklist_db)
            return web.json_response({"success": True})
        return web.json_response({"error": "User not found"}, status=404)
    except Exception:
        return web.json_response({"error": "Invalid request"}, status=400)


async def handle_dashboard_staff_blacklist(request):
    err = require_dashboard_key(request)
    if err:
        return err
    
    async def enrich_user(uid, data):
        entry = {
            "user_id": uid,
            "added_by": data.get("added_by", ""),
            "added_at": data.get("added_at", ""),
            "username": data.get("username", None),
            "avatar_url": data.get("avatar_url", None),
            "note": data.get("note", ""),
            "duration": data.get("duration", "permanent"),
            "barred_until": data.get("barred_until", None),
        }
        if not entry["username"]:
            try:
                user = await bot.fetch_user(int(uid))
                entry["username"] = str(user)
                entry["avatar_url"] = str(user.display_avatar.url)
                staff_blacklist_db[uid]["username"] = entry["username"]
                staff_blacklist_db[uid]["avatar_url"] = entry["avatar_url"]
                save_json(STAFF_BLACKLIST_FILE, staff_blacklist_db)
            except Exception:
                entry["username"] = uid
        return entry
    
    tasks = [enrich_user(uid, d) for uid, d in staff_blacklist_db.items()]
    users = await asyncio.gather(*tasks) if tasks else []
    return web.json_response({"users": users})


async def handle_dashboard_staff_blacklist_add(request):
    err = require_dashboard_key(request)
    if err:
        return err
    
    try:
        body = await request.json()
        user_id = body.get("user_id", "").strip()
        note = body.get("note", "").strip()
        duration = body.get("duration", "permanent")
        if duration not in ("3m", "6m", "permanent"):
            duration = "permanent"
        if not user_id:
            return web.json_response({"error": "User ID required"}, status=400)
        
        now = datetime.datetime.now(datetime.timezone.utc)
        entry = {
            "added_by": "dashboard",
            "added_at": now.isoformat(),
            "note": note,
            "duration": duration,
        }
        if duration == "3m":
            entry["barred_until"] = (now + datetime.timedelta(days=90)).isoformat()
        elif duration == "6m":
            entry["barred_until"] = (now + datetime.timedelta(days=180)).isoformat()
        else:
            entry["barred_until"] = None
        
        try:
            user = await bot.fetch_user(int(user_id))
            entry["username"] = str(user)
            entry["avatar_url"] = str(user.display_avatar.url)
        except Exception:
            entry["username"] = user_id
        
        staff_blacklist_db[user_id] = entry
        save_json(STAFF_BLACKLIST_FILE, staff_blacklist_db)
        
        # Assign staff blacklist role
        guild = bot.get_guild(GUILD_ID)
        if guild:
            member = guild.get_member(int(user_id))
            if member:
                try:
                    role = guild.get_role(STAFF_BLACKLIST_ROLE_ID)
                    if role:
                        await member.add_roles(role, reason="Staff blacklisted via dashboard")
                except Exception as e:
                    print(f"[STAFF-BL] Failed to assign role: {e}")
        
        # DM the user
        try:
            dur_label = {"3m": "3 months", "6m": "6 months", "permanent": "permanently"}[duration]
            bl_msg = (
                f"You have been **staff blacklisted** from **Oklahoma State Roleplay**.\n\n"
                f"You are barred from applying for staff for **{dur_label}**."
            )
            if duration == "permanent":
                bl_msg = (
                    f"You have been **staff blacklisted** and **permanently barred** from applying "
                    f"for staff on **Oklahoma State Roleplay**.\n\n"
                    f"This decision is final."
                )
            if note:
                bl_msg += f"\n\nReason: {note}"
            await user.send(bl_msg)
        except Exception:
            pass
        
        return web.json_response({"success": True, "user": entry})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)


async def handle_dashboard_staff_blacklist_remove(request):
    err = require_dashboard_key(request)
    if err:
        return err
    
    try:
        body = await request.json()
        user_id = body.get("user_id", "").strip()
        if user_id in staff_blacklist_db:
            del staff_blacklist_db[user_id]
            save_json(STAFF_BLACKLIST_FILE, staff_blacklist_db)
            
            # Remove staff blacklist role
            guild = bot.get_guild(GUILD_ID)
            if guild:
                member = guild.get_member(int(user_id))
                if member:
                    try:
                        role = guild.get_role(STAFF_BLACKLIST_ROLE_ID)
                        if role:
                            await member.remove_roles(role, reason="Staff un-blacklisted via dashboard")
                    except Exception as e:
                        print(f"[STAFF-BL] Failed to remove role: {e}")
            
            return web.json_response({"success": True})
        return web.json_response({"error": "User not found"}, status=404)
    except Exception:
        return web.json_response({"error": "Invalid request"}, status=400)


async def handle_dashboard_guild_info(request):
    err = require_dashboard_key(request)
    if err:
        return err
    
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return web.json_response({"error": "Guild not found"}, status=404)
    
    return web.json_response({
        "name": guild.name,
        "icon_url": str(guild.icon.url) if guild.icon else None,
        "member_count": guild.member_count,
    })


async def handle_dashboard_appeals(request):
    err = require_dashboard_key(request)
    if err:
        return err
    
    sorted_appeals = sorted(
        [
            {
                "appeal_id": aid,
                "discord_username": a.get("discord_username", "Unknown"),
                "discord_id": a.get("discord_id", ""),
                "why_banned": a.get("why_banned", a.get("ban_reason", "")),
                "ban_reason": a.get("ban_reason", ""),
                "status": a.get("status", "unknown"),
                "submitted_at": a.get("submitted_at", ""),
            }
            for aid, a in appeals_db.items()
        ],
        key=lambda x: x.get("submitted_at", ""),
        reverse=True,
    )
    
    return web.json_response({"appeals": sorted_appeals})


ROOT_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>OSRP</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#090b12;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;background:radial-gradient(ellipse at 50% 0%,rgba(79,195,247,0.06) 0%,transparent 60%)}
.card{max-width:440px;width:100%;background:#0f121e;border:1px solid #222844;border-radius:16px;padding:40px;box-shadow:0 20px 60px rgba(0,0,0,0.5);text-align:center}
.logo{width:56px;height:56px;background:linear-gradient(135deg,#4fc3f7,#0288d1);border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:800;color:#090b12;margin:0 auto 20px;box-shadow:0 0 30px rgba(79,195,247,0.15)}
h1{font-size:22px;font-weight:700;margin-bottom:4px}
.sub{color:#8892a4;font-size:14px;margin-bottom:28px}
.links{display:flex;flex-direction:column;gap:12px}
.links a{display:flex;align-items:center;justify-content:center;gap:8px;padding:14px 20px;border-radius:10px;font-size:15px;font-weight:600;text-decoration:none;transition:all 0.2s}
.links a.primary{background:linear-gradient(135deg,#4fc3f7,#0288d1);color:#090b12}
.links a.primary:hover{opacity:0.9;box-shadow:0 0 20px rgba(79,195,247,0.2)}
.links a.secondary{background:#151929;border:1px solid #222844;color:#e2e8f0}
.links a.secondary:hover{background:#1b2037;border-color:#4fc3f7}
.footer{margin-top:24px;font-size:12px;color:#8892a4}
</style>
</head>
<body>
<div class="card">
<div class="logo">OS</div>
<h1>Oklahoma State Roleplay</h1>
<p class="sub">Bot Management Portal</p>
<div class="links">
<a href="/appeal" class="primary">Submit Ban Appeal</a>
<a href="/dashboard" class="secondary">Staff Dashboard</a>
</div>
<div class="footer">OSRP Management Bot</div>
</div>
</body>
</html>"""


async def root_handler(request):
    return web.Response(text=ROOT_HTML, content_type="text/html")


async def start_web_server():
    app = web.Application()
    
    # Root & health (always work, no bot dependency)
    app.router.add_get("/", root_handler)
    async def healthz(request):
        return web.Response(text="ok")
    app.router.add_get("/healthz", healthz)
    
    # Serve static pages and API
    app.router.add_get("/appeal", handle_appeal_page)
    app.router.add_get("/api/appeal/info", handle_appeal_info)
    app.router.add_post("/api/appeal/submit", handle_appeal_submit)
    
    # Dashboard
    app.router.add_get("/dashboard", handle_dashboard_page)
    app.router.add_get("/api/dashboard/guild-info", handle_dashboard_guild_info)
    app.router.add_get("/api/dashboard/data", handle_dashboard_data)
    app.router.add_get("/api/dashboard/appeals", handle_dashboard_appeals)
    app.router.add_get("/api/dashboard/blacklist", handle_dashboard_blacklist)
    app.router.add_post("/api/dashboard/blacklist/add", handle_dashboard_blacklist_add)
    app.router.add_post("/api/dashboard/blacklist/remove", handle_dashboard_blacklist_remove)
    app.router.add_get("/api/dashboard/staff-blacklist", handle_dashboard_staff_blacklist)
    app.router.add_post("/api/dashboard/staff-blacklist/add", handle_dashboard_staff_blacklist_add)
    app.router.add_post("/api/dashboard/staff-blacklist/remove", handle_dashboard_staff_blacklist_remove)

    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    for attempt in range(5):
        try:
            site = web.TCPSite(runner, "0.0.0.0", port)
            await site.start()
            break
        except OSError:
            if attempt < 4:
                await asyncio.sleep(2)
                continue
            raise
    print(f"[WEB] Listening on 0.0.0.0:{port}")


# â”€â”€ Commands â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@bot.command()
async def mypoints(ctx):
    member = ctx.author
    total = points_db.get(str(member.id), 0)
    point_word = "point" if total == 1 else "points"
    msg = await ctx.send(f"{member.mention}, you have **{total} {point_word}**.")
    asyncio.create_task(delete_after_delay(msg, 25))


def parse_duration(text: str) -> tuple[int | None, str]:
    """Parse a duration string like '1d', '24hr', '12h', '7d' into (minutes, display_string).
    Returns (None, original_text) if not a valid duration."""
    if not text:
        return None, text
    match = re.match(r"^(\d+)\s*(d|day|days|h|hr|hrs|hour|hours|m|min|mins|minute|minutes)$", text.strip(), re.IGNORECASE)
    if not match:
        return None, text
    num = int(match.group(1))
    unit = match.group(2).lower()
    if unit in ("d", "day", "days"):
        return num * 1440, f"{num}d"
    if unit in ("h", "hr", "hrs", "hour", "hours"):
        return num * 60, f"{num}hr"
    if unit in ("m", "min", "mins", "minute", "minutes"):
        return num, f"{num}m"
    return None, text


def resolve_member(ctx, user_id: str) -> discord.Member | None:
    """Resolve a member from mention, raw ID, or try guild lookup."""
    uid = resolve_user_id(user_id)
    try:
        return ctx.guild.get_member(int(uid))
    except (ValueError, TypeError):
        return None


async def apply_punishment(ctx, target_id: str, punishment: str, points: int, duration: str = None, reason: str = None):
    """Common logic for tracking a punishment and showing the result."""
    user_id_str = resolve_user_id(target_id)
    current_points = points_db.get(user_id_str, 0) + points
    points_db[user_id_str] = current_points
    save_json(POINTS_FILE, points_db)

    case_number = str(int(datetime.datetime.now().timestamp()))
    case_data = {
        "user_id": user_id_str,
        "punishment": punishment,
        "points": points,
        "guild_id": str(ctx.guild.id),
        "moderator": str(ctx.author.id),
        "reason": reason or "No reason provided"
    }
    if duration:
        case_data["duration"] = duration
    cases_db[case_number] = case_data
    save_json(CASES_FILE, cases_db)

    member = ctx.guild.get_member(int(user_id_str))
    mention = member.mention if member else f"<@{user_id_str}>"
    point_word = "point" if current_points == 1 else "points"

    processed_cases.add(case_number)
    recent_punishments[user_id_str] = time.time()
    action_name = punishment.title()
    msg = f"{mention} has been **{action_name}**. They now have **{current_points} {point_word}**."
    if duration:
        msg += f" (Duration: {duration})"
    if reason:
        msg += f" Reason: {reason}"
    await ctx.send(msg)


@bot.command()
async def warn(ctx, user_id: str, *, reason: str = None):
    if not has_any_role(ctx.author, PUNISHER_ROLES) and not has_staff_role(ctx.author):
        return
    await apply_punishment(ctx, user_id, "warn", 1, reason=reason)


@bot.command()
async def mute(ctx, user_id: str, duration: str = None, *, reason: str = None):
    if not has_any_role(ctx.author, PUNISHER_ROLES) and not has_staff_role(ctx.author):
        return
    dur_minutes, dur_display = parse_duration(duration or "")
    if not duration or dur_minutes is None:
        await ctx.send("Usage: `!mute <user_id> <duration> [reason]`\nDurations: `1d`, `24hr`, `12h`, `30m`, etc.")
        return
    await apply_punishment(ctx, user_id, "mute", 2, duration=dur_display, reason=reason)


@bot.command()
async def softban(ctx, user_id: str, *, reason: str = None):
    if not has_any_role(ctx.author, PUNISHER_ROLES) and not has_staff_role(ctx.author):
        return
    await apply_punishment(ctx, user_id, "softban", 2, reason=reason)


def parse_ban_args(user_id: str, duration: str = None, *, reason: str = None):
    """Parse ban args. Duration optional — if present = tempban (4pts), if absent = perma ban (5pts)."""
    if duration:
        dur_minutes, dur_display = parse_duration(duration)
        if dur_minutes is not None:
            return user_id, "tempban", 4, dur_display, reason
        reason = f"{duration} {reason or ''}".strip()
    return user_id, "ban", 5, None, reason


@bot.command()
async def ban(ctx, user_id: str, duration: str = None, *, reason: str = None):
    if not has_any_role(ctx.author, PUNISHER_ROLES) and not has_staff_role(ctx.author):
        return
    uid, punishment, pts, dur, reas = parse_ban_args(user_id, duration, reason=reason)
    await apply_punishment(ctx, uid, punishment, pts, duration=dur, reason=reas)
    total = points_db.get(uid, 0)
    await handle_ban_appeal_dm(uid, punishment, total)


@bot.command()
async def points(ctx, member: discord.Member = None):
    if not has_any_role(ctx.author, VOID_ROLES) and not has_staff_role(ctx.author):
        return await ctx.send(
            "You don't have permission. Requires **[Management Team]** or **[Directorship Team]**."
        )
    if not member:
        return await ctx.send("Please specify a user: `!points <@user or user_id>`")
    total = points_db.get(str(member.id), 0)
    point_word = "point" if total == 1 else "points"
    await ctx.send(f"{member.mention} has **{total} {point_word}**.")


@bot.command()
async def void(ctx, raw_user: str, *, raw_case: str):
    """Remove points for a case. Usage: !void <@user or user_id> <case number>"""
    if not has_any_role(ctx.author, VOID_ROLES) and not has_staff_role(ctx.author):
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
    roblox_id, roblox_url, _ = await get_roblox_info("vgxbak")
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


@bot.command()
@commands.has_permissions(manage_guild=True)
async def sampleappeal(ctx):
    """Create a test appeal code for you to try the website."""
    user_id_str = str(ctx.author.id)
    
    token = generate_appeal_token()
    appeal_tokens_db[token] = {
        "user_id": user_id_str,
        "punishment": "test ban",
        "total_points": 10,
        "created_at": (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=31)).isoformat(),
        "used": False
    }
    save_json(APPEAL_TOKENS_FILE, appeal_tokens_db)
    
    try:
        appeal_url = f"{BASE_URL}/appeal"
        msg = (
            f"Your ban appeal code for **Oklahoma State Roleplay:**\n\n"
            f"`{{ {token} }}`\n\n"
            f"Go to {appeal_url} and enter this code to submit your appeal.\n\n"
            f"**This code is unique to you, sharing it can result in a permanent ban\n_ _\nwithout appeal from the server.**"
        )
        await ctx.author.send(msg)
        await ctx.send(f"Test appeal code sent to your DMs! Check your DMs.")
    except Exception:
        appeal_url = f"{BASE_URL}/appeal"
        await ctx.send(f"Could not DM you. Your code is: `{{ {token} }}`\n\nGo to {appeal_url} and enter it.")


@bot.command()
@commands.has_permissions(manage_guild=True)
async def sampleapprove(ctx):
    """Preview the approve DM message."""
    guild = ctx.guild
    invite = None
    welcome_ch = guild.get_channel(WELCOME_CHANNEL_ID)
    if welcome_ch:
        try:
            invite = await welcome_ch.create_invite(max_uses=1, max_age=86400)
        except Exception:
            pass
    msg = (
        f"Your ban appeal has been reviewed and has been **approved**. "
        f"You have been unbanned from **{guild.name}**."
    )
    if invite:
        msg += f"\n\nInvite: {invite.url}\n*Expires in 24 hours.*"
    await ctx.send(msg)


@bot.command()
@commands.has_permissions(manage_guild=True)
async def sampledeny(ctx):
    """Preview the deny DM message."""
    two_months = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=60)).strftime("%B %d, %Y")
    msg = (
            f"Your ban appeal for **{ctx.guild.name}** has been reviewed and unfortunately has been **denied**.\n\n"
            f"You may submit another appeal after **{two_months}** (2 months from today).\n\n"
        f"We appreciate your understanding."
    )
    await ctx.send(msg)


@bot.command()
@commands.has_permissions(manage_guild=True)
async def sendinvites(ctx):
    """Send department invite buttons to the welcome channel."""
    await ctx.message.delete()
    dept_channel = bot.get_channel(WELCOME_CHANNEL_ID)
    if not dept_channel:
        return await ctx.send("Welcome channel not found.", delete_after=5)
    view = discord.ui.View()
    depts = [
        (discord.PartialEmoji(name="OCPD", id=1517969578435543090), "https://discord.gg/3DHMDaP8aw"),
        (discord.PartialEmoji(name="OHP", id=1517969606612881660), "https://discord.gg/nyPwdazyZw"),
        (discord.PartialEmoji(name="OCSO", id=1523420934755844167), "https://discord.gg/zpGMDzgSDu"),
        (discord.PartialEmoji(name="EMSA", id=1523424479467012248), "https://discord.gg/QAvtTW9SvC"),
        (discord.PartialEmoji(name="OCFD", id=1517969633896828942), "https://discord.gg/5v49YjdssZ"),
    ]
    for emoji, url in depts:
        view.add_item(discord.ui.Button(emoji=emoji, url=url, style=discord.ButtonStyle.link))
    await dept_channel.send("**__Department Invites__**", view=view)
    await ctx.send("Department invites sent!", delete_after=3)


@bot.command()
async def samplewelcome(ctx):
    """Preview the welcome message."""
    member_count = sum(1 for m in ctx.guild.members if not m.bot)
    ordinal = get_ordinal(member_count)
    content = (
        f"Welcome to **Oklahoma State Roleplay** {ctx.author.mention}.\n"
        f"You are our **{ordinal}** member!"
    )
    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        emoji=discord.PartialEmoji(name="member", id=1523432455091261500),
        label=f"{member_count}",
        style=discord.ButtonStyle.secondary,
        disabled=True
    ))
    view.add_item(discord.ui.Button(
        emoji=discord.PartialEmoji(name="info", id=1523432618866118767),
        label="Dashboard",
        url="https://discord.com/channels/1517672283513294868/1517682110842798192",
        style=discord.ButtonStyle.link
    ))
        
    await ctx.send(content=content, view=view)


@bot.command()
async def checkperms(ctx):
    """Check bot permissions in the dashboard channel."""
    channel = ctx.guild.get_channel(1517682110842798192)
    if not channel:
        return await ctx.send("Dashboard channel `1517682110842798192` not found in this guild.")
    bot_member = ctx.guild.me
    perms = channel.permissions_for(bot_member)
    needed = ["view_channel", "send_messages", "read_message_history", "embed_links"]
    lines = [f"Bot permissions in <#{channel.id}>:"]
    for p in needed:
        lines.append(f"- **{p}**: {'✅' if getattr(perms, p, False) else '❌'}")
    await ctx.send("\n".join(lines))





@bot.command()
@commands.has_permissions(manage_guild=True)
async def testkick(ctx, roblox_username: str):
    """Simulate an ingame kick (for testing). Admin only."""
    guild = ctx.guild
    
    # Look up Roblox info
    roblox_id, roblox_url, roblox_created = await get_roblox_info(roblox_username)
    
    # Find the Discord user by nickname
    kicked_user = None
    for member in guild.members:
        if extract_roblox_username(member).lower() == roblox_username.lower():
            kicked_user = member
            break
    
    if not kicked_user:
        await ctx.send(f"No Discord member found with Roblox username '{roblox_username}' in their nickname.")
        return
    
    kicked_db[str(kicked_user.id)] = {
        "roblox_username": roblox_username,
        "roblox_id": roblox_id,
        "roblox_url": roblox_url,
        "roblox_created": roblox_created,
        "kicked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    save_json(KICKED_FILE, kicked_db)
    await ctx.send(f"âœ… Tracked kick for {roblox_username} (Discord: {kicked_user.mention}). They will be reminded if they rejoin within {KICK_REMINDER_WINDOW_MINUTES} minutes.")


@bot.command()
@commands.has_permissions(manage_guild=True)
async def checkbanappeal(ctx, user_id: str):
    """Check ban appeal status for a user. Admin only."""
    user_appeals = {k: v for k, v in appeals_db.items() if v.get("user_id") == user_id}
    if not user_appeals:
        await ctx.send(f"No appeals found for user ID {user_id}.")
        return
    
    latest_key = max(user_appeals.keys())
    appeal = user_appeals[latest_key]
    await ctx.send(f"**Latest Appeal for {user_id}**\n"
                   f"Status: {appeal['status']}\n"
                   f"Submitted: {appeal['submitted_at']}\n"
                   f"Appeal ID: {latest_key}")


@bot.command()
@commands.has_permissions(manage_guild=True)
async def resendappeallink(ctx, member: discord.Member):
    """Resend the ban appeal code to a user. Admin only."""
    user_id_str = str(member.id)
    
    existing_token = None
    for token, data in appeal_tokens_db.items():
        if data.get("user_id") == user_id_str and not data.get("used"):
            existing_token = token
            break
    
    appeal_url = f"{BASE_URL}/appeal"

    async def send_appeal_dm(user, code):
        msg = (
            f"Your ban appeal code for **Oklahoma State Roleplay:**\n\n"
            f"`{{ {code} }}`\n\n"
            f"Go to {appeal_url} and enter this code to submit your appeal.\n\n"
            f"**This code is unique to you, sharing it can result in a permanent ban\n_ _\nwithout appeal from the server.**"
        )
        await user.send(msg)

    if existing_token:
        try:
            await send_appeal_dm(member, existing_token)
            await ctx.send(f"Appeal code resent to {member.mention}.")
        except Exception:
            await ctx.send(f"Could not DM {member.mention}. They may have DMs closed.")
    else:
        token = generate_appeal_token()
        appeal_tokens_db[token] = {
            "user_id": user_id_str,
            "punishment": "ban",
            "total_points": points_db.get(user_id_str, 0),
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "used": False
        }
        save_json(APPEAL_TOKENS_FILE, appeal_tokens_db)
        try:
            await send_appeal_dm(member, token)
            await ctx.send(f"New appeal code created and sent to {member.mention}.")
        except Exception:
            await ctx.send(f"Could not DM {member.mention}. They may have DMs closed.")


@bot.command()
@commands.has_permissions(manage_guild=True)
async def blacklist(ctx, action: str = None, user_id: str = None):
    """Manage appeal blacklist. Usage: !blacklist add <user_id> | !blacklist remove <user_id> | !blacklist list"""
    if action == "add":
        if not user_id:
            return await ctx.send("Usage: `!blacklist add <discord_user_id>`")
        blacklist_db[user_id] = {
            "added_by": str(ctx.author.id),
            "added_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        save_json(BLACKLIST_FILE, blacklist_db)
        await ctx.send(f"User `{user_id}` has been blacklisted from submitting appeals.")
    
    elif action == "remove":
        if not user_id:
            return await ctx.send("Usage: `!blacklist remove <discord_user_id>`")
        if user_id in blacklist_db:
            del blacklist_db[user_id]
            save_json(BLACKLIST_FILE, blacklist_db)
            await ctx.send(f"User `{user_id}` has been removed from the appeal blacklist.")
        else:
            await ctx.send(f"User `{user_id}` is not in the blacklist.")
    
    elif action == "list":
        if not blacklist_db:
            return await ctx.send("The blacklist is empty.")
        entries = [f"<@{uid}> ({uid}) - {data['added_at']}" for uid, data in blacklist_db.items()]
        await ctx.send(f"**Blacklisted Users ({len(entries)}):**\n" + "\n".join(entries))
    
    else:
        await ctx.send("Usage: `!blacklist add <user_id>` | `!blacklist remove <user_id>` | `!blacklist list`")


@bot.command()
async def sampleping(ctx):
    """Preview the direct @mention anti-ping response."""
    embed = discord.Embed(
        description="**Do not @ mention members of the Directorship Team.**\n@ mentioning directors is a violation of [rule 4](https://discord.com/channels/1517672283513294868/1517682110842798192).",
        color=0x01d3ff
    )
    embed.set_image(url="https://media.tenor.com/7694799882666584177/discord-ping-off-no-ping-reply-ping.gif")
    await ctx.send(content=ctx.author.mention, embed=embed)


@bot.command()
async def samplereplyping(ctx):
    """Preview the reply-ping anti-ping response."""
    embed = discord.Embed(
        description="Do not @ mention members of the **Directorship Team.**",
        color=0x01d3ff
    )
    embed.set_image(url="https://media.tenor.com/7694799882666584177/discord-ping-off-no-ping-reply-ping.gif")
    await ctx.send(content=ctx.author.mention, embed=embed)


async def main():
    # Start web server always (even without Discord token)
    web_task = asyncio.create_task(start_web_server())
    await asyncio.sleep(0.5)
    
    if TOKEN:
        async with bot:
            await bot.start(TOKEN)
    else:
        print("[WARNING] DISCORD_TOKEN not set - bot will not connect. Web server running.")
        # Keep the process alive
        while True:
            await asyncio.sleep(3600)


asyncio.run(main())
