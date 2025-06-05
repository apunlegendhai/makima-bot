import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
import os
from typing import Optional, Dict, Tuple, List
import asyncio
import random

class BanCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = None
        self.config_cache = {}
        self.cache_ready = asyncio.Event()
        self.responses = []
        self.last_modified_time = 0
        self.ban_dm_messages = [
            "Congratulations! You've won a lifetime ban from {server}! 🎉",
            "Achievement unlocked: Get banned from {server}! 🏆",
            "Oops! Looks like you've been voted off the {server} island. Bye bye! 👋",
            "Breaking news: You've been banned from {server}. Try not to cry too much. 📰",
            "The admins of {server} have collectively decided they've seen enough of you. Shocker!",
            "Your behavior in {server} was so bad, even a bot had to step in. That's pretty sad.",
            "The door of {server} just hit you on the way out! How's that feel? 🚪",
            "You've been promoted to ex-member of {server}! What an accomplishment!",
            "Wow, you really outdid yourself this time! Banned from {server} in record time!",
            "The trash took itself out of {server} today. How convenient! 🗑️",
            "You thought the rules didn't apply to you? {server} says otherwise!",
            "In the game of {server}, you lost. Banned!",
            "Sorry not sorry, you've been banned from {server}. Maybe try behaving better next time?",
            "Your {server} free trial has expired... permanently.",
            "Error 403: Access to {server} forbidden. Reason: You're banned!"
        ]
        self.load_responses()
        self.file_watcher_task = None

    async def watch_responses_file(self):
        last_modified = 0
        while True:
            try:
                if os.path.exists("responses.txt"):
                    current_modified = os.path.getmtime("responses.txt")
                    if current_modified > last_modified:
                        with open("responses.txt", "r") as f:
                            self.responses = [line.strip() for line in f if line.strip()]
                        last_modified = current_modified
            except:
                pass
            await asyncio.sleep(1)

    def load_responses(self):
        try:
            with open("responses.txt", "r") as f:
                self.responses = [line.strip() for line in f if line.strip()]
        except:
            self.responses = ["@user has been banned for [reason]"]

    async def setup_database(self):
        database_folder = "database"
        if not os.path.exists(database_folder):
            os.makedirs(database_folder)
        self.db_path = os.path.join(database_folder, "ban_config.db")
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS ban_config (
                        guild_id INTEGER PRIMARY KEY,
                        command TEXT NOT NULL,
                        response TEXT NOT NULL
                    )
                ''')
                await db.commit()
            await self.load_config_cache()
        except:
            self.cache_ready.set()

    async def load_config_cache(self):
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute('SELECT guild_id, command, response FROM ban_config') as cursor:
                    records = await cursor.fetchall()
                    self.config_cache = {str(record[0]): (record[1], record[2]) for record in records}
            self.cache_ready.set()
        except:
            self.cache_ready.set()

    @app_commands.command(name="setban", description="Set custom ban command")
    @app_commands.checks.has_permissions(administrator=True)
    async def setban(
        self, 
        interaction: discord.Interaction, 
        command: str
    ):
        if not interaction.guild:
            return await interaction.response.send_message("This command can only be used in a server!", ephemeral=True)
        try:
            placeholder_response = "Random response will be used"
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO ban_config (guild_id, command, response) VALUES (?, ?, ?)",
                    (interaction.guild.id, command, placeholder_response)
                )
                await db.commit()
            self.config_cache[str(interaction.guild.id)] = (command, placeholder_response)
            response_count = len(self.responses)
            await interaction.response.send_message(
                f"<a:heartspar:1335854160322498653> Ban configuration updated!\n"
                f"Command: `.{command}`\n"
                f"The bot will randomly choose from {response_count} different ban messages when this command is used.",
                ephemeral=True
            )
        except:
            await interaction.response.send_message(
                "<a:heartspar:1335854160322498653> An error occurred while updating the ban configuration.",
                ephemeral=True
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not message.content.startswith('.'):
            return

        await self.cache_ready.wait()
        guild_id = str(message.guild.id)
        config = self.config_cache.get(guild_id)

        if not config:
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    async with db.execute(
                        "SELECT command, response FROM ban_config WHERE guild_id = ?",
                        (int(guild_id),)
                    ) as cursor:
                        row = await cursor.fetchone()
                        if row:
                            config = (row[0], row[1])
                            self.config_cache[guild_id] = config
            except:
                pass

        if not config:
            return

        command, response = config
        dot_command = '.' + command
        if not message.content.startswith(dot_command):
            return

        if not isinstance(message.author, discord.Member) or not message.author.guild_permissions.administrator:
            return await message.reply("<a:heartspar:1335854160322498653> You don't have permission to use this command. Only administrators can use it.")

        content = message.content[len(dot_command):].strip()
        target = None

        if message.mentions:
            target = message.mentions[0]
            reason = content[content.find(target.mention) + len(target.mention):].strip()
        else:
            parts = content.split()
            if not parts:
                return await message.reply("<a:heartspar:1335854160322498653> You need to mention a user or provide a user ID to ban.")
            potential_id = parts[0].strip()
            for char in ['<', '>', '@', '!']:
                potential_id = potential_id.replace(char, '')
            try:
                user_id = int(potential_id)
                try:
                    target = await self.bot.fetch_user(user_id)
                    reason = ' '.join(parts[1:]).strip()
                except discord.NotFound:
                    return await message.reply("<a:heartspar:1335854160322498653> Could not find a user with that ID.")
                except:
                    return await message.reply("<a:heartspar:1335854160322498653> An error occurred while trying to find the user.")
            except ValueError:
                return await message.reply("<a:heartspar:1335854160322498653> Invalid user ID or mention. Please provide a valid user mention or ID.")

        if not target:
            return await message.reply("<a:heartspar:1335854160322498653> You need to mention a user or provide a user ID to ban.")
        if not reason:
            reason = "No reason provided"

        try:
            if not message.guild.me.guild_permissions.ban_members:
                return await message.reply("<a:heartspar:1335854160322498653> I don't have the ban permission in this server.")
            if isinstance(target, discord.Member) and target.top_role >= message.guild.me.top_role:
                return await message.reply("<a:heartspar:1335854160322498653> I cannot ban this user as their role is higher than or equal to mine.")
            try:
                random_dm_message = random.choice(self.ban_dm_messages)
                formatted_dm_message = random_dm_message.format(server=message.guild.name)
                await target.send(formatted_dm_message)
                await asyncio.sleep(1)
            except:
                pass
            await message.guild.ban(target, reason=f"Banned by {message.author}: {reason}")

            if self.responses:
                random_response = random.choice(self.responses)
                formatted_response = random_response.replace("@user", target.mention).replace("[reason]", reason)
            else:
                formatted_response = response.replace("@user", target.mention).replace("[reason]", reason)

            await message.channel.send(formatted_response)
        except discord.Forbidden:
            await message.reply("<a:heartspar:1335854160322498653> I don't have permission to ban that user.")
        except:
            await message.reply("<a:heartspar:1335854160322498653> An error occurred while trying to ban the user. Make sure I have the proper permissions.")

    async def cog_load(self):
        await self.setup_database()
        self.file_watcher_task = asyncio.create_task(self.watch_responses_file())

    async def cog_unload(self):
        if self.file_watcher_task and not self.file_watcher_task.done():
            self.file_watcher_task.cancel()

async def setup(bot: commands.Bot):
    await bot.add_cog(BanCog(bot))
