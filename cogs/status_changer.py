import discord
from discord.ext import commands, tasks
import asyncio
import logging
import os
import aiofiles  # Asynchronous file I/O

# Set up logging
logging.basicConfig(
    filename='status_change.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class StatusCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.status_index = 0

    @tasks.loop(seconds=60)
    async def status_cycle(self):
        """Updates the bot's status from text.txt every 60 seconds."""
        try:
            # Wait until the bot is ready
            await self.bot.wait_until_ready()

            if not os.path.exists("text.txt"):
                logging.error("text.txt file not found")
                return

            # Read the statuses asynchronously
            async with aiofiles.open("text.txt", "r") as file:
                lines = await file.readlines()

            # Clean and filter out empty lines
            statuses = [line.strip() for line in lines if line.strip()]
            if not statuses:
                logging.warning("text.txt is empty or contains only blank lines")
                return

            # Cycle through statuses one by one
            message = statuses[self.status_index % len(statuses)]
            self.status_index += 1

            await self.change_status(message)

        except Exception as e:
            logging.error(f"Unexpected error occurred in status_cycle: {e}")

    async def change_status(self, message):
        """Changes the bot's status and logs the update.
           If a rate limit (429) is encountered, it logs the event and skips this update cycle."""
        if not self.bot:
            logging.error("Bot instance is None. Cannot change status.")
            return

        try:
            activity = discord.Game(name=message)
            await self.bot.change_presence(activity=activity, status=discord.Status.idle)
            logging.info(f"Status changed to: {message}")

        except discord.HTTPException as e:
            if e.status == 429:
                # Extract a retry delay from the exception if available
                retry_after = getattr(e, "retry_after", None)
                if retry_after is None:
                    try:
                        retry_after = float(e.response.headers.get('Retry-After', 5))
                    except Exception:
                        retry_after = 5
                logging.warning(f"Rate limit hit while changing status. "
                                f"Retry after {retry_after:.2f} seconds. Skipping this update cycle.")
            else:
                logging.error(f"HTTP exception while changing status: {e}")

        except Exception as e:
            logging.error(f"Unexpected error occurred while changing status: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Starts the status cycling when the bot is ready."""
        logging.info("Bot is ready. Starting status cycling.")
        if not self.status_cycle.is_running():
            self.status_cycle.start()

# Setup function to load the cog
async def setup(bot):
    await bot.add_cog(StatusCog(bot))
