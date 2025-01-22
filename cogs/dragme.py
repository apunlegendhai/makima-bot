# import discord
# from discord.ext import commands
# import logging
# import os
# import json

# # Set up logging
# logger = logging.getLogger(__name__)
# logging.basicConfig(level=logging.DEBUG)

# # Global dictionary to store request channels by guild ID
# request_channels = {}

# def load_request_channels():
#     global request_channels
#     if os.path.exists("request_channels.json"):
#         with open("request_channels.json", "r") as f:
#             try:
#                 request_channels = json.load(f)
#                 logger.info("Loaded request channels: %s", request_channels)
#             except json.JSONDecodeError:
#                 logger.warning("Failed to load JSON. Initializing empty.")
#                 request_channels = {}
#     else:
#         request_channels = {}

# def save_request_channels():
#     global request_channels
#     try:
#         with open("request_channels.json", "w") as f:
#             json.dump(request_channels, f, indent=4)
#         logger.info("Saved request channels: %s", request_channels)
#     except IOError as e:
#         logger.error("Failed to save request channels: %s", e)

# class DragmeButtons(discord.ui.View):
#     def __init__(self, target_user, interaction_user, target_voice_channel, request_message=None, timeout=30):
#         super().__init__(timeout=timeout)
#         self.target_user = target_user
#         self.interaction_user = interaction_user
#         self.target_voice_channel = target_voice_channel
#         self.request_message = request_message

#     @discord.ui.button(label="", style=discord.ButtonStyle.green, emoji="<:sukoon_tick:1321088727912808469>")
#     async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
#         if interaction.user != self.target_user:
#             await interaction.response.send_message("You're not authorized to accept this request.", ephemeral=True)
#             return

#         # Check bot permissions
#         permissions = self.target_voice_channel.permissions_for(interaction.guild.me)
#         logger.info(f"Bot permissions in target channel: {permissions}")

#         if not permissions.connect or not permissions.move_members:
#             await interaction.response.send_message("I don't have the necessary permissions to move the user.", ephemeral=True)
#             return

#         try:
#             # Temporarily unlock the channel to allow bot to connect and move user
#             await self.target_voice_channel.set_permissions(interaction.guild.me, connect=True)

#             # Move the user to the target voice channel
#             await self.interaction_user.move_to(self.target_voice_channel)
#             await interaction.response.send_message(f"<a:sukoon_greendot:1321066597120737321> {self.interaction_user.mention} has been moved to {self.target_voice_channel.name}.")

#             # Lock the channel again after the move
#             await self.target_voice_channel.set_permissions(interaction.guild.me, connect=False)

#         except discord.Forbidden as e:
#             logger.error(f"Forbidden error moving {self.interaction_user} to {self.target_voice_channel}: {e}")
#             await interaction.response.send_message("I don't have permission to move the user to this channel.", ephemeral=True)
#         except discord.HTTPException as e:
#             logger.error(f"HTTP error moving {self.interaction_user} to {self.target_voice_channel}: {e}")
#             await interaction.response.send_message("There was an error processing the move.", ephemeral=True)
#         except Exception as e:
#             logger.error(f"Unexpected error moving {self.interaction_user} to {self.target_voice_channel}: {e}")
#             await interaction.response.send_message("Error moving user.")

#     @discord.ui.button(label="", style=discord.ButtonStyle.red, emoji="<:sukoon_cross:1321088770845708288>")
#     async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
#         if interaction.user != self.target_user:
#             await interaction.response.send_message("You're not authorized to reject this request.", ephemeral=True)
#             return

#         await interaction.response.send_message(f"<a:sukoon_reddot:1321066529436991539> {self.interaction_user.mention}'s request has been rejected.")

#         if self.request_message:
#             await self.request_message.delete()

#     async def on_timeout(self):
#         """Handles the timeout of the request."""
#         if self.request_message:
#             try:
#                 # Clear the buttons without leaving any timeout message
#                 self.clear_items()
#                 await self.request_message.edit(view=self)
#             except discord.errors.NotFound:
#                 logger.warning("Request message not found during timeout.")
#             except discord.errors.Forbidden:
#                 logger.warning("Bot doesn't have permission to edit the request message.")

#         # Disable all buttons on timeout (no extra messages are sent)
#         self.clear_items()

# class DragmeCog(commands.Cog):
#     def __init__(self, bot):
#         self.bot = bot
#         load_request_channels()
#         logger.info("DragmeCog initialized.")

#     async def check_permissions(self, interaction):
#         # Check for additional permissions
#         bot_permissions = interaction.guild.me.guild_permissions
#         required_permissions = ["move_members", "connect", "manage_channels"]
#         missing_permissions = [perm for perm in required_permissions if not getattr(bot_permissions, perm)]

#         if missing_permissions:
#             await interaction.response.send_message(
#                 f"I don't have the following permissions: {', '.join(missing_permissions)}.",
#                 ephemeral=True
#             )
#             return False
#         return True

#     @commands.cooldown(1, 60, commands.BucketType.user)
#     @discord.app_commands.command(name="dragmee", description="Request to be dragged into a voice channel.")
#     async def dragme(self, interaction: discord.Interaction, target_user: discord.Member):
#         request_channel_id = request_channels.get(str(interaction.guild.id))

#         if request_channel_id is None or interaction.channel.id != int(request_channel_id):
#             await interaction.response.send_message("This command can only be used in the drag-requests channel.", ephemeral=True)
#             return

#         if not await self.check_permissions(interaction):
#             return

#         if interaction.user.voice is None:
#             await interaction.response.send_message(f"{interaction.user.mention}, you need to be in a voice channel to use this command.", ephemeral=True)
#             return

#         if target_user.voice is None:
#             await interaction.response.send_message(f"{target_user.mention} is not in a voice channel.", ephemeral=True)
#             return

#         target_voice_channel = target_user.voice.channel

#         if interaction.user.voice.channel == target_voice_channel:
#             await interaction.response.send_message(f"{interaction.user.mention}, you're already in {target_user.mention}'s voice channel.", ephemeral=True)
#             return

#         await interaction.response.send_message(f"Request to join {target_user.mention}'s voice channel sent.", ephemeral=True)

#         # Send request message with buttons
#         view = DragmeButtons(target_user, interaction.user, target_voice_channel)
#         request_message = await interaction.channel.send(f"{target_user.mention}, {interaction.user.mention} wants to join your voice channel.", view=view, delete_after=30)
#         view.request_message = request_message

#     @discord.app_commands.command(name="setup", description="Set up a channel to receive dragme requests.")
#     @discord.app_commands.default_permissions(administrator=True)  # Ensures only admins can see and use this command
#     async def setup(self, interaction: discord.Interaction):
#         guild_id = str(interaction.guild.id)

#         # Check if a request channel already exists for this guild
#         if guild_id in request_channels:
#             existing_channel_id = request_channels[guild_id]
#             existing_channel = interaction.guild.get_channel(int(existing_channel_id))
#             if existing_channel is None:
#                 logger.warning(f"Request channel {existing_channel_id} not found. Removing from saved data.")
#                 del request_channels[guild_id]
#                 save_request_channels()
#                 load_request_channels()  # Reload the channels after removal
#                 await interaction.response.send_message(embed=discord.Embed(
#                     title="Error",
#                     description="The previous request channel is missing or deleted. A new one will be created.",
#                     color=discord.Color.red()
#                 ), ephemeral=True)
#                 return
#             else:
#                 await interaction.response.send_message(embed=discord.Embed(
#                     title="Error",
#                     description=f"A request channel is already set up: {existing_channel.mention}",
#                     color=discord.Color.red()
#                 ), ephemeral=True)
#                 return

#         if not interaction.guild.me.guild_permissions.manage_channels:
#             await interaction.response.send_message(embed=discord.Embed(
#                 title="Error",
#                 description="I do not have permission to manage channels.",
#                 color=discord.Color.red()
#             ), ephemeral=True)
#             return

#         try:
#             request_channel = await interaction.guild.create_text_channel("drag-requests")
#             request_channels[guild_id] = str(request_channel.id)
#             save_request_channels()
#             await interaction.response.send_message(embed=discord.Embed(
#                 title="Setup Complete",
#                 description=f"Request channel {request_channel.mention} has been created successfully!",
#                 color=discord.Color.green()
#             ))
#         except discord.Forbidden as e:
#             await interaction.response.send_message(embed=discord.Embed(
#                 title="Error",
#                 description=f"I don't have permission to create a channel: {e}",
#                 color=discord.Color.red()
#             ), ephemeral=True)
#         except discord.HTTPException as e:
#             await interaction.response.send_message(embed=discord.Embed(
#                 title="Error",
#                 description=f"Failed to create the request channel due to an HTTP error: {e}",
#                 color=discord.Color.red()
#             ), ephemeral=True)
#         except Exception as e:
#             await interaction.response.send_message(embed=discord.Embed(
#                 title="Unexpected Error",
#                 description=f"An error occurred: {e}",
#                 color=discord.Color.red()
#             ), ephemeral=True)

#     @dragme.error
#     async def dragme_error(self, interaction: discord.Interaction, error: Exception):
#         if isinstance(error, commands.CommandOnCooldown):
#             retry_after = error.retry_after
#             await interaction.response.send_message(
#                 f"Please wait {retry_after:.2f} seconds before using this command again.",
#                 ephemeral=True
#             )
#         else:
#             logger.error(f"Unexpected error occurred: {error}", exc_info=True)
#             await interaction.response.send_message(f"An unexpected error occurred: {error}", ephemeral=True)

#     @setup.error
#     async def setup_error(self, interaction: discord.Interaction, error: Exception):
#         if isinstance(error, commands.MissingPermissions):
#             await interaction.response.send_message("You don't have permission to set up the request channel.", ephemeral=True)
#         else:
#             logger.error(f"Unexpected error occurred in setup: {error}", exc_info=True)
#             await interaction.response.send_message(f"An unexpected error occurred: {error}", ephemeral=True)

# # Ensure cog is set up correctly
# async def setup(bot):
#     await bot.add_cog(DragmeCog(bot))
#     await bot.tree.sync()
#     logger.info("DragmeCog and commands synced.")
