import discord
from discord.ext import commands, tasks
import asyncio
import logging
import os

# Set up logging
logging.basicConfig(filename='status_change.log', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

class StatusCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.status_cycle.start()

    @tasks.loop(seconds=60)  # Adjust the loop interval as needed
    async def status_cycle(self):
        """Cycles through status messages from text.txt."""
        try:
            if not os.path.exists("text.txt"):
                logging.error("text.txt file not found")
                return

            with open("text.txt", "r") as file:
                lines = file.readlines()

            if not lines:
                logging.warning("text.txt file is empty")
                return

            for line in lines:
                await self.change_status(line.strip())  # Cycle through each status
                await asyncio.sleep(60)  # Delay between status changes

        except Exception as e:
            logging.error(f"Unexpected error occurred while cycling status: {e}")

    async def change_status(self, message):
        """Changes the bot's status and custom status message."""
        try:
            activity = discord.CustomActivity(name=message, type=discord.ActivityType.playing)
            await self.bot.change_presence(activity=activity, status=discord.Status.idle)
            logging.info(f"Status changed to: {message}")
        except discord.HTTPException as e:
            if e.status == 429:  # Rate limit hit
                retry_after = int(e.response.headers.get('Retry-After', 5))
                logging.warning(f"Rate limit hit, retrying after {retry_after} seconds")
                await asyncio.sleep(retry_after)
                await self.change_status(message)  # Retry after waiting
            else:
                logging.error(f"Failed to change status: {e}")
        except Exception as e:
            logging.error(f"Unexpected error occurred while changing status: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Starts the status cycling when the bot is ready."""
        logging.info(f'Bot is ready and status cycling has started.')

# Setup function to load the cog
async def setup(bot):
    await bot.add_cog(StatusCog(bot))
