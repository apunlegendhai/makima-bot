import discord
from discord.ext import commands
import logging
import os
from dotenv import load_dotenv
import asyncio
import aiohttp
import sys
import signal
from typing import Optional, Dict
from logging.handlers import TimedRotatingFileHandler
from pyfiglet import Figlet

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# Directory constants
LOGS_DIR = "logs"
DATABASE_DIR = "database"
COGS_DIR = "cogs"


def setup_directories() -> None:
    for directory in (LOGS_DIR, DATABASE_DIR, COGS_DIR):
        os.makedirs(directory, exist_ok=True)


def setup_logging() -> None:
    class MessageReceivedFilter(logging.Filter):
        def filter(self, record):
            return 'Message received:' not in record.getMessage()

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    handler = TimedRotatingFileHandler(
        os.path.join(LOGS_DIR, "bot.log"),
        when="midnight",
        backupCount=7,
        utc=True
    )
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    handler.addFilter(MessageReceivedFilter())
    logger.addHandler(handler)


def validate_environment() -> None:
    missing = []
    if not DISCORD_TOKEN:
        missing.append("DISCORD_TOKEN")
    if not WEBHOOK_URL:
        missing.append("WEBHOOK_URL")
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


def print_banner(bot_name: str = "Discord Bot") -> None:
    f = Figlet(font='slant')
    banner = f.renderText(bot_name)
    print("\033[36m" + banner + "\033[0m")
    print("\033[33m" + "=" * 50 + "\033[0m")
    print("\033[32m" + "Bot is starting up..." + "\033[0m")
    print("\033[33m" + "=" * 50 + "\033[0m\n")


class DiscordBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.presences = True
        intents.message_content = True

        super().__init__(command_prefix=".", intents=intents)
        self.session: Optional[aiohttp.ClientSession] = None
        self.command_locks: Dict[str, asyncio.Lock] = {}
        self._shutdown_event = asyncio.Event()
        self._ready_once = False

        @self.command()
        @commands.cooldown(1, 3, commands.BucketType.user)
        async def ping(ctx):
            lock = await self.get_command_lock(ctx.author.id, ctx.command.name)
            async with lock:
                await ctx.send(f'<a:sukoon_greendot:1322894177775783997> Latency: {self.latency*1000:.2f}ms')

    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession()
        await self.load_cogs()

    async def on_ready(self):
        if not self._ready_once:
            self._ready_once = True
            print("\033[2J\033[H")
            print_banner(self.user.name)
            logging.info(f"Logged in as {self.user.name} ({self.user.id})")
            print(f"\033[32mLogged in as {self.user.name} ({self.user.id})\033[0m")

            try:
                synced = await self.tree.sync()
                logging.info(f"Synced {len(synced)} slash commands")
                for cmd in synced:
                    logging.info(f"- /{cmd.name}: {cmd.description}")
            except Exception as e:
                logging.error(f"Failed to sync slash commands: {e}")

    async def close(self) -> None:
        logging.info("Shutting down bot...")
        print("\n\033[33m" + "=" * 50 + "\033[0m")
        print("\033[31mBot is shutting down...\033[0m")
        print("\033[33m" + "=" * 50 + "\033[0m\n")

        self._shutdown_event.set()
        if self.session:
            await self.session.close()

        for guild in self.guilds:
            vc = guild.voice_client
            if vc:
                try:
                    await asyncio.wait_for(vc.disconnect(force=True), timeout=5.0)
                except asyncio.TimeoutError:
                    logging.warning(f"Timeout while disconnecting VC in guild {guild.id}")
                except Exception as e:
                    logging.error(f"Error disconnecting VC in guild {guild.id}: {e}")

        await super().close()

    def signal_handler(self):
        logging.info("Received shutdown signal")
        asyncio.create_task(self.close())

    @property
    def is_shutting_down(self) -> bool:
        return self._shutdown_event.is_set()

    async def get_command_lock(self, user_id: int, command_name: str) -> asyncio.Lock:
        key = f"{user_id}:{command_name}"
        if key not in self.command_locks:
            self.command_locks[key] = asyncio.Lock()
        return self.command_locks[key]

    async def load_cogs(self) -> None:
        if not os.path.isdir(COGS_DIR):
            logging.warning(f"No '{COGS_DIR}' directory found; skipping cog loading.")
            return

        for filename in os.listdir(COGS_DIR):
            if not filename.endswith('.py') or filename.startswith('__'):
                continue
            module = f"{COGS_DIR}.{filename[:-3]}"
            try:
                await self.load_extension(module)
                logging.info(f"Loaded cog {module}")
            except Exception as e:
                logging.error(f"Failed to load cog {module}: {e}")

    async def send_error_report(self, error_message: str) -> None:
        if not self.session:
            return
        try:
            async with self.session.post(WEBHOOK_URL, json={"content": error_message}) as resp:
                resp.raise_for_status()
        except Exception as e:
            logging.error(f"Failed to send error report: {e}")

    async def process_commands(self, message):
        if message.author.bot:
            return
        ctx = await self.get_context(message)
        if not ctx.command:
            return
        lock = await self.get_command_lock(ctx.author.id, ctx.command.name)
        async with lock:
            await super().process_commands(message)


def setup_signal_handlers(bot: DiscordBot) -> None:
    loop = asyncio.get_event_loop()
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGTERM, bot.signal_handler)
        loop.add_signal_handler(signal.SIGINT, bot.signal_handler)
    else:
        # On Windows, fallback to the standard signal module
        signal.signal(signal.SIGTERM, lambda *_: bot.signal_handler())
        signal.signal(signal.SIGINT, lambda *_: bot.signal_handler())


async def main():
    try:
        setup_directories()
        setup_logging()
        validate_environment()
    except ValueError as e:
        print(f"\033[31mStartup error: {e}\033[0m")
        sys.exit(1)

    bot = DiscordBot()
    setup_signal_handlers(bot)

    try:
        await bot.start(DISCORD_TOKEN)
        await bot._shutdown_event.wait()
    except Exception as e:
        logging.error(f"Fatal error in main: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\033[31mBot shutdown by keyboard interrupt\033[0m")
    except Exception as e:
        print(f"\n\033[31mFatal error during startup: {e}\033[0m")
        logging.error(f"Fatal error during startup: {e}")
        sys.exit(1)
