import discord
from discord.ext import commands
from discord import app_commands, ui
import datetime
import os
import pathlib
import logging
import asyncio
import re
import aiosqlite
import sqlite3

# Set up logger
logger = logging.getLogger('discord.autoresponder')

# Define a Modal for autoresponder input
class AutoresponderModal(ui.Modal, title="Add Autoresponder"):
    # Input fields with larger text boxes
    trigger_input = ui.TextInput(
        label="Trigger Text",
        placeholder="Enter the text that will trigger the autoresponse",
        required=True,
        style=discord.TextStyle.short
    )
    
    response_input = ui.TextInput(
        label="Response Message",
        placeholder="Enter the response message (supports placeholders like {user}, {server}, etc.)",
        required=True,
        style=discord.TextStyle.paragraph,  # Paragraph style for multiline input
        max_length=2000  # Discord message limit
    )
    
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
    
    async def on_submit(self, interaction: discord.Interaction):
        # Get values from the modal
        trigger = self.trigger_input.value
        response = self.response_input.value
        
        # Process the autoresponder addition
        await self.cog._process_autoresponder_add(interaction, trigger, response)

class AutoResponder(commands.Cog):
    """Cog for automatic responses to specific triggers in messages."""
    
    def __init__(self, bot):
        self.bot = bot
        # Create data directory if it doesn't exist
        pathlib.Path("database").mkdir(exist_ok=True)
        self.db_path = os.path.join("database", "autoresponses.db")
        # Add a lock for thread safety
        self.db_lock = asyncio.Lock()
        # Track processed messages to avoid duplicate responses
        self.processed_messages = set()
        # Initialize database in setup method
        
    async def _init_db(self):
        """Initialize the SQLite database for autoresponses"""
        async with self.db_lock:
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    # Create tables if they don't exist
                    await db.execute('''
                    CREATE TABLE IF NOT EXISTS autoresponses (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id TEXT NOT NULL,
                        trigger TEXT NOT NULL,
                        response TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        created_by TEXT NOT NULL,
                        UNIQUE(guild_id, trigger)
                    )
                    ''')
                    await db.commit()
                logger.info("Autoresponder database initialized successfully")
            except Exception as e:
                logger.error(f"Error initializing autoresponder database: {e}")
                
    async def _migrate_from_json(self):
        """Migrate data from JSON file to SQLite if JSON file exists"""
        json_file = os.path.join("database", "autoresponses.json")
        if not os.path.exists(json_file):
            return
            
        try:
            # Load JSON data
            import json
            with open(json_file, 'r', encoding='utf-8') as f:
                autoresponses = json.load(f)
                
            if not autoresponses:
                return
                
            # Migrate to SQLite
            async with aiosqlite.connect(self.db_path) as db:
                for guild_id, responses in autoresponses.items():
                    for entry in responses:
                        await db.execute(
                            "INSERT OR REPLACE INTO autoresponses (guild_id, trigger, response, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
                            (guild_id, entry["trigger"], entry["response"], 
                             entry.get("created_at", datetime.datetime.now().isoformat()),
                             entry.get("created_by", "unknown"))
                        )
                await db.commit()
                
            # Create backup of the JSON file
            import shutil
            backup_file = f"{json_file}.migrated"
            shutil.copy2(json_file, backup_file)
            logger.info(f"Successfully migrated data from JSON to SQLite. Backup created at {backup_file}")
        except Exception as e:
            logger.error(f"Error migrating from JSON to SQLite: {e}")
    
    async def _get_guild_autoresponses(self, guild_id):
        """Get all autoresponses for a specific guild from the database"""
        async with self.db_lock:
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        "SELECT * FROM autoresponses WHERE guild_id = ? ORDER BY id",
                        (str(guild_id),)
                    ) as cursor:
                        return [dict(row) for row in await cursor.fetchall()]
            except Exception as e:
                logger.error(f"Error getting autoresponses for guild {guild_id}: {e}")
                return []
    
    async def _add_autoresponse(self, guild_id, trigger, response, user_id):
        """Add a new autoresponse to the database"""
        async with self.db_lock:
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    # Check if trigger already exists
                    async with db.execute(
                        "SELECT id FROM autoresponses WHERE guild_id = ? AND LOWER(trigger) = LOWER(?)",
                        (str(guild_id), trigger)
                    ) as cursor:
                        if await cursor.fetchone():
                            return False, "An autoresponse with this trigger already exists"
                    
                    # Add new autoresponse
                    await db.execute(
                        "INSERT INTO autoresponses (guild_id, trigger, response, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
                        (str(guild_id), trigger, response, datetime.datetime.now().isoformat(), str(user_id))
                    )
                    await db.commit()
                    return True, "Autoresponse added successfully"
            except Exception as e:
                logger.error(f"Error adding autoresponse: {e}")
                return False, f"Database error: {str(e)}"
    
    async def _remove_autoresponse(self, guild_id, trigger):
        """Remove an autoresponse from the database"""
        async with self.db_lock:
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    # Check if trigger exists
                    async with db.execute(
                        "SELECT id FROM autoresponses WHERE guild_id = ? AND LOWER(trigger) = LOWER(?)",
                        (str(guild_id), trigger.lower())
                    ) as cursor:
                        if not await cursor.fetchone():
                            return False, "No autoresponse with this trigger exists"
                    
                    # Remove autoresponse
                    await db.execute(
                        "DELETE FROM autoresponses WHERE guild_id = ? AND LOWER(trigger) = LOWER(?)",
                        (str(guild_id), trigger.lower())
                    )
                    await db.commit()
                    return True, "Autoresponse removed successfully"
            except Exception as e:
                logger.error(f"Error removing autoresponse: {e}")
                return False, f"Database error: {str(e)}"
    
    def _format_response(self, response, message):
        """Format the response with template variables."""
        now = datetime.datetime.now()
        return response.replace(
            "{user}", message.author.mention
        ).replace(
            "{server}", message.guild.name
        ).replace(
            "{channel}", message.channel.name
        ).replace(
            "{date}", now.strftime("%Y-%m-%d")
        ).replace(
            "{time}", now.strftime("%H:%M")
        )
    
    @commands.Cog.listener()
    async def on_message(self, message):
        """Listen for messages and respond to triggers."""
        # Ignore messages from bots
        if message.author.bot:
            return
        
        # Ignore DMs
        if not message.guild:
            return
        
        # Avoid processing the same message multiple times
        if message.id in self.processed_messages:
            return
        
        # Get guild ID as string
        guild_id = str(message.guild.id)
        
        try:
            # Get all autoresponses for this guild
            autoresponses = await self._get_guild_autoresponses(guild_id)
            if not autoresponses:
                return
            
            # Check each trigger - only match exact messages
            message_content = message.content.lower().strip()
            
            for entry in autoresponses:
                trigger = entry["trigger"].lower().strip()
                
                # Only trigger on exact message match (when the message is exactly the trigger)
                if message_content == trigger:
                    # Format the response with variables
                    response = self._format_response(entry["response"], message)
                    
                    # Send the response
                    try:
                        await message.channel.send(response)
                        # Only trigger once per message
                        break
                    except discord.Forbidden:
                        logger.error(f"Missing permissions to send autoresponse in channel {message.channel.id}")
                    except discord.HTTPException as e:
                        logger.error(f"HTTP error sending autoresponse: {e}")
                    except Exception as e:
                        logger.error(f"Unexpected error sending autoresponse: {e}")
        except Exception as e:
            # Log any errors but don't crash the bot
            logger.error(f"Error in autoresponder on_message: {e}")
            # Don't raise the exception - we want the bot to continue running
        
        # Mark the message as processed
        self.processed_messages.add(message.id)
        # Limit the size of the processed messages set
        if len(self.processed_messages) > 1000:
            # Remove the oldest messages
            self.processed_messages = set(list(self.processed_messages)[-500:])
    
    # We now use self.processed_messages set to track processed messages in memory
    
    # Create a command group for autoresponder commands
    autoresponder_group = app_commands.Group(
        name="autoresponder",
        description="Manage automatic responses to message triggers"
    )
    
    @autoresponder_group.command(name="add")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def autoresponder_add(self, interaction: discord.Interaction):
        """Add a new autoresponder trigger and response using a popup form."""
        # Create and send the modal
        modal = AutoresponderModal(self)
        await interaction.response.send_modal(modal)

    async def _process_autoresponder_add(self, interaction: discord.Interaction, trigger: str, response: str):
        """Process adding a new autoresponder after modal submission."""
        guild_id = str(interaction.guild_id)
        
        # Add the new autoresponse to the database
        success, message = await self._add_autoresponse(guild_id, trigger, response, interaction.user.id)
        
        if not success:
            await interaction.response.send_message(
                f"❌ {message}",
                ephemeral=True
            )
            return
        
        # Create confirmation embed
        embed = discord.Embed(
            title="Autoresponse Added",
            description="Your autoresponse has been added successfully.",
            color=discord.Color.green()
        )
        
        # Add details to the embed
        embed.add_field(name="Trigger", value=f"`{trigger}`", inline=False)
        
        # Format the response for display, preserving line breaks
        formatted_response = response.replace("\n", "\n> ")
        embed.add_field(name="Response", value=f"> {formatted_response}", inline=False)
        
        # Add a note about exact matching
        embed.add_field(
            name="Important Note", 
            value="The bot will only respond when a message **exactly matches** the trigger text.", 
            inline=False
        )
        
        # Add a note about custom emojis and formatting
        embed.set_footer(text="Custom emojis and multiple lines are supported in the response.")
        
        # Send the confirmation message
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @autoresponder_group.command(name="remove")
    @app_commands.describe(trigger="The trigger text to remove")
    async def autoresponder_remove(self, interaction: discord.Interaction, trigger: str):
        """Remove an autoresponse trigger."""
        guild_id = str(interaction.guild_id)
        
        # Remove the autoresponse from the database
        success, message = await self._remove_autoresponse(guild_id, trigger)
        
        if success:
            await interaction.response.send_message(
                f"✅ Autoresponse with trigger `{trigger}` has been removed.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ {message}",
                ephemeral=True
            )
    
    @autoresponder_group.command(name="list")
    async def autoresponder_list(self, interaction: discord.Interaction):
        """List all autoresponse triggers and responses for this server."""
        guild_id = str(interaction.guild.id)
        
        # Get autoresponses from the database
        autoresponses = await self._get_guild_autoresponses(guild_id)
        
        # Check if guild has any autoresponses
        if not autoresponses:
            await interaction.response.send_message(
                "❌ This server has no autoresponses set up.",
                ephemeral=True
            )
            return
        
        # Create paginated embeds
        embeds = []
        items_per_page = 5
        total_pages = (len(autoresponses) + items_per_page - 1) // items_per_page
        
        for page in range(total_pages):
            start_idx = page * items_per_page
            end_idx = min(start_idx + items_per_page, len(autoresponses))
            page_items = autoresponses[start_idx:end_idx]
            
            embed = discord.Embed(
                title="Autoresponses",
                description=f"Page {page+1}/{total_pages} - {len(autoresponses)} total autoresponses",
                color=discord.Color.blue()
            )
            
            for entry in page_items:
                # Truncate response if it's too long for the embed
                response = entry["response"]
                if len(response) > 200:
                    response = response[:197] + "..."
                    
                embed.add_field(
                    name=f"Trigger: {entry['trigger']}",
                    value=f"Response: {response}",
                    inline=False
                )
            
            # Add a note about exact matching
            if page == 0:  # Only on first page
                embed.set_footer(text="Note: Triggers only work on exact message matches")
            
            embeds.append(embed)
        
        # Send the first page
        if embeds:
            await interaction.response.send_message(embed=embeds[0])
        else:
            # This should never happen, but just in case
            await interaction.response.send_message("❌ Error creating autoresponse list.")
    
    @commands.command(name="autoresponder_placeholders", aliases=["placeholders", "ph"])
    async def autoresponder_placeholders(self, ctx):
        """Show all available placeholders for the autoresponder.
        
        Usage: .autoresponder_placeholders
        Aliases: .placeholders, .ph
        """
        embed = self._create_placeholders_embed()
        
        try:
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Error sending placeholders embed: {e}")
            await ctx.send("Error displaying placeholders. Please try again later.")
    
    @autoresponder_group.command(name="placeholders")
    async def slash_placeholders(self, interaction: discord.Interaction):
        """Show all available placeholders for autoresponses"""
        embed = self._create_placeholders_embed()
        
        try:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Error sending placeholders embed via slash command: {e}")
            await interaction.response.send_message("Error displaying placeholders. Please try again later.", ephemeral=True)
    
    def _create_placeholders_embed(self):
        """Create an embed with placeholders information"""
        embed = discord.Embed(
            title="Autoresponder Placeholders",
            description="These placeholders can be used in autoresponse messages:",
            color=discord.Color.green()
        )
        
        placeholders = {
            "{user}": "Mentions the user who triggered the autoresponse",
            "{server}": "The name of the server",
            "{channel}": "The name of the channel",
            "{date}": "Current date (YYYY-MM-DD)",
            "{time}": "Current time (HH:MM)"
        }
        
        for placeholder, description in placeholders.items():
            embed.add_field(
                name=placeholder,
                value=description,
                inline=False
            )
        
        # Add usage examples
        embed.add_field(
            name="Example Usage",
            value="Hello {user}! Welcome to {server}!",
            inline=False
        )
        
        embed.set_footer(text="Use these placeholders in your autoresponses for dynamic content.")
        return embed

    @commands.command(name="add_response")
    @commands.has_permissions(manage_guild=True)
    async def add_response(self, ctx, trigger: str, *, response: str):
        """Add a new autoresponse trigger and response.
        
        This command is deprecated. Please use the slash command /autoresponder add instead.
        
        Usage: .add_response "trigger text" response text here
        """
        # Redirect to slash command
        await ctx.send("⚠️ This command is deprecated. Please use `/autoresponder add` instead for a better interface.")
        
        # Add the response anyway
        guild_id = str(ctx.guild.id)
        
        # Reload data to ensure we have the latest version
        await self._load_autoresponses()
        
        # Initialize guild entry if it doesn't exist
        if guild_id not in self.autoresponses:
            self.autoresponses[guild_id] = []
        
        # Check if trigger already exists
        trigger_lower = trigger.lower()
        for entry in self.autoresponses[guild_id]:
            if entry["trigger"].lower() == trigger_lower:
                await ctx.send(f"⚠️ A trigger with text `{trigger}` already exists. Remove it first if you want to change it.")
                return
        
        # Add the new trigger-response pair
        self.autoresponses[guild_id].append({
            "trigger": trigger,
            "response": response,
            "created_at": datetime.datetime.now().isoformat(),
            "created_by": str(ctx.author.id)
        })
        
        # Save to file
        try:
            await self._save_autoresponses()
            await ctx.send(f"✅ Added autoresponse for trigger: `{trigger}`")
        except Exception as e:
            logger.error(f"Error saving after adding autoresponse: {e}")
            await ctx.send("❌ An error occurred while saving the autoresponse. Please try again later.")
            # Remove the entry from memory since we couldn't save it
            self.autoresponses[guild_id].pop()
    
    @commands.command(name="remove_response")
    @commands.has_permissions(manage_guild=True)
    async def remove_response(self, ctx, *, trigger: str):
        """Remove an autoresponse trigger.
        
        This command is deprecated. Please use the slash command /autoresponder remove instead.
        
        Usage: .remove_response "trigger text"
        """
        # Redirect to slash command
        await ctx.send("⚠️ This command is deprecated. Please use `/autoresponder remove` instead.")
        
        # Remove the response anyway
        guild_id = str(ctx.guild.id)
        
        # Reload data to ensure we have the latest version
        await self._load_autoresponses()
        
        # Check if guild has any autoresponses
        if guild_id not in self.autoresponses:
            await ctx.send("❌ This server has no autoresponses set up.")
            return
        
        # Find and remove the trigger
        trigger_lower = trigger.lower()
        found = False
        for i, entry in enumerate(self.autoresponses[guild_id]):
            if entry["trigger"].lower() == trigger_lower:
                self.autoresponses[guild_id].pop(i)
                found = True
                break
        
        if found:
            try:
                await self._save_autoresponses()
                await ctx.send(f"✅ Removed autoresponse for trigger: `{trigger}`")
            except Exception as e:
                logger.error(f"Error saving after removing autoresponse: {e}")
                await ctx.send("❌ An error occurred while removing the autoresponse. Please try again later.")
        else:
            # Trigger not found
            await ctx.send(f"❌ No autoresponse found for trigger: `{trigger}`")
    
    @commands.command(name="list_responses")
    async def list_responses(self, ctx):
        """List all autoresponse triggers and responses for this server.
        
        This command is deprecated. Please use the slash command /autoresponder list instead.
        
        Usage: .list_responses
        """
        # Redirect to slash command
        await ctx.send("⚠️ This command is deprecated. Please use `/autoresponder list` instead.")
        
        # List the responses anyway
        guild_id = str(ctx.guild.id)
        
        # Reload data to ensure we have the latest version
        await self._load_autoresponses()
        
        # Check if guild has any autoresponses
        if guild_id not in self.autoresponses or not self.autoresponses[guild_id]:
            await ctx.send("❌ This server has no autoresponses set up.")
            return
        
        # Get all autoresponses for this guild
        autoresponses = self.autoresponses[guild_id]
        
        # Create paginated embeds (max 5 entries per page)
        embeds = []
        items_per_page = 5
        total_pages = (len(autoresponses) + items_per_page - 1) // items_per_page
        
        for page in range(total_pages):
            start_idx = page * items_per_page
            end_idx = min(start_idx + items_per_page, len(autoresponses))
            page_items = autoresponses[start_idx:end_idx]
            
            embed = discord.Embed(
                title="Autoresponses",
                description=f"Page {page+1}/{total_pages} - {len(autoresponses)} total autoresponses",
                color=discord.Color.blue()
            )
            
            for entry in page_items:
                # Truncate response if it's too long for the embed
                response = entry['response']
                if len(response) > 200:
                    response = response[:197] + "..."
                    
                embed.add_field(
                    name=f"Trigger: {entry['trigger']}",
                    value=f"Response: {response}",
                    inline=False
                )
            
            embeds.append(embed)
        
        # Send the first page
        if embeds:
            await ctx.send(embed=embeds[0])
        else:
            # This should never happen, but just in case
            await ctx.send("❌ Error creating autoresponse list.")
    
    @commands.command(name="autoresponder_help", aliases=["ar_help"])
    async def autoresponder_help(self, ctx):
        """Show help for autoresponder commands.
        
        Usage: .autoresponder_help
        Aliases: .ar_help
        """
        embed = discord.Embed(
            title="Autoresponder Help",
            description="Commands for managing automatic responses to message triggers",
            color=discord.Color.blue()
        )
        
        # Slash commands section
        embed.add_field(
            name="Slash Commands (Recommended)",
            value=(
                "`/autoresponder add` - Add a new autoresponse with a popup form\n"
                "`/autoresponder remove <trigger>` - Remove an autoresponse\n"
                "`/autoresponder list` - List all autoresponses\n"
                "`/autoresponder placeholders` - Show available placeholders"
            ),
            inline=False
        )
        
        # Traditional commands section
        embed.add_field(
            name="Traditional Commands (Deprecated)",
            value=(
                "`.add_response <trigger> <response>` - Add a new autoresponse\n"
                "`.remove_response <trigger>` - Remove an autoresponse\n"
                "`.list_responses` - List all autoresponses\n"
                "`.placeholders` or `.ph` - Show available placeholders"
            ),
            inline=False
        )
        
        # Examples section
        embed.add_field(
            name="Examples",
            value=(
                "`.add_response \"hello\" Hello {user}! Welcome to {server}!`\n"
                "`.remove_response \"hello\"`\n"
            ),
            inline=False
        )
        
        # Placeholders section
        embed.add_field(
            name="Available Placeholders",
            value=(
                "`{user}` - Mentions the user who triggered the response\n"
                "`{server}` - The name of the server\n"
                "`{channel}` - The name of the channel\n"
                "`{date}` - Current date (YYYY-MM-DD)\n"
                "`{time}` - Current time (HH:MM)"
            ),
            inline=False
        )
        
        embed.set_footer(text="Slash commands are recommended for better user experience.")
        
        try:
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Error sending help embed: {e}")
            await ctx.send("Error displaying help. Please try again later.")

async def setup(bot):
    # Create the cog instance
    cog = AutoResponder(bot)
    
    # Initialize the database
    await cog._init_db()
    
    # Migrate data from JSON if needed
    await cog._migrate_from_json()
    
    # Add the cog to the bot
    await bot.add_cog(cog)
    
    logger.info("Autoresponder cog loaded with SQLite database")
