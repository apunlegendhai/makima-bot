# confessions.py
import discord
from discord import app_commands
from discord.ext import commands
import json
import os
from typing import Optional
from datetime import datetime
import aiohttp
import io

class ConfigManager:
    def __init__(self, filename='confession_settings.json'):
        self.filename = filename
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not os.path.exists(self.filename):
            with open(self.filename, 'w') as f:
                json.dump({}, f)

    def load_settings(self):
        with open(self.filename, 'r') as f:
            return json.load(f)

    def save_settings(self, settings):
        with open(self.filename, 'w') as f:
            json.dump(settings, f, indent=4)

    def get_guild_settings(self, guild_id: str):
        settings = self.load_settings()
        return settings.get(str(guild_id), {})

    def update_guild_settings(self, guild_id: str, new_settings: dict):
        settings = self.load_settings()
        if str(guild_id) not in settings:
            settings[str(guild_id)] = {}
        settings[str(guild_id)].update(new_settings)
        self.save_settings(settings)

class ConfessionModal(discord.ui.Modal):
    def __init__(self, bot, is_reply=False, original_message_id=None):
        super().__init__(title="Submit a Confession" if not is_reply else "Reply to Confession")
        self.bot = bot
        self.is_reply = is_reply
        self.original_message_id = original_message_id

        self.title_input = discord.ui.TextInput(
            label="Title (Optional)",
            style=discord.TextStyle.short,
            placeholder="Enter a title for your confession (optional)",
            required=False,
            max_length=100
        )

        self.confession_input = discord.ui.TextInput(
            label="Your Message",
            style=discord.TextStyle.paragraph,
            placeholder="Type your message here...",
            required=True,
            max_length=2000
        )

        self.attachment_url = discord.ui.TextInput(
            label="Attachment URL (Optional)",
            style=discord.TextStyle.short,
            placeholder="Paste an image URL here (optional)",
            required=False,
            max_length=200
        )

        self.add_item(self.title_input)
        self.add_item(self.confession_input)
        self.add_item(self.attachment_url)

    async def on_submit(self, interaction: discord.Interaction):
        config = ConfigManager()
        guild_settings = config.get_guild_settings(str(interaction.guild_id))

        confession_channel_id = guild_settings.get('confession_channel')
        log_channel_id = guild_settings.get('log_channel')
        banned_users = guild_settings.get('banned_users', [])

        if str(interaction.user.id) in banned_users:
            await interaction.response.send_message("You are banned from using confessions.", ephemeral=True)
            return

        if not confession_channel_id:
            await interaction.response.send_message("Confession channel has not been set up!", ephemeral=True)
            return

        confession_channel = interaction.guild.get_channel(int(confession_channel_id))
        if not confession_channel:
            await interaction.response.send_message("Confession channel not found!", ephemeral=True)
            return

        # Download attachment if provided
        file = None
        if self.attachment_url.value:
            file = await self.download_attachment(self.attachment_url.value)

        # Create embed
        embed = discord.Embed(
            title=self.title_input.value if self.title_input.value else None,
            description=self.confession_input.value,
            color=discord.Color.from_str(guild_settings.get('embed_color', '#2f3136')),
            timestamp=discord.utils.utcnow()
        )

        if file:
            embed.set_image(url="attachment://attachment.png")

        if not self.is_reply:
            view = ConfessionView(self.bot)
            message = await confession_channel.send(embed=embed, view=view, file=file)

            # Save message data for verification
            if 'message_data' not in guild_settings:
                guild_settings['message_data'] = {}
            guild_settings['message_data'][str(message.id)] = {
                'author_id': str(interaction.user.id),  # Ensure user ID is stored as a string
                'timestamp': datetime.utcnow().isoformat()
            }
            config.update_guild_settings(str(interaction.guild_id), guild_settings)

        else:
            # Handle reply in thread
            original_message = await confession_channel.fetch_message(self.original_message_id)
            thread = original_message.thread
            if not thread:
                thread = await original_message.create_thread(name="Confession Discussion")
            await thread.send(embed=embed, file=file)

        # Log the confession
        if log_channel_id:
            log_channel = interaction.guild.get_channel(int(log_channel_id))
            if log_channel:
                log_embed = discord.Embed(
                    title="New Confession Log",
                    description=f"**Author:** {interaction.user} (ID: {interaction.user.id})\n"
                              f"**Type:** {'Reply' if self.is_reply else 'Original confession'}\n"
                              f"**Title:** {self.title_input.value or 'None'}\n"
                              f"**Content:**\n{self.confession_input.value}",
                    color=discord.Color.blue(),
                    timestamp=discord.utils.utcnow()
                )
                if file:
                    log_embed.add_field(name="Attachment", value="Image included", inline=False)
                await log_channel.send(embed=log_embed)

        await interaction.response.send_message("Your message has been submitted!", ephemeral=True)
        
class ConfessionView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Reply", style=discord.ButtonStyle.secondary, custom_id="confession_reply")
    async def reply(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ConfessionModal(self.bot, is_reply=True, original_message_id=interaction.message.id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Report", style=discord.ButtonStyle.danger, custom_id="confession_report")
    async def report(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = ConfigManager()
        guild_settings = config.get_guild_settings(str(interaction.guild_id))
        log_channel_id = guild_settings.get('log_channel')

        if log_channel_id:
            log_channel = interaction.guild.get_channel(int(log_channel_id))
            if log_channel:
                report_embed = discord.Embed(
                    title="Confession Report",
                    description=f"**Reported Message ID:** {interaction.message.id}\n"
                              f"**Reported by:** {interaction.user} (ID: {interaction.user.id})\n"
                              f"**Original Content:**\n{interaction.message.embeds[0].description}",
                    color=discord.Color.red(),
                    timestamp=discord.utils.utcnow()
                )
                await log_channel.send(embed=report_embed)

        await interaction.response.send_message("Report submitted to moderators.", ephemeral=True)

class Confessions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = ConfigManager()

    @app_commands.command(name="confess")
    async def confess(self, interaction: discord.Interaction):
        """Submit an anonymous confession"""
        modal = ConfessionModal(self.bot)
        await interaction.response.send_modal(modal)

    @app_commands.command(name="setconfessionchannel")
    @app_commands.default_permissions(administrator=True)
    async def set_confession_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Set the channel for confessions"""
        self.config.update_guild_settings(str(interaction.guild_id), {'confession_channel': channel.id})
        await interaction.response.send_message(f"Confession channel set to {channel.mention}", ephemeral=True)

    @app_commands.command(name="setlogchannel")
    @app_commands.default_permissions(administrator=True)
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Set the channel for confession logs"""
        self.config.update_guild_settings(str(interaction.guild_id), {'log_channel': channel.id})
        await interaction.response.send_message(f"Log channel set to {channel.mention}", ephemeral=True)

    @app_commands.command(name="banuser")
    @app_commands.default_permissions(administrator=True)
    async def ban_user(self, interaction: discord.Interaction, user: discord.User, action: str = "ban"):
        """Ban or unban a user from using confessions"""
        guild_settings = self.config.get_guild_settings(str(interaction.guild_id))

        if 'banned_users' not in guild_settings:
            guild_settings['banned_users'] = []

        if action.lower() == "ban":
            if str(user.id) not in guild_settings['banned_users']:
                guild_settings['banned_users'].append(str(user.id))
            message = f"{user} has been banned from using confessions."
        else:
            if str(user.id) in guild_settings['banned_users']:
                guild_settings['banned_users'].remove(str(user.id))
            message = f"{user} has been unbanned from using confessions."

        self.config.update_guild_settings(str(interaction.guild_id), guild_settings)
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="confessionstats")
    @app_commands.default_permissions(administrator=True)
    async def confession_stats(self, interaction: discord.Interaction):
        """View confession statistics"""
        guild_settings = self.config.get_guild_settings(str(interaction.guild_id))
        message_data = guild_settings.get('message_data', {})

        total_confessions = len(message_data)
        unique_users = len(set(data['author_id'] for data in message_data.values()))

        embed = discord.Embed(
            title="Confession Statistics",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="Total Confessions", value=str(total_confessions), inline=True)
        embed.add_field(name="Unique Users", value=str(unique_users), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="setconfessioncolor")
    @app_commands.default_permissions(administrator=True)
    async def set_confession_color(self, interaction: discord.Interaction, color: str):
        """Set the color for confession embeds (hex code)"""
        try:
            # Validate color format
            if not color.startswith('#'):
                color = f'#{color}'
            discord.Color.from_str(color)
            self.config.update_guild_settings(str(interaction.guild_id), {'embed_color': color})
            await interaction.response.send_message(f"Confession embed color set to {color}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("Invalid color format. Please use hex color code (e.g., #FF0000)", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Confessions(bot))