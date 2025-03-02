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
    "Thanks for being part of Sukoon's story. There's always room for another chapter!",
    "Until we meet again at Sukoon, take care and know you're welcome back anytime!"
]

class SukoonInviteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # Persistent view with no timeout
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
            logger.info("Successfully connected to MongoDB")
        except Exception as e:
            logger.error(f"Error connecting to MongoDB: {e}")
            raise

    def __del__(self):
        if hasattr(self, 'client'):
            self.client.close()
        logger.info("Cleaning up FarewellCog instance")

    def _can_send_message(self, user_id: int) -> bool:
        try:
            if self.sent_messages.find_one({'user_id': user_id}):
                logger.info(f"User {user_id} already received a farewell message (database check)")
                return False

            self.sent_messages.insert_one({
                'user_id': user_id,
                'timestamp': datetime.utcnow()
            })
            logger.info(f"Recording new farewell message for user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Error in _can_send_message: {e}")
            return False

    def _record_message(self, success: bool, error_type: str = "unknown"):
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

            # Update daily stats
            self.analytics.update_one(
                {'date': today},
                update,
                upsert=True
            )

            # Update overall stats
            self.analytics.update_one(
                {'_id': 'overall_stats'},
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

    async def send_farewell_message(self, user: discord.User) -> bool:
        logger.info(f"Processing farewell for {user.name} (ID: {user.id})")
        if not self._can_send_message(user.id):
            logger.info(f"Skipping farewell for {user.name} - already sent")
            return False

        try:
            message = (
                f"{BORDER}\n\n"
                f"Dear {user.mention},\n\n"
                f"{random.choice(farewell_messages)}\n\n"
                f"~Sukoon ♡\n\n"
                f"{BORDER}"
            )
            await user.send(message, view=SukoonInviteView())
            logger.info(f"Successfully sent farewell to {user.name} (ID: {user.id})")
            self._record_message(success=True)
            return True

        except discord.Forbidden:
            error_msg = f"Cannot send DM to {user.name} - DMs closed"
            logger.warning(error_msg)
            self._record_message(success=False, error_type="dm_closed")

            # Attempt to notify via the system channel if possible
            if hasattr(user, "guild") and user.guild and user.guild.system_channel:
                try:
                    await user.guild.system_channel.send(
                        f"⚠️ Unable to send farewell message to {user.mention} as their DMs are closed.",
                        allowed_mentions=discord.AllowedMentions.none()
                    )
                except Exception as e:
                    logger.error(f"Failed to send notification to system channel: {e}")
            return False

        except Exception as e:
            logger.error(f"Error sending farewell to {user.name}: {e}")
            self._record_message(success=False, error_type="other")
            return False

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        logger.info(f"Member leave event for {member.name} (ID: {member.id})")
        success = await self.send_farewell_message(member)
        if not success:
            logger.warning(f"Failed to send farewell to {member.name}")

    @app_commands.command(
        name='farewell-stats',
        description='View statistics about farewell messages sent by the bot'
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def farewell_stats(self, interaction: discord.Interaction):
        try:
            today = datetime.utcnow().strftime('%Y-%m-%d')
            today_stats = self.analytics.find_one({'date': today}) or {'total': 0, 'successful': 0, 'failed': 0, 'errors': {}}
            overall_stats = self.analytics.find_one({'_id': 'overall_stats'}) or {
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
                value=f"Total Messages: {total_messages:,}\n"
                      f"Success Rate: {success_rate:.1f}%",
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
                embed.add_field(
                    name="Error Statistics",
                    value=error_text,
                    inline=False
                )

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
        description='Test the farewell message by sending it to yourself'
    )
    async def test_farewell(self, interaction: discord.Interaction):
        try:
            # Defer the response to allow time for DM operations
            await interaction.response.defer(ephemeral=True)
            success = await self.send_farewell_message(interaction.user)
            if success:
                await interaction.followup.send("✅ Test farewell message sent! Check your DMs.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Failed to send test message. Make sure your DMs are open.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error in test-farewell command: {e}")
            await interaction.followup.send("❌ An error occurred while testing the farewell message.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(FarewellCog(bot))
