import discord
from discord.ext import commands, tasks
import asyncio
import logging
import os

# Ensure the logs folder exists
logs_dir = "logs"
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)

# Configure logging for errors only
logging.basicConfig(
    filename=os.path.join(logs_dir, "bot.log"),
    level=logging.ERROR,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

class StatusCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Removed self.status_cycle.start() from __init__

    @tasks.loop(seconds=60)  # Adjust the loop interval as needed
    async def status_cycle(self):
        """Cycles through status messages from text.txt."""
        try:
            if not os.path.exists("text.txt"):
                return

            with open("text.txt", "r") as file:
                lines = file.readlines()

            if not lines:
                return

            for line in lines:
                await self.change_status(line.strip())  # Cycle through each status
                await asyncio.sleep(60)  # Delay between status changes

        except Exception as e:
            logging.error(f"Error in status_cycle: {e}")

    async def change_status(self, message):
        """Changes the bot's status and custom status message."""
        try:
            activity = discord.CustomActivity(name=message, type=discord.ActivityType.playing)
            await self.bot.change_presence(activity=activity, status=discord.Status.idle)
        except discord.HTTPException as e:
            if e.status == 429:  # Rate limit hit
                retry_after = int(e.response.headers.get('Retry-After', 5))
                await asyncio.sleep(retry_after)
                await self.change_status(message)  # Retry after waiting
        except Exception as e:
            logging.error(f"Error in change_status: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Starts the status cycling when the bot is ready."""
        try:
            if not self.status_cycle.is_running():
                self.status_cycle.start()
        except Exception as e:
            logging.error(f"Error in on_ready: {e}")

# Setup function to load the cog
async def setup(bot):
    await bot.add_cog(StatusCog(bot))
