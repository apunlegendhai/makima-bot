import discord
from discord.ext import commands, tasks
import asyncio
import logging
import os
import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError
from typing import Dict, Any

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class StickyBot(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.locks: Dict[int, asyncio.Lock] = {}
        self.sticky_cache: Dict[int, Dict[str, Any]] = {}

        # MongoDB connection setup
        mongo_uri = os.getenv("MONGO_URL")
        if not mongo_uri:
            logging.error("MONGO_URL environment variable not set.")
            raise ValueError("MONGO_URL environment variable not set.")
        self.mongo_client = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=5000)
        self.db = self.mongo_client["sticky_bot_db"]
        self.sticky_collection = self.db["sticky_messages"]
        logging.info("MongoDB client initialized.")

        # Start the background task
        self.sticky_task.start()
        logging.info("StickyBot cog loaded successfully.")

    def get_lock(self, channel_id: int) -> asyncio.Lock:
        """
        Return an asyncio lock for the given channel to avoid race conditions.
        """
        if channel_id not in self.locks:
            self.locks[channel_id] = asyncio.Lock()
        return self.locks[channel_id]

    async def has_permissions(self, ctx: commands.Context) -> bool:
        """
        Verify the bot has the required permissions in the channel.
        """
        permissions = ctx.channel.permissions_for(ctx.guild.me)
        if not (permissions.send_messages and permissions.manage_messages and permissions.view_channel):
            await ctx.send("The bot lacks necessary permissions (Send, Manage, View Channel) in this channel.")
            return False
        return True

    async def _post_sticky_message(self, channel: discord.TextChannel, sticky_doc: Dict[str, Any]) -> None:
        """
        Delete the previous sticky message (if any), send a new sticky message,
        and update the database document with the new message ID and timestamp.
        Debounce updates to avoid rapid reposting.
        """
        lock = self.get_lock(channel.id)
        async with lock:
            # Debounce: skip update if last update was less than 2 seconds ago
            last_posted_str = sticky_doc.get("last_posted")
            if last_posted_str:
                try:
                    last_posted = datetime.datetime.fromisoformat(last_posted_str)
                    now = datetime.datetime.utcnow()
                    if (now - last_posted).total_seconds() < 2:
                        return
                except Exception as e:
                    logging.error(f"Error parsing last_posted time: {e}")

            last_message_id = sticky_doc.get("last_message_id")
            if last_message_id:
                try:
                    last_msg = await channel.fetch_message(last_message_id)
                    # Only delete if the message was sent by the bot
                    if last_msg.author == self.bot.user:
                        await last_msg.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass

            try:
                new_msg = await channel.send(sticky_doc["message"])
                now_iso = datetime.datetime.utcnow().isoformat()
                update_data = {
                    "last_message_id": new_msg.id,
                    "last_posted": now_iso,
                }
                await self.sticky_collection.update_one(
                    {"channel_id": channel.id},
                    {"$set": update_data},
                )
                # Update in-memory cache as well
                sticky_doc["last_message_id"] = new_msg.id
                sticky_doc["last_posted"] = now_iso
                self.sticky_cache[channel.id] = sticky_doc
            except discord.Forbidden:
                logging.error(f"Missing permissions to send a message in channel {channel.id}.")
            except Exception as e:
                logging.error(f"Error sending sticky message in channel {channel.id}: {e}")

    @commands.command()
    async def stick(self, ctx: commands.Context, *, message: str) -> None:
        """
        Set or overwrite a sticky message in the current channel.
        """
        if not ctx.author.guild_permissions.manage_messages:
            await ctx.send("You do not have permission to manage sticky messages.")
            return

        if not await self.has_permissions(ctx):
            return

        if len(message) > 1900:
            await ctx.send("The sticky message is too long! Please limit it to 1900 characters.")
            return

        channel_id = ctx.channel.id
        # Check cache first; if not present, try the database.
        existing_doc = self.sticky_cache.get(channel_id) or await self.sticky_collection.find_one({"channel_id": channel_id})
        if existing_doc:
            confirmation_message = await ctx.send(
                f"A sticky message already exists: `{existing_doc['message']}`.\n"
                "React with ✅ to overwrite it or ❌ to keep the old message."
            )
            perms = ctx.channel.permissions_for(ctx.guild.me)
            if not perms.add_reactions:
                await ctx.send("I don't have permission to add reactions. Overwriting the sticky message by default.")
            else:
                await confirmation_message.add_reaction("✅")
                await confirmation_message.add_reaction("❌")

                def reaction_check(reaction: discord.Reaction, user: discord.User) -> bool:
                    return (
                        user == ctx.author
                        and str(reaction.emoji) in ["✅", "❌"]
                        and reaction.message.id == confirmation_message.id
                    )

                try:
                    reaction, _ = await self.bot.wait_for("reaction_add", timeout=30.0, check=reaction_check)
                    if str(reaction.emoji) == "❌":
                        await ctx.send("Keeping the old sticky message.")
                        await confirmation_message.delete()
                        return
                except asyncio.TimeoutError:
                    await confirmation_message.delete()
                    await ctx.send("You didn't react in time. Sticky message setup canceled.")
                    return

        sticky_data = {
            "channel_id": channel_id,
            "guild_id": ctx.guild.id,
            "message": message,
            "last_posted": None,
            "last_message_id": None,
        }
        await self.sticky_collection.update_one(
            {"channel_id": channel_id}, {"$set": sticky_data}, upsert=True
        )
        # Update cache
        self.sticky_cache[channel_id] = sticky_data
        await ctx.send(f"Sticky message set for this channel: {message}")

    @commands.command()
    async def stickstop(self, ctx: commands.Context) -> None:
        """
        Stop and remove the sticky message in the current channel.
        """
        if not ctx.author.guild_permissions.manage_messages:
            await ctx.send("You do not have permission to stop the sticky message.")
            return

        if not await self.has_permissions(ctx):
            return

        channel_id = ctx.channel.id
        async with self.get_lock(channel_id):
            result = await self.sticky_collection.delete_one({"channel_id": channel_id})
            if result.deleted_count > 0:
                self.sticky_cache.pop(channel_id, None)
                await ctx.send("Sticky message stopped.")
            else:
                await ctx.send("There is no sticky message in this channel.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        When a new message is sent, update the sticky message for that channel if configured.
        """
        if not message.guild or message.author.bot:
            return

        permissions = message.channel.permissions_for(message.guild.me)
        if not (permissions.send_messages and permissions.manage_messages):
            return

        sticky_doc = self.sticky_cache.get(message.channel.id)
        if sticky_doc:
            await self._post_sticky_message(message.channel, sticky_doc)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """
        When the bot is ready, reinitialize sticky messages for all channels.
        This ensures that sticky messages persist across bot restarts.
        """
        logging.info("Bot is ready; reinitializing sticky messages for all channels.")
        async for sticky_doc in self.sticky_collection.find():
            channel_id = sticky_doc.get("channel_id")
            if not channel_id:
                continue
            self.sticky_cache[channel_id] = sticky_doc
            channel = self.bot.get_channel(channel_id)
            if not channel:
                await self.sticky_collection.delete_one({"channel_id": channel_id})
                self.sticky_cache.pop(channel_id, None)
                continue
            await self._post_sticky_message(channel, sticky_doc)

    @tasks.loop(seconds=600)
    async def sticky_task(self) -> None:
        """
        Periodically repost sticky messages in all channels.
        """
        for channel_id, sticky_doc in list(self.sticky_cache.items()):
            channel = self.bot.get_channel(channel_id)
            if not channel:
                await self.sticky_collection.delete_one({"channel_id": channel_id})
                self.sticky_cache.pop(channel_id, None)
                continue
            await self._post_sticky_message(channel, sticky_doc)

    @sticky_task.before_loop
    async def before_sticky_task(self) -> None:
        await self.bot.wait_until_ready()

    def cog_unload(self) -> None:
        """
        Clean up resources when the cog is unloaded.
        """
        if self.sticky_task.is_running():
            self.sticky_task.cancel()
        self.mongo_client.close()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StickyBot(bot))
    logging.info("StickyBot cog loaded successfully.")
