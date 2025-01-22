import discord
import random
import sqlite3
from datetime import datetime
from discord.ext import commands, tasks
import logging
from logging.handlers import RotatingFileHandler
import asyncio
from typing import Optional, List, Tuple
import pytz

# Constants
LOG_FILE = 'giveaway_logs.log'
DATABASE_FILE = 'giveaways.db'
REACTION_EMOJI = "<:sukoon_taaada:1324071825910792223>"  # Standard party emoji
DOT_EMOJI = "<:sukoon_blackdot:1322894649488314378>"
RED_DOT_EMOJI = "<:sukoon_redpoint:1322894737736339459>"
EMBED_COLOR = 0x2f3136
CLEANUP_INTERVAL = 60  # Interval to check for ended giveaways

# Configure logging
logger = logging.getLogger('GiveawayBot')
handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.WARNING)  # Set to WARNING to reduce logs

class DatabaseManager:
    """Manages database interactions with asyncio support."""

    def __init__(self):
        self.lock = asyncio.Lock()

    async def execute(self, query: str, params: tuple = (), fetch: bool = False) -> Optional[List[Tuple]]:
        """Executes SQL queries with thread safety."""
        async with self.lock:
            try:
                conn = sqlite3.connect(DATABASE_FILE)
                cur = conn.cursor()
                cur.execute(query, params)
                result = cur.fetchall() if fetch else None
                conn.commit()
                conn.close()
                return result
            except Exception as e:
                logger.error(f"Database error: {e}")
                if 'conn' in locals():
                    conn.rollback()
                    conn.close()
                return [] if fetch else None

    async def init(self):
        """Initializes the required database tables."""
        # Drop and recreate giveaways table
        await self.execute('''
            DROP TABLE IF EXISTS giveaways
        ''')
        await self.execute('''
            CREATE TABLE IF NOT EXISTS giveaways (
                message_id TEXT PRIMARY KEY, 
                channel_id INTEGER, 
                end_time INTEGER, 
                winners INTEGER, 
                prize TEXT, 
                status TEXT, 
                host_id INTEGER,
                created_at INTEGER
            )
        ''')
        # Drop and recreate participants table
        await self.execute('''
            DROP TABLE IF EXISTS participants
        ''')
        await self.execute('''
            CREATE TABLE IF NOT EXISTS participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT, 
                user_id INTEGER,
                UNIQUE(message_id, user_id)
            )
        ''')
        # Log the table schema for debugging
        schema = await self.execute("PRAGMA table_info(giveaways)", fetch=True)
        logger.warning(f"Giveaways table schema: {schema}")


class Giveaway(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = DatabaseManager()
        self._checking = False
        self._ready = asyncio.Event()
        self.check_giveaways.start()

    async def cog_load(self):
        await self.db.init()
        self._ready.set()

    def cog_unload(self):
        self.check_giveaways.cancel()

    async def check_bot_permissions(self, channel):
        """Checks if the bot has required permissions in the given channel."""
        perms = channel.permissions_for(channel.guild.me)
        required = {'send_messages', 'embed_links', 'add_reactions', 'read_message_history'}
        return all(getattr(perms, perm, False) for perm in required)

    @discord.app_commands.command(name="giveaway", description="Start a new giveaway")
    @discord.app_commands.guild_only()
    @discord.app_commands.default_permissions(administrator=True)
    async def start_giveaway(self, interaction: discord.Interaction, duration: str, winners: int, prize: str):
        """Starts a new giveaway."""
        try:
            await interaction.response.defer(ephemeral=True)

            if not await self.check_bot_permissions(interaction.channel):
                await interaction.followup.send("I need proper permissions to start a giveaway.", ephemeral=True)
                return

            if not 1 <= winners <= 20:
                raise ValueError("Winners must be between 1 and 20.")

            # Parse duration
            time_units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
            unit = duration[-1].lower()
            if unit not in time_units or not duration[:-1].isdigit():
                raise ValueError("Use number + s/m/h/d (e.g., 1h, 2d).")
            duration_seconds = int(duration[:-1]) * time_units[unit]
            if not 30 <= duration_seconds <= 2592000:
                raise ValueError("Duration must be between 30 seconds and 30 days.")

            end_timestamp = int(datetime.utcnow().timestamp() + duration_seconds)

            # Send the giveaway title before the embed
            await interaction.channel.send("**🎉 GIVEAWAY 🎉**")

            # Format the end time for the embed footer
            end_time = datetime.utcfromtimestamp(end_timestamp).replace(tzinfo=pytz.utc)
            local_time = end_time.astimezone(pytz.timezone("Asia/Kolkata"))  # Replace with your timezone if needed
            formatted_time = local_time.strftime("%A at %I:%M %p") if local_time.date() > datetime.utcnow().date() else local_time.strftime("Today at %I:%M %p")

            # Create and send the giveaway embed
            embed = discord.Embed(
                description=f"{DOT_EMOJI} Ends: <t:{end_timestamp}:R>\n"
                            f"{DOT_EMOJI} Hosted by: {interaction.user.mention}",
                color=EMBED_COLOR
            )
            embed.set_author(name=prize, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
            embed.set_footer(text=f"Ends at • {formatted_time}")

            message = await interaction.channel.send(embed=embed)
            await message.add_reaction(REACTION_EMOJI)

            # Store giveaway in the database
            await self.db.execute(
                "INSERT INTO giveaways VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(message.id),                     # message_id
                    interaction.channel.id,              # channel_id
                    end_timestamp,                       # end_time
                    winners,                             # winners
                    prize,                               # prize
                    "active",                            # status
                    interaction.user.id,                 # host_id
                    int(datetime.utcnow().timestamp())   # created_at
                )
            )

            await interaction.followup.send("Giveaway started successfully!", ephemeral=True)

        except ValueError as e:
            await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)
        except Exception as e:
            logger.error(f"Error starting giveaway: {e}")
            await interaction.followup.send("An unexpected error occurred.", ephemeral=True)

    async def end_giveaway(self, message_id: str):
        """Ends an active giveaway and announces winners."""
        try:
            giveaway = await self.db.execute(
                "SELECT * FROM giveaways WHERE message_id=? AND status='active'",
                (message_id,), True
            )
            if not giveaway:
                return

            giveaway = giveaway[0]
            channel = self.bot.get_channel(giveaway[1])
            if not channel:
                return

            try:
                message = await channel.fetch_message(int(message_id))
            except discord.NotFound:
                await self.db.execute("UPDATE giveaways SET status='ended' WHERE message_id=?", (message_id,))
                return

            participants = await self.db.execute(
                "SELECT DISTINCT user_id FROM participants WHERE message_id=?",
                (message_id,), True
            )
            valid_participants = [p[0] for p in participants if p[0] != self.bot.user.id]

            winners = random.sample(valid_participants, min(len(valid_participants), giveaway[3])) if valid_participants else []
            winner_mentions = [f"<@{w}>" for w in winners] if winners else ["No winners (no participants)."]

            # Format the end time for the footer
            end_time = datetime.utcnow().replace(tzinfo=pytz.utc)
            local_time = end_time.astimezone(pytz.timezone("Asia/Kolkata"))  # Replace with your timezone if needed
            formatted_time = local_time.strftime("%m/%d/%y, %I:%M %p")

            # Update the embed with the results
            embed = discord.Embed(
                description=f"{DOT_EMOJI} Ended: <t:{int(datetime.utcnow().timestamp())}:R>\n"
                            f"{RED_DOT_EMOJI} Winners: {', '.join(winner_mentions)}\n"
                            f"{DOT_EMOJI} Hosted by: <@{giveaway[6]}>",
                color=EMBED_COLOR
            )
            embed.set_author(name=giveaway[4], icon_url=channel.guild.icon.url if channel.guild.icon else None)
            embed.set_footer(text=f"Ended at • {formatted_time}")
            await message.edit(embed=embed)

            if winners:
                await message.reply(f"<:sukoon_taaada:1324071825910792223> Congratulations {', '.join(winner_mentions)}! "
                                    f"You won **{giveaway[4]}**!")

            await self.db.execute("UPDATE giveaways SET status='ended' WHERE message_id=?", (message_id,))

        except Exception as e:
            logger.error(f"Error ending giveaway {message_id}: {e}")

    @tasks.loop(seconds=CLEANUP_INTERVAL)
    async def check_giveaways(self):
        """Checks for giveaways that need to be ended."""
        await self._ready.wait()
        if self._checking:
            return

        try:
            self._checking = True
            current_time = int(datetime.utcnow().timestamp())
            active_giveaways = await self.db.execute(
                "SELECT message_id FROM giveaways WHERE end_time <= ? AND status='active'",
                (current_time,), True
            )

            for giveaway in active_giveaways:
                await self.end_giveaway(giveaway[0])

        finally:
            self._checking = False

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Handles reaction adds for giveaway participation."""
        if str(payload.emoji) != REACTION_EMOJI or payload.user_id == self.bot.user.id:
            return

        await self.db.execute(
            "INSERT OR IGNORE INTO participants (message_id, user_id) VALUES (?, ?)",
            (str(payload.message_id), payload.user_id)
        )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """Handles reaction removal for giveaway participation."""
        if str(payload.emoji) != REACTION_EMOJI or payload.user_id == self.bot.user.id:
            return

        await self.db.execute(
            "DELETE FROM participants WHERE message_id=? AND user_id=?",
            (str(payload.message_id), payload.user_id)
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaway(bot))
