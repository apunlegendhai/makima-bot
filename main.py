import discord
from discord.ext import commands
import logging
import os
from dotenv import load_dotenv
import asyncio
import aiohttp
import sys
from typing import Dict

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# Directory constants
LOGS_DIR = "logs"
DATABASE_DIR = "database"

def setup_directories() -> None:
    for directory in (LOGS_DIR, DATABASE_DIR):
        os.makedirs(directory, exist_ok=True)

def setup_logging() -> None:
    logging.basicConfig(
        filename=os.path.join(LOGS_DIR, "bot.log"),
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

def validate_environment() -> None:
    if not all([DISCORD_TOKEN, WEBHOOK_URL]):
        missing = []
        if not DISCORD_TOKEN:
            missing.append("DISCORD_TOKEN")
        if not WEBHOOK_URL:
            missing.append("WEBHOOK_URL")
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

class DiscordBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.presences = True  # Enable presences required by vanityrole cog
        intents.message_content = True

        super().__init__(command_prefix=".", intents=intents)
        self.session: aiohttp.ClientSession = None
        self.command_locks: Dict[str, asyncio.Lock] = {}
        self.processing_commands: Dict[str, bool] = {}
        self.cogs_list = [
            "cogs.greet",
            "cogs.autoresponder",
            "cogs.status_changer",
            "cogs.dragmee",
            "cogs.AvatarBannerUpdater",
            "cogs.giveaway",
            "cogs.steal",
            "cogs.stats",
            "cogs.afk_cog",
            "cogs.purge",
            "cogs.key_generator",
            "cogs.av",
            "cogs.status",
            "cogs.thread",
            "cogs.sticky",
            "cogs.reqrole",
            "cogs.confess",
            "cogs.snipe",
            "cogs.leaderboard",
        ]

    async def setup_hook(self) -> None:
        # Set up aiohttp session and load cogs
        self.session = aiohttp.ClientSession()
        await self.load_cogs()

    async def close(self) -> None:
        if self.session:
            await self.session.close()
        await super().close()

    async def get_command_lock(self, user_id: int, command_name: str) -> asyncio.Lock:
        lock_key = f"{user_id}:{command_name}"
        if lock_key not in self.command_locks:
            self.command_locks[lock_key] = asyncio.Lock()
        return self.command_locks[lock_key]

    async def load_cogs(self) -> None:
        for cog in self.cogs_list:
            try:
                if cog not in self.extensions:
                    await self.load_extension(cog)
                    logging.info(f"Loaded {cog}")
            except Exception as e:
                logging.error(f"Error loading {cog}: {e}")

    async def send_error_report(self, error_message: str) -> None:
        if not self.session:
            return
        try:
            async with self.session.post(WEBHOOK_URL, json={"content": error_message}) as response:
                response.raise_for_status()
        except Exception as e:
            logging.error(f"Failed to send error report: {e}")

    async def process_commands(self, message):
        if message.author.bot:
            return
        ctx = await self.get_context(message)
        if ctx.command is None:
            return
        command_key = f"{ctx.author.id}:{ctx.command.name}"
        if self.processing_commands.get(command_key, False):
            return
        try:
            self.processing_commands[command_key] = True
            await super().process_commands(message)
        finally:
            self.processing_commands[command_key] = False

class Bot(DiscordBot):
    async def setup_hook(self) -> None:
        # Call the parent's setup_hook to set up the session and load cogs
        await super().setup_hook()

        # Register commands and events here so they are only set up once.
        @self.command()
        @commands.cooldown(1, 3, commands.BucketType.user)
        async def ping(ctx):
            lock = await self.get_command_lock(ctx.author.id, ctx.command.name)
            if lock.locked():
                return
            async with lock:
                await ctx.send(f'<a:sukoon_greendot:1322894177775783997> Latency: {self.latency * 1000:.2f}ms')

        @self.event
        async def on_ready():
            print(f'Logged in as {self.user}')
            try:
                synced = await self.tree.sync()
                print(f"Synced {len(synced)} command(s)")
                print("Registered slash commands:")
                for command in self.tree.get_commands():
                    print(f"- {command.name}")
            except Exception as e:
                logging.error(f"Error syncing commands: {e}")

        @self.event
        async def on_command(ctx):
            logging.info(f"Command {ctx.command.name} used by {ctx.author}")

        @self.event
        async def on_command_error(ctx, error):
            if isinstance(error, commands.CommandOnCooldown):
                await ctx.send(f"Please wait {error.retry_after:.1f}s before using this command again.")
                return

            error_msg = f"Error in {ctx.command}: {str(error)}"
            logging.error(error_msg)

            if isinstance(error, commands.CommandNotFound):
                return
            elif isinstance(error, commands.MissingRequiredArgument):
                await ctx.send(f"Missing required argument: {error.param}")
            elif isinstance(error, commands.CheckFailure):
                await ctx.send("You don't have permission to use this command.")
            else:
                await ctx.send(f"An error occurred: {str(error)}")
                await self.send_error_report(error_msg)

def main():
    try:
        setup_directories()
        setup_logging()
        validate_environment()

        bot = Bot()
        # Let bot.run handle the setup; setup_hook is automatically called.
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logging.critical(f"Critical error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
