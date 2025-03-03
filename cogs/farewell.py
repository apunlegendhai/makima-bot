import discord
from discord.ext import commands
from discord import app_commands
import random
from datetime import datetime
import os
import logging
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
MONGO_URL = os.getenv('MONGO_URL')
if not MONGO_URL:
    raise ValueError("MONGO_URL environment variable is not set.")

# Define a border for messages
BORDER = "✧ ⋆ ┈ ┈ ┈ ┈ ┈ ┈ ┈ ⋆ ✧"

# Setup logging
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/farewell.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Define 50 farewell messages
farewell_messages = [
    "We at Sukoon will miss having you around! Our door is always open if you'd like to return.",
    "Your presence made Sukoon brighter. Hope to see you again soon!",
    "Every goodbye at Sukoon is just a 'see you later'. We'll be here when you're ready to return!",
    "Thanks for being part of Sukoon's journey. You're always welcome back!",
    "Your contributions enriched our community. We'll miss you dearly!",
    "Although it's farewell for now, remember that you'll always have a home at Sukoon.",
    "Saying goodbye is never easy. Your absence will be felt here.",
    "You brought joy to our community. We look forward to your return someday.",
    "Your presence was a gift. We hope our paths cross again soon.",
    "The memories you left behind will always be cherished at Sukoon.",
    "It’s not a goodbye, it’s a see you later. We can’t wait to have you back.",
    "Thank you for being an amazing part of our journey. Farewell until we meet again.",
    "Your spirit made our community stronger. We'll miss your unique energy.",
    "Even as you depart, the memories will always linger here at Sukoon.",
    "Farewell, dear friend. Your legacy will live on in our community.",
    "Goodbye for now. Your contributions have left a lasting mark on Sukoon.",
    "Wishing you all the best on your new adventures. Sukoon will always welcome you back.",
    "Your time with us was treasured. We hope you'll return one day.",
    "You will be missed, but your memories will keep us company.",
    "Take care on your journey. Remember, Sukoon is just a heartbeat away.",
    "We’re sad to see you go, but excited for your future endeavors.",
    "Until we meet again, keep the wonderful memories of Sukoon alive in your heart.",
    "Your departure leaves a void, but your spirit remains with us always.",
    "Farewell, and may your future be as bright as your time with us.",
    "It’s hard to say goodbye, but easier knowing you'll return someday.",
    "Thank you for the laughter and love you brought to our community.",
    "Our community won’t be the same without you. Come back soon!",
    "Every ending is a new beginning. We look forward to your return.",
    "Your journey continues beyond these walls. Safe travels, friend.",
    "Parting is such sweet sorrow, but we'll always remember you.",
    "Though you're leaving, your imprint on our hearts will never fade.",
    "Farewell, friend. Our door remains open whenever you wish to return.",
    "We wish you the best on your path ahead. Sukoon will be here waiting.",
    "Goodbye for now, but not forever. We’ll eagerly await your return.",
    "We cherish the moments shared and hope to make more in the future.",
    "Our community shines brighter with you in it. Farewell, until next time.",
    "Your departure may be painful, but the memories are forever.",
    "As you leave, know that you will always be part of the Sukoon family.",
    "Wishing you success and happiness on your next adventure.",
    "Our hearts are heavy, but full of gratitude for your time with us.",
    "Your journey continues elsewhere, but you'll always have a home here.",
    "Every farewell reminds us of how much you meant to us. Until we meet again.",
    "Your time here was a gift. We look forward to your return someday.",
    "Our community misses you already. Hope your path brings you joy.",
    "We bid you farewell with love and the hope of a future reunion.",
    "Your unique spirit enriched our lives. We eagerly await your return.",
    "Goodbye, and best of luck in all that you do.",
    "Until we meet again, know that you'll always be a part of Sukoon.",
    "We’ll miss your smile, your energy, and your kind heart.",
    "Farewell, dear friend. Our community will always welcome you back with open arms."
]

# A persistent view for the invite button
class SukoonInviteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            style=discord.ButtonStyle.link,
            label="Return to Sukoon",
            url="https://discord.gg/sukoon",
            emoji="🌸"
        ))

# Farewell Cog that sends a DM every time a member leaves
class FarewellCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Add the persistent view so that buttons remain active
        self.bot.add_view(SukoonInviteView())
        logger.info("Added persistent invite view")
        try:
            self.client = MongoClient(MONGO_URL)
            self.db = self.client.sukoon_bot
            self.analytics = self.db.farewell_analytics
            self.configs = self.db.guild_configs
            logger.info("Successfully connected to MongoDB")
        except Exception as e:
            logger.error(f"Error connecting to MongoDB: {e}")
            raise

    def __del__(self):
        if hasattr(self, 'client'):
            self.client.close()
        logger.info("Cleaning up FarewellCog instance")

    def is_farewell_enabled(self, guild_id) -> bool:
        doc = self.configs.find_one({'_id': guild_id})
        # Default is False unless explicitly enabled
        return doc.get('farewell_enabled', False) if doc else False

    def _record_message(self, guild_id, success: bool, error_type: str = "unknown"):
        today = datetime.utcnow().strftime('%Y-%m-%d')
        try:
            update = {
                '$inc': {
                    'total': 1,
                    'successful': 1 if success else 0,
                    'failed': 0 if success else 1
                }
            }
            if not success and error_type:
                update['$inc'][f'errors.{error_type}'] = 1

            self.analytics.update_one(
                {'guild_id': guild_id, 'date': today},
                update,
                upsert=True
            )
            self.analytics.update_one(
                {'_id': f'overall_stats_{guild_id}'},
                {
                    '$inc': {
                        'messages_sent': 1,
                        'successful_sends': 1 if success else 0,
                        'failed_sends': 0 if success else 1,
                        f'errors.{error_type}': 1 if error_type else 0
                    },
                    '$set': {
                        'most_recent_send': datetime.utcnow()
                    }
                },
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error recording analytics: {e}")

    async def log_dm_attempt(self, guild_id, user: discord.User, success: bool, error_type: str, details: str):
        config_doc = self.configs.find_one({'_id': guild_id})
        if config_doc and 'log_channel' in config_doc:
            log_channel_id = config_doc['log_channel']
            channel = self.bot.get_channel(log_channel_id)
            if channel is None:
                logger.warning(f"Log channel with ID {log_channel_id} not found for guild {guild_id}.")
                return
            embed = discord.Embed(
                title="Farewell DM Attempt",
                color=discord.Color.green() if success else discord.Color.red(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="User", value=f"{user} ({user.id})", inline=False)
            embed.add_field(name="Status", value="Success" if success else "Failed", inline=True)
            if not success:
                embed.add_field(name="Error Type", value=error_type, inline=True)
            embed.add_field(name="Details", value=details, inline=False)
            embed.set_footer(text=f"Guild ID: {guild_id}")
            try:
                await channel.send(embed=embed)
            except Exception as e:
                logger.error(f"Error sending log embed to channel {log_channel_id} for guild {guild_id}: {e}")

    async def send_farewell_message(self, user: discord.User, guild: discord.Guild = None, force: bool = False) -> bool:
        guild_id = guild.id if guild else (user.guild.id if isinstance(user, discord.Member) else "global")
        if not force and not self.is_farewell_enabled(guild_id):
            logger.info(f"Farewell messages are disabled in guild {guild_id}. Skipping farewell for {user.name}.")
            return False

        logger.info(f"Processing farewell for {user.name} (ID: {user.id}) in guild {guild_id}")
        try:
            message = (
                f"{BORDER}\n\n"
                f"Dear {user.mention},\n\n"
                f"{random.choice(farewell_messages)}\n\n"
                f"~ Sukoon ♡\n\n"
                f"{BORDER}"
            )
            await user.send(message, view=SukoonInviteView())
            logger.info(f"Successfully sent farewell to {user.name} (ID: {user.id}) in guild {guild_id}")
            self._record_message(guild_id, success=True)
            await self.log_dm_attempt(guild_id, user, success=True, error_type="", details="Farewell DM sent successfully.")
            return True
        except discord.Forbidden:
            error_msg = f"Cannot send DM to {user.name} – DMs closed"
            logger.warning(error_msg)
            self._record_message(guild_id, success=False, error_type="dm_closed")
            await self.log_dm_attempt(guild_id, user, success=False, error_type="DMs Closed", details=error_msg)
            return False
        except Exception as e:
            logger.error(f"Error sending farewell to {user.name}: {e}")
            self._record_message(guild_id, success=False, error_type="other")
            await self.log_dm_attempt(guild_id, user, success=False, error_type="Other", details=str(e))
            return False

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild_id = member.guild.id
        if not self.is_farewell_enabled(guild_id):
            logger.info(f"Farewell messages are disabled in guild {guild_id}. No farewell sent for {member.name}.")
            return
        logger.info(f"Member leave event for {member.name} (ID: {member.id}) in guild {guild_id}")
        success = await self.send_farewell_message(member, guild=member.guild)
        if not success:
            logger.warning(f"Failed to send farewell to {member.name} in guild {guild_id}")

    @app_commands.command(
        name='farewell',
        description='Enable or disable farewell messages in this server'
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def farewell(self, interaction: discord.Interaction, enabled: bool):
        guild_id = interaction.guild.id if interaction.guild else "global"
        try:
            self.configs.update_one(
                {'_id': guild_id},
                {'$set': {'farewell_enabled': enabled}},
                upsert=True
            )
            status = "enabled" if enabled else "disabled"
            await interaction.response.send_message(
                f"Farewell messages have been {status} in this server.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error updating farewell setting for guild {guild_id}: {e}")
            await interaction.response.send_message("❌ Error updating settings.", ephemeral=True)

    @app_commands.command(
        name='farewell-stats',
        description='View statistics about farewell messages sent by the bot for this guild'
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def farewell_stats(self, interaction: discord.Interaction):
        try:
            today = datetime.utcnow().strftime('%Y-%m-%d')
            guild_id = interaction.guild.id if interaction.guild else "global"
            today_stats = self.analytics.find_one({'guild_id': guild_id, 'date': today}) or {
                'total': 0, 'successful': 0, 'failed': 0, 'errors': {}
            }
            overall_stats = self.analytics.find_one({'_id': f'overall_stats_{guild_id}'}) or {
                'messages_sent': 0,
                'successful_sends': 0,
                'failed_sends': 0,
                'errors': {}
            }

            total_messages = overall_stats.get('messages_sent', 0)
            success_rate = (overall_stats.get('successful_sends', 0) / total_messages * 100
                            if total_messages > 0 else 0)

            embed = discord.Embed(
                title="📊 Farewell Message Statistics",
                color=discord.Color.blue(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="Overall Statistics",
                value=f"Total Messages: {total_messages:,}\nSuccess Rate: {success_rate:.1f}%",
                inline=False
            )
            embed.add_field(
                name="Today's Statistics",
                value=(
                    f"Total: {today_stats.get('total', 0):,}\n"
                    f"Successful: {today_stats.get('successful', 0):,}\n"
                    f"Failed: {today_stats.get('failed', 0):,}"
                ),
                inline=False
            )
            if overall_stats.get('errors'):
                error_stats = overall_stats['errors']
                error_text = "Error Breakdown:\n"
                if error_stats.get('dm_closed'):
                    error_text += f"• DMs Closed: {error_stats['dm_closed']:,}\n"
                if error_stats.get('other'):
                    error_text += f"• Other Errors: {error_stats['other']:,}"
                embed.add_field(name="Error Statistics", value=error_text, inline=False)
            embed.set_footer(text="Stats are updated in real-time")
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logger.error(f"Error fetching stats: {e}")
            await interaction.response.send_message("❌ Error fetching statistics.", ephemeral=True)

    @farewell_stats.error
    async def farewell_stats_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need administrator permissions to use this command.", ephemeral=True)
        else:
            logger.error(f"Error in farewell-stats: {error}")
            await interaction.response.send_message("❌ An error occurred while executing the command.", ephemeral=True)

    @app_commands.command(
        name='test-farewell',
        description='Test the farewell message by sending it to yourself (if enabled)'
    )
    async def test_farewell(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            guild_id = interaction.guild.id if interaction.guild else "global"
            if not self.is_farewell_enabled(guild_id):
                await interaction.followup.send("Farewell messages are disabled in this server. Enable them with /farewell true.", ephemeral=True)
                return
            success = await self.send_farewell_message(interaction.user, guild=interaction.guild, force=True)
            if success:
                await interaction.followup.send("✅ Test farewell message sent! Check your DMs.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Failed to send test message. Make sure your DMs are open.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in test-farewell command: {e}")
            await interaction.followup.send("❌ An error occurred while testing the farewell message.", ephemeral=True)

    @app_commands.command(
        name='farewell-log',
        description='Set the channel where farewell DM attempts are logged'
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def farewell_log(self, interaction: discord.Interaction, channel: discord.TextChannel):
        guild_id = interaction.guild.id if interaction.guild else "global"
        try:
            self.configs.update_one(
                {'_id': guild_id},
                {'$set': {'log_channel': channel.id}},
                upsert=True
            )
            await interaction.response.send_message(
                f"Log channel has been set to {channel.mention}.", ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error setting log channel for guild {guild_id}: {e}")
            await interaction.response.send_message("❌ Error setting log channel.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(FarewellCog(bot))
