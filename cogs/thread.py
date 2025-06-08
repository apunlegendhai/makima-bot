# cogs/threads.py

import os
import logging
from datetime import datetime

import discord
from discord.ext import commands
from discord import app_commands, Interaction, TextChannel
from discord.app_commands import checks
from pymongo import MongoClient

logger = logging.getLogger(__name__)


class ThreadCreatorCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # MongoDB setup
        mongo_uri = os.getenv("MONGO_URL")
        if not mongo_uri:
            raise ValueError("MONGO_URL is not set in the environment variables.")
        self.mongo_client = MongoClient(mongo_uri)
        self.db = self.mongo_client["threads"]
        self.guild_configs = self.db["guild_configs"]
        self.cooldowns = self.db["cooldowns"]

    def cog_unload(self):
        try:
            self.mongo_client.close()
            logger.info("MongoDB connection closed.")
        except Exception as e:
            logger.warning(f"Error closing MongoDB connection: {e}")

    def is_on_cooldown(self, guild_id: str, user_id: str, cooldown: int):
        now = datetime.utcnow()
        entry = self.cooldowns.find_one({"guild_id": guild_id, "user_id": user_id})
        if entry:
            last_used = entry.get("last_used", now)
            elapsed = (now - last_used).total_seconds()
            if elapsed < cooldown:
                return True, cooldown - elapsed
        return False, 0

    def update_cooldown(self, guild_id: str, user_id: str):
        now = datetime.utcnow()
        self.cooldowns.update_one(
            {"guild_id": guild_id, "user_id": user_id},
            {"$set": {"last_used": now}},
            upsert=True
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        # allow for future text commands
        await self.bot.process_commands(message)

        guild_id = str(message.guild.id)
        channel_id = str(message.channel.id)
        user_id = str(message.author.id)

        config = self.guild_configs.find_one(
            {"guild_id": guild_id, "channel_id": channel_id}
        )
        if not config or not message.attachments:
            return

        cooldown = config.get("cooldown", 30)
        on_cd, remaining = self.is_on_cooldown(guild_id, user_id, cooldown)
        if on_cd:
            await message.channel.send(
                f"⏳ Cooldown active. Try again in {remaining:.0f}s.",
                delete_after=5
            )
            return

        thread_name = message.content.strip()[:50] or f"Thread by {message.author.display_name}"

        try:
            thread = await message.create_thread(
                name=thread_name,
                auto_archive_duration=1440
            )
        except discord.Forbidden:
            await message.channel.send(
                "❌ I don’t have permission to create threads here.",
                delete_after=5
            )
            logger.warning(f"No thread-create permission in #{message.channel.name}")
            return
        except discord.HTTPException as e:
            await message.channel.send(
                "⚠️ Something went wrong; please try again later.",
                delete_after=5
            )
            logger.error(f"HTTPException creating thread: {e}")
            return

        self.update_cooldown(guild_id, user_id)
        await thread.send(
            f"📎 Thread created by {message.author.mention}",
            delete_after=10
        )

    @app_commands.command(
        name="thread_channel",
        description="Toggle thread-creation on a channel (add/remove)."
    )
    @checks.has_permissions(administrator=True)
    async def configure_channel(
        self,
        interaction: Interaction,
        channel: TextChannel,
        cooldown: int = 30
    ):
        guild_id = str(interaction.guild_id)
        channel_id = str(channel.id)

        # If already configured, remove it
        existing = self.guild_configs.find_one(
            {"guild_id": guild_id, "channel_id": channel_id}
        )
        if existing:
            self.guild_configs.delete_one(
                {"guild_id": guild_id, "channel_id": channel_id}
            )
            return await interaction.response.send_message(
                f"🗑️ Thread creation disabled in {channel.mention}.",
                ephemeral=True
            )

        # Otherwise, ensure the bot has thread permissions
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.create_public_threads or perms.create_private_threads):
            return await interaction.response.send_message(
                "❌ I need Create Threads permission in that channel first.",
                ephemeral=True
            )

        # Add new config
        self.guild_configs.update_one(
            {"guild_id": guild_id, "channel_id": channel_id},
            {"$set": {"cooldown": cooldown}},
            upsert=True
        )
        await interaction.response.send_message(
            f"✅ Thread creation enabled in {channel.mention} with {cooldown}s cooldown.",
            ephemeral=True
        )

    @app_commands.command(
        name="thread_status",
        description="Check thread settings for this server."
    )
    @checks.has_permissions(administrator=True)
    async def thread_status(self, interaction: Interaction):
        guild_id = str(interaction.guild_id)
        configs = list(self.guild_configs.find({"guild_id": guild_id}))

        if not configs:
            return await interaction.response.send_message(
                "❌ No channels configured for auto-thread creation.",
                ephemeral=True
            )

        embed = discord.Embed(
            title="📊 Thread Configuration",
            color=discord.Color.blue()
        )
        for cfg in configs:
            chan = interaction.guild.get_channel(int(cfg["channel_id"]))
            if chan:
                embed.add_field(
                    name=f"#{chan.name}",
                    value=f"Cooldown: {cfg.get('cooldown', 30)}s",
                    inline=False
                )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ThreadCreatorCog(bot))
