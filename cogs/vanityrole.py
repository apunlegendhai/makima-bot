import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
import os
import aiosqlite
import asyncio
from datetime import datetime
from collections import defaultdict
import aiohttp

# Directory constants
DATABASE_DIR = "database"
LOG_DIR = "logs"

# Ensure required directories exist
os.makedirs(DATABASE_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Setup cog-specific logger
logger = logging.getLogger('vanityrole')
if not logger.handlers:
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(
        filename=os.path.join(LOG_DIR, 'vanityrole.log'),
        encoding='utf-8',
        mode='a'
    )
    handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
    logger.addHandler(handler)


class StatusManager(commands.Cog):
    """
    A cog for managing vanity roles based on user status.
    Required intents: members, presences
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = os.path.join(DATABASE_DIR, "vanityroles.db")
        self.rate_limits = defaultdict(dict)  # {guild_id: {member_id: last_action_timestamp}}
        self.last_check = {}  # {guild_id: last_check_timestamp}
        self.member_role_states = {}  # {f"{guild_id}:{member_id}": bool}
        self.session: aiohttp.ClientSession | None = None

        if not (self.bot.intents.members and self.bot.intents.presences):
            logger.error("Missing required intents: members and presences must be enabled")
            raise RuntimeError("StatusManager requires members and presences intents")

        # Setup the database asynchronously
        asyncio.create_task(self.setup_database())
        logger.info("StatusManager cog initialized")

    async def cog_load(self):
        try:
            self.session = aiohttp.ClientSession()
            self.start_checker.start()  # Start the status-check loop
            logger.info("Cog loaded successfully and status checker started")
        except Exception as e:
            logger.error(f"Failed to initialize cog: {e}")
            raise

    async def cog_unload(self):
        try:
            self.start_checker.cancel()  # Stop the loop
            if self.session and not self.session.closed:
                await self.session.close()
            # Clear caches
            self.rate_limits.clear()
            self.last_check.clear()
            self.member_role_states.clear()
            logger.info("StatusManager cog unloaded successfully")
        except Exception as e:
            logger.error(f"Error during cog unload: {e}")

    @tasks.loop(seconds=30)
    async def start_checker(self):
        if not self.bot.is_ready():
            return

        try:
            logger.info("Starting status check cycle")
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM guild_configs") as cursor:
                    configs = await cursor.fetchall()

                if not configs:
                    logger.debug("No guild configurations found")
                    return

                current_time = datetime.utcnow().timestamp()
                for config in configs:
                    guild_id = config["guild_id"]
                    # Skip if we've checked this guild less than 30 seconds ago
                    if current_time - self.last_check.get(guild_id, 0) < 30:
                        continue

                    logger.debug(f"Processing guild {guild_id}")
                    await self.check_guild_statuses(config)
                    self.last_check[guild_id] = current_time

            logger.info("Status check cycle completed")
        except Exception as e:
            logger.error(f"Status checker error: {e}", exc_info=True)
            await self.send_error_webhook(f"Status checker error: {str(e)}")
            await asyncio.sleep(60)

    @start_checker.before_loop
    async def before_checker(self):
        await self.bot.wait_until_ready()
        logger.info("Status checker ready to start")

    async def check_guild_statuses(self, config):
        guild_id = config["guild_id"]
        guild = self.bot.get_guild(guild_id)

        if not guild:
            logger.warning(f"Could not find guild {guild_id}")
            return

        role = guild.get_role(config["role_id"])
        if not role:
            logger.warning(f"Could not find role {config['role_id']} in guild {guild_id}")
            return

        if not await self.verify_permissions(guild, role):
            return

        current_time = datetime.utcnow().timestamp()
        for member in guild.members:
            if member.bot:
                continue

            # Check if the member is manageable by the bot by comparing role positions.
            if member.top_role >= guild.me.top_role:
                logger.warning(
                    f"Skipping member {member.name} because their highest role "
                    f"({member.top_role.name}) is equal or higher than the bot's highest role "
                    f"({guild.me.top_role.name})."
                )
                continue

            member_key = f"{guild_id}:{member.id}"
            current_has_role = role in member.roles

            # Rate-limit role changes to one per minute per member.
            if (member.id in self.rate_limits.get(guild_id, {}) and
                    current_time - self.rate_limits[guild_id].get(member.id, 0) < 60):
                continue

            try:
                # Check for a custom status in the member's activities.
                status_text = ""
                for activity in member.activities:
                    if isinstance(activity, discord.CustomActivity) and activity.name:
                        status_text = activity.name
                        break

                # Determine if the status starts with the configured status word.
                expected_prefix = f"/{config['status_word'].lower()}"
                should_have_role = bool(status_text and status_text.lower().startswith(expected_prefix))

                if should_have_role != current_has_role:
                    try:
                        if should_have_role:
                            await member.add_roles(role, reason="Status match detected")
                            await self.log_role_change(guild_id, config["log_channel_id"], member, role, "added")
                        else:
                            await member.remove_roles(role, reason="Status no longer matches")
                            await self.log_role_change(guild_id, config["log_channel_id"], member, role, "removed")

                        self.rate_limits[guild_id][member.id] = current_time
                        self.member_role_states[member_key] = should_have_role

                    except discord.Forbidden:
                        logger.error(f"Missing permissions to update role for {member.name}")
                    except discord.HTTPException as e:
                        logger.error(f"HTTP error updating role for {member.name}: {e}")
                    except Exception as e:
                        logger.error(f"Failed to update role for member {member.name}: {e}")
            except Exception as e:
                logger.error(f"Error processing member {member.name}: {e}")

            await asyncio.sleep(0.1)

    async def verify_permissions(self, guild: discord.Guild, role: discord.Role) -> bool:
        if not guild.me.guild_permissions.manage_roles:
            logger.error(f"Missing Manage Roles permission in guild {guild.name}")
            await self.send_error_webhook(f"Missing Manage Roles permission in guild {guild.name}")
            return False

        if role >= guild.me.top_role:
            logger.error(f"Cannot manage role {role.name} due to hierarchy in {guild.name}")
            await self.send_error_webhook(f"Role hierarchy issue in guild {guild.name}")
            return False

        return True

    async def log_role_change(self, guild_id: int, channel_id: int, user: discord.Member, role: discord.Role, action: str):
        try:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                logger.error(f"Could not find log channel {channel_id}")
                await self.send_error_webhook(f"Log channel not found in guild {guild_id}")
                return

            if not channel.permissions_for(channel.guild.me).send_messages:
                logger.error(f"Missing permissions to send messages in channel {channel_id}")
                await self.send_error_webhook(f"Missing send message permissions in log channel for guild {guild_id}")
                return

            if action == "added":
                title = "Vanity Added"
                description = (
                    f"<a:sukoon_white_bo:1335856241011855430> {user.mention}! You've been granted the {role.mention} role. "
                    "Enjoy your time with us and make the most out of your stay!"
                )
            else:
                title = "Vanity Removed"
                description = (
                    f"<a:sukoon_yflower:1323990499660664883> {user.mention} Your {role.mention} role has been removed. "
                    "We'll miss you in that role, but we hope to see you back soon!"
                )

            embed = discord.Embed(
                title=title,
                description=description,
                color=discord.Color.from_str('#2f3136'),
                timestamp=datetime.utcnow()
            )
            embed.set_thumbnail(url=user.display_avatar.url)

            guild_obj = self.bot.get_guild(guild_id)
            if guild_obj:
                guild_icon = guild_obj.icon.url if guild_obj.icon else None
                embed.set_footer(
                    text=guild_obj.name,
                    icon_url=guild_icon or self.bot.user.display_avatar.url
                )

            await channel.send(embed=embed)
            logger.info(f"Role change logged for user {user.id} in guild {guild_id}")

        except Exception as e:
            logger.error(f"Failed to log role change: {e}")
            await self.send_error_webhook(f"Failed to log role change in guild {guild_id}: {str(e)}")

    @app_commands.command(name="vanity-setup")
    @app_commands.default_permissions(administrator=True)
    async def setup(
        self,
        interaction: discord.Interaction,
        status_word: str,
        role: discord.Role,
        log_channel: discord.TextChannel
    ):
        """
        Set up the vanity role monitoring configuration for this server.
        """
        try:
            await interaction.response.defer(ephemeral=True)

            if not interaction.guild.me.guild_permissions.manage_roles:
                embed = discord.Embed(
                    title="❌ Permission Required",
                    description="I don't have permission to manage roles!",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Missing Permissions", icon_url=self.bot.user.display_avatar.url)
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            if role >= interaction.guild.me.top_role:
                embed = discord.Embed(
                    title="⚠️ Role Hierarchy Issue",
                    description="I cannot manage this role as it's higher than my highest role!",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Role Hierarchy Error", icon_url=self.bot.user.display_avatar.url)
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            if not log_channel.permissions_for(interaction.guild.me).send_messages:
                embed = discord.Embed(
                    title="❌ Channel Access Required",
                    description=f"I don't have permission to send messages in {log_channel.mention}!",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Missing Permissions", icon_url=self.bot.user.display_avatar.url)
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO guild_configs 
                    (guild_id, guild_name, status_word, role_id, log_channel_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        interaction.guild.id,
                        interaction.guild.name,
                        status_word,
                        role.id,
                        log_channel.id
                    )
                )
                await db.commit()

            embed = discord.Embed(
                title="✨ Setup Complete!",
                description=(
                    f"Configuration saved successfully, {interaction.user.mention}!\n"
                    f"• Status word: `{status_word}`\n"
                    f"• Role: {role.mention}\n"
                    f"• Log channel: {log_channel.mention}\n\n"
                    "The bot will now automatically manage roles based on user statuses."
                ),
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else self.bot.user.display_avatar.url)
            guild_icon = interaction.guild.icon.url if interaction.guild.icon else self.bot.user.display_avatar.url
            embed.set_footer(
                text=interaction.guild.name,
                icon_url=guild_icon
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"Configuration updated for guild: {interaction.guild.name}")

        except Exception as e:
            logger.error(f"Setup command error: {e}", exc_info=True)
            embed = discord.Embed(
                title="❌ Setup Failed",
                description="An error occurred while saving your configuration.",
                color=discord.Color.red()
            )
            embed.set_footer(text="Error • Setup Failed", icon_url=self.bot.user.display_avatar.url)
            await interaction.followup.send(embed=embed, ephemeral=True)
            await self.send_error_webhook(f"Setup failed in guild {interaction.guild.id}: {str(e)}")

    async def setup_database(self):
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS guild_configs (
                        guild_id INTEGER PRIMARY KEY,
                        guild_name TEXT,
                        status_word TEXT,
                        role_id INTEGER,
                        log_channel_id INTEGER
                    )
                ''')
                await db.commit()
                logger.info("Database setup completed successfully")

                async with db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='guild_configs'") as cursor:
                    if await cursor.fetchone() is None:
                        raise Exception("Failed to create guild_configs table")
        except Exception as e:
            logger.error(f"Database setup failed: {e}")
            raise

    async def send_error_webhook(self, error_message: str):
        try:
            webhook_url = os.getenv('WEBHOOK_URL')
            if not webhook_url:
                logger.error("Webhook URL not configured")
                return

            if not self.session or self.session.closed:
                self.session = aiohttp.ClientSession()

            async with self.session.post(webhook_url, json={'content': error_message}) as resp:
                if resp.status != 204:
                    text = await resp.text()
                    logger.error(f"Failed to send webhook: {text}")
                else:
                    logger.info("Error webhook sent successfully")
        except Exception as e:
            logger.error(f"Failed to send error report: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(StatusManager(bot))
    logger.info("StatusManager cog loaded")
