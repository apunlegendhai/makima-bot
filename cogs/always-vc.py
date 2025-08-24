import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import json
import asyncio
import datetime
import time
import logging
from collections import defaultdict
from typing import List, Optional

# Set up logging
logger = logging.getLogger('alwaysvc')
logger.setLevel(logging.INFO)

# Create logs directory if it doesn't exist
os.makedirs('logs', exist_ok=True)

# Set up file handler if not already configured
if not logger.handlers:
    handler = logging.FileHandler(filename='logs/bot.log', encoding='utf-8', mode='a')
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)
    logger.info('Logger initialized')

class ConnectionManager:
    """Handle connection attempts with rate limiting."""
    def __init__(self):
        self.connection_attempts = defaultdict(int)
        self.last_attempt = defaultdict(float)
        self._connection_locks = defaultdict(asyncio.Lock)
        self._voice_state_locks = defaultdict(asyncio.Lock)  # Add lock for voice state changes

    async def attempt_connection(self, guild_id: str, channel: discord.VoiceChannel) -> bool:
        """Attempt to connect to a voice channel with rate limiting."""
        current_time = time.time()
        
        # Use a lock to prevent multiple simultaneous connection attempts
        async with self._connection_locks[guild_id]:
            # Check cooldown
            if current_time - self.last_attempt[guild_id] < 30:
                logger.debug(f"Connection attempt for guild {guild_id} is on cooldown")
                return False
                
            # Check if channel still exists
            if not channel or not channel.guild:
                logger.error(f"Channel {channel.id if channel else 'None'} no longer exists")
                return False
                
            # Check if we have permission to join
            permissions = channel.permissions_for(channel.guild.me)
            if not permissions.connect or not permissions.speak:
                logger.error(f"Missing permissions to join channel {channel.id} in guild {guild_id}")
                return False
                
            self.last_attempt[guild_id] = current_time
            try:
                # Check if we're already connected
                if channel.guild.voice_client:
                    if channel.guild.voice_client.channel.id == channel.id:
                        logger.debug(f"Already connected to channel {channel.id}")
                        return True
                    else:
                        logger.info(f"Disconnecting from previous channel in guild {guild_id}")
                        await channel.guild.voice_client.disconnect(force=True)
                        await asyncio.sleep(1)
                
                try:
                    # Clean up any existing voice connections first
                    try:
                        if channel.guild.voice_client:
                            await channel.guild.voice_client.disconnect(force=True)
                            logger.info(f"Disconnected from previous voice in guild {guild_id}")
                        await asyncio.sleep(5)  # Wait longer after disconnect
                    except Exception as e:
                        logger.error(f"Error during cleanup: {str(e)}")
                        await asyncio.sleep(5)  # Wait anyway if cleanup fails

                    # Try multiple connection attempts with increasing delays
                    for attempt in range(3):
                        try:
                            # Reset voice state before attempting connection
                            await channel.guild.change_voice_state(channel=None)
                            await asyncio.sleep(2)

                            logger.info(f"Attempting connection {attempt + 1}/3 to channel {channel.id}")
                            voice_client = await channel.connect(
                                timeout=45.0,  # Longer timeout
                                self_mute=True,
                                self_deaf=True,
                                reconnect=True
                            )
                            
                            # Wait and verify connection
                            await asyncio.sleep(3)
                            if voice_client and voice_client.is_connected():
                                voice_client.self_mute = True
                                voice_client.self_deaf = True
                                self.connection_attempts[guild_id] = 0
                                logger.info(f"Successfully connected and verified connection to channel {channel.id}")
                                return True
                            
                        except Exception as e:
                            logger.error(f"Connection attempt {attempt + 1} failed: {str(e)}")
                            # Cleanup after failed attempt
                            try:
                                await channel.guild.change_voice_state(channel=None)
                            except:
                                pass
                            
                            if attempt < 2:
                                delay = 5 * (attempt + 1)  # 5s, 10s between attempts
                                logger.info(f"Waiting {delay}s before next attempt")
                                await asyncio.sleep(delay)
                            
                    logger.warning(f"All connection attempts failed for channel {channel.id}")
                    return False
                    
                except Exception as e:
                    logger.error(f"Fatal connection error for channel {channel.id} in guild {guild_id}: {str(e)}")
                    return False
                
            except asyncio.TimeoutError:
                logger.error(f"Connection attempt timed out for guild {guild_id}")
                self.connection_attempts[guild_id] += 1
                return False
            except Exception as e:
                logger.error(f"Error connecting to channel {channel.id} in guild {guild_id}: {str(e)}")
                self.connection_attempts[guild_id] += 1
                return False

class AlwaysVC(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_folder = 'database'
        self.db_file = os.path.join(self.db_folder, 'vc_data.json')
        self.guild_configs = {}
        self.connection_manager = ConnectionManager()
        self.load_data()
        self._guild_locks = defaultdict(asyncio.Lock)
        self._rejoin_cooldown = defaultdict(float)
        self._ready = False
        
        # Start health check when bot is ready
        self.bot.loop.create_task(self._start_health_check())
        
    async def _start_health_check(self):
        await self.bot.wait_until_ready()
        if not self.health_check.is_running():
            self.health_check.start()
            logger.info("Started voice channel health check")

    def load_data(self):
        """Load guild configurations from file."""
        try:
            if not os.path.exists(self.db_folder):
                os.makedirs(self.db_folder)
                logger.info(f"Created database folder: {self.db_folder}")
                
            if os.path.isfile(self.db_file):
                with open(self.db_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.guild_configs = data.get('guild_configs', {})
                logger.info(f"Loaded configurations for {len(self.guild_configs)} guilds")
            else:
                self.guild_configs = {}
                logger.info("No existing configuration file found, starting fresh")
                
        except Exception as e:
            logger.error(f"Error loading data: {str(e)}")
            self.guild_configs = {}

    def save_data(self):
        """Save guild configurations to file with backup."""
        try:
            # Create backup of current file if it exists
            if os.path.isfile(self.db_file):
                backup_path = f"{self.db_file}.backup"
                os.replace(self.db_file, backup_path)
                logger.info(f"Created backup at {backup_path}")
            
            # Write new data
            with open(self.db_file, 'w', encoding='utf-8') as f:
                json.dump({'guild_configs': self.guild_configs}, f, indent=2)
            logger.info(f"Saved configurations for {len(self.guild_configs)} guilds")
            
        except Exception as e:
            logger.error(f"Error saving data: {str(e)}")
            # If we have a backup, restore it
            backup_path = f"{self.db_file}.backup"
            if os.path.isfile(backup_path):
                os.replace(backup_path, self.db_file)
                logger.info("Restored backup file after save error")

    async def join_vc(self, guild, attempt=1):
        """Join a voice channel in the specified guild."""
        guild_id = str(guild.id)
        max_attempts = 5
        
        try:
            # Get configuration
            config = self.guild_configs.get(guild_id)
            if not config:
                logger.debug(f"No config found for guild {guild_id}")
                return
                
            vc_channel_id = config.get('vc_channel_id')
            if not vc_channel_id:
                logger.debug(f"No voice channel configured for guild {guild_id}")
                return
                
            # Add cooldown check to prevent spam
            now = time.time()
            if now - self._rejoin_cooldown.get(guild_id, 0) < 30:  # 30 second cooldown
                logger.debug(f"Join attempt for guild {guild_id} is on cooldown")
                return
            self._rejoin_cooldown[guild_id] = now

            vc_channel = self.bot.get_channel(vc_channel_id)
            if not vc_channel:
                logger.warning(f"Configured channel {vc_channel_id} not found in guild {guild_id}")
                config['vc_channel_id'] = None
                self.save_data()
                return

            # Check permissions first
            permissions = vc_channel.permissions_for(guild.me)
            if not permissions.connect or not permissions.speak:
                logger.error(f"Missing required permissions for channel {vc_channel_id} in guild {guild_id}")
                return

            async with self._guild_locks[guild_id]:
                # Check if we're already in the right channel
                if guild.voice_client and guild.voice_client.channel.id == vc_channel.id:
                    # Verify self mute and deafen state
                    if config.get('mute_on_join', False) and (not guild.voice_client.self_mute or not guild.voice_client.self_deaf):
                        guild.voice_client.self_mute = True
                        guild.voice_client.self_deaf = True
                        logger.info(f"Restored self mute and deafen state in guild {guild_id}")
                    return

                # Apply configured join delay
                join_delay = config.get('join_delay', 3)
                if join_delay > 0:
                    logger.debug(f"Waiting {join_delay}s before joining channel in guild {guild_id}")
                    await asyncio.sleep(join_delay)
                    
                # Attempt connection
                connected = await self.connection_manager.attempt_connection(guild_id, vc_channel)
                if connected:
                    logger.info(f"Successfully connected to channel {vc_channel_id} in guild {guild_id}")
                    if config.get('mute_on_join', False):
                        await asyncio.sleep(1)  # Wait for connection to stabilize
                        guild.voice_client.self_mute = True
                        guild.voice_client.self_deaf = True
                        logger.info(f"Applied self mute and deafen state in guild {guild_id}")
                else:
                    logger.warning(f"Failed to connect to channel {vc_channel_id} in guild {guild_id}")
                    if attempt < max_attempts:
                        delay = (2 ** attempt)
                        logger.info(f"Retrying in {delay}s (attempt {attempt + 1}/{max_attempts})")
                        await asyncio.sleep(delay)
                        await self.join_vc(guild, attempt + 1)
                    
        except discord.Forbidden as e:
            logger.error(f"Forbidden error in guild {guild_id}: {str(e)}")
        except discord.HTTPException as e:
            logger.error(f"HTTP error in guild {guild_id}: {str(e)}")
            if attempt < max_attempts:
                delay = (2 ** attempt)
                logger.info(f"Retrying in {delay}s (attempt {attempt + 1}/{max_attempts})")
                await asyncio.sleep(delay)
                await self.join_vc(guild, attempt + 1)
        except Exception as e:
            logger.error(f"Unexpected error in guild {guild_id}: {str(e)}")

            try:
                connected = await self.connection_manager.attempt_connection(guild_id, vc_channel)
                if connected:
                    if config.get('mute_on_join', False):
                        await asyncio.sleep(1)  # Wait for connection to stabilize
                        if guild.voice_client:
                            try:
                                guild.voice_client.self_mute = True
                                guild.voice_client.self_deaf = True
                            except Exception as mute_error:
                                print(f"Failed to self-mute/deafen in guild {guild.name}: {mute_error}")
            except Exception as e:
                print(f"Error connecting to VC on guild {guild.name} attempt {attempt}: {e}")
                max_attempts = 5
                if attempt < max_attempts:
                    delay = (2 ** attempt)
                    await asyncio.sleep(delay)
                    await self.join_vc(guild, attempt + 1)

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info("AlwaysVC cog ready, waiting 5 seconds before connecting to voice channels...")
        await asyncio.sleep(5)  # Wait for bot to fully initialize
        
        # Attempt to join configured voice channels
        for guild in self.bot.guilds:
            guild_id = str(guild.id)
            config = self.guild_configs.get(guild_id, {})
            
            if config.get('vc_channel_id') and config.get('auto_rejoin', True):
                logger.info(f"Attempting to rejoin voice channel in guild {guild.name} ({guild_id})")
                await self.join_vc(guild)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member != self.bot.user:
            return

        guild_id = str(before.channel.guild.id if before.channel else after.channel.guild.id)
        config = self.guild_configs.get(guild_id)
        
        # If no config or auto-rejoin is disabled, don't do anything
        if not config or not config.get('auto_rejoin', True):
            return
            
        # If we're disconnected (moved from a channel to no channel)
        if before.channel and not after.channel:
            now = time.time()
            last_time = self._rejoin_cooldown.get(guild_id, 0)
            
            if now - last_time < 10:  # Prevent spam reconnects
                return
                
            self._rejoin_cooldown[guild_id] = now
            await self.smart_rejoin(before.channel.guild)
            
        # If we're moved to a different channel and we should be in a specific one
        elif after.channel and config.get('vc_channel_id'):
            if after.channel.id != config['vc_channel_id']:
                await self.join_vc(after.channel.guild)

    async def smart_rejoin(self, guild, attempt=1):
        max_attempts = 5
        if attempt > max_attempts:
            return
        await asyncio.sleep(2 ** attempt)
        await self.join_vc(guild, attempt)

    @tasks.loop(seconds=30)  # Check more frequently
    async def health_check(self):
        """Periodic check to ensure voice connections are maintained."""
        try:
            for guild_id, config in list(self.guild_configs.items()):  # Use list to allow dict modification
                if not config.get('auto_rejoin', True):
                    continue
                    
                try:
                    guild = self.bot.get_guild(int(guild_id))
                    if not guild:
                        logger.warning(f"Guild {guild_id} no longer exists or bot was removed")
                        continue
                        
                    vc_channel_id = config.get('vc_channel_id')
                    if not vc_channel_id:
                        continue
                        
                    channel = guild.get_channel(vc_channel_id)
                    if not channel:
                        logger.warning(f"Channel {vc_channel_id} in guild {guild_id} no longer exists")
                        config['vc_channel_id'] = None
                        self.save_data()
                        continue
                        
                    # Check if we should be connected but aren't
                    if not guild.voice_client or guild.voice_client.channel.id != vc_channel_id:
                        logger.info(f"Reconnecting to channel {vc_channel_id} in guild {guild_id}")
                        await self.join_vc(guild)
                        
                    # Check if we're properly self-muted and self-deafened if we should be
                    elif config.get('mute_on_join', False) and guild.voice_client:
                        if not guild.voice_client.self_mute or not guild.voice_client.self_deaf:
                            logger.info(f"Restoring self-mute and self-deaf state in guild {guild_id}")
                            guild.voice_client.self_mute = True
                            guild.voice_client.self_deaf = True
                            
                except Exception as e:
                    logger.error(f"Error in health check for guild {guild_id}: {str(e)}")
                    
        except Exception as e:
            logger.error(f"Error in health check: {str(e)}")

    async def cog_unload(self):
        """Cleanup when cog is unloaded."""
        try:
            # Cancel health check task
            self.health_check.cancel()
            
            # Disconnect from all voice channels
            for guild in self.bot.guilds:
                if guild.voice_client:
                    await guild.voice_client.disconnect(force=True)
            
            # Save current state
            self.save_data()
            
            logger.info("Cog unloaded successfully")
        except Exception as e:
            logger.error(f"Error during cog unload: {str(e)}")

    async def cog_load(self):
        """Setup when cog is loaded."""
        try:
            logger.info("Initializing AlwaysVC cog")
            # Ensure database directory exists
            os.makedirs(self.db_folder, exist_ok=True)
            self.load_data()
            logger.info("Cog loaded successfully")
        except Exception as e:
            logger.error(f"Error during cog load: {str(e)}")
            raise

    # CORRECTED SLASH COMMANDS - Use app_commands instead
    @app_commands.command(name='always-vc', description='Setup or stop the bot staying in a VC')
    @app_commands.describe(channel="Voice channel to always stay in")
    @app_commands.default_permissions(administrator=True)
    async def always_vc(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        await interaction.response.defer()
        
        try:
            guild_id = str(interaction.guild.id)
            config = self.guild_configs.get(guild_id, {})
            current_channel_id = config.get('vc_channel_id')
            
            logger.info(f"Always-VC command received for channel {channel.name} ({channel.id}) in guild {interaction.guild.name}")

            # Verify permissions first
            permissions = channel.permissions_for(interaction.guild.me)
            if not permissions.connect or not permissions.speak:
                await interaction.followup.send(f"❌ I don't have permission to join **{channel.name}**!")
                logger.error(f"Missing permissions for channel {channel.id} in guild {guild_id}")
                return

            # If we're already set to this channel, stop staying in it
            if current_channel_id == channel.id:
                config['vc_channel_id'] = None
                config['auto_rejoin'] = False  # Disable auto-rejoin when stopping
                self.guild_configs[guild_id] = config
                self.save_data()
                logger.info(f"Stopping always-VC in channel {channel.name} ({channel.id})")
                
                # Disconnect if connected
                if interaction.guild.voice_client:
                    await interaction.guild.voice_client.disconnect(force=True)
                
                await interaction.followup.send(f"✅ Stopped staying in VC **{channel.name}**.")
                return

            # Set up new channel configuration
            config['vc_channel_id'] = channel.id
            config['auto_rejoin'] = True  # Enable auto-rejoin for new channel
            config.setdefault('mute_on_join', True)  # Default to muted
            config.setdefault('join_delay', 5)
            self.guild_configs[guild_id] = config
            self.save_data()
            logger.info(f"Configured always-VC for channel {channel.name} ({channel.id})")
            
            # Disconnect from current channel if in a different one
            if interaction.guild.voice_client:
                logger.info(f"Disconnecting from current channel in {interaction.guild.name}")
                await interaction.guild.voice_client.disconnect(force=True)
                await asyncio.sleep(2)  # Wait a bit before reconnecting
            
            # Try to connect to new channel
            await interaction.followup.send(f"🔄 Connecting to VC **{channel.name}**...")
            
            # Attempt connection with retry logic
            for attempt in range(3):
                try:
                    # Direct connection attempt
                    voice_client = await channel.connect(timeout=15.0, self_mute=True, self_deaf=True, reconnect=True)
                    if voice_client and voice_client.is_connected():
                        voice_client.self_mute = True
                        voice_client.self_deaf = True
                        logger.info(f"Successfully connected to channel {channel.name} ({channel.id})")
                        await interaction.followup.send(f"✅ Successfully connected to VC **{channel.name}**!")
                        return
                except Exception as e:
                    logger.error(f"Connection attempt {attempt + 1} failed: {str(e)}")
                    if attempt < 2:  # Don't wait on last attempt
                        await asyncio.sleep(2 ** attempt)
            
            # If we get here, all connection attempts failed
            logger.error(f"All connection attempts failed for channel {channel.name} ({channel.id})")
            await interaction.followup.send(f"⚠️ Failed to connect to VC **{channel.name}**. Will keep trying...")
            
        except Exception as e:
            logger.error(f"Error in always-vc command: {str(e)}")
            await interaction.followup.send(f"❌ An error occurred: {str(e)}")

    @app_commands.command(name='always-stats', description='Show voice channel statistics')
    @app_commands.default_permissions(administrator=True)
    async def vc_stats(self, interaction: discord.Interaction):
        await interaction.response.defer()  # Defer response immediately
        
        try:
            guild_id = str(interaction.guild.id)
            config = self.guild_configs.get(guild_id)
            if not config or not config.get('vc_channel_id'):
                await interaction.followup.send("❌ No always-vc configured for this server.")
                return
                
            embed = discord.Embed(title="🎤 Voice Channel Statistics", color=0x00ff00)
            chan_id = config['vc_channel_id']
            embed.add_field(name="Current Channel", value=f"<#{chan_id}>")
            embed.add_field(name="Auto-Rejoin", value="✅ Enabled" if config.get('auto_rejoin') else "❌ Disabled")
            embed.add_field(name="Mute on Join", value="✅ Enabled" if config.get('mute_on_join') else "❌ Disabled")
            embed.add_field(name="Join Delay", value=f"{config.get('join_delay', 3)}s")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ An error occurred: {str(e)}")

    async def get_join_delay_choices(self, _: discord.Interaction) -> List[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=f"{i} seconds", value=str(i))
            for i in [1, 2, 3, 5, 10, 15, 30, 60]
        ]

    @app_commands.command(name='always-config', description='Configure bot settings')
    @app_commands.describe(
        setting="Setting to change",
        value="Select the new value"
    )
    @app_commands.choices(
        setting=[
            app_commands.Choice(name="🔄 Auto Rejoin (Bot rejoins if disconnected)", value="auto_rejoin"),
            app_commands.Choice(name="⏲️ Join Delay (Seconds to wait before joining)", value="join_delay"),
            app_commands.Choice(name="🔇 Mute on Join (Bot joins muted)", value="mute_on_join")
        ],
        value=[
            app_commands.Choice(name="✅ Enabled", value="true"),
            app_commands.Choice(name="❌ Disabled", value="false")
        ]
    )
    @app_commands.default_permissions(administrator=True)
    async def vc_config(self, interaction: discord.Interaction, setting: app_commands.Choice[str], value: app_commands.Choice[str]):
        await interaction.response.defer()  # Defer response immediately
        
        try:
            guild_id = str(interaction.guild.id)
            setting = setting.value
            value_str = value.value
            
            # Get or create config for this guild
            config = self.guild_configs.get(guild_id, {})
            
            if setting in ['auto_rejoin', 'mute_on_join']:
                config[setting] = value_str == 'true'  # Compare directly with string
            elif setting == 'join_delay':
                if not value_str.isdigit() or not (1 <= int(value_str) <= 60):
                    await interaction.followup.send("❌ Join Delay must be a number between 1 and 60 seconds")
                    return
                config[setting] = int(value_str)
            
            self.guild_configs[guild_id] = config
            self.save_data()
            
            # Format the value for display
            display_value = "✅ Enabled" if config[setting] == True else "❌ Disabled"
            if setting == 'join_delay':
                display_value = f"{config[setting]} seconds"
                
            await interaction.followup.send(f"✅ Updated **{setting}** to {display_value}")
            
        except Exception as e:
            await interaction.followup.send(f"❌ An error occurred: {str(e)}")
            
        config = self.guild_configs.get(guild_id, {})
        if setting in ['auto_rejoin', 'mute_on_join']:
            config[setting] = value.lower() == 'true'
        else:
            config[setting] = int(value)
            
        self.guild_configs[guild_id] = config
        self.save_data()
        await interaction.response.send_message(f"✅ Updated {setting} to {value}")

    async def create_backup(self):
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = os.path.join(self.db_folder, f'backup_{timestamp}.json')
        with open(backup_file, 'w') as f:
            json.dump(self.guild_configs, f, indent=2)

    @app_commands.command(name='vc-backup', description='Create configuration backup')
    @app_commands.default_permissions(administrator=True)
    async def backup_config(self, interaction: discord.Interaction):
        await interaction.response.defer()  # Defer response immediately
        
        try:
            await self.create_backup()
            await interaction.followup.send("✅ Configuration backup created successfully!")
        except Exception as e:
            await interaction.followup.send(f"❌ An error occurred while creating backup: {str(e)}")

async def setup(bot):
    cog = AlwaysVC(bot)
    await bot.add_cog(cog)
