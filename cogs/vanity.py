import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import os
import datetime
import asyncio
from typing import Optional, Dict
import logging
import random

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()])
logger = logging.getLogger('VanityRole')


def get_unique_color():
    """Generates a unique Discord.Color"""
    #Simple approach, can be improved for better uniqueness
    r = random.randint(0, 255)
    g = random.randint(0, 255)
    b = random.randint(0, 255)
    while (r, g, b) in get_unique_color.used_colors:
        r = random.randint(0, 255)
        g = random.randint(0, 255)
        b = random.randint(0, 255)
    get_unique_color.used_colors.add((r, g, b))
    return discord.Color.from_rgb(r, g, b)


get_unique_color.used_colors = set()


class VanityRole(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.vanity_config = {}
        self.cooldowns = {}
        self.rate_limit_cache: Dict[int, Dict[int, float]] = {
        }  # guild_id -> {user_id -> timestamp}
        self.guild_rate_limits: Dict[int, float] = {
        }  # guild_id -> last_processed_time
        self.processing_locks: Dict[int, asyncio.Lock] = {}  # guild_id -> lock
        self.debug_mode = False
        self.GUILD_COOLDOWN = 1.0  # 1 second cooldown per guild
        self.BULK_PROCESS_SIZE = 10  # Process users in batches of 10

        # Ensure database directory exists
        os.makedirs('database', exist_ok=True)

        # Connect to SQLite database
        self.conn = sqlite3.connect('database/vanity_config.db')
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()

        # Create table if it doesn't exist
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS vanity_config (
            guild_id INTEGER PRIMARY KEY,
            vanity_keyword TEXT,
            vanity_role TEXT,
            vanity_log_channel_id INTEGER,
            enabled BOOLEAN DEFAULT 1
        )
        ''')
        self.conn.commit()

        # Load config from database
        self._load_configs()

    def _load_configs(self):
        self.cursor.execute('SELECT * FROM vanity_config WHERE enabled = 1')
        rows = self.cursor.fetchall()

        for row in rows:
            guild_id = row['guild_id']
            self.vanity_config[guild_id] = {
                "keyword": row['vanity_keyword'],
                "role": int(row['vanity_role']),
                "log_channel_id": row['vanity_log_channel_id'],
                "enabled": bool(row['enabled'])
            }

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(
            f"VanityRole cog loaded. Monitoring {len(self.vanity_config)} guilds."
        )

    @app_commands.command(
        name="set-vanity-role",
        description=
        "Set up a role to be assigned based on a keyword in custom status")
    @app_commands.describe(
        keyword="The keyword to detect in user's custom status",
        role="The role to assign when the keyword is detected",
        log_channel="The channel where role assignments will be logged")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_vanity_role(self, interaction: discord.Interaction,
                              keyword: str, role: discord.Role,
                              log_channel: discord.TextChannel):
        guild_id = interaction.guild.id

        # Check bot's role hierarchy
        if role >= interaction.guild.me.top_role:
            try:
                await interaction.response.send_message(
                    "I cannot manage this role as it's higher than or equal to my highest role.",
                    ephemeral=True)
            except discord.errors.InteractionResponded:
                await interaction.followup.send(
                    "I cannot manage this role as it's higher than or equal to my highest role.",
                    ephemeral=True)
            return

        # Check bot permissions
        if not interaction.guild.me.guild_permissions.manage_roles:
            try:
                await interaction.response.send_message(
                    "I don't have the 'Manage Roles' permission required to assign roles.",
                    ephemeral=True)
            except discord.errors.InteractionResponded:
                await interaction.followup.send(
                    "I don't have the 'Manage Roles' permission required to assign roles.",
                    ephemeral=True)
            return

        try:
            # Use a transaction
            with self.conn:
                # Update database
                self.cursor.execute(
                    '''INSERT OR REPLACE INTO vanity_config 
                    (guild_id, vanity_keyword, vanity_role, vanity_log_channel_id, enabled) 
                    VALUES (?, ?, ?, ?, 1)''',
                    (guild_id, keyword, role.id, log_channel.id))

            # Update in-memory cache
            self.vanity_config[guild_id] = {
                "keyword": keyword,
                "role": role.id,
                "log_channel_id": log_channel.id,
                "enabled": True
            }

            success_message = f"Vanity role configuration set. Users with '{keyword}' in their custom status will receive the {role.mention} role."
            try:
                await interaction.response.send_message(success_message,
                                                        ephemeral=True)
            except discord.errors.InteractionResponded:
                await interaction.followup.send(success_message,
                                                ephemeral=True)

            # Send confirmation to log channel
            random_color = get_unique_color()
            embed = discord.Embed(
                title="Vanity Role Configuration Updated",
                description=f"Vanity role system has been configured.",
                color=random_color,
                timestamp=datetime.datetime.now())
            embed.add_field(name="Keyword", value=keyword, inline=True)
            embed.add_field(name="Role", value=role.mention, inline=True)
            embed.add_field(name="Configured by",
                            value=interaction.user.mention,
                            inline=False)
            await log_channel.send(embed=embed)

        except Exception as e:
            error_message = f"Error setting up vanity role: {str(e)}"
            logger.error(error_message)
            try:
                await interaction.response.send_message(error_message,
                                                        ephemeral=True)
            except discord.errors.InteractionResponded:
                await interaction.followup.send(error_message, ephemeral=True)

    @app_commands.command(
        name="disable-vanity-role",
        description="Disable the vanity role feature for this server")
    @app_commands.checks.has_permissions(administrator=True)
    async def disable_vanity_role(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id

        if guild_id not in self.vanity_config:
            await interaction.response.send_message(
                "Vanity role is not configured for this server.",
                ephemeral=True)
            return

        try:
            # Use a transaction
            with self.conn:
                self.cursor.execute(
                    "UPDATE vanity_config SET enabled = 0 WHERE guild_id = ?",
                    (guild_id, ))

            # Remove from in-memory cache
            if guild_id in self.vanity_config:
                config = self.vanity_config[guild_id]
                del self.vanity_config[guild_id]

                # Log to channel if available
                try:
                    log_channel = interaction.guild.get_channel(
                        config["log_channel_id"])
                    if log_channel:
                        random_color = get_unique_color()
                        embed = discord.Embed(
                            title="Vanity Role System Disabled",
                            description=
                            "The vanity role system has been disabled for this server.",
                            color=random_color,
                            timestamp=datetime.datetime.now())
                        embed.add_field(name="Disabled by",
                                        value=interaction.user.mention)
                        await log_channel.send(embed=embed)
                except Exception as e:
                    logger.error(f"Error sending disable log: {str(e)}")

            await interaction.response.send_message(
                "Vanity role system has been disabled.", ephemeral=True)

        except Exception as e:
            logger.error(f"Error disabling vanity role: {str(e)}")
            await interaction.response.send_message(
                f"Error disabling vanity role: {str(e)}", ephemeral=True)

    @app_commands.command(
        name="toggle-debug",
        description="Toggle debug mode for vanity role system")
    @app_commands.checks.has_permissions(administrator=True)
    async def toggle_debug(self, interaction: discord.Interaction):
        self.debug_mode = not self.debug_mode
        await interaction.response.send_message(
            f"Debug mode is now {'enabled' if self.debug_mode else 'disabled'}.",
            ephemeral=True)

    async def _rate_limited(self, guild_id, user_id):
        """Implements a sophisticated rate limiting system with guild-level throttling"""
        now = datetime.datetime.now().timestamp()

        # Check guild-level rate limit
        if guild_id in self.guild_rate_limits:
            if now - self.guild_rate_limits[guild_id] < self.GUILD_COOLDOWN:
                return True
        self.guild_rate_limits[guild_id] = now

        # Initialize guild entry if needed
        if guild_id not in self.rate_limit_cache:
            self.rate_limit_cache[guild_id] = {}

        # Check if user is rate-limited
        if user_id in self.rate_limit_cache[guild_id]:
            last_update = self.rate_limit_cache[guild_id][user_id]
            if now - last_update < 3:  # 3 second cooldown
                return True

        # Update timestamp
        self.rate_limit_cache[guild_id][user_id] = now

        # Clean up old entries (every 25 operations or so)
        if len(self.rate_limit_cache[guild_id]) > 50:
            # Remove entries older than 10 seconds
            cutoff = now - 10
            self.rate_limit_cache[guild_id] = {
                uid: timestamp
                for uid, timestamp in self.rate_limit_cache[guild_id].items()
                if timestamp > cutoff
            }

        return False

    @commands.Cog.listener()
    async def on_presence_update(self, before, after):
        if after.bot:  # Skip bots
            return

        guild_id = after.guild.id
        if guild_id not in self.vanity_config:
            return

        # Get or create guild-specific lock
        if guild_id not in self.processing_locks:
            self.processing_locks[guild_id] = asyncio.Lock()

        # Use guild-specific lock
        async with self.processing_locks[guild_id]:
            # Apply rate limiting
            if await self._rate_limited(guild_id, after.id):
                return

            try:
                await self._process_presence_update(before, after)
            except Exception as e:
                logger.error(f"Error in presence update handler: {str(e)}")
                # Try to log to the configured log channel
                try:
                    config = self.vanity_config.get(guild_id)
                    if config:
                        log_channel = after.guild.get_channel(
                            config["log_channel_id"])
                        if log_channel:
                            await log_channel.send(
                                f"Error processing presence update: {str(e)}")
                except Exception:
                    pass  # Silently fail if we can't log the error

    async def _process_presence_update(self, before, after):
        guild_id = after.guild.id
        config = self.vanity_config.get(guild_id)

        if not config:
            return

        # Verify config is still valid
        guild = after.guild
        role_id = config["role"]
        role = guild.get_role(role_id)

        if not role:
            logger.warning(
                f"Role with ID {role_id} no longer exists in guild {guild.name}"
            )
            return

        # Verify bot permissions
        if not guild.me.guild_permissions.manage_roles:
            logger.warning(
                f"Missing 'Manage Roles' permission in guild {guild.name}")
            return

        # Verify bot's role hierarchy
        if role >= guild.me.top_role:
            logger.warning(
                f"Cannot manage role {role.name} in guild {guild.name} due to role hierarchy"
            )
            return

        keyword = config["keyword"]
        log_channel_id = config["log_channel_id"]

        # Check if user status is invisible
        is_invisible = after.status == discord.Status.invisible

        # Get current custom status
        current_status = self._get_custom_status(after)

        # Check if status contains keyword
        has_keyword = current_status and keyword.lower(
        ) in current_status.lower()
        has_role = role in after.roles

        if self.debug_mode:
            logger.info(
                f"User: {after.name}, Status: {after.status}, Custom status: '{current_status}', Has keyword: {has_keyword}, Has role: {has_role}"
            )

        # Handle role assignment or removal
        try:
            if has_keyword and not has_role and not is_invisible:
                # Only add role if keyword is present AND user is not invisible
                await after.add_roles(
                    role, reason=f"Custom status contains keyword: {keyword}")
                await self._send_log(guild, log_channel_id, after, role,
                                     "added", current_status)
                if self.debug_mode:
                    logger.info(f"Added role {role.name} to {after.name}")
            elif not has_keyword and has_role:
                # Remove role if keyword is not present
                await after.remove_roles(
                    role, reason=f"Custom status no longer contains keyword")
                await self._send_log(guild, log_channel_id, after, role,
                                     "removed", current_status)
                if self.debug_mode:
                    logger.info(
                        f"Removed role {role.name} from {after.name} (no keyword)"
                    )
            elif is_invisible and has_role:
                # Remove role if user is invisible
                await after.remove_roles(role, reason=f"User is invisible")
                await self._send_log(guild, log_channel_id, after, role,
                                     "removed", "user is invisible")
                if self.debug_mode:
                    logger.info(
                        f"Removed role {role.name} from {after.name} (invisible)"
                    )
        except discord.Forbidden:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel:
                await log_channel.send(
                    f"⚠️ Failed to manage role for {after.mention}: Missing permissions"
                )
            logger.error(
                f"Missing permissions to manage roles for user {after.name} in guild {guild.name}"
            )
        except Exception as e:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel:
                await log_channel.send(
                    f"⚠️ Error managing role for {after.mention}: {str(e)}")
            logger.error(f"Error managing role: {str(e)}")

    def _get_custom_status(self, member) -> Optional[str]:
        """Get the user's custom status text"""
        if not member or not member.activities:
            return None

        for activity in member.activities:
            if isinstance(activity, discord.CustomActivity):
                # Check both state and name attributes
                status_text = activity.state or activity.name
                return status_text

        # If no custom activity is found
        return None

    async def _send_log(self,
                        guild,
                        log_channel_id,
                        member,
                        role,
                        action,
                        status_text=None):
        log_channel = guild.get_channel(log_channel_id)
        if not log_channel:
            return

        random_color = get_unique_color()

        # Create title based on action
        title = "<a:003_bel:1341822673247797319> Vanity Added" if action == "added" else "<a:sukoon_yflower:1323990499660664883> Vanity Removed"

        # Create description based on action
        if action == "added":
            description = f"{member.mention} has been honored with {role.mention} • for displaying our signature vanity `{status_text}`in their status — keep shining!"
        else:
            description = f"{member.mention} has stepped back from {role.mention} • as the vanity is no longer in their status. We appreciate your past support!"

        embed = discord.Embed(title=title,
                              description=description,
                              color=random_color,
                              timestamp=datetime.datetime.now())

        # Set author's avatar as thumbnail
        embed.set_thumbnail(url=member.display_avatar.url)

        # Removed footer text since Discord already shows timestamp automatically

        await log_channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        guild_id = guild.id
        if guild_id in self.vanity_config:
            # Remove from memory
            del self.vanity_config[guild_id]

            # Remove from database
            try:
                with self.conn:
                    self.cursor.execute(
                        'DELETE FROM vanity_config WHERE guild_id = ?',
                        (guild_id, ))
            except Exception as e:
                logger.error(f"Error removing guild from database: {str(e)}")

    def cog_unload(self):
        self.conn.close()


async def setup(bot):
    await bot.add_cog(VanityRole(bot))
