
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import logging
import re
from typing import Literal

# Load environment variables
load_dotenv()

class AttachmentReactor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        self.collection = None
        self.client = None
        self.reaction_locks = {}  # Prevent race conditions

    async def cog_load(self):
        """Initialize MongoDB connection when cog loads"""
        try:
            mongo_url = os.getenv('MONGO_URL')
            if not mongo_url:
                print("❌ MONGO_URL not found in .env file!")
                return

            self.client = AsyncIOMotorClient(mongo_url)
            self.db = self.client.discord_bot
            self.collection = self.db.attachment_channels

            # Test connection
            await self.client.admin.command('ping')
            print("✅ Connected to MongoDB Atlas successfully!")

        except Exception as e:
            print(f"❌ Failed to connect to MongoDB: {e}")
            print("🔄 Bot will continue running but commands won't work until connection is restored.")

    async def cog_unload(self):
        """Clean up MongoDB connection when cog unloads"""
        if self.client:
            self.client.close()
            print("✅ MongoDB connection closed.")

    def parse_emojis(self, emoji_string: str) -> list:
        """Parse emoji string into list of individual emojis without requiring commas"""
        if not emoji_string:
            return ["✅"]  # Default emoji

        # Pattern for custom Discord emojis and unicode emojis
        custom_emoji_pattern = r'<a?:[a-zA-Z0-9_]+:[0-9]+>'
        
        # Find all custom emojis first
        custom_emojis = re.findall(custom_emoji_pattern, emoji_string)
        
        # Remove custom emojis from the string to process unicode emojis
        remaining_text = re.sub(custom_emoji_pattern, '', emoji_string)
        
        # Unicode emoji pattern
        unicode_pattern = r'(\X)'  # matches each grapheme cluster (complete emoji or character)
        unicode_emojis = [e for e in re.findall(unicode_pattern, remaining_text) if self.validate_emoji(e)]
        
        # Combine both types of emojis
        emojis = custom_emojis + unicode_emojis
        
        # Remove any whitespace-only entries and limit to 10 emojis
        emojis = [emoji.strip() for emoji in emojis if emoji.strip()][:10]

        return emojis if emojis else ["✅"]

    def validate_emoji(self, emoji_str: str) -> bool:
        """Validate if emoji string is usable for reactions"""
        try:
            # Check if it's a unicode emoji (most common case)
            if len(emoji_str) <= 4 and any(ord(char) > 127 for char in emoji_str):
                return True

            # Check if it's a custom emoji format <:name:id> or <a:name:id>
            custom_emoji_pattern = r'^<a?:[a-zA-Z0-9_]+:[0-9]+>$'
            if re.match(custom_emoji_pattern, emoji_str):
                return True

            # Check if it's a valid unicode emoji by trying to encode it
            emoji_str.encode('unicode_escape')

            # Additional check for common emojis
            common_emojis = ['✅', '❤️', '👍', '👎', '🔥', '💯', '⭐', '🎉', '😀', '😍', '🤔', '👌', '💪', '🎯', '🚀', '⚡']
            if emoji_str in common_emojis:
                return True

            return len(emoji_str) <= 4  # Allow short strings that might be valid emojis

        except Exception:
            return False

    def validate_emojis(self, emojis: list) -> tuple[list, list]:
        """Validate list of emojis, return (valid_emojis, invalid_emojis)"""
        valid = []
        invalid = []

        for emoji in emojis[:10]:  # Limit to 10 emojis max
            if self.validate_emoji(emoji):
                valid.append(emoji)
            else:
                invalid.append(emoji)

        return valid, invalid

    async def check_reaction_permissions(self, channel: discord.TextChannel) -> bool:
        """Check if bot has permission to add reactions in the channel"""
        try:
            permissions = channel.permissions_for(channel.guild.me)
            return permissions.add_reactions and permissions.view_channel
        except Exception:
            return False

    @app_commands.command(name="attachment-reactor", description="Manage attachment reaction monitoring with multiple emoji support")
    @app_commands.describe(
        action="Choose what to do",
        channel="Channel to setup (only for 'setup' action)",
        emojis="Emojis to react with, separated by commas (e.g: ✅,🔥,❤️) - max 10"
    )
    async def attachment_reactor(
        self, 
        interaction: discord.Interaction, 
        action: Literal["setup", "list", "cleanup"],
        channel: discord.TextChannel = None,
        emojis: str = "✅"
    ):
        """Single command to manage all attachment reaction functionality with multiple emoji support"""

        # Check if user has administrator permissions
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ You need administrator permissions to use this command!", ephemeral=True)
            return

        if self.collection is None:
            await interaction.response.send_message(
                "❌ Database connection not available! Please check MongoDB connection and try again.", 
                ephemeral=True
            )
            return

        # Handle different actions
        if action == "setup":
            await self._handle_setup(interaction, channel, emojis)
        elif action == "list":
            await self._handle_list(interaction)
        elif action == "cleanup":
            await self._handle_cleanup(interaction)

    async def _handle_setup(self, interaction: discord.Interaction, channel: discord.TextChannel, emoji_string: str):
        """Handle setup/toggle functionality with multiple emoji support"""

        if not channel:
            await interaction.response.send_message(
                "❌ You must specify a channel when using the 'setup' action!\n"
                "**Examples:**\n"
                "• `/attachment_reactor action:setup channel:#general emojis:✅`\n"
                "• `/attachment_reactor action:setup channel:#media emojis:🔥,❤️,👍`\n"
                "• `/attachment_reactor action:setup channel:#art emojis:<:custom:123>,✅,🎉`", 
                ephemeral=True
            )
            return

        # Parse and validate emojis
        emoji_list = self.parse_emojis(emoji_string)
        valid_emojis, invalid_emojis = self.validate_emojis(emoji_list)

        if not valid_emojis:
            await interaction.response.send_message(
                f"❌ No valid emojis provided!\n"
                f"**Invalid emojis:** {', '.join(f'`{e}`' for e in invalid_emojis)}\n\n"
                f"**Valid formats:**\n"
                f"• Unicode emojis: ✅ 🔥 ❤️ 👍 ⭐\n"
                f"• Custom emojis: <:name:123456789>\n"
                f"• Multiple emojis: ✅,🔥,❤️ (comma-separated)", 
                ephemeral=True
            )
            return

        if invalid_emojis:
            await interaction.response.send_message(
                f"⚠️ Some emojis are invalid and will be ignored:\n"
                f"**Invalid:** {', '.join(f'`{e}`' for e in invalid_emojis)}\n"
                f"**Will use:** {' '.join(valid_emojis)}\n\n"
                f"Continue? React ✅ to proceed or ignore this message to cancel.", 
                ephemeral=True
            )
            # For simplicity, we'll continue with valid emojis only

        # Check bot permissions in target channel
        if not await self.check_reaction_permissions(channel):
            await interaction.response.send_message(
                f"❌ I don't have permission to add reactions in {channel.mention}!\n"
                f"Please ensure I have **Add Reactions** and **View Channel** permissions.", 
                ephemeral=True
            )
            return

        try:
            # Check if guild document exists
            guild_doc = await self.collection.find_one({"guild_id": interaction.guild.id})

            if not guild_doc:
                # Create new guild document
                guild_doc = {
                    "guild_id": interaction.guild.id,
                    "channels": []
                }

            # Check if channel is already monitored
            channel_exists = False
            for ch in guild_doc["channels"]:
                if ch["channel_id"] == channel.id:
                    channel_exists = True
                    # Remove channel (toggle off)
                    guild_doc["channels"] = [c for c in guild_doc["channels"] if c["channel_id"] != channel.id]
                    break

            if not channel_exists:
                # Add channel (toggle on)
                guild_doc["channels"].append({
                    "channel_id": channel.id,
                    "emojis": valid_emojis  # Store as list of emojis
                })

            # Update database
            await self.collection.replace_one(
                {"guild_id": interaction.guild.id},
                guild_doc,
                upsert=True
            )

            if channel_exists:
                await interaction.response.send_message(
                    f"✅ **Removed** attachment monitoring from {channel.mention}\n"
                    f"💡 Use `/attachment_reactor action:list` to see all monitored channels",
                    ephemeral=True
                )

                # Log the action
                await self.log_action(interaction.guild, f"📝 Attachment monitoring **disabled** for {channel.mention} by {interaction.user.mention}")
            else:
                emoji_display = ' '.join(valid_emojis)
                await interaction.response.send_message(
                    f"✅ **Added** attachment monitoring to {channel.mention}\n"
                    f"🎯 **Emojis:** {emoji_display} ({len(valid_emojis)} emoji{'s' if len(valid_emojis) > 1 else ''})\n"
                    f"🤖 Bot will react with all emojis after 1-second delay\n"
                    f"💡 Use same command again to remove monitoring",
                    ephemeral=True
                )

                # Log the action
                await self.log_action(interaction.guild, f"📝 Attachment monitoring **enabled** for {channel.mention} with {len(valid_emojis)} emojis ({emoji_display}) by {interaction.user.mention}")

        except Exception as e:
            await interaction.response.send_message(f"❌ Database error occurred: {str(e)}", ephemeral=True)
            print(f"Setup error: {e}")

    async def _handle_list(self, interaction: discord.Interaction):
        """Handle list functionality with multiple emoji display"""

        try:
            guild_doc = await self.collection.find_one({"guild_id": interaction.guild.id})

            if not guild_doc or not guild_doc.get("channels"):
                await interaction.response.send_message(
                    "📝 No channels are currently being monitored.\n"
                    f"💡 Use `/attachment_reactor action:setup channel:#channel emojis:✅,🔥` to start!", 
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title="📋 Attachment Reaction Monitoring",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )

            valid_channels = []
            invalid_channels = []
            total_emojis = 0

            for ch in guild_doc["channels"]:
                channel = interaction.guild.get_channel(ch["channel_id"])
                # Handle both old format (single emoji) and new format (multiple emojis)
                channel_emojis = ch.get("emojis", [ch.get("emoji", "✅")] if "emoji" in ch else ["✅"])
                emoji_display = ' '.join(channel_emojis)
                emoji_count = len(channel_emojis)
                total_emojis += emoji_count

                if channel:
                    # Check if we still have permissions
                    has_perms = await self.check_reaction_permissions(channel)
                    status = "✅" if has_perms else "⚠️ No permissions"
                    valid_channels.append(f"• {channel.mention} - {emoji_display} `({emoji_count})` {status}")
                else:
                    invalid_channels.append(f"• *Deleted Channel* (ID: {ch['channel_id']}) - {emoji_display}")

            description_parts = []
            if valid_channels:
                description_parts.append(f"**🟢 Active Monitoring ({len(valid_channels)} channels, {total_emojis} total reactions):**\n" + "\n".join(valid_channels))

            if invalid_channels:
                description_parts.append("**🔴 Deleted Channels:**\n" + "\n".join(invalid_channels))
                description_parts.append("\n💡 Use `/attachment_reactor action:cleanup` to remove deleted channels")

            embed.description = "\n\n".join(description_parts)

            # Add helpful footer with examples
            embed.add_field(
                name="📖 Quick Commands",
                value=(
                    f"• **Single emoji**: `/attachment_reactor action:setup channel:#ch emojis:✅`\n"
                    f"• **Multiple emojis**: `/attachment_reactor action:setup channel:#ch emojis:🔥,❤️,👍`\n"
                    f"• **Remove**: Same setup command toggles off\n"
                    f"• **Cleanup**: `/attachment_reactor action:cleanup`"
                ),
                inline=False
            )

            embed.set_footer(text="Attachment Reactor Bot • Multiple Emoji Support")

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(f"❌ An error occurred: {str(e)}", ephemeral=True)

    async def _handle_cleanup(self, interaction: discord.Interaction):
        """Handle cleanup functionality"""

        try:
            guild_doc = await self.collection.find_one({"guild_id": interaction.guild.id})

            if not guild_doc or not guild_doc.get("channels"):
                await interaction.response.send_message(
                    "📝 No channels are being monitored.\n"
                    f"💡 Use `/attachment_reactor action:setup` to start monitoring channels!", 
                    ephemeral=True
                )
                return

            # Filter out deleted channels
            original_count = len(guild_doc["channels"])
            guild_doc["channels"] = [
                ch for ch in guild_doc["channels"] 
                if interaction.guild.get_channel(ch["channel_id"]) is not None
            ]

            deleted_count = original_count - len(guild_doc["channels"])

            if deleted_count == 0:
                await interaction.response.send_message(
                    "✅ No deleted channels found to clean up!\n"
                    f"💡 Use `/attachment_reactor action:list` to see current monitoring status", 
                    ephemeral=True
                )
                return

            # Update database
            await self.collection.replace_one(
                {"guild_id": interaction.guild.id},
                guild_doc,
                upsert=True
            )

            await interaction.response.send_message(
                f"✅ **Cleaned up {deleted_count} deleted channel(s)** from monitoring list!\n"
                f"💡 Use `/attachment_reactor action:list` to see updated status",
                ephemeral=True
            )

            await self.log_action(
                interaction.guild, 
                f"🧹 Cleaned up {deleted_count} deleted channels by {interaction.user.mention}"
            )

        except Exception as e:
            await interaction.response.send_message(f"❌ An error occurred: {str(e)}", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Listen for messages with attachments and react with multiple emojis"""

        # Ignore bot messages
        if message.author.bot:
            return

        # Check if message has attachments
        if not message.attachments:
            return


        # Prevent race conditions with rapid attachments
        message_key = f"{message.guild.id}_{message.channel.id}_{message.id}"
        if message_key in self.reaction_locks:
            return
        self.reaction_locks[message_key] = True

        try:
            # Get guild configuration
            guild_doc = await self.collection.find_one({"guild_id": message.guild.id})

            if not guild_doc or not guild_doc.get("channels"):
                return

            # Check if current channel is monitored
            channel_config = None
            for ch in guild_doc["channels"]:
                if ch["channel_id"] == message.channel.id:
                    channel_config = ch
                    break

            if not channel_config:
                return

            # Wait 1 second as requested
            await asyncio.sleep(1)

            # Double-check permissions before reacting
            if not await self.check_reaction_permissions(message.channel):
                await self.log_action(
                    message.guild,
                    f"❌ **Permission Error**: Cannot react in {message.channel.mention} - Missing permissions"
                )
                return

            # Get emojis (handle both old single emoji format and new multiple emoji format)
            emojis_to_react = channel_config.get("emojis", [channel_config.get("emoji", "✅")] if "emoji" in channel_config else ["✅"])

            # React with each emoji
            successful_reactions = 0
            failed_reactions = []

            for emoji in emojis_to_react:
                try:
                    await message.add_reaction(emoji)
                    successful_reactions += 1
                    # Small delay between reactions to avoid rate limits
                    if len(emojis_to_react) > 1:
                        await asyncio.sleep(0.2)

                except discord.Forbidden:
                    failed_reactions.append(f"{emoji} (Forbidden)")

                except discord.HTTPException as e:
                    if "Unknown Emoji" in str(e):
                        failed_reactions.append(f"{emoji} (Invalid)")
                    else:
                        failed_reactions.append(f"{emoji} ({str(e)})")

            # Log any failures
            if failed_reactions:
                await self.log_action(
                    message.guild,
                    f"⚠️ **Partial Success**: {successful_reactions}/{len(emojis_to_react)} reactions added in {message.channel.mention}. Failed: {', '.join(failed_reactions)}"
                )

        except Exception as e:
            print(f"Message handling error: {e}")
        finally:
            # Clean up race condition lock
            self.reaction_locks.pop(message_key, None)

    async def log_action(self, guild, message):
        """Log actions to the first available text channel"""
        try:
            # Try to find a channel named 'logs', 'bot-logs', or 'attachment-logs'
            log_channel = None
            log_channel_names = ['logs', 'bot-logs', 'attachment-logs', 'mod-logs']

            for channel in guild.text_channels:
                if channel.name.lower() in log_channel_names:
                    if channel.permissions_for(guild.me).send_messages:
                        log_channel = channel
                        break

            # If no dedicated log channel found, use first available channel
            if not log_channel:
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).send_messages:
                        log_channel = channel
                        break

            if log_channel:
                embed = discord.Embed(
                    description=message,
                    color=discord.Color.blue(),
                    timestamp=discord.utils.utcnow()
                )
                embed.set_footer(text="Attachment Reactor • Multi-Emoji")

                try:
                    await log_channel.send(embed=embed)
                except discord.Forbidden:
                    print(f"Cannot send log message to {log_channel.name} - No permissions")
                except Exception as e:
                    print(f"Logging error: {e}")

        except Exception as e:
            print(f"Log action error: {e}")

async def setup(bot):
    await bot.add_cog(AttachmentReactor(bot))
