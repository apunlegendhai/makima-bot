import discord
from discord.ext import commands
from discord import app_commands
import io
import logging
import base64
import aiohttp
import time
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    filename='avatar_banner_update.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class AvatarBannerUpdater(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.owner_ids = set(map(int, os.getenv('OWNER_IDS').split(',')))  # Load owner IDs from .env
        self.guild_id = int(os.getenv('GUILD_ID'))  # Load guild ID from .env
        self.last_avatar_update = 0  # Track last avatar update time
        self.last_banner_update = 0  # Track last banner update time
        logging.info(f"AvatarBannerUpdater initialized with owner IDs: {self.owner_ids}")

    def is_owner(self, interaction: discord.Interaction):
        """Check if the user is the bot owner."""
        return interaction.user.id in self.owner_ids

    @app_commands.command(name='updateavatar', description='Update the bot\'s avatar with an image file.')
    @app_commands.default_permissions()  # Command permissions are explicitly managed
    async def update_avatar(self, interaction: discord.Interaction, image: discord.Attachment):
        if not self.is_owner(interaction):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        # Cooldown check for avatar updates
        current_time = time.time()
        if current_time - self.last_avatar_update < 60:
            await interaction.response.send_message(
                f"Please wait {int(60 - (current_time - self.last_avatar_update))} more seconds before updating the avatar.",
                ephemeral=True,
            )
            return

        # File validation
        if not image.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            await interaction.response.send_message("Unsupported file type.", ephemeral=True)
            return

        if image.size > 8 * 1024 * 1024:  # 8 MB limit
            await interaction.response.send_message("File is too large. Please upload an image under 8 MB.", ephemeral=True)
            return

        # Acknowledge and process the avatar update
        await interaction.response.send_message("<a:sukoon_loading:1322897472338526240> Processing avatar update... Please wait.", ephemeral=True)
        try:
            image_data = io.BytesIO(await image.read())
            await self.bot.user.edit(avatar=image_data.read())
            await interaction.followup.send("<a:sukoon_greendot:1322894177775783997> Bot avatar updated successfully!")
            logging.info(f"Bot avatar updated by user {interaction.user.name}")
            self.last_avatar_update = current_time
        except discord.HTTPException as e:
            await interaction.followup.send(f"<a:sukoon_reddot:1322894157794119732> Failed to update avatar: {e}", ephemeral=True)
            logging.error(f"Failed to update avatar: {e}")

    @app_commands.command(name='updatebanner', description='Update the bot\'s banner with an image file.')
    @app_commands.default_permissions()  # Command permissions are explicitly managed
    async def update_banner(self, interaction: discord.Interaction, image: discord.Attachment):
        if not self.is_owner(interaction):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        # Cooldown check for banner updates
        current_time = time.time()
        if current_time - self.last_banner_update < 60:
            await interaction.response.send_message(
                f"Please wait {int(60 - (current_time - self.last_banner_update))} more seconds before updating the banner.",
                ephemeral=True,
            )
            return

        # File validation
        if not image.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            await interaction.response.send_message("Unsupported file type.", ephemeral=True)
            return

        if image.size > 8 * 1024 * 1024:  # 8 MB limit
            await interaction.response.send_message("File is too large. Please upload an image under 8 MB.", ephemeral=True)
            return

        # Acknowledge and process the banner update
        await interaction.response.send_message("<a:sukoon_loading:1322897472338526240> Processing banner update... Please wait.", ephemeral=True)
        try:
            image_data = io.BytesIO(await image.read())
            banner_base64 = base64.b64encode(image_data.getvalue()).decode('utf-8')
            payload = {'banner': f"data:image/gif;base64,{banner_base64}"}

            headers = {
                'Authorization': f'Bot {os.getenv("DISCORD_TOKEN")}',
                'Content-Type': 'application/json',
            }

            async with aiohttp.ClientSession() as session:
                async with session.patch('https://discord.com/api/v10/users/@me', headers=headers, json=payload) as response:
                    response_text = await response.text()
                    if response.status == 200:
                        await interaction.followup.send("<a:sukoon_greendot:1322894177775783997> Bot banner updated successfully!")
                        logging.info(f"Bot banner updated by user {interaction.user.name}")
                        self.last_banner_update = current_time
                    else:
                        await interaction.followup.send(f"Failed to update banner: {response_text}", ephemeral=True)
                        logging.error(f"<a:sukoon_reddot:1322894157794119732> Failed to update banner: {response_text}")
        except aiohttp.ClientError as e:
            await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)
            logging.error(f"Error updating banner: {e}")
        except Exception as e:
            await interaction.followup.send(f"Unexpected error: {e}", ephemeral=True)
            logging.error(f"Unexpected error: {e}")

async def setup(bot):
    try:
        guild = discord.Object(id=int(os.getenv('GUILD_ID')))
        cog = AvatarBannerUpdater(bot)
        await bot.add_cog(cog)
        await bot.tree.sync(guild=guild)

        # Restrict visibility of commands to bot owners
        for command in cog.bot.tree.get_commands(guild=guild):
            command.default_permissions = None  # Disable for all
            command.guild_only = True  # Ensure guild visibility only

        logging.info("Slash commands synced for guild and restricted to bot owners.")
    except Exception as e:
        logging.error(f"Error adding cog or syncing commands: {e}")
