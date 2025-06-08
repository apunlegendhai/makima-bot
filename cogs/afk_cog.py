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

# Load environment variables
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
      • embed.timestamp is set dynamically so Discord displays the current time in the corner.
      • Footer: Displays the bot’s name.
      • Uses a random non‑repeating color from a 30‑color pool for each page.
    """
    def __init__(self, mentions: List[Dict[str, Any]], author: discord.Member, bot: commands.Bot):
        super().__init__(timeout=180)  # 3‑minute timeout
        self.bot = bot
        self.author = author
        self.mentions = mentions
        self.current_page = 0
        self.total_pages = len(mentions)
        self.message: Optional[discord.Message] = None

        # Predefined pool of 30 unique colors
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
                    content=f"{self.author.mention}, here's your AFK mention summary:",
                    embed=initial_embed,
                    view=self
                )
                self.message = await ctx.original_response()
            else:
                self.message = await ctx.send(
                    content=f"{self.author.mention}, here's your AFK mention summary:",
                    embed=initial_embed,
                    view=self
                )
        except Exception as e:
            logger.error(f"Error starting paginator: {e}")
            raise

    def get_page_content(self) -> discord.Embed:
        """Build the embed for the current mention."""
        mention = self.mentions[self.current_page]

        # Extract mention data
        guild_id = mention["guild_id"]
        channel_id = mention["channel_id"]
        message_id = mention["message_id"]
        created_at_str = mention["created_at"]
        mentioner_id = mention["mentioned_by"]

        # Construct jump-to-message URL
        jump_url = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"

        # Convert timestamp from ISO to datetime (ensure timezone aware)
        try:
            mention_time = datetime.fromisoformat(created_at_str)
            if mention_time.tzinfo is None:
                mention_time = mention_time.replace(tzinfo=timezone.utc)
        except ValueError:
            mention_time = datetime.now(timezone.utc)

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

        # Set embed.timestamp to current time for dynamic display in Discord's UI
        embed.timestamp = datetime.now(timezone.utc)

        # Set footer to show only the bot's name (the dynamic timestamp will be shown automatically)
        if self.bot.user and self.bot.user.avatar:
            embed.set_footer(text=f"{self.bot.user.name}", icon_url=self.bot.user.avatar.url)
        else:
            embed.set_footer(text=f"{self.bot.user.name}")

        # Thumbnail: mentioner's avatar (if available)
        if mentioner and mentioner.avatar:
            embed.set_thumbnail(url=mentioner.avatar.url)

        return embed

    @discord.ui.button(emoji="<:sukoon_left_arro:1345075074012676219>", style=discord.ButtonStyle.secondary, disabled=True)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Show the previous mention."""
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("You cannot use these controls.", ephemeral=True)
        self.current_page = max(0, self.current_page - 1)
        await self._update_buttons()
        await interaction.response.edit_message(embed=self.get_page_content(), view=self)

    @discord.ui.button(emoji="<:sukoon_right_arro:1345075121039216693>", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Show the next mention."""
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("You cannot use these controls.", ephemeral=True)
        self.current_page = min(self.total_pages - 1, self.current_page + 1)
        await self._update_buttons()
        await interaction.response.edit_message(embed=self.get_page_content(), view=self)

    async def _update_buttons(self):
        """Enable or disable navigation buttons based on the current page."""
        self.children[0].disabled = self.current_page == 0
        self.children[1].disabled = self.current_page == (self.total_pages - 1)

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
        success = await self.afk_cog.set_afk_status(interaction.user.id, self.afk_reason, scope="global", server_id=None)
        if success:
            embed = discord.Embed(
                description=f"<a:sukoon_whitetick:1344600976962748458> | Successfully set your AFK status for reason: {self.afk_reason}",
                color=random.randint(0, 0xFFFFFF)
            )
            await interaction.response.send_message(embed=embed)
            # Delete the original message with the buttons.
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
        success = await self.afk_cog.set_afk_status(interaction.user.id, self.afk_reason, scope="server", server_id=server_id)
        if success:
            embed = discord.Embed(
                description=f"<a:sukoon_whitetick:1344600976962748458> | Successfully set your AFK status for reason: {self.afk_reason}",
                color=random.randint(0, 0xFFFFFF)
            )
            await interaction.response.send_message(embed=embed)
            # Delete the original message with the buttons.
            await interaction.message.delete()
        else:
            await interaction.response.send_message("Failed to set AFK status.", ephemeral=True)
        self.stop()

class AFK(commands.Cog):
    """AFK cog that handles AFK status, recording mentions, and sending a mention summary upon return."""
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

        # Cache: user_id -> dict with keys: reason, timestamp, scope, server_id, original_nick
        self._cache: Dict[int, Dict[str, Any]] = {}
        self.cache_expiry_duration: timedelta = timedelta(hours=1)
        self.connection_retry_delay: int = 5
        self.max_reason_length: int = 100
        self.mention_retention_days: int = 7
        self.tasks_started = False
        self.afk_prefix = "[AFK] "  # Prefix to add to nicknames when AFK

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
            self.tasks_started = True
            logger.info("Background tasks started")

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
                "created_at": {"$lt": cutoff.isoformat()}
            })
            if result.deleted_count:
                logger.info(f"Cleaned up {result.deleted_count} old mention records")
        except Exception as e:
            logger.error(f"Error in cleanup_mentions: {e}")

    async def get_afk_status(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve a user's AFK status, checking cache first."""
        try:
            if user_id in self._cache:
                record = self._cache[user_id]
                if datetime.now(timezone.utc) - record["timestamp"] <= self.cache_expiry_duration:
                    return record
                del self._cache[user_id]

            result = await self.afk_collection.find_one({"user_id": user_id})
            if result:
                reason = result["reason"]
                timestamp = datetime.fromisoformat(result["timestamp"])
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                record = {
                    "reason": reason,
                    "timestamp": timestamp,
                    "scope": result.get("scope", "global"),
                    "server_id": result.get("server_id")
                }
                self._cache[user_id] = record
                return record
            return None
        except Exception as e:
            logger.error(f"Error fetching AFK status for {user_id}: {e}")
            return None

    async def set_afk_status(self, user_id: int, reason: str, scope: str = "global", server_id: Optional[int] = None) -> bool:
        """Set or update a user's AFK status with a scope (global or server)."""
        try:
            reason = discord.utils.escape_markdown(reason.strip())[:self.max_reason_length]
            now = datetime.now(timezone.utc)
            
            # Handle nickname updates
            original_nick = None
            new_nick = None
            try:
                if scope == "server" and server_id:
                    guild = self.bot.get_guild(server_id)
                    if guild:
                        member = guild.get_member(user_id)
                        bot_member = guild.me
                        logger.info(f"Checking permissions in guild {guild.name} ({guild.id})")
                        logger.info(f"Bot permissions: {bot_member.guild_permissions}")
                        logger.info(f"Target member roles: {[role.name for role in member.roles]}")
                        logger.info(f"Bot roles: {[role.name for role in bot_member.roles]}")

                        if member and bot_member.guild_permissions.manage_nicknames:
                            # Check role hierarchy
                            if bot_member.top_role > member.top_role:
                                original_nick = member.display_name
                                if not member.display_name.startswith(self.afk_prefix):
                                    new_nick = f"{self.afk_prefix}{member.display_name}"[:32]
                                    logger.info(f"Attempting to change nickname from {original_nick} to {new_nick}")
                                    await member.edit(nick=new_nick)
                                    logger.info(f"Successfully changed nickname for {member.name}")
                            else:
                                logger.warning(f"Bot's role ({bot_member.top_role}) is not high enough to modify {member.name}'s role ({member.top_role})")
                        else:
                            logger.warning(f"Bot missing manage_nicknames permission in guild {guild.name}")
                elif scope == "global":
                    for guild in self.bot.guilds:
                        member = guild.get_member(user_id)
                        bot_member = guild.me
                        if member and bot_member.guild_permissions.manage_nicknames:
                            if bot_member.top_role > member.top_role:
                                original_nick = original_nick or member.display_name
                                if not member.display_name.startswith(self.afk_prefix):
                                    new_nick = f"{self.afk_prefix}{member.display_name}"[:32]
                                    await member.edit(nick=new_nick)
                                    logger.info(f"Successfully changed nickname in guild {guild.name}")
                            else:
                                logger.warning(f"Bot's role too low in guild {guild.name}")
            except discord.Forbidden as e:
                logger.warning(f"Discord Forbidden error: {str(e)}")
                logger.warning(f"Bot lacks permission to change nickname for user {user_id} in guild {guild.id}")
            except Exception as e:
                logger.warning(f"Failed to update nickname for user {user_id}: {str(e)}")

            data = {
                "reason": reason,
                "timestamp": now.isoformat(),
                "updated_at": now.isoformat(),
                "scope": scope,
                "server_id": server_id if scope == "server" else None,
                "original_nick": original_nick,
                "afk_nick": new_nick
            }

            await self.afk_collection.update_one(
                {"user_id": user_id},
                {"$set": data, "$setOnInsert": {"user_id": user_id}},
                upsert=True
            )

            self._cache[user_id] = {
                "reason": reason,
                "timestamp": now,
                "scope": scope,
                "server_id": server_id if scope == "server" else None,
                "original_nick": original_nick,
                "afk_nick": new_nick
            }
            return True
        except Exception as e:
            logger.error(f"Error setting AFK status for {user_id}: {e}")
            return False

    async def remove_afk_status(self, user_id: int) -> bool:
        """Remove a user's AFK status and restore original nickname."""
        try:
            # Get the AFK data before removing it
            afk_data = await self.afk_collection.find_one({"user_id": user_id})
            
            if afk_data:
                # Restore nickname based on scope
                try:
                    scope = afk_data.get("scope", "global")
                    server_id = afk_data.get("server_id")
                    original_nick = afk_data.get("original_nick")

                    if scope == "server" and server_id:
                        guild = self.bot.get_guild(server_id)
                        if guild:
                            member = guild.get_member(user_id)
                            if member and original_nick and guild.me.guild_permissions.manage_nicknames:
                                await member.edit(nick=original_nick)
                    elif scope == "global":
                        for guild in self.bot.guilds:
                            member = guild.get_member(user_id)
                            if member and original_nick and guild.me.guild_permissions.manage_nicknames:
                                current_nick = member.display_name
                                if current_nick.startswith(self.afk_prefix):
                                    await member.edit(nick=original_nick)
                except discord.Forbidden:
                    logger.warning(f"Bot lacks permission to restore nickname for user {user_id}")
                except Exception as e:
                    logger.warning(f"Failed to restore nickname for user {user_id}: {e}")

            # Remove from database and cache
            result = await self.afk_collection.delete_one({"user_id": user_id})
            self._cache.pop(user_id, None)
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error removing AFK status for {user_id}: {e}")
            return False

    @commands.command()
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK"):
        """Command to set your AFK status with an optional reason.
           Instead of immediately setting the status, this command now prompts you to choose
           whether you want the AFK to be global or server only.
        """
        try:
            embed = discord.Embed(
                description="<a:sukoon_rabbit:1344601134010073158> Please choose your AFK scope:",
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
        """Check messages for mentions of AFK users and handle AFK returns."""
        if message.author.bot:
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
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
            if afk_status:
                if afk_status["scope"] == "server":
                    if not message.guild or message.guild.id != afk_status["server_id"]:
                        continue  # Skip if the AFK is set only for a specific server
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
        """If a user who is AFK sends a message, remove their status and send a mention summary."""
        afk_status = await self.get_afk_status(message.author.id)
        if afk_status:
            if afk_status["scope"] == "server":
                if not message.guild or message.guild.id != afk_status["server_id"]:
                    return  # Do not remove AFK status if the message is not in the specified server
            await self._send_return_message(message, afk_status["timestamp"])
            await self._send_mention_summary(message, afk_status["timestamp"], afk_status["scope"], afk_status.get("server_id"))
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
                "created_at": datetime.now(timezone.utc).isoformat(),
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

    async def _send_mention_summary(self, message: discord.Message, afk_start_time: datetime, scope: str, server_id: Optional[int] = None):
        """Send a paginated summary of mentions received while the user was AFK."""
        try:
            if self.mentions_collection is None:
                logger.error("Mentions collection is not initialized.")
                return

            query = {
                "user_id": message.author.id,
                "created_at": {"$gte": afk_start_time.isoformat()}
            }
            if scope == "server" and server_id is not None:
                query["guild_id"] = server_id

            mentions = await self.mentions_collection.find(query).to_list(length=None)

            if not mentions:
                logger.info(f"No mentions found for user {message.author.id} during AFK period.")
                return

            valid_mentions = []
            for m in mentions:
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

    async def cog_unload(self):
        """Clean up background tasks and close the MongoDB connection when the cog unloads."""
        try:
            if self.tasks_started:
                self.clean_cache.cancel()
                self.cleanup_mentions.cancel()
            if self.db_client is not None:
                self.db_client.close()
            logger.info("AFK cog unloaded successfully.")
        except Exception as e:
            logger.error(f"Error during cog unload: {e}")

async def setup(bot: commands.Bot):
    """Initialize and load the AFK cog."""
    try:
        cog = AFK(bot)
        await cog.init_db()
        cog.start_tasks()
        await bot.add_cog(cog)
        logger.info("AFK cog loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load AFK cog: {e}")
        raise
