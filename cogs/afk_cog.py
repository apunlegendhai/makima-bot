import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import aiosqlite
import asyncio

class AFK(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_file = 'afk.db'
        self.db_conn = None  # Persistent DB connection
        self._cache = {}  # Cache for AFK status
        self._afk_task = {}  # Cache for active AFK countdown tasks
        self.cache_expiry_duration = timedelta(hours=24)  # Cache expires after 24 hours

    async def init_db(self):
        """Initialize the AFK table in the database."""
        if self.db_conn is None:
            self.db_conn = await aiosqlite.connect(self.db_file)

        # Check and create table if necessary
        async with self.db_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='afk';") as cursor:
            result = await cursor.fetchone()
            if not result:
                await self.db_conn.execute('''CREATE TABLE IF NOT EXISTS afk (
                                                user_id INTEGER PRIMARY KEY,
                                                reason TEXT,
                                                timestamp TEXT
                                            )''')
                await self.db_conn.commit()
                print("AFK table created successfully.")
            else:
                print("AFK table already exists.")

    async def get_afk_status(self, user_id):
        """Get the AFK status from cache or database."""
        if user_id in self._cache:
            reason, timestamp = self._cache[user_id]
            # Check if the cache has expired
            if datetime.utcnow() - timestamp > self.cache_expiry_duration:
                # Remove expired cache entry
                del self._cache[user_id]
                return None
            return reason, timestamp

        try:
            async with self.db_conn.execute("SELECT reason, timestamp FROM afk WHERE user_id = ?", (user_id,)) as cursor:
                result = await cursor.fetchone()
            if result:
                reason, timestamp = result
                # Cache the result with the current time as datetime
                self._cache[user_id] = (reason, datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S'))
            return result
        except Exception as e:
            print(f"Error fetching AFK status for {user_id}: {e}")
            return None

    async def set_afk_status(self, user_id, reason):
        """Set the AFK status in the database and cache."""
        timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        try:
            async with self.db_conn.execute("REPLACE INTO afk (user_id, reason, timestamp) VALUES (?, ?, ?)", 
                                            (user_id, reason, timestamp)):
                await self.db_conn.commit()
            # Store current time as a datetime object in cache
            self._cache[user_id] = (reason, datetime.utcnow())  
        except Exception as e:
            print(f"Error setting AFK status for {user_id}: {e}")

    async def remove_afk_status(self, user_id):
        """Remove AFK status from the database and cache."""
        try:
            async with self.db_conn.execute("DELETE FROM afk WHERE user_id = ?", (user_id,)):
                await self.db_conn.commit()
            if user_id in self._cache:
                del self._cache[user_id]
        except Exception as e:
            print(f"Error removing AFK status for {user_id}: {e}")

    @commands.command()
    async def afk(self, ctx, *, reason: str = "AFK"):
        """Set yourself as AFK with an optional reason."""
        user_id = ctx.author.id
        await self.set_afk_status(user_id, reason)

        embed = discord.Embed(
            description=f"<:sukoon_info:1323251063910043659> | Successfully set your AFK status with reason: {reason}",
            color=0x2f3136
        )
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle incoming messages to check or clear AFK statuses."""
        if message.author.bot:
            return

        # Check if the message is a bot command
        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return  # Skip processing if it's a command

        # Check if the sender is AFK
        result = await self.get_afk_status(message.author.id)
        if result:
            reason, timestamp = result
            await self.remove_afk_status(message.author.id)

            time_diff = datetime.utcnow() - timestamp
            time_ago = self.format_time_ago(time_diff)

            embed = discord.Embed(
                description=f"<:sukoon_info:1323251063910043659> | Successfully removed your AFK. You were AFK for {time_ago}.",
                color=0x2f3136
            )
            await message.channel.send(embed=embed)

        # Notify if an AFK user is mentioned in the message
        elif message.mentions:
            for mention in message.mentions:
                result = await self.get_afk_status(mention.id)
                if result:
                    reason, timestamp = result
                    time_diff = datetime.utcnow() - timestamp
                    time_ago = self.format_time_ago(time_diff)

                    embed = discord.Embed(
                        description=f"<:sukoon_info:1323251063910043659> |{mention.mention} went afk {time_ago} with reason {reason}.",
                        color=0x2f3136
                    )
                    await message.channel.send(embed=embed)

            await self.bot.process_commands(message)

    @commands.command()
    async def afk_status(self, ctx, member: discord.Member = None):
        """Check the AFK status of yourself or another member."""
        member = member or ctx.author
        result = await self.get_afk_status(member.id)

        if result:
            reason, timestamp = result
            time_diff = datetime.utcnow() - timestamp
            time_ago = self.format_time_ago(time_diff)

            embed = discord.Embed(
                description=f"<:sukoon_info:1323251063910043659> {member.mention} is AFK. Reason: {reason}. AFK since: {time_ago}.",
                color=0x2f3136
            )
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                description=f"<a:sukoon_reddot:1322894157794119732> |{member.mention} is not AFK.",
                color=0x2f3136
            )
            await ctx.send(embed=embed)

    def format_time_ago(self, time_diff):
        """Format a timedelta object into a human-readable 'time ago' string."""
        seconds = time_diff.total_seconds()
        minutes = seconds // 60
        hours = minutes // 60
        days = hours // 24

        if days > 0:
            return f"{int(days)} days ago"
        elif hours > 0:
            return f"{int(hours)} hours ago"
        elif minutes > 0:
            return f"{int(minutes)} minutes ago"
        else:
            return f"{int(seconds)} seconds ago"

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Automatically remove AFK status if a user leaves the server."""
        await self.remove_afk_status(member.id)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Automatically remove AFK status when a user joins the server."""
        await self.remove_afk_status(member.id)

    async def check_afk_expiration(self):
        """Automatically remove AFK status for users who have been AFK too long."""
        async with self.db_conn.execute("SELECT user_id, timestamp FROM afk") as cursor:
            rows = await cursor.fetchall()  # Fetch all rows before iterating
            for user_id, timestamp in rows:
                timestamp_dt = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
                if datetime.utcnow() - timestamp_dt > timedelta(hours=72):  # Expire after 72 hours
                    await self.remove_afk_status(user_id)

    @tasks.loop(hours=1)  # Checks every hour
    async def afk_expiration_task(self):
        """Run the AFK expiration check periodically."""
        await self.check_afk_expiration()

    @commands.command()
    async def remove_afk(self, ctx, member: discord.Member):
        """Manually remove AFK status of a user."""
        await self.remove_afk_status(member.id)
        await ctx.send(f"{member.mention}'s AFK status has been removed.")

# Setup function to add the cog
async def setup(bot):
    cog = AFK(bot)
    await cog.init_db()
    cog.afk_expiration_task.start()  # Start the AFK expiration task
    await bot.add_cog(cog)
