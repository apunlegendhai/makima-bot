import discord
from discord.ext import commands
from discord import app_commands
import asyncpg
import os
import logging
from typing import Optional, Dict, Tuple, List
import asyncio
import random

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('ban_cog')

class BanCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_pool = None
        self.config_cache = {}  # In-memory cache for faster access
        self.cache_ready = asyncio.Event()  # To signal when cache is ready
        self.responses = []  # Ban responses from file
        self.last_modified_time = 0  # Track the last modified time of responses.txt
        self.load_responses()
        
        # Start the background task to watch for file changes
        self.file_watcher_task = None
        
    async def watch_responses_file(self):
        """Watch for changes in responses.txt file"""
        last_modified = 0
        while True:
            try:
                current_modified = os.path.getmtime("responses.txt")
                if current_modified > last_modified:
                    with open("responses.txt", "r") as f:
                        self.responses = [line.strip() for line in f if line.strip()]
                    logger.info(f"Automatically reloaded {len(self.responses)} ban responses")
                    last_modified = current_modified
            except Exception as e:
                logger.error(f"Error watching responses file: {e}")
            await asyncio.sleep(1)  # Check every second

    def load_responses(self):
        """Initial load of ban responses"""
        try:
            with open("responses.txt", "r") as f:
                self.responses = [line.strip() for line in f if line.strip()]
            logger.info(f"Loaded {len(self.responses)} ban responses")
        except Exception as e:
            logger.error(f"Error loading ban responses: {e}")
            self.responses = ["@user has been banned for [reason]"]
        
    async def setup_database(self):
        # Get database URL from environment variable
        database_url = os.environ.get('DATABASE_URL')
        if not database_url:
            logger.error("DATABASE_URL environment variable not set")
            self.cache_ready.set()  # Signal cache is ready even though there's no DB
            return
            
        try:
            # Create connection pool
            self.db_pool = await asyncpg.create_pool(database_url)
            
            # Create table if it doesn't exist
            await self.db_pool.execute('''
                CREATE TABLE IF NOT EXISTS ban_config (
                    guild_id BIGINT PRIMARY KEY,
                    command TEXT NOT NULL,
                    response TEXT NOT NULL
                )
            ''')
            
            # Load all configurations into cache
            await self.load_config_cache()
            
        except Exception as e:
            logger.error(f"Database setup error: {e}")
            self.cache_ready.set()  # Signal cache is ready even though there was an error
    
    async def load_config_cache(self):
        """Load all configurations into memory for faster access"""
        try:
            if self.db_pool:
                records = await self.db_pool.fetch('SELECT guild_id, command, response FROM ban_config')
                self.config_cache = {str(record['guild_id']): (record['command'], record['response']) for record in records}
                logger.info(f"Loaded {len(self.config_cache)} ban configurations into cache")
            self.cache_ready.set()  # Signal cache is ready
        except Exception as e:
            logger.error(f"Error loading config cache: {e}")
            self.cache_ready.set()  # Signal anyway to prevent hangs

    # Removed reload_responses slash command as responses are now reloaded automatically
        
    @app_commands.command(name="setban", description="Set custom ban command")
    @app_commands.checks.has_permissions(administrator=True)
    async def setban(
        self, 
        interaction: discord.Interaction, 
        command: str
    ):
        if not interaction.guild:
            return await interaction.response.send_message("This command can only be used in a server!", ephemeral=True)
            
        # Store the configuration in database
        try:
            # Use a placeholder for response since we'll be using random responses from file
            placeholder_response = "Random response will be used"
            
            if self.db_pool:
                await self.db_pool.execute(
                    "INSERT INTO ban_config (guild_id, command, response) VALUES ($1, $2, $3) ON CONFLICT (guild_id) DO UPDATE SET command = $2, response = $3",
                    interaction.guild.id, command, placeholder_response
                )
            
            # Update cache
            self.config_cache[str(interaction.guild.id)] = (command, placeholder_response)
            
            # Show number of loaded responses
            response_count = len(self.responses)
            
            await interaction.response.send_message(
                f"<a:heartspar:1335854160322498653> Ban configuration updated!\nCommand: `.{command}`\nThe bot will randomly choose from {response_count} different ban messages when this command is used.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error saving ban config: {e}")
            await interaction.response.send_message(
                "<a:heartspar:1335854160322498653> An error occurred while updating the ban configuration.",
                ephemeral=True
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
            
        # Debug logging
        logger.info(f"Message received: {message.content}")
            
        # Check if message starts with a dot
        if not message.content.startswith('.'):
            return
            
        logger.info(f"Message starts with dot: {message.content}")
        
        # Wait for cache to be ready
        await self.cache_ready.wait()
            
        # Get custom command configuration from cache first
        guild_id = str(message.guild.id)
        config = self.config_cache.get(guild_id)
        
        # If not in cache, try getting from database
        if not config and self.db_pool:
            try:
                row = await self.db_pool.fetchrow(
                    "SELECT command, response FROM ban_config WHERE guild_id = $1",
                    int(guild_id)
                )
                if row:
                    config = (row['command'], row['response'])
                    # Update cache
                    self.config_cache[guild_id] = config
            except Exception as e:
                logger.error(f"Database query error: {e}")
        
        logger.info(f"Config result: {config}")
                
        if not config:
            return
            
        command, response = config
        logger.info(f"Configured command: '{command}', Message: '{message.content}'")
        
        # Check if message matches custom command (with dot prefix)
        dot_command = '.' + command
        if not message.content.startswith(dot_command):
            logger.info(f"Command mismatch: expected '{dot_command}', got '{message.content}'")
            return
            
        # Check permissions - only administrators can use this command
        if not isinstance(message.author, discord.Member) or not message.author.guild_permissions.administrator:
            return await message.reply("<a:heartspar:1335854160322498653> You don't have permission to use this command. Only administrators can use it.")
            
        # Parse mention/user ID and reason - account for dot prefix
        content = message.content[len(dot_command):].strip()
        logger.info(f"Content after command: '{content}'")
        
        # First try to find a mention
        target = None
        
        if message.mentions:
            target = message.mentions[0]
            logger.info(f"Target mention: {target.mention}")
            reason = content[content.find(target.mention) + len(target.mention):].strip()
        else:
            # No mention found, try to extract a user ID
            parts = content.split()
            if not parts:
                return await message.reply("<a:heartspar:1335854160322498653> You need to mention a user or provide a user ID to ban.")
                
            potential_id = parts[0].strip()
            
            # Remove any extra characters that might be in the ID
            for char in ['<', '>', '@', '!']:
                potential_id = potential_id.replace(char, '')
                
            logger.info(f"Potential user ID: {potential_id}")
            
            try:
                user_id = int(potential_id)
                # Try to fetch the user
                try:
                    target = await self.bot.fetch_user(user_id)
                    logger.info(f"Found user by ID: {target}")
                    # Get reason from the remaining content
                    reason = ' '.join(parts[1:]).strip()
                except discord.NotFound:
                    return await message.reply("<a:heartspar:1335854160322498653> Could not find a user with that ID.")
                except Exception as e:
                    logger.error(f"Error fetching user by ID: {e}")
                    return await message.reply("<a:heartspar:1335854160322498653> An error occurred while trying to find the user.")
            except ValueError:
                return await message.reply("<a:heartspar:1335854160322498653> Invalid user ID or mention. Please provide a valid user mention or ID.")
        
        if not target:
            return await message.reply("<a:heartspar:1335854160322498653> You need to mention a user or provide a user ID to ban.")
            
        # If no reason is provided, use a default reason
        if not reason:
            reason = "No reason provided"
            
        try:
            # Check if bot has ban permissions
            if not message.guild.me.guild_permissions.ban_members:
                return await message.reply("<a:heartspar:1335854160322498653> I don't have the ban permission in this server.")
                
            # Check if target can be banned
            if isinstance(target, discord.Member) and target.top_role >= message.guild.me.top_role:
                return await message.reply("<a:heartspar:1335854160322498653> I cannot ban this user as their role is higher than or equal to mine.")
                
            # Try to ban the user
            await message.guild.ban(target, reason=f"Banned by {message.author}: {reason}")
            
            # Get a random response from the responses.txt file
            if self.responses:
                random_response = random.choice(self.responses)
                formatted_response = random_response.replace("@user", target.mention).replace("[reason]", reason)
            else:
                # Fallback to stored response if no responses are loaded from file
                formatted_response = response.replace("@user", target.mention).replace("[reason]", reason)
                
            logger.info(f"Selected ban response: {formatted_response}")
            await message.channel.send(formatted_response)
            
        except discord.Forbidden:
            await message.reply("<a:heartspar:1335854160322498653> I don't have permission to ban that user.")
        except Exception as e:
            logger.error(f"Error banning user: {e}")
            await message.reply("<a:heartspar:1335854160322498653> An error occurred while trying to ban the user. Make sure I have the proper permissions.")

    async def cog_load(self):
        # Setup database
        await self.setup_database()
        
        # Start the file watcher task
        self.file_watcher_task = asyncio.create_task(self.watch_responses_file())
        logger.info("Started response file watcher task")
        
    async def cog_unload(self):
        # Cancel the file watcher task
        if self.file_watcher_task and not self.file_watcher_task.done():
            self.file_watcher_task.cancel()
            
        # Close database connection when cog is unloaded
        if self.db_pool:
            await self.db_pool.close()
            logger.info("Database connection closed")

async def setup(bot: commands.Bot):
    await bot.add_cog(BanCog(bot))
