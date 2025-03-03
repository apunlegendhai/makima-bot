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

# Border style
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

farewell_messages = [
    "We at Sukoon will miss having you around! Our door is always open if you'd like to return.",
    "Your presence made Sukoon brighter. Hope to see you again soon!",
    "Every goodbye at Sukoon is just a 'see you later'. We'll be here when you're ready to return!",
    "Thanks for being part of Sukoon's journey. You're always welcome back!",
    "Your contributions to Sukoon were valuable. Hope our paths cross again!",
    "We understand that paths sometimes diverge. Know you can always find your way back to Sukoon.",
    "Wishing you the best on your journey! Sukoon will welcome you back anytime.",
    "Your unique perspective will be missed in Sukoon. Don't be a stranger!",
    "Sometimes we need a break, and that's okay. Sukoon will keep your spot warm!",
    "Sukoon won't be quite the same without you. Hope to catch you later!",
    "May your adventures be exciting! Remember, you've got friends here at Sukoon when you return.",
    "Your departure leaves a space in Sukoon. Feel free to fill it again anytime!",
    "Today's goodbye doesn't have to be forever. Looking forward to your return to Sukoon!",
    "Thanks for all the memories at Sukoon! Here's hoping we make more in the future.",
    "Your presence made Sukoon better. The door remains open for your return!",
    "We at Sukoon respect your decision to leave, but know you're always welcome back!",
    "Missing you already! Don't forget about Sukoon on your journey.",
    "Every member matters to Sukoon, and you're no exception. Hope to see you again!",
    "Your time here at Sukoon was appreciated. Remember, good friends are always welcome back.",
    "Wherever your path leads, remember you've got friends here at Sukoon!",
    "Sometimes the best journeys include a return home. Sukoon will be here!",
    "Thank you for being part of Sukoon. Come back anytime!",
    "Your contributions made a difference at Sukoon. Hope you'll return to make more!",
    "Sad to see you go, but excited for your potential return to Sukoon!",
    "Our Sukoon community was better with you in it. The door's always open!",
    "Wishing you the best on your new path. Don't forget to visit Sukoon!",
    "You'll be missed more than you know at Sukoon. Hope to see you again soon!",
    "Thanks for sharing your time with Sukoon. Remember, you're always welcome back!",
    "Your departure leaves a void in Sukoon, but we hope it's not permanent!",
    "May your journey be wonderful, but know you can always return home to Sukoon.",
    "We enjoyed having you here at Sukoon! Don't make this goodbye permanent.",
    "Sukoon was brighter with you in it. Hope you'll light it up again soon!",
    "Remember all the good times at Sukoon? There's more to be had when you return!",
    "Your chapter at Sukoon might be paused, but it doesn't have to be over!",
    "We'll keep your memories alive at Sukoon, hoping you'll come make new ones!",
    "Sometimes the best decision is taking a break. Sukoon will welcome you back anytime!",
    "Your presence in Sukoon will be missed! Don't forget about your friends here.",
    "Sukoon community won't be the same without you. Hope it's just temporary!",
    "Thanks for all you brought to Sukoon. Come back and bring more!",
    "We understand needing change, but remember where your Sukoon family is!",
    "Your spot in Sukoon will always be here for you.",
    "Hoping this goodbye is more of a 'see you later' from Sukoon!",
    "We'll miss your unique energy in Sukoon! Bring it back sometime!",
    "Sukoon's door will always be open for your return.",
    "Your journey may lead elsewhere, but remember where Sukoon awaits!",
    "We at Sukoon respect your choice to leave, but hope you'll choose to return!",
    "May new adventures bring you joy, and Sukoon welcome you back!",
    "This farewell from Sukoon doesn't have to be final. We'll be here!",
    "Thanks for being part of Sukoon's story. There's always room for another chapter!"
]

class SukoonInviteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            style=discord.ButtonStyle.link,
            label="Return to Sukoon",
            url="https://discord.gg/sukoon",
            emoji="🌸"
        ))

class FarewellCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.add_view(SukoonInviteView())
        logger.info("Added persistent invite view")
        try:
            self.client = MongoClient(MONGO_URL)
            self.db = self.client.sukoon_bot
            self.analytics = self.db.farewell_analytics
            self.sent_messages = self.db.sent_messages
            # New collection for guild configuration (farewell enabled/disabled and log channel)
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
        return doc.get('farewell_enabled', False) if doc else False

    def _can_send_message(self, user: discord.User, guild_id) -> bool:
        try:
            # Check if a farewell message for this user has already been sent in this guild
            if self.sent_messages.find_one({'user_id': user.id, 'guild_id': guild_id}):
                logger.info(f"User {user.id} already received a farewell message in guild {guild_id}")
                return False

            # Record that a farewell message has been sent for this user in this guild
            self.sent_messages.insert_one({
                'user_id': user.id,
                'guild_id': guild_id,
                'timestamp': datetime.utcnow()
            })
            logger.info(f"Recording farewell message for user {user.id} in guild {guild_id}")
            return True

        except Exception as e:
            logger.error(f"Error in _can_send_message: {e}")
            return False

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

            # Update daily stats for this guild
            self.analytics.update_one(
                {'guild_id': guild_id, 'date': today},
                update,
                upsert=True
            )

            # Update overall stats for this guild
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
        """Log the DM attempt in the configured channel using an embed."""
        # Fetch configuration to see if a log channel is set
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
        # Determine the guild ID from the passed guild or from the user (if Member)
        guild_id = None
        if guild:
            guild_id = guild.id
        elif isinstance(user, discord.Member):
            guild_id = user.guild.id
        else:
            logger.warning("No guild information available; using 'global' as guild id.")
            guild_id = "global"

        # Only send farewell if the feature is enabled for this guild (unless forced via test command)
        if not force and not self.is_farewell_enabled(guild_id):
            logger.info(f"Farewell messages are disabled in guild {guild_id}. Skipping farewell for {user.name}.")
            return False

        logger.info(f"Processing farewell for {user.name} (ID: {user.id}) in guild {guild_id}")
        if not force:
            if not self._can_send_message(user, guild_id):
                logger.info(f"Skipping farewell for {user.name} in guild {guild_id} – already sent")
                return False
        else:
            logger.info("Force sending farewell message, bypassing duplicate check.")

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
        # Only process farewell if enabled in this guild
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
        """
        Toggle farewell messages for this server.
        Use enabled=true to turn on farewell messages,
        or enabled=false to turn them off.
        """
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
                value=f"Total: {today_stats.get('total', 0):,}\n"
                      f"Successful: {today_stats.get('successful', 0):,}\n"
                      f"Failed: {today_stats.get('failed', 0):,}",
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
            # Check if farewell messages are enabled before testing
            if not self.is_farewell_enabled(guild_id):
                await interaction.followup.send("Farewell messages are disabled in this server. Enable them with /farewell true.", ephemeral=True)
                return
            # Pass the current guild and force the message to bypass duplicate check during testing
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
