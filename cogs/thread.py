import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
from datetime import datetime, timedelta

class ThreadCreatorCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config_file = "thread_config.json"
        self.guild_configs = {}
        self.cooldowns = {}
        self.load_config()

    def load_config(self):
        """Load configuration from JSON."""
        try:
            with open(self.config_file, "r") as f:
                self.guild_configs = json.load(f)
        except FileNotFoundError:
            self.guild_configs = {}

    def save_config(self):
        """Save configuration to JSON."""
        with open(self.config_file, "w") as f:
            json.dump(self.guild_configs, f, indent=4)

    def is_on_cooldown(self, guild_id, user_id, cooldown):
        """Check if a user is on cooldown."""
        now = datetime.utcnow().timestamp()
        last_used = self.cooldowns.get((guild_id, user_id), 0)
        if now - last_used < cooldown:
            return True, cooldown - (now - last_used)
        self.cooldowns[(guild_id, user_id)] = now
        return False, 0

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle thread creation."""
        if message.author.bot or not message.guild:
            return

        guild_id, channel_id = str(message.guild.id), str(message.channel.id)
        config = self.guild_configs.get(guild_id, {}).get(channel_id)
        if not config or not message.attachments:
            return

        cooldown = config.get("cooldown", 30)
        on_cooldown, remaining = self.is_on_cooldown(guild_id, message.author.id, cooldown)
        if on_cooldown:
            await message.channel.send(f"⏳ Cooldown active. Try again in {remaining:.0f}s.", delete_after=5)
            return

        thread_name = message.content[:50] or f"Thread by {message.author.display_name}"
        thread = await message.create_thread(name=thread_name, auto_archive_duration=1440)
        await thread.send(f"📎 Thread created by {message.author.mention}", delete_after=10)

    @app_commands.command(name="thread_channel", description="Configure a channel for thread creation.")
    async def configure_channel(self, interaction: discord.Interaction, channel: discord.TextChannel, cooldown: int = 30):
        """Set up thread creation for a channel."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return

        guild_id, channel_id = str(interaction.guild.id), str(channel.id)
        self.guild_configs.setdefault(guild_id, {})[channel_id] = {"cooldown": cooldown}
        self.save_config()
        await interaction.response.send_message(f"✅ Thread creation enabled in {channel.mention} with {cooldown}s cooldown.", ephemeral=True)

    @app_commands.command(name="thread_status", description="Check thread settings for this server.")
    async def thread_status(self, interaction: discord.Interaction):
        """Show all configured channels."""
        guild_id = str(interaction.guild.id)
        config = self.guild_configs.get(guild_id, {})
        if not config:
            await interaction.response.send_message("❌ No channels configured.", ephemeral=True)
            return

        embed = discord.Embed(title="📊 Thread Configuration", color=discord.Color.blue())
        for channel_id, settings in config.items():
            channel = interaction.guild.get_channel(int(channel_id))
            if channel:
                embed.add_field(name=f"#{channel.name}", value=f"Cooldown: {settings['cooldown']}s", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(ThreadCreatorCog(bot))
