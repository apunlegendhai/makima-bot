import discord
import logging
import asyncio
import os
import aiosqlite
from typing import Optional
from discord.ext import commands, tasks
from discord import app_commands

# Configure logging
logger = logging.getLogger('activity-role-bot')
logging.getLogger('aiosqlite').setLevel(logging.ERROR)

class ActivityRole(commands.Cog):
    """
    Cog for managing Spotify and Crunchyroll activity roles in Discord servers.
    Automatically assigns and removes roles when users start/stop an activity.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # role_cache structure: { guild_id: { "Spotify": role_name, "Crunchyroll": role_name } }
        self.role_cache = {}
        self.role_cache_lock = asyncio.Lock()
        self.db_path = 'database/activity_config.db'
        self.db = None
        self.bot.loop.create_task(self.setup_database())
        self.cache_refresh.start()

    async def setup_database(self):
        """Set up the SQLite database"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        try:
            self.db = await aiosqlite.connect(self.db_path, timeout=5.0)
            await self.db.execute("PRAGMA foreign_keys = ON")
            # Create table with default roles if not set
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS guild_config (
                    guild_id INTEGER PRIMARY KEY,
                    spotify_role TEXT DEFAULT 'Spotify',
                    crunchyroll_role TEXT DEFAULT 'Crunchyroll'
                )
            """)
            await self.db.commit()
            await self.load_config()
            logger.info("Database setup complete")
        except Exception as e:
            logger.error(f"Error setting up database: {e}")
            self.bot.loop.create_task(self.reconnect_database())

    async def reconnect_database(self):
        """Attempt to reconnect to the database after failure"""
        try:
            logger.info("Attempting to reconnect to database in 5 seconds...")
            await asyncio.sleep(5)
            await self.setup_database()
        except Exception as e:
            logger.error(f"Failed to reconnect to database: {e}")

    async def load_config(self):
        """Load guild-specific configurations from database"""
        if not self.db:
            logger.error("Database connection not established")
            return

        try:
            async with self.role_cache_lock:
                self.role_cache.clear()
                async with self.db.execute("SELECT guild_id, spotify_role, crunchyroll_role FROM guild_config") as cursor:
                    async for row in cursor:
                        guild_id, spotify_role, crunchyroll_role = row
                        self.role_cache[guild_id] = {
                            "Spotify": spotify_role,
                            "Crunchyroll": crunchyroll_role
                        }
                logger.info(f"Loaded configuration for {len(self.role_cache)} guilds")
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")

    async def save_guild_config(self, guild_id: int):
        """Save guild-specific configuration to database"""
        if not self.db:
            logger.error("Database connection not established")
            return

        try:
            roles = self.role_cache.get(guild_id, {"Spotify": "Spotify", "Crunchyroll": "Crunchyroll"})
            await self.db.execute("""
                INSERT INTO guild_config (guild_id, spotify_role, crunchyroll_role)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    spotify_role = ?,
                    crunchyroll_role = ?
            """, (guild_id, roles["Spotify"], roles["Crunchyroll"], roles["Spotify"], roles["Crunchyroll"]))
            await self.db.commit()
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")

    async def get_role_name(self, guild_id: int, activity_type: str) -> str:
        """Get the configured role name for a given activity (Spotify or Crunchyroll)"""
        async with self.role_cache_lock:
            return self.role_cache.get(guild_id, {}).get(activity_type, activity_type)

    async def set_role_name(self, guild_id: int, activity_type: str, role_name: str):
        """Set the role name for a given activity"""
        async with self.role_cache_lock:
            if guild_id not in self.role_cache:
                self.role_cache[guild_id] = {}
            self.role_cache[guild_id][activity_type] = role_name
            await self.save_guild_config(guild_id)

    async def get_activity_role(self, guild: discord.Guild, activity_type: str) -> Optional[discord.Role]:
        """Get the role object for a given activity type"""
        role_name = await self.get_role_name(guild.id, activity_type)
        return discord.utils.get(guild.roles, name=role_name)

    @tasks.loop(hours=1)
    async def cache_refresh(self):
        """Periodically refresh the role cache"""
        for guild in self.bot.guilds:
            try:
                await self.get_activity_role(guild, "Spotify")
                await self.get_activity_role(guild, "Crunchyroll")
            except Exception as e:
                logger.error(f"Error refreshing roles in {guild.name}: {e}")

    @cache_refresh.before_loop
    async def before_cache_refresh(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """When the bot joins a guild, load configuration."""
        logger.info(f"Joined guild {guild.name} (ID: {guild.id})")

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """When the bot is removed from a guild, clear configuration."""
        logger.info(f"Left guild {guild.name} (ID: {guild.id})")
        async with self.role_cache_lock:
            if guild.id in self.role_cache:
                del self.role_cache[guild.id]
            try:
                await self.db.execute("DELETE FROM guild_config WHERE guild_id = ?", (guild.id,))
                await self.db.commit()
            except Exception as e:
                logger.error(f"Error deleting configuration: {e}")

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        """Detect when a user starts or stops an activity (Spotify or Crunchyroll)"""
        if after.bot:
            return

        guild = after.guild
        if not guild.me.guild_permissions.manage_roles:
            return

        try:
            spotify_role = await self.get_activity_role(guild, "Spotify")
            crunchyroll_role = await self.get_activity_role(guild, "Crunchyroll")

            # Check for Spotify activity using discord.Spotify
            is_listening_spotify = any(isinstance(a, discord.Spotify) for a in after.activities)

            # Check for Crunchyroll activity (adjust detection logic as needed)
            is_watching_crunchyroll = any(a.name and "Crunchyroll" in a.name for a in after.activities)

            # Manage Spotify role
            if is_listening_spotify and spotify_role and spotify_role not in after.roles:
                if spotify_role.position < guild.me.top_role.position:
                    await after.add_roles(spotify_role, reason="Started listening to Spotify")
            elif not is_listening_spotify and spotify_role and spotify_role in after.roles:
                if spotify_role.position < guild.me.top_role.position:
                    await after.remove_roles(spotify_role, reason="Stopped listening to Spotify")

            # Manage Crunchyroll role
            if is_watching_crunchyroll and crunchyroll_role and crunchyroll_role not in after.roles:
                if crunchyroll_role.position < guild.me.top_role.position:
                    await after.add_roles(crunchyroll_role, reason="Started watching Crunchyroll")
            elif not is_watching_crunchyroll and crunchyroll_role and crunchyroll_role in after.roles:
                if crunchyroll_role.position < guild.me.top_role.position:
                    await after.remove_roles(crunchyroll_role, reason="Stopped watching Crunchyroll")

        except discord.Forbidden:
            logger.warning(f"Missing permissions to manage roles in {guild.name}")
        except Exception as e:
            logger.error(f"Error in on_presence_update: {e}")

    config = app_commands.Group(name="config-activities", description="Configure activity tracking settings")

    @config.command(name="role", description="Set roles for Spotify and Crunchyroll activities")
    @app_commands.describe(activity="Select an activity", role="The role to assign")
    @app_commands.choices(activity=[
        app_commands.Choice(name="Spotify", value="Spotify"),
        app_commands.Choice(name="Crunchyroll", value="Crunchyroll")
    ])
    async def set_role_cmd(self, interaction: discord.Interaction, activity: app_commands.Choice[str], role: discord.Role):
        """Set the role for a specific activity."""
        guild = interaction.guild
        if role.position >= guild.me.top_role.position:
            await interaction.response.send_message(
                f"I cannot manage the {role.mention} role because it's higher than my highest role.",
                ephemeral=True
            )
            return

        await self.set_role_name(guild.id, activity.value, role.name)
        logger.info(f"{activity.value} role set to {role.name} in {guild.name} by {interaction.user.name}")
        await interaction.response.send_message(
            f"Role for **{activity.value}** has been set to {role.mention}.",
            ephemeral=True
        )

    async def cog_unload(self):
        """Cancel background tasks and close database connection on unload."""
        self.cache_refresh.cancel()
        if self.db:
            await self.db.close()
            logger.info("Database connection closed")

async def setup(bot: commands.Bot):
    """Setup function for the cog"""
    await bot.add_cog(ActivityRole(bot))
