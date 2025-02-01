import os
from pymongo import MongoClient
import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# MongoDB connection setup using the environment variable
MONGODB_URI = os.getenv('MONGO_URL')
client = MongoClient(MONGODB_URI)  # Use the MongoDB URI from environment variable
db = client['dragmebot']  # Database name
request_channels_collection = db['request_channels']  # Collection for storing request channels

# Set up logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

# Global dictionary to store request channels by guild ID {guild_id: channel_id}
request_channels = {}

def load_request_channels(guild_id):
    """Load the request channel for a specific guild from MongoDB."""
    global request_channels
    # Convert guild_id to string for consistency
    guild_id_str = str(guild_id)
    # Find the document for the guild
    channel_data = request_channels_collection.find_one({"guild_id": guild_id_str})
    if channel_data:
        channel_id = channel_data.get("channel_id")
        if channel_id:
            request_channels[guild_id_str] = channel_id
            logger.info(f"Loaded request channel for guild {guild_id_str}: {channel_id}")
        else:
            # Remove the guild from the cache if no channel_id is found
            request_channels.pop(guild_id_str, None)
            logger.warning(f"No channel_id found for guild {guild_id_str}")
    else:
        # Guild not found in the database, ensure it's removed from cache
        request_channels.pop(guild_id_str, None)
        logger.warning(f"No data found for guild {guild_id_str}")

def save_request_channels(guild_id):
    """Save the request channel for a specific guild to MongoDB."""
    global request_channels
    guild_id_str = str(guild_id)
    channel_id = request_channels.get(guild_id_str)
    # Update or insert the document for the guild
    request_channels_collection.update_one(
        {"guild_id": guild_id_str},
        {"$set": {"channel_id": channel_id}},
        upsert=True
    )
    logger.info(f"Saved request channel for guild {guild_id_str}: {channel_id}")

class DragmeButtons(discord.ui.View):
    def __init__(self, target_user, interaction_user, target_voice_channel, timeout=30):
        super().__init__(timeout=timeout)
        self.target_user = target_user
        self.interaction_user = interaction_user
        self.target_voice_channel = target_voice_channel
        self.message = None

    async def on_timeout(self):
        try:
            if self.message:
                try:
                    # Remove buttons and update message
                    await self.message.edit(
                        content=f"<a:sukoon_reddot:1322894157794119732> Request from {self.interaction_user.mention} has timed out.",
                        view=None  # This removes the buttons
                    )
                except discord.NotFound:
                    logger.warning("Message not found during timeout edit.")
                except discord.Forbidden:
                    logger.warning("Missing permissions to edit message during timeout.")
                except Exception as e:
                    logger.error(f"Error editing timeout message: {e}")

            self.stop()

        except Exception as e:
            logger.error(f"Unexpected error in timeout handler: {e}")

    @discord.ui.button(label="", style=discord.ButtonStyle.green, emoji="<:sukoon_tick:1321088727912808469>")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.target_user:
            await interaction.response.send_message("You're not authorized to accept this request.", ephemeral=True)
            return

        permissions = self.target_voice_channel.permissions_for(interaction.guild.me)
        logger.info(f"Bot permissions in target channel: {permissions}")

        if not permissions.connect or not permissions.move_members:
            await interaction.response.send_message("I don't have the necessary permissions to move the user.", ephemeral=True)
            return

        try:
            # Temporarily unlock the channel for the bot
            await self.target_voice_channel.set_permissions(interaction.guild.me, connect=True)

            # Move the user to the target voice channel
            await self.interaction_user.move_to(self.target_voice_channel)

            # Update message without buttons
            await interaction.response.edit_message(
                content=f"<a:sukoon_greendot:1322894177775783997> {self.interaction_user.mention} has been moved to {self.target_voice_channel.name}.",
                view=None  # This removes the buttons
            )

            # Revert the bot's connect permission
            await self.target_voice_channel.set_permissions(interaction.guild.me, connect=False)

        except discord.Forbidden as e:
            logger.error(f"Forbidden error moving {self.interaction_user} to {self.target_voice_channel}: {e}")
            await interaction.response.send_message("I don't have permission to move the user to this channel.", ephemeral=True)
        except discord.HTTPException as e:
            logger.error(f"HTTP error moving {self.interaction_user} to {self.target_voice_channel}: {e}")
            await interaction.response.send_message("There was an error processing the move.", ephemeral=True)
        except Exception as e:
            logger.error(f"Unexpected error moving {self.interaction_user} to {self.target_voice_channel}: {e}")
            await interaction.response.send_message("Error moving user.", ephemeral=True)

        self.stop()

    @discord.ui.button(label="", style=discord.ButtonStyle.red, emoji="<:sukoon_cross:1321088770845708288>")
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.target_user:
            await interaction.response.send_message("You're not authorized to reject this request.", ephemeral=True)
            return

        # Update message without buttons
        await interaction.response.edit_message(
            content=f"<a:sukoon_reddot:1322894157794119732> {self.interaction_user.mention}'s request has been rejected.",
            view=None  # This removes the buttons
        )

        self.stop()

class DragmeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        logger.info("DragmeCog initialized.")

    async def check_permissions(self, interaction):
        """Check if the bot has necessary permissions."""
        bot_permissions = interaction.guild.me.guild_permissions
        required_permissions = ["move_members", "connect", "manage_channels"]
        missing_permissions = [perm for perm in required_permissions if not getattr(bot_permissions, perm)]

        if missing_permissions:
            await interaction.response.send_message(
                f"I don't have the following permissions: {', '.join(missing_permissions)}.",
                ephemeral=True
            )
            return False
        return True

    @commands.cooldown(1, 60, commands.BucketType.user)
    @discord.app_commands.command(name="dragmee", description="Request to be dragged into a voice channel.")
    async def dragmee(self, interaction: discord.Interaction, target_user: discord.Member):
        # Load the request channel for the guild
        load_request_channels(interaction.guild.id)
        guild_id_str = str(interaction.guild.id)
        request_channel_id = request_channels.get(guild_id_str)

        if request_channel_id is None or interaction.channel.id != int(request_channel_id):
            await interaction.response.send_message("This command can only be used in the drag-requests channel.", ephemeral=True)
            return

        if not await self.check_permissions(interaction):
            return

        if interaction.user.voice is None:
            await interaction.response.send_message(f"{interaction.user.mention}, you need to be in a voice channel to use this command.", ephemeral=True)
            return

        if target_user.voice is None:
            await interaction.response.send_message(f"{target_user.mention} is not in a voice channel.", ephemeral=True)
            return

        target_voice_channel = target_user.voice.channel

        if interaction.user.voice.channel == target_voice_channel:
            await interaction.response.send_message(f"{interaction.user.mention}, you're already in {target_user.mention}'s voice channel.", ephemeral=True)
            return

        await interaction.response.send_message(f"Request to join {target_user.mention}'s voice channel sent.", ephemeral=True)

        # Send request message with buttons
        view = DragmeButtons(target_user, interaction_user=interaction.user, target_voice_channel=target_voice_channel)
        view.message = await interaction.channel.send(
            f"{target_user.mention}, {interaction.user.mention} wants to join your voice channel.",
            view=view,
        )

    @discord.app_commands.command(name="setup", description="Set up or specify a channel to receive dragme requests.")
    @discord.app_commands.default_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        guild_id = str(interaction.guild.id)

        if channel:
            # Check bot permissions in the specified channel
            bot_permissions = channel.permissions_for(interaction.guild.me)
            if not bot_permissions.send_messages or not bot_permissions.view_channel:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="Permission Issue",
                        description=f"I cannot use {channel.mention} due to missing permissions (Send Messages, View Channel).",
                        color=discord.Color.orange()
                    ),
                    ephemeral=True
                )
                return

            # Update the request channel for the guild
            request_channels[guild_id] = str(channel.id)
            save_request_channels(interaction.guild.id)
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Setup Complete",
                    description=f"Drag requests channel set to {channel.mention}.",
                    color=discord.Color.green()
                )
            )
        else:
            # Check if a channel is already set
            current_channel_id = request_channels.get(guild_id)
            if current_channel_id:
                current_channel = interaction.guild.get_channel(int(current_channel_id))
                if current_channel:
                    bot_permissions = current_channel.permissions_for(interaction.guild.me)
                    if bot_permissions.send_messages and bot_permissions.view_channel:
                        await interaction.response.send_message(
                            embed=discord.Embed(
                                title="Channel Already Set",
                                description=f"Current drag requests channel: {current_channel.mention}",
                                color=discord.Color.green()
                            ),
                            ephemeral=True
                        )
                        return
                    else:
                        await interaction.response.send_message(
                            embed=discord.Embed(
                                title="Permission Issue",
                                description=f"I lack permissions in {current_channel.mention}. Please adjust permissions or set a new channel.",
                                color=discord.Color.orange()
                            ),
                            ephemeral=True
                        )
                        return

            # Create a new channel
            if not interaction.guild.me.guild_permissions.manage_channels:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="Error",
                        description="I need the Manage Channels permission to create a new channel.",
                        color=discord.Color.red()
                    ),
                    ephemeral=True
                )
                return

            try:
                # Create the new channel with appropriate permissions
                overwrites = {
                    interaction.guild.default_role: discord.PermissionOverwrite(send_messages=False, view_channel=True),
                    interaction.guild.me: discord.PermissionOverwrite(send_messages=True, manage_channels=True)
                }
                new_channel = await interaction.guild.create_text_channel(
                    "drag-requests",
                    overwrites=overwrites
                )
                request_channels[guild_id] = str(new_channel.id)
                save_request_channels(interaction.guild.id)
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="Setup Complete",
                        description=f"New drag requests channel created: {new_channel.mention}",
                        color=discord.Color.green()
                    )
                )
            except Exception as e:
                logger.error(f"Error creating channel: {e}")
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="Error",
                        description=f"Failed to create channel: {e}",
                        color=discord.Color.red()
                    ),
                    ephemeral=True
                )

    @dragmee.error
    async def dragmee_error(self, interaction: discord.Interaction, error: Exception):
        if isinstance(error, commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"Please wait {error.retry_after:.1f} seconds before using this command again.",
                ephemeral=True
            )
        else:
            logger.error(f"Dragme error: {error}", exc_info=True)
            await interaction.response.send_message(
                "An error occurred while processing your request.",
                ephemeral=True
            )

    @setup.error
    async def setup_error(self, interaction: discord.Interaction, error: Exception):
        if isinstance(error, commands.MissingPermissions):
            await interaction.response.send_message(
                "You need administrator permissions to use this command.",
                ephemeral=True
            )
        else:
            logger.error(f"Setup error: {error}", exc_info=True)
            await interaction.response.send_message(
                "An error occurred during setup.",
                ephemeral=True
            )

async def setup(bot):
    await bot.add_cog(DragmeCog(bot))
    await bot.tree.sync()
    logger.info("DragmeCog loaded and commands synced.")
