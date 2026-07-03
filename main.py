import discord
from discord.ext import commands, tasks
import json
import os
import re
import datetime
import aiohttp
from aiohttp import web
import secrets
import asyncio

TOKEN = os.environ["DISCORD_TOKEN"]

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
    "temp ban": 4,
    "tempban": 4,
    "temp banned": 4,
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
APPEAL_COOLDOWN_DAYS = 30  # Must wait 1 month before appealing
KICK_REMINDER_WINDOW_MINUTES = 30  # Only remind if user rejoins within 30 min

# Channels
WELCOME_CHANNEL_ID = 1517684680005124136
DASHBOARD_CHANNEL_ID = 1517682110842798192
APPEALS_CHANNEL_ID = 1519408033170460672
INGAME_KICK_CHANNEL_ID = 1521216668402188461  # ERLC webhook channel (kick + join events)
INGAME_REMINDER_CHANNEL_ID = 1519468672849150022
INGAME_MODERATING_ROLE_ID = 1520870451923124415

GUILD_ID = 1517672283513294868

BASE_URL = os.getenv("BASE_URL", "https://osrp-bot-production.up.railway.app")

EMBED_COLOR = 0x01D3FF

POINTS_FILE = os.path.join(os.path.dirname(__file__), "points.json")
CASES_FILE  = os.path.join(os.path.dirname(__file__), "cases.json")
APPEALS_FILE = os.path.join(os.path.dirname(__file__), "appeals.json")
KICKED_FILE = os.path.join(os.path.dirname(__file__), "kicked.json")
APPEAL_TOKENS_FILE = os.path.join(os.path.dirname(__file__), "appeal_tokens.json")

last_command_channel: dict[str, int] = {}
processed_cases: set[str] = set()
processed_message_ids: set[int] = set()
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
    except:
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
    
    embed.add_field(name="Why were you banned? Why do you deserve to be unbanned?", 
                    value=appeal_data.get("ban_reason", "N/A"), inline=False)
    embed.add_field(name="Time Since Ban", value=appeal_data.get("time_since_ban", "N/A"), inline=False)
    
    if appeal_data.get("extra_info"):
        embed.add_field(name="Do you have any extra information to provide?", 
                        value=appeal_data.get("extra_info"), inline=False)
    
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
    
    # â”€â”€ Welcome message â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    welcome_channel = guild.get_channel(WELCOME_CHANNEL_ID)
    if not welcome_channel:
        return
    
    member_count = sum(1 for m in guild.members if not m.bot)
    ordinal = get_ordinal(member_count)
    
    embed = discord.Embed(
        description=f"Welcome {member.mention} to <:OSRP:1517680995678027957> Oklahoma State Roleplay, you are our **{ordinal} member**",
        color=EMBED_COLOR,
    )
    
    class DashboardButton(discord.ui.View):
        def __init__(self):
            super().__init__()
            self.add_item(discord.ui.Button(
                label="Dashboard",
                url=f"https://discord.com/channels/{GUILD_ID}/{DASHBOARD_CHANNEL_ID}",
                style=discord.ButtonStyle.link
            ))
    
    await welcome_channel.send(embed=embed, view=DashboardButton())


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
        self.ban_reason = discord.ui.TextInput(
            label="Why were you banned? Why do you deserve to be unbanned?",
            placeholder="Explain why you were banned and why you should be unbanned...",
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
        super().__init__(items=[self.discord_username, self.discord_id, self.ban_reason, self.time_since_ban, self.extra_info])

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        user_id = interaction.user.id
        appeal_id = f"{user_id}_{int(datetime.datetime.now().timestamp())}"
        
        # Verify this is the same user who was sent the form
        if interaction.user.id != self.user.id:
            await interaction.followup.send("âŒ This appeal form is not for your account.", ephemeral=True)
            return
        
        # Store appeal data
        appeals_db[appeal_id] = {
            "user_id": str(user_id),
            "discord_username": self.discord_username.value,
            "discord_id": self.discord_id.value,
            "ban_reason": self.ban_reason.value,
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
            ban_reason=self.ban_reason.value,
            time_since_ban=self.time_since_ban.value,
            extra_info=self.extra_info.value,
        )
        
        class AppealReviewView(discord.ui.View):
            def __init__(self):
                super().__init__()
            
            @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
            async def approve_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                if not has_any_role(button_interaction.user, APPEAL_REVIEW_ROLES):
                    await button_interaction.response.send_message("âŒ You don't have permission to approve appeals.", ephemeral=True)
                    return
                
                appeals_db[appeal_id]["status"] = "approved"
                save_json(APPEALS_FILE, appeals_db)
                
                try:
                    await guild.unban(discord.Object(int(user_id)), reason=f"Ban appeal approved - {button_interaction.user.name}")
                    await interaction.user.send(f"âœ… Your ban appeal has been **APPROVED**! You have been unbanned from {guild.name}.")
                except Exception as e:
                    print(f"[APPEAL] Failed to unban {user_id}: {e}")
                
                embed.color = discord.Color.green()
                embed.description = "**__Ban Appeal - APPROVED__**"
                embed.add_field(name="Approved By", value=button_interaction.user.mention, inline=False)
                await button_interaction.response.edit_message(embed=embed, view=None)
            
            @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
            async def deny_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                if not has_any_role(button_interaction.user, APPEAL_REVIEW_ROLES):
                    await button_interaction.response.send_message("âŒ You don't have permission to deny appeals.", ephemeral=True)
                    return
                
                appeals_db[appeal_id]["status"] = "denied"
                save_json(APPEALS_FILE, appeals_db)
                
                try:
                    await interaction.user.send(f"âŒ Your ban appeal has been **DENIED**. You can re-appeal after 3 months.")
                except:
                    pass
                
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
    
    # Start web server for ban appeal website
    asyncio.create_task(start_web_server())
    
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

    # â”€â”€ Track which channel the last punishment command was issued in â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if not message.author.bot and message.content and message.content.startswith("!"):
        cmd = message.content.lstrip("!").lower().split()[0]
        if any(cmd.startswith(p.replace(" ", "")) for p in POINTS):
            if has_any_role(message.author, PUNISHER_ROLES):
                last_command_channel[guild_id] = message.channel.id

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

                channel_id = last_command_channel.get(guild_id)
                target_channel = (
                    message.guild.get_channel(channel_id)
                    if channel_id else message.channel
                )

                pts_msg = await target_channel.send(
                    f"{mention} now has **{current_points} {point_word}**."
                )
                asyncio.create_task(delete_after_delay(pts_msg, 25))
                
                # If it's a ban/temp ban, generate appeal token and DM
                if matched_punishment[0] in ("ban", "temp ban", "tempban", "temp banned"):
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
    
    try:
        user = await bot.fetch_user(int(user_id_str))
        await user.send(
            f"You have been {punishment_type} from **Oklahoma State Roleplay**.\n\n"
            f"You currently have **{total_points} points** (threshold: {POINT_THRESHOLD}).\n\n"
            f"**Note:** You can only submit a ban appeal **{APPEAL_COOLDOWN_DAYS} days** after your ban.\n"
            f"Once the cooldown has passed, go to {BASE_URL}/appeal and enter your unique appeal code.\n\n"
            f"**Your Appeal Code:** `{token}`\n\n"
            f"*This code is unique to you â€” do not share it.*"
        )
        print(f"[APPEAL] DM sent to {user_id} with appeal token {token}")
    except discord.Forbidden:
        print(f"[APPEAL] Cannot DM {user_id} - DMs closed")
    except discord.NotFound:
        print(f"[APPEAL] User {user_id} not found")
    except Exception as e:
        print(f"[APPEAL] Failed to send DM to {user_id}: {e}")


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
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
            color: #c9d1d9;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            max-width: 600px;
            width: 100%;
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 12px;
            padding: 40px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.4);
        }
        h1 { color: #01d3ff; margin-bottom: 8px; font-size: 24px; }
        .subtitle { color: #8b949e; margin-bottom: 24px; font-size: 14px; }
        .info-box {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 24px;
            font-size: 13px;
            line-height: 1.6;
        }
        .info-box .label { color: #8b949e; }
        .info-box .value { color: #c9d1d9; font-weight: 600; }
        form { display: flex; flex-direction: column; gap: 16px; }
        label { font-size: 13px; font-weight: 600; color: #c9d1d9; }
        input, textarea {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 10px 14px;
            color: #c9d1d9;
            font-size: 14px;
            font-family: inherit;
            transition: border-color 0.2s;
            box-sizing: border-box;
            width: 100%;
        }
        input:focus, textarea:focus {
            outline: none;
            border-color: #01d3ff;
        }
        input:disabled {
            opacity: 0.6;
            cursor: not-allowed;
        }
        textarea { resize: vertical; min-height: 100px; }
        button {
            background: #01d3ff;
            color: #0d1117;
            border: none;
            border-radius: 6px;
            padding: 12px 24px;
            font-size: 16px;
            font-weight: 700;
            cursor: pointer;
            transition: background 0.2s;
        }
        button:hover { background: #00b8e6; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
        .error { color: #f85149; font-size: 13px; margin-top: 4px; }
        .success { color: #3fb950; font-size: 14px; text-align: center; padding: 20px; }
        .hidden { display: none; }
        .field-note { font-size: 11px; color: #8b949e; margin-top: -8px; }
        .cooldown-warning {
            background: rgba(240, 136, 62, 0.1);
            border: 1px solid #f0883e;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 24px;
            font-size: 13px;
            line-height: 1.6;
            color: #f0883e;
        }
        .code-input {
            text-align: center;
            font-size: 24px;
            letter-spacing: 8px;
            text-transform: uppercase;
            font-weight: 700;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>OSRP Ban Appeal</h1>
        <p class="subtitle">Oklahoma State Roleplay - Ban Appeal Submission</p>
        
        <div id="error-box" class="error hidden"></div>
        <div id="success-box" class="success hidden"></div>
        <div id="cooldown-box" class="cooldown-warning hidden"></div>
        
        <div id="code-section">
            <p style="margin-bottom:16px;color:#8b949e;">Enter the 10-character appeal code sent to your Discord DMs.</p>
            <input type="text" id="code-input" class="code-input" maxlength="10" placeholder="XXXXXXXXXX" autocomplete="off">
            <button id="code-submit-btn" style="margin-top:16px;width:100%;">Verify Code</button>
        </div>
        
        <div id="info-box" class="info-box hidden">
            <div><span class="label">Discord:</span> <span class="value" id="info-discord"></span></div>
            <div><span class="label">Punishment:</span> <span class="value" id="info-punishment"></span></div>
            <div><span class="label">Status:</span> <span class="value" id="info-status"></span></div>
        </div>
        
        <form id="appeal-form" class="hidden">
            <div>
                <label for="discord_username">Discord Username</label>
                <input type="text" id="discord_username" disabled>
            </div>
            <div>
                <label for="discord_id">Discord ID</label>
                <input type="text" id="discord_id" disabled>
            </div>
            <div>
                <label for="ban_reason">Why were you banned? Why do you deserve to be unbanned?</label>
                <textarea id="ban_reason" required></textarea>
            </div>
            <div>
                <label for="time_since_ban">Time Since Ban</label>
                <input type="text" id="time_since_ban" placeholder="e.g. 2 months" required>
            </div>
            <div>
                <label for="extra_info">Do you have any extra information to provide?</label>
                <textarea id="extra_info" placeholder="Optional"></textarea>
                <div class="field-note">Any additional context or information you'd like to share</div>
            </div>
            <button type="submit" id="submit-btn">Submit Appeal</button>
        </form>
    </div>
    
    <script>
        const tokenInput = document.getElementById('code-input');
        const codeSection = document.getElementById('code-section');
        const infoBox = document.getElementById('info-box');
        const appealForm = document.getElementById('appeal-form');
        const errorBox = document.getElementById('error-box');
        const successBox = document.getElementById('success-box');
        const cooldownBox = document.getElementById('cooldown-box');
        
        let currentToken = null;
        
        tokenInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                document.getElementById('code-submit-btn').click();
            }
        });
        
        document.getElementById('code-submit-btn').addEventListener('click', function() {
            const token = tokenInput.value.trim().toUpperCase();
            if (token.length !== 10) {
                errorBox.textContent = 'Please enter a valid 10-character appeal code.';
                errorBox.classList.remove('hidden');
                return;
            }
            errorBox.classList.add('hidden');
            
            fetch('/api/appeal/info?token=' + encodeURIComponent(token))
                .then(r => r.json())
                .then(data => {
                    if (data.error) {
                        errorBox.textContent = data.error;
                        errorBox.classList.remove('hidden');
                        return;
                    }
                    
                    if (data.used) {
                        errorBox.textContent = 'This appeal code has already been used. If you need to submit another appeal, please contact staff.';
                        errorBox.classList.remove('hidden');
                        return;
                    }
                    
                    currentToken = token;
                    codeSection.classList.add('hidden');
                    
                    document.getElementById('info-discord').textContent = data.discord_username + ' (#' + data.discord_id + ')';
                    document.getElementById('info-punishment').textContent = data.punishment;
                    document.getElementById('info-status').textContent = data.cooldown_active ? 'Cooldown Active' : 'Eligible to Appeal';
                    infoBox.classList.remove('hidden');
                    
                    document.getElementById('discord_username').value = data.discord_username;
                    document.getElementById('discord_id').value = data.discord_id;
                    
                    if (data.cooldown_active) {
                        cooldownBox.textContent = 'You are currently on cooldown. You can submit an appeal after ' + data.cooldown_ends + '. Please wait until the cooldown has passed.';
                        cooldownBox.classList.remove('hidden');
                        document.getElementById('submit-btn').disabled = true;
                    } else {
                        appealForm.classList.remove('hidden');
                    }
                })
                .catch(err => {
                    errorBox.textContent = 'Failed to load appeal info. Please try again later.';
                    errorBox.classList.remove('hidden');
                });
        });
        
        appealForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const submitBtn = document.getElementById('submit-btn');
            submitBtn.disabled = true;
            submitBtn.textContent = 'Submitting...';
            
            const data = {
                token: currentToken,
                discord_username: document.getElementById('discord_username').value,
                discord_id: document.getElementById('discord_id').value,
                ban_reason: document.getElementById('ban_reason').value,
                time_since_ban: document.getElementById('time_since_ban').value,
                extra_info: document.getElementById('extra_info').value
            };
            
            fetch('/api/appeal/submit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            })
            .then(r => r.json())
            .then(response => {
                if (response.success) {
                    appealForm.classList.add('hidden');
                    infoBox.classList.add('hidden');
                    successBox.textContent = 'Your ban appeal has been submitted successfully! Staff will review it and you will be notified via Discord. Your appeal ID is: ' + response.appeal_id;
                    successBox.classList.remove('hidden');
                } else {
                    errorBox.textContent = response.error || 'Submission failed. Please try again.';
                    errorBox.classList.remove('hidden');
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Submit Appeal';
                }
            })
            .catch(err => {
                errorBox.textContent = 'Network error. Please try again.';
                errorBox.classList.remove('hidden');
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
        return web.json_response({"error": "Invalid or expired appeal link."}, status=404)
    
    user_id = token_data["user_id"]
    
    # Look up Discord user info
    guild = bot.get_guild(GUILD_ID)
    discord_username = "Unknown"
    if guild:
        member = guild.get_member(int(user_id))
        if member:
            discord_username = str(member)
    
    # Check cooldown (1 month from creation)
    created_at = datetime.datetime.fromisoformat(token_data["created_at"])
    now = datetime.datetime.now(datetime.timezone.utc)
    days_elapsed = (now - created_at).days
    cooldown_active = days_elapsed < APPEAL_COOLDOWN_DAYS
    cooldown_ends = (created_at + datetime.timedelta(days=APPEAL_COOLDOWN_DAYS)).strftime("%Y-%m-%d %H:%M UTC")
    
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
    except:
        return web.json_response({"error": "Invalid JSON body."}, status=400)
    
    token = body.get("token", "")
    ban_reason = body.get("ban_reason", "").strip()
    time_since_ban = body.get("time_since_ban", "").strip()
    extra_info = body.get("extra_info", "").strip()
    discord_username = body.get("discord_username", "").strip()
    discord_id = body.get("discord_id", "").strip()
    
    if not token or not ban_reason or not time_since_ban:
        return web.json_response({"error": "Required fields missing."}, status=400)
    
    token_data = appeal_tokens_db.get(token)
    if not token_data:
        return web.json_response({"error": "Invalid or expired appeal link."}, status=404)
    
    if token_data.get("used"):
        return web.json_response({"error": "This appeal link has already been used."}, status=400)
    
    user_id = token_data["user_id"]
    
    # Verify that the submitted Discord ID matches the token's user
    if discord_id != user_id:
        return web.json_response({"error": "Discord ID mismatch. This appeal link is not for this account."}, status=403)
    
    # Check cooldown
    created_at = datetime.datetime.fromisoformat(token_data["created_at"])
    now = datetime.datetime.now(datetime.timezone.utc)
    days_elapsed = (now - created_at).days
    if days_elapsed < APPEAL_COOLDOWN_DAYS:
        cooldown_ends = (created_at + datetime.timedelta(days=APPEAL_COOLDOWN_DAYS)).strftime("%Y-%m-%d %H:%M UTC")
        return web.json_response({
            "error": f"You are still on cooldown. You can submit an appeal after {cooldown_ends}."
        }, status=400)
    
    # Create the appeal
    appeal_id = f"{user_id}_{int(now.timestamp())}"
    
    appeals_db[appeal_id] = {
        "user_id": user_id,
        "discord_username": discord_username,
        "discord_id": discord_id,
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
                ban_reason=ban_reason,
                time_since_ban=time_since_ban,
                extra_info=extra_info,
            )
            
            class AppealReviewView(discord.ui.View):
                def __init__(self):
                    super().__init__()
                
                @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
                async def approve_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                    if not has_any_role(button_interaction.user, APPEAL_REVIEW_ROLES):
                        await button_interaction.response.send_message("âŒ You don't have permission to approve appeals.", ephemeral=True)
                        return
                    
                    appeals_db[appeal_id]["status"] = "approved"
                    save_json(APPEALS_FILE, appeals_db)
                    
                    try:
                        await guild.unban(discord.Object(int(user_id)), reason=f"Ban appeal approved - {button_interaction.user.name}")
                        try:
                            user = await bot.fetch_user(int(user_id))
                            await user.send(f"âœ… Your ban appeal has been **APPROVED**! You have been unbanned from {guild.name}.")
                        except:
                            pass
                    except Exception as e:
                        print(f"[APPEAL] Failed to unban {user_id}: {e}")
                    
                    embed.color = discord.Color.green()
                    embed.description = "**__Ban Appeal - APPROVED__**"
                    embed.add_field(name="Approved By", value=button_interaction.user.mention, inline=False)
                    await button_interaction.response.edit_message(embed=embed, view=None)
                
                @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
                async def deny_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                    if not has_any_role(button_interaction.user, APPEAL_REVIEW_ROLES):
                        await button_interaction.response.send_message("âŒ You don't have permission to deny appeals.", ephemeral=True)
                        return
                    
                    appeals_db[appeal_id]["status"] = "denied"
                    save_json(APPEALS_FILE, appeals_db)
                    
                    try:
                        user = await bot.fetch_user(int(user_id))
                        await user.send(f"âŒ Your ban appeal has been **DENIED**. You can re-appeal after 3 months.")
                    except:
                        pass
                    
                    embed.color = discord.Color.red()
                    embed.description = "**__Ban Appeal - DENIED__**"
                    embed.add_field(name="Denied By", value=button_interaction.user.mention, inline=False)
                    await button_interaction.response.edit_message(embed=embed, view=None)
            
            await appeals_channel.send(embed=embed, view=AppealReviewView())
    
    return web.json_response({"success": True, "appeal_id": appeal_id})


async def handle_appeal_page(request):
    return web.Response(text=APPEAL_HTML, content_type="text/html")


async def start_web_server():
    app = web.Application()
    
    # Serve static pages and API
    app.router.add_get("/appeal", handle_appeal_page)
    app.router.add_get("/api/appeal/info", handle_appeal_info)
    app.router.add_post("/api/appeal/submit", handle_appeal_submit)
    
    # Health check
    async def healthz(request):
        return web.Response(text="ok")
    app.router.add_get("/healthz", healthz)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[WEB] Listening on 0.0.0.0:{port}")


# â”€â”€ Commands â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@bot.command()
async def mypoints(ctx):
    member = ctx.author
    total = points_db.get(str(member.id), 0)
    point_word = "point" if total == 1 else "points"
    msg = await ctx.send(f"{member.mention}, you have **{total} {point_word}**.")
    asyncio.create_task(delete_after_delay(msg, 25))


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
        await ctx.author.send(
            f"**Test Appeal Code**\n\n"
            f"Your unique 10-character code: **`{token}`**\n\n"
            f"Go to {BASE_URL}/appeal and enter this code to test the appeal form.\n\n"
            f"*This token is backdated so the 30-day cooldown is already passed.*"
        )
        await ctx.send(f"Test appeal code sent to your DMs! Check your DMs.")
    except:
        await ctx.send(f"Could not DM you. Your code is: **`{token}`**\n\nGo to {BASE_URL}/appeal and enter it.")


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
    
    if existing_token:
        try:
            await member.send(f"Your ban appeal code for **Oklahoma State Roleplay**:\n\n**`{existing_token}`**\n\nGo to {BASE_URL}/appeal and enter this code to submit your appeal.\n\n*This code is unique to you â€” do not share it.*")
            await ctx.send(f"Appeal code resent to {member.mention}.")
        except:
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
            await member.send(f"Your ban appeal code for **Oklahoma State Roleplay**:\n\n**`{token}`**\n\nGo to {BASE_URL}/appeal and enter this code to submit your appeal.\n\n*This code is unique to you â€” do not share it.*")
            await ctx.send(f"New appeal code created and sent to {member.mention}.")
        except:
            await ctx.send(f"Could not DM {member.mention}. They may have DMs closed.")


# â”€â”€ Main â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def main():
    async with bot:
        await bot.start(TOKEN)


asyncio.run(main())
