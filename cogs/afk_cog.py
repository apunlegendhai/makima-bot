import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
from dotenv import load_dotenv
import os
import asyncio
from typing import Optional, Dict, Any, List, Union
import logging
import random

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables (make sure MONGO_URL is set)
load_dotenv()

class DatabaseError(Exception):
    """Custom exception for database-related errors."""
    pass

class MentionPaginator(discord.ui.View):
    """
    A paginator that shows one mention per page, with:
      • Title: "Mentions from {mentioner_name}"
      • Description: Contains:
            - A clickable "[Jump to Message](...)" link,
            - "When: {relative_time}" computed from the mention timestamp,
            - "Message: {msg_content}" from the stored message.
      • Thumbnail: The mentioner’s avatar (if available)
      • embed.timestamp is set dynamically so Discord displays current time in the corner.
      • Footer: Displays the bot’s name.
      • Uses a random non-repeating color from a 30-color pool for each page.
    """
    def __init__(self, mentions: List[Dict[str, Any]], author: discord.Member, bot: commands.Bot):
        super().__init__(timeout=180)  # 3-minute timeout
        self.bot = bot
        self.author = author
        self.mentions = mentions
        self.current_page = 0
        self.total_pages = len(mentions)
        self.message: Optional[discord.Message] = None

        # Predefined pool of 30 unique colors (as hex ints)
        color_pool = [
            0xFF5733, 0x33FF57, 0x3357FF, 0xFF33A1, 0xA133FF, 0x33FFA1, 0xA1FF33,
            0x5733FF, 0xFF8C33, 0x8C33FF, 0x33FF8C, 0xFFC733, 0x33C7FF, 0xC733FF,
            0xFF3366, 0x66FF33, 0x3366FF, 0xFFCC33, 0xCC33FF, 0x33FFCC, 0xFF3399,
            0x99FF33, 0x3399FF, 0xFF6633, 0x6633FF, 0x33FF66, 0xFF9933, 0x9933FF,
            0x66CCFF, 0xCCFF66
        ]

        if self.total_pages <= len(color_pool):
            self.colors = random.sample(color_pool, self.total_pages)
        else:
            self.colors = random.sample(color_pool, len(color_pool))
            extra_needed = self.total_pages - len(color_pool)
            self.colors += random.choices(color_pool, k=extra_needed)

        # Immediately set button states before sending the initial message
        self._update_buttons()

    @staticmethod
    def format_time_ago(diff: timedelta) -> str:
        """Return a human-readable relative time (e.g. '5m ago') given a timedelta."""
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m ago"
        hours, minutes = divmod(minutes, 60)
        if hours < 24:
            return f"{hours}h {minutes}m ago"
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h ago"

    async def start(self, ctx: Union[discord.abc.Messageable, discord.Interaction]) -> None:
        """Send the initial embed and attach this view."""
        try:
            initial_embed = self.get_page_content()
            if isinstance(ctx, discord.Interaction):
                await ctx.response.send_message(
                    content=f"{self.author.mention}, here’s your AFK mention summary:",
                    embed=initial_embed,
                    view=self
                )
                self.message = await ctx.original_response()
            else:
                self.message = await ctx.send(
                    content=f"{self.author.mention}, here’s your AFK mention summary:",
                    embed=initial_embed,
                    view=self
                )
        except Exception as e:
            logger.error(f"Error starting paginator: {e}")
            raise

    def get_page_content(self) -> discord.Embed:
        """Build the embed for the current mention."""
        mention = self.mentions[self.current_page]

        guild_id = mention["guild_id"]
        channel_id = mention["channel_id"]
        message_id = mention["message_id"]
        mention_time: datetime = mention["created_at"]  # Now a datetime object
        mentioner_id = mention["mentioned_by"]

        # Construct jump-to-message URL
        jump_url = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"

        # Compute relative time based on the mention's timestamp
        time_diff = datetime.now(timezone.utc) - mention_time
        relative_time = self.format_time_ago(time_diff)

        # Fetch the mentioner (fallback to raw mention if not found)
        mentioner = self.bot.get_user(mentioner_id)
        mentioner_name = mentioner.display_name if mentioner else f"<@{mentioner_id}>"

        # Retrieve message content (if available)
        msg_content = mention.get("message_content", "No content provided.")

        # Select the unique color for this page
        embed_color = self.colors[self.current_page]

        # Build the embed description
        description = f"[Jump to Message]({jump_url})\nWhen: {relative_time}\nMessage: {msg_content}"
        embed = discord.Embed(
            title=f"Mentions from {mentioner_name}",
            description=description,
            color=embed_color
        )

        # Set embed.timestamp to current time for dynamic display
        embed.timestamp = datetime.now(timezone.utc)

        # Footer: show only bot’s name
        if self.bot.user and self.bot.user.avatar:
            embed.set_footer(text=f"{self.bot.user.name}", icon_url=self.bot.user.avatar.url)
        else:
            embed.set_footer(text=f"{self.bot.user.name}")

        # Thumbnail: mentioner’s avatar (if available)
        if mentioner and mentioner.avatar:
            embed.set_thumbnail(url=mentioner.avatar.url)

        return embed

    @discord.ui.button(emoji="<:sukoon_left_arro:1345075074012676219>",
                       style=discord.ButtonStyle.secondary,
                       disabled=True)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Show the previous mention."""
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("You cannot use these controls.", ephemeral=True)

        self.current_page = max(0, self.current_page - 1)
        await self._update_buttons()
        await interaction.response.edit_message(embed=self.get_page_content(), view=self)

    @discord.ui.button(emoji="<:sukoon_right_arro:1345075121039216693>",
                       style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Show the next mention."""
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("You cannot use these controls.", ephemeral=True)

        self.current_page = min(self.total_pages - 1, self.current_page + 1)
        await self._update_buttons()
        await interaction.response.edit_message(embed=self.get_page_content(), view=self)

    async def _update_buttons(self):
        """Enable or disable navigation buttons based on the current page."""
        self.children[0].disabled = (self.current_page == 0)
        self.children[1].disabled = (self.current_page == (self.total_pages - 1))

    async def on_timeout(self):
        """Disable all buttons when the view times out."""
        for child in self.children:
            child.disabled = True
        try:
            if self.message and self.message.embeds:
                embed = self.message.embeds[0]
                embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} • Navigation timed out")
                await self.message.edit(embed=embed, view=self)
        except Exception as e:
            logger.error(f"Error on timeout: {e}")

class AFKChoiceView(discord.ui.View):
    """
    A view to let the user choose between a Global or Server Only AFK mode.
    The initial message is sent in an embed and after a choice is made, the buttons are removed.
    Additionally, the original message is deleted and a success embed is sent.
    """
    def __init__(self, afk_reason: str, afk_cog: "AFK", author: discord.Member):
        super().__init__(timeout=60)
        self.afk_reason = afk_reason
        self.afk_cog = afk_cog
        self.author = author
        self.message: Optional[discord.Message] = None

    async def on_timeout(self):
        """When the view times out, remove the buttons."""
        if self.message:
            await self.message.edit(view=None)

    @discord.ui.button(label="Global", style=discord.ButtonStyle.secondary)
    async def global_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("This is not for you.", ephemeral=True)
            return

        success = await self.afk_cog.set_afk_status(
            interaction.user.id,
            self.afk_reason,
            scope="global",
            server_id=None
        )
        if success:
            embed = discord.Embed(
                description=f"<a:sukoon_whitetick:1344600976962748458> | Successfully set your AFK status for reason: {self.afk_reason}",
                color=random.randint(0, 0xFFFFFF)
            )
            await interaction.response.send_message(embed=embed)
            await interaction.message.delete()
        else:
            await interaction.response.send_message("Failed to set AFK status.", ephemeral=True)

        self.stop()

    @discord.ui.button(label="Server Only", style=discord.ButtonStyle.secondary)
    async def server_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("This is not for you.", ephemeral=True)
            return

        server_id = interaction.guild.id if interaction.guild else None
        if server_id is None:
            await interaction.response.send_message("Server information is not available.", ephemeral=True)
            return

        success = await self.afk_cog.set_afk_status(
            interaction.user.id,
            self.afk_reason,
            scope="server",
            server_id=server_id
        )
        if success:
            embed = discord.Embed(
                description=f"<a:sukoon_whitetick:1344600976962748458> | Successfully set your AFK status for reason: {self.afk_reason}",
                color=random.randint(0, 0xFFFFFF)
            )
            await interaction.response.send_message(embed=embed)
            await interaction.message.delete()
        else:
            await interaction.response.send_message("Failed to set AFK status.", ephemeral=True)

        self.stop()

class AFK(commands.Cog):
    """AFK cog that handles AFK status, recording mentions, nickname changes, and sending a mention summary upon return."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.mongo_uri: str = os.getenv("MONGO_URL", "")
        self.database_name: str = "discord_bot"
        self.collection_name: str = "afk"
        self.mentions_collection_name: str = "afk_mentions"
        self.db_client: Optional[AsyncIOMotorClient] = None
        self.db: Optional[AsyncIOMotorDatabase] = None
        self.afk_collection: Optional[AsyncIOMotorCollection] = None
        self.mentions_collection: Optional[AsyncIOMotorCollection] = None

        # Cache: user_id -> dict with keys: reason, timestamp, scope, server_id, old_nick
        self._cache: Dict[int, Dict[str, Any]] = {}
        self.cache_expiry_duration: timedelta = timedelta(hours=1)
        self.connection_retry_delay: int = 5
        self.max_reason_length: int = 100
        self.mention_retention_days: int = 7
        self.tasks_started = False

    async def init_db(self) -> None:
        """Initialize MongoDB connection with retry logic."""
        retries = 3
        for attempt in range(retries):
            try:
                if not self.mongo_uri:
                    raise DatabaseError("MongoDB URI not found in environment variables")

                self.db_client = AsyncIOMotorClient(
                    self.mongo_uri,
                    serverSelectionTimeoutMS=5000,
                    connectTimeoutMS=5000,
                    retryWrites=True
                )
                await self.db_client.server_info()

                self.db = self.db_client[self.database_name]
                self.afk_collection = self.db[self.collection_name]
                self.mentions_collection = self.db[self.mentions_collection_name]

                await self.afk_collection.create_index("user_id", unique=True)
                await self.mentions_collection.create_index([("user_id", 1), ("created_at", 1)])

                logger.info("MongoDB connection established successfully")
                return
            except Exception as e:
                logger.error(f"Database connection attempt {attempt + 1} failed: {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(self.connection_retry_delay)
                else:
                    raise DatabaseError(f"Failed to connect to MongoDB after {retries} attempts")

    def start_tasks(self) -> None:
        """Start background tasks once the database is initialized."""
        if not self.tasks_started:
            self.clean_cache.start()
            self.cleanup_mentions.start()
            self.load_nicknames_cache.start()
            self.tasks_started = True
            logger.info("AFK background tasks started.")

    @tasks.loop(minutes=30)
    async def clean_cache(self):
        """Clean expired entries from the AFK cache."""
        try:
            now = datetime.now(timezone.utc)
            expired_keys = [
                key for key, record in self._cache.items()
                if now - record["timestamp"] > self.cache_expiry_duration
            ]
            for key in expired_keys:
                del self._cache[key]
            if expired_keys:
                logger.info(f"Cleaned {len(expired_keys)} expired cache entries")
        except Exception as e:
            logger.error(f"Error cleaning cache: {e}")

    @tasks.loop(hours=12)
    async def cleanup_mentions(self):
        """Delete old mention records from the database."""
        try:
            if self.mentions_collection is None:
                logger.warning("Mentions collection not initialized; skipping cleanup")
                return
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.mention_retention_days)
            result = await self.mentions_collection.delete_many({
                "created_at": {"$lt": cutoff}
            })
            if result.deleted_count:
                logger.info(f"Cleaned up {result.deleted_count} old mention records")
        except Exception as e:
            logger.error(f"Error in cleanup_mentions: {e}")

    @tasks.loop(hours=1)
    async def load_nicknames_cache(self):
        """
        Every hour, load all AFK records from the database into cache and reapply “[AFK]” nicknames.
        Ensures that if the bot restarts while users remain AFK, their nickname stays prefixed.
        """
        if self.afk_collection is None:
            return

        try:
            cursor = self.afk_collection.find({})
            async for doc in cursor:
                user_id = doc["user_id"]
                scope = doc.get("scope", "global")
                server_id = doc.get("server_id")
                reason = doc.get("reason", "")
                timestamp: datetime = doc["timestamp"]
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                old_nick = doc.get("old_nick")

                self._cache[user_id] = {
                    "reason": reason,
                    "timestamp": timestamp,
                    "scope": scope,
                    "server_id": server_id,
                    "old_nick": old_nick
                }

                if scope == "server" and server_id:
                    guild = self.bot.get_guild(server_id)
                    if guild:
                        member = guild.get_member(user_id)
                        if member:
                            display_str = member.display_name
                            truncated = display_str if len(display_str) <= 25 else display_str[:25] + "…"
                            new_nick = f"[AFK] {truncated}"
                            try:
                                await member.edit(nick=new_nick)
                            except discord.Forbidden:
                                logger.warning(f"Missing permissions to reapply AFK nickname for {member} in guild {guild.id}")
                            except Exception as e:
                                logger.error(f"Error reapplying AFK nickname for {member.id} in guild {guild.id}: {e}")
                else:
                    for guild in self.bot.guilds:
                        member = guild.get_member(user_id)
                        if member:
                            display_str = member.display_name
                            truncated = display_str if len(display_str) <= 25 else display_str[:25] + "…"
                            new_nick = f"[AFK] {truncated}"
                            try:
                                await member.edit(nick=new_nick)
                            except discord.Forbidden:
                                continue
                            except Exception as e:
                                logger.error(f"Error reapplying AFK nickname for {member.id} in guild {guild.id}: {e}")
            logger.info("Reapplied AFK nicknames for existing AFK users on schedule.")
        except Exception as e:
            logger.error(f"Error loading nicknames cache: {e}")

    async def _reapply_nicknames_once(self):
        """
        One-time pass to load all AFK records from the database into cache and reapply “[AFK]” nicknames.
        Called once at startup before tasks begin.
        """
        if self.afk_collection is None:
            return

        try:
            cursor = self.afk_collection.find({})
            async for doc in cursor:
                user_id = doc["user_id"]
                scope = doc.get("scope", "global")
                server_id = doc.get("server_id")
                reason = doc.get("reason", "")
                timestamp: datetime = doc["timestamp"]
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                old_nick = doc.get("old_nick")

                self._cache[user_id] = {
                    "reason": reason,
                    "timestamp": timestamp,
                    "scope": scope,
                    "server_id": server_id,
                    "old_nick": old_nick
                }

                if scope == "server" and server_id:
                    guild = self.bot.get_guild(server_id)
                    if guild:
                        member = guild.get_member(user_id)
                        if member:
                            display_str = member.display_name
                            truncated = display_str if len(display_str) <= 25 else display_str[:25] + "…"
                            new_nick = f"[AFK] {truncated}"
                            try:
                                await member.edit(nick=new_nick)
                            except discord.Forbidden:
                                logger.warning(f"Missing permissions to reapply AFK nickname for {member} in guild {guild.id}")
                            except Exception as e:
                                logger.error(f"Error reapplying AFK nickname for {member.id} in guild {guild.id}: {e}")
                else:
                    for guild in self.bot.guilds:
                        member = guild.get_member(user_id)
                        if member:
                            display_str = member.display_name
                            truncated = display_str if len(display_str) <= 25 else display_str[:25] + "…"
                            new_nick = f"[AFK] {truncated}"
                            try:
                                await member.edit(nick=new_nick)
                            except discord.Forbidden:
                                continue
                            except Exception as e:
                                logger.error(f"Error reapplying AFK nickname for {member.id} in guild {guild.id}: {e}")
            logger.info("Reapplied AFK nicknames for existing users (one-time startup pass).")
        except Exception as e:
            logger.error(f"Error in one-time nickname reappliance: {e}")

    async def get_afk_status(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a user's AFK status, checking cache first."""
        try:
            if user_id in self._cache:
                record = self._cache[user_id]
                if datetime.now(timezone.utc) - record["timestamp"] <= self.cache_expiry_duration:
                    return record
                del self._cache[user_id]

            result = await self.afk_collection.find_one({"user_id": user_id})
            if not result:
                return None

            reason = result["reason"]
            timestamp: datetime = result["timestamp"]
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            old_nick = result.get("old_nick")
            record = {
                "reason": reason,
                "timestamp": timestamp,
                "scope": result.get("scope", "global"),
                "server_id": result.get("server_id"),
                "old_nick": old_nick
            }
            self._cache[user_id] = record
            return record
        except Exception as e:
            logger.error(f"Error fetching AFK status for {user_id}: {e}")
            return None

    async def set_afk_status(
        self,
        user_id: int,
        reason: str,
        scope: str = "global",
        server_id: Optional[int] = None
    ) -> bool:
        """
        Set or update a user's AFK status with a scope (global or server).
        Also: record their old nickname and change it to "[AFK] <display_name>".
        """
        try:
            reason = discord.utils.escape_markdown(reason.strip())[:self.max_reason_length]
            now = datetime.now(timezone.utc)

            old_nick: Optional[str] = None

            if scope == "server":
                guild = self.bot.get_guild(server_id) if server_id else None
                if guild:
                    member = guild.get_member(user_id)
                    if member:
                        old_nick = member.nick
                        display_str = member.display_name
                        truncated = display_str if len(display_str) <= 25 else display_str[:25] + "…"
                        new_nick = f"[AFK] {truncated}"
                        try:
                            await member.edit(nick=new_nick)
                        except discord.Forbidden:
                            logger.warning(f"Missing permissions to set AFK nickname for {member} in guild {guild.id}")
                        except Exception as e:
                            logger.error(f"Error setting AFK nickname for {member.id} in guild {guild.id}: {e}")
            else:  # global
                for guild in self.bot.guilds:
                    member = guild.get_member(user_id)
                    if member:
                        if old_nick is None:
                            old_nick = member.nick
                        display_str = member.display_name
                        truncated = display_str if len(display_str) <= 25 else display_str[:25] + "…"
                        new_nick = f"[AFK] {truncated}"
                        try:
                            await member.edit(nick=new_nick)
                        except discord.Forbidden:
                            continue
                        except Exception as e:
                            logger.error(f"Error setting AFK nickname for {member.id} in guild {guild.id}: {e}")

            data = {
                "user_id": user_id,
                "reason": reason,
                "timestamp": now,
                "updated_at": now,
                "scope": scope,
                "server_id": server_id if scope == "server" else None,
                "old_nick": old_nick
            }

            await self.afk_collection.update_one(
                {"user_id": user_id},
                {"$set": data},
                upsert=True
            )

            self._cache[user_id] = {
                "reason": reason,
                "timestamp": now,
                "scope": scope,
                "server_id": server_id if scope == "server" else None,
                "old_nick": old_nick
            }
            return True
        except Exception as e:
            logger.error(f"Error setting AFK status for {user_id}: {e}")
            return False

    async def remove_afk_status(self, user_id: int) -> bool:
        """Remove a user's AFK status."""
        try:
            result = await self.afk_collection.delete_one({"user_id": user_id})
            self._cache.pop(user_id, None)
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error removing AFK status for {user_id}: {e}")
            return False

    @commands.command()
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        """
        Command to set your AFK status with an optional reason.
        Instead of immediately setting the status, this command now prompts you to choose
        whether you want the AFK to be global or server only.
        """
        try:
            embed = discord.Embed(
                description="<a:000_b:1379359306130133003> Please choose your AFK scope:",
                color=random.randint(0, 0xFFFFFF)
            )
            view = AFKChoiceView(reason, self, ctx.author)
            msg = await ctx.send(embed=embed, view=view)
            view.message = msg
        except Exception as e:
            logger.error(f"Error in afk command: {e}")
            await ctx.send("An error occurred while setting your AFK status. Please try again later.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Check messages for mentions of AFK users, handle AFK returns, and allow commands to run."""
        if message.author.bot:
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            await self.bot.process_commands(message)
            return

        try:
            if message.mentions:
                await self._handle_mentions(message)
            await self._handle_afk_return(message)
        except Exception as e:
            logger.error(f"Error in on_message: {e}")

    async def _handle_mentions(self, message: discord.Message):
        """Record a mention if the mentioned user is AFK."""
        for mention in message.mentions:
            if mention.bot:
                continue
            afk_status = await self.get_afk_status(mention.id)
            if not afk_status:
                continue

            if afk_status["scope"] == "server":
                if not message.guild or message.guild.id != afk_status["server_id"]:
                    continue

            reason = afk_status["reason"]
            afk_timestamp = afk_status["timestamp"]
            await self._record_mention(mention, message)
            rel_time = self.clean_time_format(afk_timestamp)
            embed = discord.Embed(
                description=f"{mention.mention} is AFK: {reason} ({rel_time})",
                color=random.randint(0, 0xFFFFFF)
            )
            await message.channel.send(embed=embed)

    def clean_time_format(self, timestamp: datetime) -> str:
        """Return a human-friendly relative time string for a given timestamp."""
        diff = datetime.now(timezone.utc) - timestamp
        return MentionPaginator.format_time_ago(diff)

    async def _handle_afk_return(self, message: discord.Message):
        """
        If a user who is AFK sends a message, revert their nickname, remove their AFK status,
        and send mention summary.
        """
        afk_status = await self.get_afk_status(message.author.id)
        if not afk_status:
            return

        if afk_status["scope"] == "server":
            if not message.guild or message.guild.id != afk_status["server_id"]:
                return

        old_nick = afk_status.get("old_nick")
        scope = afk_status["scope"]
        server_id = afk_status.get("server_id")
        await self._revert_nickname(message.author.id, old_nick, scope, server_id)

        await self._send_return_message(message, afk_status["timestamp"])
        await self._send_mention_summary(
            message,
            afk_status["timestamp"],
            afk_status["scope"],
            afk_status.get("server_id")
        )

        await self.remove_afk_status(message.author.id)

    async def _record_mention(self, mentioned_user: discord.Member, message: discord.Message):
        """Record a mention of an AFK user in the database."""
        try:
            data = {
                "user_id": mentioned_user.id,
                "message_id": message.id,
                "channel_id": message.channel.id,
                "guild_id": message.guild.id,
                "mentioned_by": message.author.id,
                "created_at": datetime.now(timezone.utc),
                "message_content": message.content[:200]
            }
            await self.mentions_collection.insert_one(data)
        except Exception as e:
            logger.error(f"Error recording mention for {mentioned_user.id}: {e}")

    async def _send_return_message(self, message: discord.Message, afk_timestamp: datetime):
        """Notify the user that they are no longer AFK."""
        rel_time = self.clean_time_format(afk_timestamp)
        embed = discord.Embed(
            description=f"{message.author.mention} is no longer AFK. They were AFK since {rel_time}.",
            color=random.randint(0, 0xFFFFFF)
        )
        await message.channel.send(embed=embed)

    async def _send_mention_summary(
        self,
        message: discord.Message,
        afk_start_time: datetime,
        scope: str,
        server_id: Optional[int] = None
    ):
        """Send a paginated summary of mentions received while the user was AFK."""
        try:
            if self.mentions_collection is None:
                logger.error("Mentions collection is not initialized.")
                return

            query: Dict[str, Any] = {
                "user_id": message.author.id,
                "created_at": {"$gte": afk_start_time}
            }
            if scope == "server" and server_id is not None:
                query["guild_id"] = server_id

            mentions_list = await self.mentions_collection.find(query).to_list(length=None)

            if not mentions_list:
                logger.info(f"No mentions found for user {message.author.id} during AFK period.")
                return

            valid_mentions = []
            for m in mentions_list:
                if all(k in m for k in ["message_id", "channel_id", "guild_id", "mentioned_by", "created_at"]):
                    valid_mentions.append(m)
                else:
                    logger.warning(f"Skipping invalid mention record: {m}")

            if not valid_mentions:
                logger.warning("No valid mentions after filtering.")
                return

            view = MentionPaginator(valid_mentions, message.author, self.bot)
            try:
                dm_channel = await message.author.create_dm()
                await view.start(dm_channel)
                logger.info(f"Sent mention summary to {message.author} via DM.")
            except (discord.Forbidden, Exception):
                logger.info(f"Falling back to channel for user {message.author.id}")
                await view.start(message.channel)

            await self.mentions_collection.delete_many({"user_id": message.author.id})
        except Exception as e:
            logger.error(f"Error in _send_mention_summary: {e}")
            await message.channel.send("There was an error displaying your AFK mentions summary.", delete_after=10)

    async def _revert_nickname(
        self,
        user_id: int,
        old_nick: Optional[str],
        scope: str,
        server_id: Optional[int]
    ):
        """
        Revert the user's nickname when they return from AFK.
        - If scope == "server", only revert in that guild.
        - If scope == "global", attempt in all guilds where the bot can.
        old_nick may be None, which means “remove any custom nickname and show default username.”
        """
        if scope == "server" and server_id:
            guild = self.bot.get_guild(server_id)
            if guild:
                member = guild.get_member(user_id)
                if member:
                    try:
                        await member.edit(nick=old_nick)
                    except discord.Forbidden:
                        logger.warning(f"Cannot revert nickname for {member} in guild {guild.id} (missing perms).")
                    except Exception as e:
                        logger.error(f"Error reverting nickname for {member.id} in guild {guild.id}: {e}")
            return

        for guild in self.bot.guilds:
            member = guild.get_member(user_id)
            if member:
                try:
                    await member.edit(nick=old_nick)
                except discord.Forbidden:
                    continue
                except Exception as e:
                    logger.error(f"Error reverting nickname for {member.id} in guild {guild.id}: {e}")

    async def cog_unload(self):
        """Clean up background tasks and close the MongoDB connection when the cog unloads."""
        try:
            if self.tasks_started:
                self.clean_cache.cancel()
                self.cleanup_mentions.cancel()
                self.load_nicknames_cache.cancel()
            if self.db_client is not None:
                self.db_client.close()
            logger.info("AFK cog unloaded successfully.")
        except Exception as e:
            logger.error(f"Error during cog unload: {e}")

async def setup(bot: commands.Bot):
    """
    Initialize and load the AFK cog, then reapply nicknames for any existing AFK users.
    """
    try:
        cog = AFK(bot)
        await cog.init_db()

        # One-time reapply of nicknames on startup
        await cog._reapply_nicknames_once()

        # Start background tasks (which include recurring reapplication)
        cog.start_tasks()

        await bot.add_cog(cog)
        logger.info("AFK cog loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load AFK cog: {e}")
        raise
