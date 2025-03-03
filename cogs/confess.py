import discord
from discord import app_commands
from discord.ext import commands
import os
import asyncio
from typing import Optional, Any, Dict, Tuple
from datetime import datetime, timedelta
import logging
import motor.motor_asyncio
from pymongo import ReturnDocument
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)

# Load environment variables
load_dotenv()

class ConfigManager:
    """
    Manages configuration and confession data in MongoDB using Motor for async operations,
    with in-memory caching for guild settings.
    """
    def __init__(self) -> None:
        self.client = motor.motor_asyncio.AsyncIOMotorClient(os.getenv('MONGO_URL'))
        self.db = self.client['confessions']
        self.guild_collection = self.db['guild_settings']
        self.confessions_collection = self.db['confessions']
        # Cache: {guild_id: (settings, timestamp)}
        self.cache: Dict[str, Tuple[Dict[str, Any], datetime]] = {}
        self.cache_expiration = timedelta(seconds=60)

    async def get_guild_settings(self, guild_id: str) -> Dict[str, Any]:
        """Retrieve guild settings from cache or DB."""
        now = datetime.utcnow()
        if guild_id in self.cache:
            settings, timestamp = self.cache[guild_id]
            if now - timestamp < self.cache_expiration:
                return settings

        try:
            guild_settings = await self.guild_collection.find_one({"guild_id": guild_id})
            guild_settings = guild_settings or {}
            self.cache[guild_id] = (guild_settings, now)
            return guild_settings
        except Exception as e:
            logging.error(f"Error fetching guild settings for {guild_id}: {e}")
            return {}

    async def update_guild_settings(self, guild_id: str, new_settings: Dict[str, Any]) -> None:
        """Update guild settings in DB and cache. Ensures guild_id is stored."""
        try:
            new_settings['guild_id'] = guild_id
            await self.guild_collection.update_one(
                {"guild_id": guild_id},
                {"$set": new_settings},
                upsert=True
            )
            self.cache[guild_id] = (new_settings, datetime.utcnow())
        except Exception as e:
            logging.error(f"Error updating guild settings for {guild_id}: {e}")

    async def add_confession(
        self, guild_id: str, message_id: str, author_id: str,
        title: Optional[str], content: str
    ) -> None:
        """Add a confession to the DB."""
        try:
            confession_data = {
                "guild_id": guild_id,
                "message_id": message_id,
                "author_id": str(author_id),
                "title": title,
                "content": content,
                "timestamp": datetime.utcnow().isoformat(),
                "reports": 0,
                "reporters": []  # Prevent duplicate reports
            }
            await self.confessions_collection.insert_one(confession_data)
        except Exception as e:
            logging.error(f"Error adding confession for guild {guild_id}: {e}")

    async def add_confession_report(self, guild_id: str, message_id: str, reporter_id: str) -> Tuple[int, bool]:
        """
        Atomically add a report (if reporter hasn't already reported).
        Returns (new_report_count, duplicate).
        """
        try:
            result = await self.confessions_collection.find_one_and_update(
                {
                    "guild_id": guild_id,
                    "message_id": message_id,
                    "reporters": {"$ne": reporter_id}
                },
                {
                    "$addToSet": {"reporters": reporter_id},
                    "$inc": {"reports": 1}
                },
                return_document=ReturnDocument.AFTER
            )
            if result is None:
                confession = await self.confessions_collection.find_one(
                    {"guild_id": guild_id, "message_id": message_id}
                )
                count = confession.get("reports", 0) if confession else 0
                return (count, True)
            else:
                return (result.get("reports", 0), False)
        except Exception as e:
            logging.error(f"Error adding report for confession {message_id} in guild {guild_id}: {e}")
            return (0, False)

    async def get_confession(self, guild_id: str, message_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a confession document."""
        try:
            return await self.confessions_collection.find_one({"guild_id": guild_id, "message_id": message_id})
        except Exception as e:
            logging.error(f"Error retrieving confession {message_id} for guild {guild_id}: {e}")
            return None

# Create a global config manager instance
CONFIG_MANAGER = ConfigManager()

class ConfessionView(discord.ui.View):
    """
    Persistent view for confession messages with Reply and Report buttons.
    """
    def __init__(self, timeout: Optional[float] = None) -> None:
        super().__init__(timeout=timeout)

    @discord.ui.button(label="Reply", style=discord.ButtonStyle.secondary, custom_id="confession_reply")
    async def reply(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        modal = ConfessionModal(is_reply=True, original_message_id=interaction.message.id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Report", style=discord.ButtonStyle.danger, custom_id="confession_report")
    async def report(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """
        Report a confession. If reports ≥ 3, remove confession and ban its author.
        Prevents duplicate reports.
        """
        if not interaction.message.embeds:
            await interaction.response.send_message("No confession content available to report.", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        guild_settings = await CONFIG_MANAGER.get_guild_settings(guild_id)
        log_channel_id = guild_settings.get('log_channel')

        if log_channel_id:
            log_channel = interaction.guild.get_channel(int(log_channel_id))
            if log_channel:
                embed_desc = interaction.message.embeds[0].description or "No description available."
                report_embed = discord.Embed(
                    title="Confession Report",
                    description=(
                        f"**Message ID:** {interaction.message.id}\n"
                        f"**Reported by:** {interaction.user} (ID: {interaction.user.id})\n"
                        f"**Content:**\n{embed_desc}"
                    ),
                    color=discord.Color.red(),
                    timestamp=discord.utils.utcnow()
                )
                await log_channel.send(embed=report_embed)
            else:
                logging.warning(f"Log channel {log_channel_id} not found.")
        else:
            logging.warning("Log channel not set.")

        report_count, duplicate = await CONFIG_MANAGER.add_confession_report(
            guild_id,
            str(interaction.message.id),
            str(interaction.user.id)
        )
        if duplicate:
            await interaction.response.send_message("You have already reported this confession.", ephemeral=True)
            return

        if report_count >= 3:
            confession_doc = await CONFIG_MANAGER.get_confession(guild_id, str(interaction.message.id))
            if confession_doc:
                author_id = confession_doc.get("author_id")
                banned_users = guild_settings.get("banned_users", [])
                if author_id not in banned_users:
                    banned_users.append(author_id)
                    # Merge current settings with the updated banned_users list
                    await CONFIG_MANAGER.update_guild_settings(guild_id, {**guild_settings, "banned_users": banned_users})
                    logging.info(f"Banned user {author_id} in guild {interaction.guild.id} due to reports.")
            original_embed = interaction.message.embeds[0]
            removed_embed = discord.Embed(
                title=original_embed.title,
                description="This confession has been removed due to multiple reports.",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            await interaction.message.edit(embed=removed_embed, view=None)
            await interaction.response.send_message("Report submitted. The confession has been removed.", ephemeral=True)
        else:
            await interaction.response.send_message("Report submitted to moderators.", ephemeral=True)

class ConfessionModal(discord.ui.Modal):
    """
    Modal for submitting (or replying to) a confession.
    """
    def __init__(self, is_reply: bool = False, original_message_id: Optional[int] = None) -> None:
        title_text = "Submit a Confession" if not is_reply else "Reply to Confession"
        super().__init__(title=title_text)
        self.is_reply = is_reply
        self.original_message_id = original_message_id

        self.title_input = discord.ui.TextInput(
            label="Title (Optional)",
            style=discord.TextStyle.short,
            placeholder="Enter a title (optional)",
            required=False,
            max_length=100
        )
        self.confession_input = discord.ui.TextInput(
            label="Your Message",
            style=discord.TextStyle.paragraph,
            placeholder="Type your confession here...",
            required=True,
            max_length=2000
        )
        self.add_item(self.title_input)
        self.add_item(self.confession_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Process the submitted confession modal."""
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id)
        guild_settings = await CONFIG_MANAGER.get_guild_settings(guild_id)

        # Use the keys: submission_channel, log_channel, embed_color
        submission_channel_id = guild_settings.get('submission_channel')
        log_channel_id = guild_settings.get('log_channel')
        banned_users = guild_settings.get('banned_users', [])

        if str(interaction.user.id) in banned_users:
            await interaction.followup.send("You are banned from submitting confessions.", ephemeral=True)
            return

        if not submission_channel_id:
            await interaction.followup.send("Confession channel not set up!", ephemeral=True)
            return

        confession_channel = interaction.guild.get_channel(int(submission_channel_id))
        if not confession_channel:
            await interaction.followup.send("Confession channel not found!", ephemeral=True)
            return

        embed = discord.Embed(
            title=self.title_input.value if self.title_input.value else "",
            description=self.confession_input.value,
            color=discord.Color.from_str(guild_settings.get('embed_color', '#2f3136')),
            timestamp=discord.utils.utcnow()
        )

        file = None
        await interaction.followup.send(
            "If you have an image attachment, please upload it now, or type `skip`.",
            ephemeral=True
        )
        try:
            def check(m: discord.Message) -> bool:
                return m.author.id == interaction.user.id and m.channel == interaction.channel
            msg = await interaction.client.wait_for("message", timeout=30.0, check=check)
            if msg.content.lower().strip() == "skip":
                file = None
            elif msg.attachments:
                valid_attachment = next(
                    (a for a in msg.attachments if a.content_type and a.content_type.startswith("image")),
                    None
                )
                if valid_attachment:
                    file = await valid_attachment.to_file()
                    embed.set_image(url=f"attachment://{file.filename}")
                else:
                    await interaction.followup.send("No valid image found, proceeding without attachment.", ephemeral=True)
                    file = None
            try:
                await msg.delete()
            except Exception as e:
                logging.warning(f"Failed to delete message {msg.id}: {e}")
        except asyncio.TimeoutError:
            file = None

        try:
            if not self.is_reply:
                view = ConfessionView()
                message = await confession_channel.send(embed=embed, view=view, file=file)
                await CONFIG_MANAGER.add_confession(
                    guild_id=guild_id,
                    message_id=str(message.id),
                    author_id=interaction.user.id,
                    title=self.title_input.value,
                    content=self.confession_input.value
                )
            else:
                try:
                    original_message = await confession_channel.fetch_message(self.original_message_id)
                except discord.HTTPException as e:
                    await interaction.followup.send(f"Failed to fetch original message: {str(e)}", ephemeral=True)
                    return

                thread = original_message.thread
                if not thread:
                    try:
                        thread = await original_message.create_thread(name="Confession Discussion")
                    except discord.HTTPException as e:
                        await interaction.followup.send(f"Failed to create thread: {str(e)}", ephemeral=True)
                        return
                await thread.send(embed=embed, file=file)

            if log_channel_id:
                log_channel = interaction.guild.get_channel(int(log_channel_id))
                if log_channel:
                    log_embed = discord.Embed(
                        title="New Confession Log",
                        description=(
                            f"**Author:** {interaction.user} (ID: {interaction.user.id})\n"
                            f"**Type:** {'Reply' if self.is_reply else 'Original confession'}\n"
                            f"**Title:** {self.title_input.value or 'None'}\n"
                            f"**Content:** {self.confession_input.value}"
                        ),
                        color=discord.Color.blue(),
                        timestamp=discord.utils.utcnow()
                    )
                    if file:
                        log_embed.add_field(name="Attachment", value="Image included", inline=False)
                    await log_channel.send(embed=log_embed)
                else:
                    logging.warning("Log channel not found.")
            await interaction.followup.send("Your confession has been submitted!", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"Failed to submit confession: {str(e)}", ephemeral=True)

class Confessions(commands.Cog):
    """
    Cog that registers three slash commands:
    • /confess – for submitting confessions
    • /setup-confess – for configuring confession settings
    • /confess-ban – for banning/unbanning users from submitting confessions
    """
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config = CONFIG_MANAGER
        bot.add_view(ConfessionView())  # Register persistent view

    async def cog_load(self) -> None:
        """Restore persistent views for bot-authored messages."""
        for guild in self.bot.guilds:
            try:
                guild_settings = await self.config.get_guild_settings(str(guild.id))
                submission_channel_id = guild_settings.get('submission_channel')
                if not submission_channel_id:
                    continue
                confession_channel = guild.get_channel(int(submission_channel_id))
                if not confession_channel:
                    continue
                processed_messages = 0
                async for message in confession_channel.history(limit=50):
                    if (
                        message.author == self.bot.user and 
                        message.embeds and 
                        message.embeds[0].description and 
                        len(message.embeds[0].description) > 10
                    ):
                        try:
                            view = ConfessionView()
                            await message.edit(view=view)
                            processed_messages += 1
                            if processed_messages % 5 == 0:
                                await asyncio.sleep(2)
                            await asyncio.sleep(0.5)
                        except discord.HTTPException as e:
                            logging.error(f"Error editing message {message.id}: {e}")
                            await asyncio.sleep(min(processed_messages * 2, 30))
                        except Exception as e:
                            logging.error(f"Unexpected error with message {message.id}: {e}")
                            await asyncio.sleep(1)
            except Exception as e:
                logging.error(f"Error restoring views for guild {guild.id}: {e}")

    @app_commands.command(name="confess", description="Submit an anonymous confession.")
    async def confess(self, interaction: discord.Interaction) -> None:
        modal = ConfessionModal()
        await interaction.response.send_modal(modal)

    @app_commands.command(name="setup-confess", description="Configure confession settings for your server.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(
        confession_channel='submission-channel',
        log_channel='log-channel',
        embed_color='embed-color'
    )
    @app_commands.describe(
        confession_channel="Channel where confessions will be posted",
        log_channel="Channel where confession logs will be sent",
        embed_color="Hex code for embed color (e.g. #FF0000)"
    )
    async def setup_confess(
        self,
        interaction: discord.Interaction,
        confession_channel: Optional[discord.TextChannel] = None,
        log_channel: Optional[discord.TextChannel] = None,
        embed_color: Optional[str] = None
    ) -> None:
        guild_id = str(interaction.guild.id)
        current_settings = await self.config.get_guild_settings(guild_id)
        new_settings = current_settings.copy()

        if confession_channel:
            new_settings['submission_channel'] = confession_channel.id
        if log_channel:
            new_settings['log_channel'] = log_channel.id
        if embed_color:
            # Ensure it starts with '#'
            if not embed_color.startswith('#'):
                embed_color = f'#{embed_color}'
            try:
                discord.Color.from_str(embed_color)
                new_settings['embed_color'] = embed_color
            except ValueError:
                await interaction.response.send_message("Invalid color format. Use hex code like #FF0000.", ephemeral=True)
                return

        await self.config.update_guild_settings(guild_id, new_settings)
        await interaction.response.send_message("Confession configurations updated successfully.", ephemeral=True)

    @app_commands.command(name="confess-ban", description="Ban or unban a user from submitting confessions.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        user="The user to ban/unban",
        action="Specify 'ban' to ban or 'unban' to remove the ban"
    )
    async def confess_ban(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        action: str
    ) -> None:
        guild_id = str(interaction.guild.id)
        current_settings = await self.config.get_guild_settings(guild_id)
        banned_users = current_settings.get('banned_users', [])
        user_id = str(user.id)

        if action.lower() == "ban":
            if user_id not in banned_users:
                banned_users.append(user_id)
            message = f"{user} has been banned from submitting confessions."
        elif action.lower() == "unban":
            if user_id in banned_users:
                banned_users.remove(user_id)
            message = f"{user} has been unbanned from submitting confessions."
        else:
            await interaction.response.send_message("Invalid action. Use 'ban' or 'unban'.", ephemeral=True)
            return

        current_settings['banned_users'] = banned_users
        await self.config.update_guild_settings(guild_id, current_settings)
        await interaction.response.send_message(message, ephemeral=True)

async def setup(bot: commands.Bot) -> None:
    """Called when loading this cog."""
    await bot.add_cog(Confessions(bot))
    # You may want to synchronize commands here or in on_ready:
    # await bot.tree.sync()
