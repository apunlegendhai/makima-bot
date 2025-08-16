import discord
from discord.ext import commands, tasks
import json
import asyncio
import os

class VoicePersistence(commands.Cog):
    """Cog for maintaining persistent voice channel presence"""
    
    def __init__(self, bot):
        self.bot = bot
        self.database_folder = 'database'
        self.config_file = os.path.join(self.database_folder, 'voice_config.json')
        
        # Create database folder if it doesn't exist
        os.makedirs(self.database_folder, exist_ok=True)
        
        self.config = self.load_config()
        
        # Start the monitoring task
        self.monitor_connections.start()
    
    def load_config(self):
        """Load configuration from file"""
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as f:
                return json.load(f)
        return {}
    
    def save_config(self):
        """Save configuration to file"""
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=4)
    
    def is_admin():
        """Check if user has admin permissions"""
        async def predicate(ctx):
            return ctx.author.guild_permissions.administrator
        return commands.check(predicate)
    
    def find_voice_channel(self, guild, channel_input):
        """Find voice channel by ID or name"""
        channel_input = channel_input.strip()
        
        # Try to parse as channel ID first
        try:
            channel_id = int(channel_input)
            channel = guild.get_channel(channel_id)
            if channel and isinstance(channel, discord.VoiceChannel):
                return channel
        except ValueError:
            pass
        
        # If not a valid ID, search by name
        channel_name = channel_input.strip('#').strip()
        for vc in guild.voice_channels:
            if vc.name.lower() == channel_name.lower():
                return vc
        
        return None
    
    @commands.Cog.listener()
    async def on_ready(self):
        """Auto-reconnect to voice channels on startup"""
        print(f'{self.bot.user} Voice Persistence Cog loaded!')
        
        # Wait for bot to fully initialize
        await asyncio.sleep(3)
        
        # Reconnect to all configured voice channels
        for guild_id_str, guild_config in self.config.items():
            if guild_config.get('always_on', False):
                guild_id = int(guild_id_str)
                guild = self.bot.get_guild(guild_id)
                
                if guild:
                    voice_channel_id = guild_config.get('voice_channel_id')
                    if voice_channel_id:
                        voice_channel = guild.get_channel(voice_channel_id)
                        
                        if voice_channel and isinstance(voice_channel, discord.VoiceChannel):
                            try:
                                # Don't connect if already connected
                                if not guild.voice_client:
                                    await voice_channel.connect()
                                    print(f"✅ Reconnected to {voice_channel.name} in {guild.name}")
                            except Exception as e:
                                print(f"❌ Failed to reconnect to {voice_channel.name}: {e}")
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle voice state changes - rejoin if disconnected"""
        if member == self.bot.user:
            guild_id = str(member.guild.id)
            
            # If bot was disconnected but should always be on
            if (before.channel and not after.channel and 
                guild_id in self.config and 
                self.config[guild_id].get('always_on', False)):
                
                # Wait a moment then reconnect
                await asyncio.sleep(2)
                
                voice_channel_id = self.config[guild_id].get('voice_channel_id')
                if voice_channel_id:
                    voice_channel = member.guild.get_channel(voice_channel_id)
                    if voice_channel:
                        try:
                            await voice_channel.connect()
                            print(f"🔄 Auto-reconnected to {voice_channel.name}")
                        except Exception as e:
                            print(f"❌ Auto-reconnect failed: {e}")
    
    @commands.command(name='vclist')
    @is_admin()
    async def list_voice_channels(self, ctx):
        """List all available voice channels with their IDs"""
        voice_channels = ctx.guild.voice_channels
        
        if not voice_channels:
            embed = discord.Embed(
                title="❌ No Voice Channels",
                description="This server has no voice channels.",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
        
        channel_list = []
        for i, vc in enumerate(voice_channels, 1):
            members_count = len(vc.members)
            channel_list.append(f"{i}. `{vc.name}` \n   📋 ID: `{vc.id}` ({members_count} members)")
        
        embed = discord.Embed(
            title="🎤 Available Voice Channels",
            description="\n".join(channel_list),
            color=0x0099ff
        )
        embed.set_footer(text="Use '.always on channel-name' or '.always on channel-id' to select")
        await ctx.send(embed=embed)
    
    @commands.command(name='vcstatus')
    @is_admin()
    async def vc_status(self, ctx):
        """Check current voice channel status"""
        guild_id = str(ctx.guild.id)
        
        if guild_id not in self.config:
            embed = discord.Embed(
                title="❌ No Configuration Found",
                description="No voice persistence configuration found for this server.\n\nUse `.always on channel-name` or `.always on channel-id` to get started.",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
        
        guild_config = self.config[guild_id]
        always_on = guild_config.get('always_on', False)
        
        if not always_on:
            embed = discord.Embed(
                title="ℹ️ Voice Persistence Status",
                description="**Status:** Disabled\n\nUse `.always on channel-name` or `.always on channel-id` to enable.",
                color=0xffaa00
            )
            await ctx.send(embed=embed)
            return
        
        voice_channel_id = guild_config.get('voice_channel_id')
        voice_channel = ctx.guild.get_channel(voice_channel_id)
        
        if voice_channel:
            connection_status = "🟢 Connected" if ctx.voice_client and ctx.voice_client.channel.id == voice_channel_id else "🔴 Disconnected"
            
            embed = discord.Embed(
                title="ℹ️ Voice Persistence Status",
                color=0x00ff00 if "Connected" in connection_status else 0xff0000
            )
            embed.add_field(name="Status", value="**Enabled**", inline=True)
            embed.add_field(name="Target Channel", value=f"`{voice_channel.name}`", inline=True)
            embed.add_field(name="Channel ID", value=f"`{voice_channel.id}`", inline=True)
            embed.add_field(name="Connection", value=connection_status, inline=False)
            
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                title="❌ Channel Not Found",
                description="The configured voice channel no longer exists.\n\nPlease reconfigure with `.always on channel-name` or `.always on channel-id`",
                color=0xff0000
            )
            await ctx.send(embed=embed)
    
    @commands.command(name='reconnect')
    @is_admin()
    async def force_reconnect(self, ctx):
        """Force reconnect to configured voice channel"""
        guild_id = str(ctx.guild.id)
        
        if (guild_id not in self.config or 
            not self.config[guild_id].get('always_on', False)):
            embed = discord.Embed(
                title="❌ Always-On Mode Disabled",
                description="Always-on mode is not enabled for this server.\n\nUse `.always on channel-name` or `.always on channel-id` first.",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
        
        voice_channel_id = self.config[guild_id].get('voice_channel_id')
        voice_channel = ctx.guild.get_channel(voice_channel_id)
        
        if not voice_channel:
            embed = discord.Embed(
                title="❌ Channel Not Found",
                description="Configured voice channel not found.\n\nPlease reconfigure with `.always on channel-name` or `.always on channel-id`",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
        
        try:
            if ctx.voice_client:
                await ctx.voice_client.disconnect()
                await asyncio.sleep(1)
            
            await voice_channel.connect()
            
            embed = discord.Embed(
                title="✅ Reconnection Successful",
                description=f"Successfully reconnected to `{voice_channel.name}` (ID: `{voice_channel.id}`)",
                color=0x00ff00
            )
            await ctx.send(embed=embed)
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Reconnection Failed",
                description=f"Failed to reconnect: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
    
    @tasks.loop(seconds=45)
    async def monitor_connections(self):
        """Monitor voice connections and reconnect if needed"""
        try:
            for guild_id_str, guild_config in self.config.items():
                if guild_config.get('always_on', False):
                    guild_id = int(guild_id_str)
                    guild = self.bot.get_guild(guild_id)
                    
                    if guild:
                        voice_client = guild.voice_client
                        voice_channel_id = guild_config.get('voice_channel_id')
                        
                        if voice_channel_id and not voice_client:
                            voice_channel = guild.get_channel(voice_channel_id)
                            if voice_channel:
                                try:
                                    await voice_channel.connect()
                                    print(f"🔄 Monitor reconnected to {voice_channel.name} in {guild.name}")
                                except Exception as e:
                                    print(f"❌ Monitor reconnect failed for {guild.name}: {e}")
        
        except Exception as e:
            print(f"❌ Monitor task error: {e}")
    
    @monitor_connections.before_loop
    async def before_monitor(self):
        """Wait for bot to be ready before starting monitor"""
        await self.bot.wait_until_ready()
    
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        """Handle command errors for this cog"""
        if isinstance(error, commands.CheckFailure):
            embed = discord.Embed(
                title="❌ Permission Denied",
                description="You need **Administrator** permissions to use voice persistence commands!",
                color=0xff0000
            )
            await ctx.send(embed=embed)
        elif isinstance(error, commands.MissingRequiredArgument):
            embed = discord.Embed(
                title="❌ Missing Arguments",
                description="Missing required arguments. Use `.help` for command usage.",
                color=0xff0000
            )
            await ctx.send(embed=embed)
    
    # Custom message handler for space-based commands
    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle custom prefix commands with spaces"""
        if message.author.bot:
            return
        
        content = message.content.lower()
        
        # Check for admin permissions
        if not message.author.guild_permissions.administrator:
            return
        
        # Handle ".always on " command (with space)
        if content.startswith('.always on '):
            channel_input = message.content[11:].strip()  # Remove ".always on " prefix
            
            if not channel_input:
                embed = discord.Embed(
                    title="❌ Missing Channel Information",
                    description="Please specify a voice channel name or ID:\n`.always on General` or `.always on 123456789`",
                    color=0xff0000
                )
                await message.channel.send(embed=embed)
                return
            
            await self.handle_always_on(message, channel_input)
        
        # Handle ".always off " command (with space)
        elif content == '.always off ':
            await self.handle_always_off(message)
    
    async def handle_always_on(self, message, channel_input):
        """Handle the always on command with space - supports both ID and name"""
        guild_id = str(message.guild.id)
        
        # Find voice channel by ID or name
        channel = self.find_voice_channel(message.guild, channel_input)
        
        if channel is None:
            # Show available channels with IDs
            available_channels = []
            for vc in message.guild.voice_channels:
                available_channels.append(f"• `{vc.name}` (ID: `{vc.id}`)")
            
            embed = discord.Embed(
                title="❌ Voice Channel Not Found",
                description=f"Channel `{channel_input}` not found!\n\n**Available voice channels:**\n" + 
                           "\n".join(available_channels),
                color=0xff0000
            )
            await message.channel.send(embed=embed)
            return
        
        # Save configuration
        if guild_id not in self.config:
            self.config[guild_id] = {}
        
        self.config[guild_id]['always_on'] = True
        self.config[guild_id]['voice_channel_id'] = channel.id
        self.config[guild_id]['channel_name'] = channel.name
        self.save_config()
        
        # Join the voice channel
        try:
            voice_client = message.guild.voice_client
            if voice_client:
                await voice_client.move_to(channel)
            else:
                await channel.connect()
            
            embed = discord.Embed(
                title="✅ Always-On Mode Enabled",
                description=f"Bot will now always stay in `{channel.name}`\n📋 Channel ID: `{channel.id}`\n\nBot will automatically reconnect if disconnected or restarted.",
                color=0x00ff00
            )
            await message.channel.send(embed=embed)
            
        except discord.ClientException as e:
            if "already connected" in str(e).lower():
                await voice_client.move_to(channel)
                embed = discord.Embed(
                    title="✅ Always-On Mode Enabled",
                    description=f"Moved to and will always stay in `{channel.name}`\n📋 Channel ID: `{channel.id}`",
                    color=0x00ff00
                )
                await message.channel.send(embed=embed)
            else:
                embed = discord.Embed(
                    title="❌ Connection Failed",
                    description=f"Failed to join voice channel: {str(e)}",
                    color=0xff0000
                )
                await message.channel.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="❌ Connection Failed",
                description=f"Failed to join voice channel: {str(e)}",
                color=0xff0000
            )
            await message.channel.send(embed=embed)
    
    async def handle_always_off(self, message):
        """Handle the always off command with space"""
        guild_id = str(message.guild.id)
        
        # Disable always-on mode
        if guild_id in self.config:
            self.config[guild_id]['always_on'] = False
            self.save_config()
        
        # Disconnect from voice
        voice_client = message.guild.voice_client
        if voice_client:
            await voice_client.disconnect()
        
        embed = discord.Embed(
            title="✅ Always-On Mode Disabled",
            description="Bot has disconnected from voice channel and will not auto-reconnect.",
            color=0x00ff00
        )
        await message.channel.send(embed=embed)
    
    def cog_unload(self):
        """Clean up when cog is unloaded"""
        self.monitor_connections.cancel()

# Setup function to add the cog to the bot
async def setup(bot):
    await bot.add_cog(VoicePersistence(bot))

