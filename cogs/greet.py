import os
import discord
import logging
import random
from discord.ext import commands
from discord import app_commands
from typing import Optional, List
from datetime import datetime, timedelta
import aiosqlite
import asyncio

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GreetingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.default_greetings = [
            "<a:sukoon_Watermelon:1325703602379161676> Welcome, {user}, to {server}! A new journey begins—make yourself at home.",
            "<a:sukoon_white_bo:1335856241011855430> Hey {user}, you’ve arrived at {server}! Breathe in the good vibes and enjoy your stay.",
            "<a:sukoon_bandaid:1323990361647087729> {user}, welcome to {server}! A space to connect, share, and grow together.",
            "<:sukoon_btfl:1335856043477041204> Welcome aboard, {user}! {server} is a place of warmth and camaraderie—glad you’re here.",
            "<a:sukoon_butterfly:1323990263609298967> Hi {user}, you’ve found your way to {server}. Let’s create wonderful memories together!",
            "<a:sukoon_:1335855101897609226> {user}, welcome to {server}! You bring new energy to our growing community of {member_count}.",
            "<a:sukoon_rabbi:1335855768301473812> Glad to have you here, {user}! {server} just got brighter with your presence.",
            "<a:sukoon_yflower:1323990499660664883> A warm welcome to you, {user}! {server} is now {member_count} strong—let’s make it unforgettable.",
            "<:sukoon_starr:1335855541335097408> Hello {user}, welcome to {server}! Let the conversations flow and friendships grow.",
            "<a:heartspar:1335854160322498653> {user}, you’re now part of {server}! Settle in, unwind, and enjoy the journey."
        ]


        self.db = None
        self.lock = asyncio.Lock()  # For database transaction safety

    async def setup_database(self):
        """Initialize database with proper error handling and retries"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                os.makedirs('database', exist_ok=True)
                db_path = os.path.join('database', 'greetings.db')
                self.db = await aiosqlite.connect(db_path)

                # Enable WAL mode for better concurrency
                await self.db.execute('PRAGMA journal_mode=WAL')

                await self.db.execute('''CREATE TABLE IF NOT EXISTS greeting_channels (
                    guild_id INTEGER,
                    channel_id INTEGER,
                    greeting_message TEXT,
                    error_count INTEGER DEFAULT 0,
                    last_error TEXT,
                    cooldown_time INTEGER DEFAULT 60,
                    enabled BOOLEAN DEFAULT TRUE,
                    PRIMARY KEY (guild_id, channel_id)
                )''')

                await self.db.execute('''CREATE TABLE IF NOT EXISTS greeting_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    user_id INTEGER,
                    channel_id INTEGER,
                    message TEXT,
                    timestamp TEXT,
                    success BOOLEAN,
                    error_message TEXT
                )''')

                # Create indexes
                await self.db.execute('''CREATE INDEX IF NOT EXISTS idx_history_guild ON greeting_history(guild_id)''')
                await self.db.execute('''CREATE INDEX IF NOT EXISTS idx_history_user ON greeting_history(user_id)''')
                await self.db.execute('''CREATE INDEX IF NOT EXISTS idx_history_timestamp ON greeting_history(timestamp)''')
                await self.db.commit()
                return
            except aiosqlite.Error as e:
                logger.error(f"Database setup failed (attempt {attempt+1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(1)

    async def cog_load(self):
        await self.setup_database()

    async def cog_unload(self):
        if self.db:
            await self.db.close()

    @app_commands.command(name="greet_enable")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        channel="The channel where greetings will be sent",
        custom_message="Optional custom greeting message",
        cooldown="Cooldown in seconds between greetings (default: 60)"
    )
    async def greet_enable(
        self, 
        interaction: discord.Interaction, 
        channel: discord.TextChannel,
        custom_message: Optional[str] = None,
        cooldown: Optional[int] = 60
    ):
        """Enable greetings in a channel with optional custom message and cooldown"""
        async with self.lock:
            try:
                # Verify bot permissions in the channel
                permissions = channel.permissions_for(interaction.guild.me)
                if not permissions.send_messages:
                    await interaction.response.send_message(
                        f"<:sukoon_info:1323251063910043659> | I don't have permission to send messages in {channel.mention}",
                        ephemeral=True
                    )
                    return

                # Check existing channels
                async with self.db.execute(
                    "SELECT COUNT(*) FROM greeting_channels WHERE guild_id = ?",
                    (interaction.guild.id,)
                ) as cursor:
                    count = (await cursor.fetchone())[0]
                    if count >= 5:
                        await interaction.response.send_message(
                            "<:sukoon_info:1323251063910043659> | Maximum limit of 5 greeting channels reached",
                            ephemeral=True
                        )
                        return

                greeting = custom_message if custom_message else None
                await self.db.execute(
                    """INSERT INTO greeting_channels 
                    (guild_id, channel_id, greeting_message, cooldown_time)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, channel_id) DO UPDATE SET
                    greeting_message = excluded.greeting_message,
                    cooldown_time = excluded.cooldown_time,
                    enabled = TRUE""",
                    (interaction.guild.id, channel.id, greeting, cooldown)
                )
                await self.db.commit()

                response_message = (
                    f"<a:sukoon_whitetick:1323992464058482729> | Greetings enabled in {channel.mention}\n"
                    f"Cooldown: {cooldown} seconds\n"
                )
                if greeting:
                    response_message += f"Custom message: {greeting}"
                else:
                    response_message += "Using randomized default greetings"

                await interaction.response.send_message(
                    response_message,
                    ephemeral=True
                )

            except Exception as e:
                logger.error(f"Error enabling greeting: {e}", exc_info=True)
                await interaction.response.send_message(
                    "<:sukoon_info:1323251063910043659> | An error occurred while enabling greetings",
                    ephemeral=True
                )

    @app_commands.command(name="greet_disable")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="The channel to disable greetings in")
    async def greet_disable(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Disable greetings in a specific channel"""
        async with self.lock:
            try:
                result = await self.db.execute(
                    "UPDATE greeting_channels SET enabled = FALSE WHERE guild_id = ? AND channel_id = ?",
                    (interaction.guild.id, channel.id)
                )
                if result.rowcount == 0:
                    await interaction.response.send_message(
                        f"<:sukoon_info:1323251063910043659> | Greetings not enabled in {channel.mention}",
                        ephemeral=True
                    )
                    return
                await self.db.commit()
                await interaction.response.send_message(
                    f"<a:sukoon_whitetick:1323992464058482729> | Greetings disabled in {channel.mention}",
                    ephemeral=True
                )
            except Exception as e:
                logger.error(f"Error disabling greeting: {e}", exc_info=True)
                await interaction.response.send_message(
                    "<:sukoon_info:1323251063910043659> | An error occurred while disabling greetings",
                    ephemeral=True
                )

    @app_commands.command(name="greet_list")
    @app_commands.checks.has_permissions(administrator=True)
    async def greet_list(self, interaction: discord.Interaction):
        """List all configured greeting channels"""
        try:
            async with self.db.execute(
                """SELECT channel_id, greeting_message, cooldown_time, error_count 
                FROM greeting_channels 
                WHERE guild_id = ? AND enabled = TRUE""",
                (interaction.guild.id,)
            ) as cursor:
                channels = await cursor.fetchall()

            if not channels:
                await interaction.response.send_message(
                    "No active greeting channels configured",
                    ephemeral=True
                )
                return

            response = ["**Configured Greeting Channels:**"]
            for channel_id, message, cooldown, errors in channels:
                channel = interaction.guild.get_channel(channel_id)
                name = channel.mention if channel else f"Deleted Channel ({channel_id})"
                message_text = message if message is not None else "Random default greetings"
                truncated = message_text[:50] + '...' if len(message_text) > 50 else message_text
                response.append(
                    f"{name}\n"
                    f"Message: {truncated}\n"
                    f"Cooldown: {cooldown}s | Errors: {errors}"
                )

            await interaction.response.send_message(
                "\n\n".join(response),
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error listing greetings: {e}", exc_info=True)
            await interaction.response.send_message(
                "<:sukoon_info:1323251063910043659> | Failed to retrieve greeting channels",
                ephemeral=True
            )

    async def _check_cooldown(self, guild_id: int, user_id: int, channel_id: int) -> bool:
        """Check if a user is in cooldown for a specific channel"""
        async with self.db.execute(
            """SELECT cooldown_time FROM greeting_channels 
            WHERE guild_id = ? AND channel_id = ?""",
            (guild_id, channel_id)
        ) as cursor:
            result = await cursor.fetchone()
            if not result:
                return False
            cooldown_time = result[0]

        async with self.db.execute(
            """SELECT timestamp FROM greeting_history 
            WHERE guild_id = ? AND user_id = ? AND channel_id = ? 
            ORDER BY timestamp DESC LIMIT 1""",
            (guild_id, user_id, channel_id)
        ) as cursor:
            last_greeting = await cursor.fetchone()
            if not last_greeting:
                return True  # No previous greeting

        last_time = datetime.fromisoformat(last_greeting[0])
        return (datetime.utcnow() - last_time).total_seconds() >= cooldown_time

    async def _send_greeting(
        self,
        guild: discord.Guild,
        member: discord.Member,
        test_mode: bool = False
    ):
        """Handle greeting logic with proper error handling and cooldown checks"""
        if member.bot and not test_mode:
            return

        async with self.lock:
            try:
                async with self.db.execute(
                    """SELECT channel_id, greeting_message 
                    FROM greeting_channels 
                    WHERE guild_id = ? AND enabled = TRUE""",
                    (guild.id,)
                ) as cursor:
                    channels = await cursor.fetchall()

                if not channels:
                    return

                for channel_id, greeting_message in channels:
                    channel = guild.get_channel(channel_id)
                    if not channel:
                        # Clean up deleted channels
                        await self.db.execute(
                            "DELETE FROM greeting_channels WHERE guild_id = ? AND channel_id = ?",
                            (guild.id, channel_id)
                        )
                        await self.db.commit()
                        continue

                    # Check cooldown from database
                    if not await self._check_cooldown(guild.id, member.id, channel_id):
                        continue

                    # Format message
                    if greeting_message is None:
                        greeting_message = random.choice(self.default_greetings)
                    message = greeting_message.format(
                        user=member.mention,
                        server=guild.name,
                        member_count=guild.member_count
                    )

                    # Send message with permission check
                    try:
                        if not channel.permissions_for(guild.me).send_messages:
                            raise discord.Forbidden(f"No permissions in {channel.name}")

                        # Send the greeting message and schedule deletion
                        sent_message = await channel.send(message)
                        await sent_message.delete(delay=60)  # Delete after 60 seconds
                        success = True
                        error_msg = None
                    except discord.HTTPException as e:
                        success = False
                        error_msg = str(e)
                        logger.error(f"Error sending greeting in {channel.name}: {error_msg}")
                    except Exception as e:
                        success = False
                        error_msg = str(e)
                        logger.error(f"Unexpected error sending greeting: {e}")

                    # Log to history
                    timestamp = datetime.utcnow().isoformat()
                    await self.db.execute(
                        """INSERT INTO greeting_history 
                        (guild_id, user_id, channel_id, message, timestamp, success, error_message)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (guild.id, member.id, channel_id, message, timestamp, success, error_msg)
                    )

                    # Update error count
                    update_query = """UPDATE greeting_channels 
                                    SET error_count = error_count + ?
                                    WHERE guild_id = ? AND channel_id = ?"""
                    await self.db.execute(
                        update_query,
                        (0 if success else 1, guild.id, channel_id)
                    )
                    await self.db.commit()

            except Exception as e:
                logger.error(f"Error in greeting handler: {e}", exc_info=True)

    @app_commands.command(name="test_greet")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="Optional channel to test in", user="Optional user to test as")
    async def test_greet(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        user: Optional[discord.Member] = None
    ):
        """Test the greeting system in a specific channel"""
        target_channel = channel or interaction.channel
        test_user = user or interaction.user

        if not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message("<:sukoon_info:1323251063910043659> | Invalid channel type", ephemeral=True)
            return

        await self._send_greeting(interaction.guild, test_user, test_mode=True)
        await interaction.response.send_message(
            f"<a:sukoon_whitetick:1323992464058482729> | Test greeting sent to {target_channel.mention}",
            ephemeral=True
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle member join events"""
        await self._send_greeting(member.guild, member)

async def setup(bot: commands.Bot):
    await bot.add_cog(GreetingCog(bot))
