import os
import discord
from discord.ext import commands
from discord import app_commands
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ServerSelectionTimeoutError as AsyncIOMotorServerSelectionTimeoutError
import asyncio
import logging
import time
import re
from urllib.parse import urlparse

logger = logging.getLogger("mediaonly")
logger.setLevel(logging.INFO)
if not logger.hasHandlers():
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(ch)


class RateLimiter:
    def __init__(self, calls: int, period: float):
        self.calls = calls
        self.period = period
        self.timestamps = {}

    def can_proceed(self, channel_id):
        now = time.monotonic()
        times = self.timestamps.get(channel_id, [])
        times = [t for t in times if now - t < self.period]
        if len(times) < self.calls:
            times.append(now)
            self.timestamps[channel_id] = times
            return True
        self.timestamps[channel_id] = times
        return False

    def cleanup(self, max_age: float = 3600):
        now = time.monotonic()
        keys_to_delete = []
        for channel_id, times in self.timestamps.items():
            if not times or (now - max(times[-1], 0)) > max_age:
                keys_to_delete.append(channel_id)
        for key in keys_to_delete:
            del self.timestamps[key]


class MediaOnly(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mongo_client = None
        self.db = None
        self.config_collection = None
        self._cache = {}
        self.rate_limiter = RateLimiter(calls=3, period=10)
        self.bot.loop.create_task(self.connect_mongo_with_retry())
        self.cleanup_rate_limiter_task = self.bot.loop.create_task(self._rate_limiter_cleanup_loop())

    async def connect_mongo_with_retry(self):
        mongo_url = os.getenv('MONGO_URL')
        while True:
            try:
                client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
                await client.server_info()
                self.mongo_client = client
                self.db = self.mongo_client['media_only_bot']
                self.config_collection = self.db['configs']
                logger.info("Connected to MongoDB successfully.")
                break
            except Exception as e:
                logger.error(f"Failed to connect to MongoDB. Retrying... Error: {e}")
                await asyncio.sleep(10)

    async def _rate_limiter_cleanup_loop(self):
        while True:
            await asyncio.sleep(3600)
            self.rate_limiter.cleanup()

    def _bot_has_perms(self, channel: discord.TextChannel, log_only=False):
        bot_member = channel.guild.me
        if not bot_member:
            return False
        perms = channel.permissions_for(bot_member)
        if log_only:
            return perms.send_messages
        return perms.manage_messages and perms.read_message_content and perms.send_messages

    async def _get_config(self, guild_id: str):
        now = time.monotonic()
        cached = self._cache.get(guild_id)
        if cached and now - cached['time'] < 60:
            logger.info(f"Using cached config for guild {guild_id}: {cached['config']}")
            return cached['config']
        if self.config_collection is None:
            logger.error("MongoDB client not initialized yet.")
            return {}
        config = await self.config_collection.find_one({'guild_id': guild_id})
        logger.info(f"Fetched config from MongoDB for guild {guild_id}: {config}")
        if config:
            if 'media_only_channels' in config:
                config['media_only_channels'] = [int(cid) for cid in config['media_only_channels']]
            if 'log_channel' in config:
                config['log_channel'] = int(config['log_channel'])
        self._cache[guild_id] = {'config': config or {}, 'time': now}
        return config or {}

    async def _update_cache(self, guild_id: str):
        if self.config_collection is None:
            return
        config = await self.config_collection.find_one({'guild_id': guild_id})
        self._cache[guild_id] = {'config': config or {}, 'time': time.monotonic()}

    def _has_media(self, message: discord.Message) -> bool:
        media_embed_types = ('image', 'video', 'gifv')
        media_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.mp4', '.mov', '.webm', '.mp3', '.wav', '.ogg')
        
        logger.info(f"Checking message {message.id} for media content")
        
        # Check attachments
        if message.attachments:
            logger.info(f"Message has attachments: {[att.filename for att in message.attachments]}")
            return True
            
        # Check embeds
        for embed in message.embeds:
            logger.info(f"Checking embed type: {embed.type}")
            if embed.type in media_embed_types:
                logger.info(f"Found media embed type: {embed.type}")
                return True
                
            url = embed.url or ''
            if any(url.lower().endswith(ext) for ext in media_extensions):
                logger.info(f"Found media URL in embed: {url}")
                return True
                
            if hasattr(embed, 'image') and embed.image and embed.image.url:
                if any(embed.image.url.lower().endswith(ext) for ext in media_extensions):
                    logger.info(f"Found image in embed: {embed.image.url}")
                    return True
                    
            if hasattr(embed, 'video') and embed.video and embed.video.url:
                if any(embed.video.url.lower().endswith(ext) for ext in media_extensions):
                    logger.info(f"Found video in embed: {embed.video.url}")
                    return True
                    
        # Check URLs in message content
        urls = re.findall(r'https?://\S+', message.content.lower())
        for url in urls:
            path = urlparse(url).path
            if any(path.endswith(ext) for ext in media_extensions):
                logger.info(f"Found media URL in content: {url}")
                return True
                
        logger.info("No media content found in message")
        return False

    @app_commands.guild_only()
    @app_commands.command(
        name='media-only',
        description='Media-only setup: toggle, set log channel, or view config in one command'
    )
    @app_commands.describe(
        action='Choose: toggle = add/remove media-only to channel, log = set/clear log channel, view = show config',
        channel='Channel (for toggle/log) or leave empty for view/clear log'
    )
    async def mediaonly(
        self,
        interaction: discord.Interaction,
        action: str,
        channel: discord.TextChannel = None
    ):
        actions = {
            "toggle": "Toggle media-only on a channel",
            "log": "Set or clear log channel",
            "view": "View current config"
        }
        action = action.lower()
        if action not in actions:
            await interaction.response.send_message("Invalid action. Use 'toggle', 'log', or 'view'.", ephemeral=True)
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message('You need administrator permissions to use this command.', ephemeral=True)
            return
        if self.config_collection is None:
            await interaction.response.send_message("Database connection is not ready. Please try later.", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        config = await self._get_config(guild_id)
        media_only_channels = config.get('media_only_channels', [])
        log_channel_id = config.get('log_channel', None)

        if action == "toggle":
            if not channel:
                await interaction.response.send_message("Please specify which channel to toggle.", ephemeral=True)
                return
            if channel.id in media_only_channels:
                await self.config_collection.update_one(
                    {'guild_id': guild_id},
                    {'$pull': {'media_only_channels': channel.id}},
                    upsert=True
                )
                await self._update_cache(guild_id)
                await interaction.response.send_message(f'Media-only restriction removed from {channel.mention}.', ephemeral=True)
            else:
                await self.config_collection.update_one(
                    {'guild_id': guild_id},
                    {'$addToSet': {'media_only_channels': channel.id}},
                    upsert=True
                )
                await self._update_cache(guild_id)
                await interaction.response.send_message(f'Media-only restriction enabled in {channel.mention}.', ephemeral=True)
            return

        if action == "log":
            if channel:
                await self.config_collection.update_one(
                    {'guild_id': guild_id},
                    {'$set': {'log_channel': channel.id}},
                    upsert=True
                )
                await self._update_cache(guild_id)
                await interaction.response.send_message(f'Logging channel set to {channel.mention}.', ephemeral=True)
            else:
                await self.config_collection.update_one(
                    {'guild_id': guild_id},
                    {'$unset': {'log_channel': ""}},
                    upsert=True
                )
                await self._update_cache(guild_id)
                await interaction.response.send_message('Logging channel cleared.', ephemeral=True)
            return

        if action == "view":
            embed = discord.Embed(title='Media-Only Configuration', color=discord.Color.blue())
            if media_only_channels:
                channels_list = '\n'.join([
                    interaction.guild.get_channel(cid).mention if interaction.guild.get_channel(cid)
                    else f'Channel ID {cid} (deleted)'
                    for cid in media_only_channels
                ])
                embed.add_field(name='Media-only Channels', value=channels_list, inline=False)
            else:
                embed.add_field(name='Media-only Channels', value='No channels are currently restricted to media-only.', inline=False)
            if log_channel_id:
                log_channel_obj = interaction.guild.get_channel(log_channel_id)
                val = log_channel_obj.mention if log_channel_obj else f'Channel ID {log_channel_id} (deleted)'
                embed.add_field(name='Log Channel', value=val, inline=False)
            else:
                embed.add_field(name='Log Channel', value='Not set', inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @mediaonly.autocomplete('action')
    async def mediaonly_action_autocomplete(self, interaction, current):
        options = ['toggle', 'log', 'view']
        return [
            app_commands.Choice(name=opt, value=opt)
            for opt in options if current.lower() in opt
        ]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        try:
            if message.author.bot or message.guild is None:
                return
            if self.config_collection is None:
                logger.warning("MongoDB collection not initialized, skipping message check")
                return

            guild_id = str(message.guild.id)
            config = await self._get_config(guild_id)
            media_only_channels = [int(cid) for cid in config.get('media_only_channels', [])]
            logger.info(f"Checking message in channel {message.channel.id}, media-only channels: {media_only_channels}")

            if message.channel.id not in media_only_channels:
                logger.debug(f"Channel {message.channel.id} is not in media-only list")
                return

            # Check bot permissions
            if not message.channel.permissions_for(message.guild.me).manage_messages:
                logger.error(f"Missing manage_messages permission in channel {message.channel.id}")
                return

            if not self.rate_limiter.can_proceed(message.channel.id):
                logger.info(f"Rate limit reached for deletions in {message.channel}. Skipping deletion.")
                return

            # Check if message has media content
            has_media = self._has_media(message)
            logger.info(f"Message {message.id} in channel {message.channel.id} has media: {has_media}")
            
            if has_media:
                return

            try:
                await message.delete()
                logger.info(f"Deleted non-media message {message.id} in channel {message.channel.id}")
            except discord.Forbidden:
                logger.warning(f"Missing permissions to delete message in {message.channel}.")
                return
            except discord.NotFound:
                return
            except Exception as exc:
                logger.error(f"Delete failed: {exc}")
                return

            try:
                dm = await message.author.send(f"Your Message is Removed - media only Channel")
                # Delete the DM after 2 minutes
                await asyncio.sleep(120)
                try:
                    await dm.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass
            except (discord.Forbidden, discord.HTTPException):
                pass  # DMs closed, blocked, or other DM error

            log_channel_id = config.get('log_channel')
            if log_channel_id:
                log_channel = message.guild.get_channel(log_channel_id)
                if log_channel and self._bot_has_perms(log_channel, log_only=True):
                    embed = discord.Embed(
                        title="Deleted non-media message",
                        description=f"User {message.author.mention} sent a non-media message in {message.channel.mention} and it was deleted.",
                        color=discord.Color.red(),
                        timestamp=discord.utils.utcnow()
                    )
                    content = message.content or "[empty]"
                    if len(content) > 1024:
                        content = content[:1021] + "..."
                    embed.add_field(name="Message content", value=content, inline=False)
                    if message.attachments:
                        att_desc = "\n".join(att.url for att in message.attachments)
                        embed.add_field(name="Attachments", value=att_desc, inline=False)
                    embed.set_footer(text=f"User ID: {message.author.id} | Message ID: {message.id}")
                    try:
                        await log_channel.send(embed=embed)
                    except Exception as ex:
                        logger.error(f"Failed to send log message: {ex}")
        except Exception as outer_exc:
            logger.error(f"Unexpected error in on_message: {outer_exc}")


async def setup(bot):
    await bot.add_cog(MediaOnly(bot))
