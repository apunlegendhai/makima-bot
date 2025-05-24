import discord
from discord.ext import commands
import random
import time
from zoneinfo import ZoneInfo
from datetime import datetime

# ─── Random Color System ───────────────────────────────────────────────────────

PALETTE_SIZE = 20

def generate_palette(n=PALETTE_SIZE):
    palette = []
    for i in range(n):
        h = i / n
        s = random.uniform(0.5, 1.0)
        v = random.uniform(0.7, 1.0)
        palette.append(discord.Color.from_hsv(h, s, v))
    random.shuffle(palette)
    return palette

_color_stack = generate_palette()

def get_next_color():
    global _color_stack
    if not _color_stack:
        _color_stack = generate_palette()
    return _color_stack.pop()

# ─── Cache & Timezone Storage ─────────────────────────────────────────────────

# Cache structure: { guild_id: { 'timestamp': float, 'embed': discord.Embed } }
CACHE_TTL = 30  # seconds
_si_cache = {}

# Guild timezones: { guild_id: "Region/City" }
_guild_timezones = {}

# ─── ServerInfo Cog ────────────────────────────────────────────────────────────

class ServerInfo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='settimezone')
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def set_timezone(self, ctx, tz: str):
        """
        Set this guild's timezone (IANA format, e.g. 'Europe/London').
        Affects how timestamps are displayed in /si.
        """
        try:
            # Validate
            ZoneInfo(tz)
        except Exception:
            return await ctx.send(f"Invalid timezone: `{tz}`. See https://en.wikipedia.org/wiki/List_of_tz_database_time_zones")
        _guild_timezones[ctx.guild.id] = tz
        await ctx.send(f"Timezone set to `{tz}` for this server.")

    @commands.command(name='si', aliases=['serverinfo', 'guildinfo'])
    @commands.guild_only()
    async def server_info(self, ctx):
        """Display detailed server information (cached for 30s; localized timestamps)."""
        guild = ctx.guild
        now = time.time()

        # Check cache
        cached = _si_cache.get(guild.id)
        if cached and now - cached['timestamp'] < CACHE_TTL:
            return await ctx.send(embed=cached['embed'])

        # Determine timezone
        tz_name = _guild_timezones.get(guild.id, 'Asia/Kolkata')
        zone = ZoneInfo(tz_name)

        # Format helper
        def fmt_time(dt: datetime, style: str = 'F'):
            ts = int(dt.replace(tzinfo=ZoneInfo('UTC')).timestamp())
            local = dt.astimezone(zone).strftime('%d %b %Y • %I:%M %p')
            return f"<t:{ts}:{style}>\n({local})"

        # Build sections
        about = (
            f"Name: {guild.name}\n"
            f"ID: {guild.id}\n"
            f"Owner <:Owner_Crow:1375731826093461544> : {guild.owner.mention if guild.owner else 'Unknown'}\n"
            f"Created At: {fmt_time(guild.created_at)}\n"
            f"Members: {guild.member_count}\n"
            f"Verification Level: {str(guild.verification_level).title()}\n"
            f"Explicit Content Filter: {str(guild.explicit_content_filter).title()}\n"
            f"Boost Level:[<a:server_boostin:1375731855008989224> {guild.premium_tier} ({guild.premium_subscription_count} boosts)]\n"
            f"Vanity URL: {guild.vanity_url_code if guild.vanity_url_code else 'None'}\n"
            f"Features: {', '.join(f'`{feature.replace('_', ' ').title()}`' for feature in guild.features) if guild.features else 'None'}"
        )
        description = guild.description or "None"

        # Build embed
        embed = discord.Embed(
            title=f"{guild.name} — Information",
            color=get_next_color(),
            timestamp=datetime.now(tz=zone)
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        embed.add_field(name="__ABOUT__", value=about, inline=False)
        embed.add_field(name="__DESCRIPTION__", value=description, inline=False)

        # Add channel counts
        channels = (
            f"Categories: {len(guild.categories)}\n"
            f"Text Channels: {len(guild.text_channels)}\n"
            f"Voice Channels: {len(guild.voice_channels)}\n"
            f"Forums: {len(guild.forums)}\n"
            f"Total: {len(guild.channels)}\n"
            f"System Channel: {guild.system_channel.mention if guild.system_channel else 'None'}\n"
            f"Rules Channel: {guild.rules_channel.mention if guild.rules_channel else 'None'}\n"
            f"Public Updates Channel: {guild.public_updates_channel.mention if guild.public_updates_channel else 'None'}"
        )
        embed.add_field(name="__CHANNELS__", value=channels, inline=False)

        # Add role counts
        roles = (
            f"Total Roles: {len(guild.roles)}\n"
            f"Managed Roles: {len([r for r in guild.roles if r.managed])}\n"
            f"Highest Role: {guild.roles[-1].mention if guild.roles else 'None'}\n"
            f"Role Color: {guild.roles[-1].color if guild.roles else 'None'}\n"
            f"Role Position: {guild.roles[-1].position if guild.roles else 'None'}"
        )
        embed.add_field(name="__ROLES__", value=roles, inline=False)

        # Add emoji counts
        emojis = (
            f"Regular: {len(guild.emojis)}\n"
            f"Animated: {len(guild.emojis) - len([e for e in guild.emojis if not e.animated])}\n"
            f"Total: {len(guild.emojis)}\n"
            f"Emoji Limit: {guild.emoji_limit}\n"
            f"Sticker Limit: {guild.sticker_limit}"
        )
        embed.add_field(name="__EMOJIS__", value=emojis, inline=False)

        # Add member stats
        online_members = len([m for m in guild.members if m.status != discord.Status.offline])
        idle_members = len([m for m in guild.members if m.status == discord.Status.idle])
        dnd_members = len([m for m in guild.members if m.status == discord.Status.dnd])
        bot_count = len([m for m in guild.members if m.bot])
        human_count = guild.member_count - bot_count

        members = (
            f"Total: {guild.member_count}\n"
            f"Humans: {human_count}\n"
            f"Bots: {bot_count}\n"
            f"Online: {online_members}\n"
            f"Idle: {idle_members}\n"
            f"Do Not Disturb: {dnd_members}\n"
            f"Offline: {guild.member_count - online_members - idle_members - dnd_members}"
        )
        embed.add_field(name="__MEMBERS__", value=members, inline=False)

        # Add server limits
        limits = (
            f"File Upload Limit: {guild.filesize_limit/1024/1024:.1f}MB\n"
            f"Bitrate Limit: {guild.bitrate_limit/1000:.0f}kbps\n"
            f"Emoji Limit: {guild.emoji_limit}\n"
            f"Sticker Limit: {guild.sticker_limit}"
        )
        embed.add_field(name="__SERVER LIMITS__", value=limits, inline=False)

        embed.set_footer(text=f"Requested by {ctx.author.display_name} • Server ID: {guild.id}")

        # Add server banner if it exists
        if guild.banner:
            embed.set_image(url=guild.banner.url)

        # Cache & send
        _si_cache[guild.id] = {'timestamp': now, 'embed': embed}
        await ctx.send(embed=embed)

    @commands.command(name='roleinfo')
    @commands.guild_only()
    async def role_info(self, ctx, *, role: discord.Role = None):
        """Display information about a role."""
        if role is None:
            return await ctx.send("Please specify a role: `.roleinfo @RoleName`")

        # (reuse your existing roleinfo code here—timestamps can also use fmt_time())

        # Example for creation date with localization:
        tz_name = _guild_timezones.get(ctx.guild.id, 'UTC')
        zone = ZoneInfo(tz_name)
        created_local = role.created_at.astimezone(zone).strftime('%Y-%m-%d %H:%M:%S')
        created_field = (
            f"<t:{int(role.created_at.timestamp())}:F> "
            f"({created_local} {tz_name})"
        )

        embed = discord.Embed(
            title=f"Role Information: {role.name}",
            color=get_next_color(),
            timestamp=datetime.now(tz=zone)
        )
        embed.add_field(name="Created At", value=created_field, inline=False)
        # … rest of your fields …

        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @commands.command(name='mc', aliases=['membercount'])
    @commands.guild_only()
    async def member_count(self, ctx):
        """Display the total member count."""
        total = ctx.guild.member_count
        tz_name = _guild_timezones.get(ctx.guild.id, 'UTC')
        zone = ZoneInfo(tz_name)
        embed = discord.Embed(
            title="Member Count",
            description=f"{total:,}",
            color=get_next_color(),
            timestamp=datetime.now(tz=zone)
        )
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ServerInfo(bot))
