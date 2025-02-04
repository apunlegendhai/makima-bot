import discord
from discord.ext import commands
import logging
import os
import asyncio
import traceback
from dotenv import load_dotenv

# Load environment variables early
load_dotenv()

class BotConfig:
    """Centralized configuration management."""
    def __init__(self):
        self.token = self._validate_token()
        self.cogs = [
            "cogs.greet", "cogs.autoresponder", "cogs.status_changer", "cogs.dragmee",
            "cogs.AvatarBannerUpdater", "cogs.giveaway", "cogs.steal", "cogs.stats",
            "cogs.afk_cog", "cogs.purge", "cogs.key_generator", "cogs.av",
            "cogs.thread", "cogs.sticky", "cogs.reqrole", "cogs.confess"
        ]
        self.log_dir = "logs"
        self.log_file = os.path.join(self.log_dir, "bot.log")
        self.log_level = logging.INFO
        self.command_prefix = "."
        self.sync_retry_attempts = 5
        self._setup_logging()

    def _validate_token(self) -> str:
        """Validate and retrieve the Discord token."""
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise ValueError("No DISCORD_TOKEN found in .env file")
        return token

    def _setup_logging(self):
        """Ensure log directory exists and set up logging."""
        os.makedirs(self.log_dir, exist_ok=True)
        logging.basicConfig(
            level=self.log_level,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler(self.log_file),
                logging.StreamHandler()
            ]
        )

class CustomHelpCommand(commands.DefaultHelpCommand):
    """Enhanced help command with embed support."""
    async def send_bot_help(self, mapping):
        embed = discord.Embed(title="Bot Commands", color=discord.Color.blue())
        for cog, cmds in mapping.items():
            if cog:
                filtered_cmds = await self.filter_commands(cmds, sort=True)
                if filtered_cmds:
                    cmd_list = [cmd.name for cmd in filtered_cmds]
                    embed.add_field(name=cog.qualified_name, value=", ".join(cmd_list), inline=False)

        await self.get_destination().send(embed=embed)

class Bot(commands.Bot):
    def __init__(self, config: BotConfig):
        intents = discord.Intents.all()  # Allow all intents
        super().__init__(
            command_prefix=config.command_prefix,
            intents=intents,
            help_command=CustomHelpCommand()
        )
        self.config = config

    async def load_extensions(self):
        """Load bot extensions with error handling."""
        for extension in self.config.cogs:
            try:
                await self.load_extension(extension)
                logging.info(f"✅ Loaded extension: {extension}")
            except ModuleNotFoundError:
                logging.error(f"❌ Cog not found: {extension}")
            except commands.ExtensionFailed as e:
                logging.error(f"❌ Failed to load {extension}: {traceback.format_exc()}")
            except Exception as e:
                logging.error(f"❌ Unknown error loading {extension}: {e}")

    async def sync_commands(self):
        """Sync commands with rate limit handling."""
        for attempt in range(1, self.config.sync_retry_attempts + 1):
            try:
                synced = await self.tree.sync()
                logging.info(f"✅ Synced {len(synced)} commands successfully.")
                return
            except discord.HTTPException as e:
                if e.code == 429:  # Rate limited
                    wait_time = e.retry_after or (2 ** attempt)
                    logging.warning(f"⏳ Rate limited. Retrying {attempt}/{self.config.sync_retry_attempts} in {wait_time:.2f} seconds...")
                    await asyncio.sleep(wait_time)
                else:
                    logging.error(f"❌ Command sync failed: {e}")
                    break

    async def setup_hook(self):
        """Async setup for bot initialization."""
        await self.load_extensions()
        await self.sync_commands()

def main():
    config = BotConfig()
    bot = Bot(config)

    @bot.event
    async def on_ready():
        """Triggered when bot is fully connected."""
        logging.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")

    @bot.event
    async def on_command_error(ctx, error):
        """Centralized error handling to prevent duplicate messages."""
        if hasattr(ctx.command, "on_error"):
            return  # Skip commands that have their own error handlers.

        ignored_errors = (commands.CommandNotFound,)  # Ignore unknown command errors.

        if isinstance(error, ignored_errors):
            return  # Don't send an error message for ignored errors.

        error_map = {
            commands.MissingRequiredArgument: f"❌ Missing argument. Use `{ctx.prefix}help {ctx.command}`.",
            commands.BadArgument: f"❌ Invalid argument. Use `{ctx.prefix}help {ctx.command}`."
        }

        for error_type, message in error_map.items():
            if isinstance(error, error_type):
                return await ctx.send(message)

        logging.error(f"❌ Unhandled error: {error}")
        await ctx.send("An unexpected error occurred.")

    @bot.command()
    async def ping(ctx):
        """Latency check command."""
        await ctx.send(f"🏓 Latency: {bot.latency * 1000:.2f}ms")

    bot.run(config.token, reconnect=True)  # Auto-reconnect enabled

if __name__ == "__main__":
    main()
    
