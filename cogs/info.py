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

# ─── Utility Functions ─────────────────────────────────────────────────────────

def truncate_field(text, max_length=1020):
    """Truncate text to fit Discord field limits with ellipsis."""
    if len(text) <= max_length:
        return text
    return text[:max_length-3] + "..."

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
        tz_name = _guild_timezones.get(guild.id, 'UTC')
        zone = ZoneInfo(tz_name)

        # Format helper
        def fmt_time(dt: datetime, style: str = 'F'):
            ts = int(dt.replace(tzinfo=ZoneInfo('UTC')).timestamp())
            local = dt.astimezone(zone).strftime('%d %b %Y • %I:%M %p')
            return f"<t:{ts}:{style}>\n({local})"

        # Build sections with length management
        about = (
            f"**Name:** {guild.name}\n"
            f"**ID:** {guild.id}\n"
            f"**Owner** <:Owner_Crow:1375731826093461544>: {guild.owner.mention if guild.owner else 'Unknown'}\n"
            f"**Members:** {guild.member_count}\n"
            f"**Verification:** {str(guild.verification_level).title()}\n"
            f"**Boost Level:** <a:server_boostin:1375731855008989224> {guild.premium_tier} ({guild.premium_subscription_count} boosts)\n"
            f"**Vanity URL:** {guild.vanity_url_code if guild.vanity_url_code else 'None'}"
        )
        
        created_info = fmt_time(guild.created_at)
        
        # Features (truncated if too long)
        features_text = ', '.join(f'`{feature.replace("_", " ").title()}`' for feature in guild.features) if guild.features else 'None'
        if len(features_text) > 200:  # Keep features reasonable
            features_text = features_text[:200] + "..."

        description = guild.description or "None"
        if len(description) > 1020:
            description = description[:1017] + "..."

        # Build embed
        embed = discord.Embed(
            title=f"{guild.name} — Information",
            color=get_next_color(),
            timestamp=datetime.now(tz=zone)
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        # Add fields with truncation
        embed.add_field(name="__ABOUT__", value=truncate_field(about), inline=False)
        embed.add_field(name="__CREATED__", value=created_info, inline=False)
        embed.add_field(name="__DESCRIPTION__", value=description, inline=False)
        embed.add_field(name="__FEATURES__", value=features_text, inline=False)

        # Add channel counts
        channels = (
            f"**Categories:** {len(guild.categories)}\n"
            f"**Text:** {len(guild.text_channels)}\n"
            f"**Voice:** {len(guild.voice_channels)}\n"
            f"**Forums:** {len(guild.forums)}\n"
            f"**Total:** {len(guild.channels)}\n"
            f"**System:** {guild.system_channel.mention if guild.system_channel else 'None'}\n"
            f"**Rules:** {guild.rules_channel.mention if guild.rules_channel else 'None'}"
        )
        embed.add_field(name="__CHANNELS__", value=truncate_field(channels), inline=True)

        # Add role counts
        roles = (
            f"**Total:** {len(guild.roles)}\n"
            f"**Managed:** {len([r for r in guild.roles if r.managed])}\n"
            f"**Highest:** {guild.roles[-1].mention if len(guild.roles) > 1 else 'None'}\n"
            f"**Color:** {str(guild.roles[-1].color) if len(guild.roles) > 1 else 'None'}"
        )
        embed.add_field(name="__ROLES__", value=roles, inline=True)

        # Add emoji counts (fixed animated count)
        animated_count = len([e for e in guild.emojis if e.animated])
        regular_count = len(guild.emojis) - animated_count
        emojis = (
            f"**Regular:** {regular_count}\n"
            f"**Animated:** {animated_count}\n"
            f"**Total:** {len(guild.emojis)}\n"
            f"**Limit:** {guild.emoji_limit}\n"
            f"**Stickers:** {guild.sticker_limit}"
        )
        embed.add_field(name="__EMOJIS__", value=emojis, inline=True)

        # Add member stats (fixed status counting)
        online_members = len([m for m in guild.members if m.status == discord.Status.online])
        idle_members = len([m for m in guild.members if m.status == discord.Status.idle])
        dnd_members = len([m for m in guild.members if m.status == discord.Status.dnd])
        offline_members = len([m for m in guild.members if m.status == discord.Status.offline])
        bot_count = len([m for m in guild.members if m.bot])
        human_count = guild.member_count - bot_count

        members = (
            f"**Total:** {guild.member_count:,}\n"
            f"**Humans:** {human_count:,}\n"
            f"**Bots:** {bot_count:,}\n"
            f"**Online:** {online_members:,}\n"
            f"**Idle:** {idle_members:,}\n"
            f"**DND:** {dnd_members:,}\n"
            f"**Offline:** {offline_members:,}"
        )
        embed.add_field(name="__MEMBERS__", value=members, inline=True)

        # Add server limits
        limits = (
            f"**Upload:** {guild.filesize_limit/1024/1024:.1f}MB\n"
            f"**Bitrate:** {guild.bitrate_limit/1000:.0f}kbps\n"
            f"**Emoji Limit:** {guild.emoji_limit}\n"
            f"**Sticker Limit:** {guild.sticker_limit}"
        )
        embed.add_field(name="__LIMITS__", value=limits, inline=True)

        # Content filter info
        security = (
            f"**Verification:** {str(guild.verification_level).title()}\n"
            f"**Content Filter:** {str(guild.explicit_content_filter).title()}\n"
            f"**2FA Required:** {'Yes' if guild.mfa_level else 'No'}"
        )
        embed.add_field(name="__SECURITY__", value=security, inline=True)

        embed.set_footer(text=f"Requested by {ctx.author.display_name} • ID: {guild.id}")

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

        tz_name = _guild_timezones.get(ctx.guild.id, 'UTC')
        zone = ZoneInfo(tz_name)
        
        # Format creation time
        created_ts = int(role.created_at.timestamp())
        created_local = role.created_at.astimezone(zone).strftime('%d %b %Y • %I:%M %p')
        created_field = f"<t:{created_ts}:F>\n({created_local})"

        # Role permissions (truncated if too long)
        perms = [perm.replace('_', ' ').title() for perm, value in role.permissions if value]
        perms_text = ', '.join(perms) if perms else 'None'
        if len(perms_text) > 1000:
            perms_text = perms_text[:997] + "..."

        embed = discord.Embed(
            title=f"Role Information: {role.name}",
            color=role.color if role.color.value else get_next_color(),
            timestamp=datetime.now(tz=zone)
        )
        
        embed.add_field(name="**ID**", value=str(role.id), inline=True)
        embed.add_field(name="**Members**", value=str(len(role.members)), inline=True)
        embed.add_field(name="**Position**", value=str(role.position), inline=True)
        embed.add_field(name="**Color**", value=str(role.color), inline=True)
        embed.add_field(name="**Mentionable**", value="Yes" if role.mentionable else "No", inline=True)
        embed.add_field(name="**Hoisted**", value="Yes" if role.hoist else "No", inline=True)
        embed.add_field(name="**Created**", value=created_field, inline=False)
        embed.add_field(name="**Permissions**", value=perms_text, inline=False)

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
            description=f"**{total:,}** members",
            color=get_next_color(),
            timestamp=datetime.now(tz=zone)
        )
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ServerInfo(bot))
