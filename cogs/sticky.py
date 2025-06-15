import discord
from discord.ext import commands, tasks
import motor.motor_asyncio
import os
from dotenv import load_dotenv
import asyncio
from datetime import datetime, timedelta
import logging
from collections import defaultdict, deque

load_dotenv()

class StickyMessages(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mongo_client = motor.motor_asyncio.AsyncIOMotorClient(os.getenv('MONGO_URL'))
        self.db = self.mongo_client.discord_bot
        self.stickies = self.db.stickies
        
        # Rate limiting with automatic cleanup
        self.rate_limits = defaultdict(deque)
        
        # Track last sticky message IDs with automatic cleanup
        self.last_sticky_messages = {}
        
        # Repost queue with size limit to prevent memory issues
        self.repost_queue = asyncio.Queue(maxsize=1000)
        self.repost_task = None
        
        # Track processing channels to prevent duplicates
        self.processing_channels = set()
        
        # Start tasks
        self.cleanup_task.start()
        self.periodic_repost.start()
        self.recovery_task.start()
        
        # Start queue processor
        self.repost_task = asyncio.create_task(self._process_repost_queue())
    
    def cog_unload(self):
        self.periodic_repost.cancel()
        self.cleanup_task.cancel()
        self.recovery_task.cancel()
        if self.repost_task:
            self.repost_task.cancel()
        asyncio.create_task(self.mongo_client.close())
    
    async def _process_repost_queue(self):
        """Process repost requests from queue to prevent race conditions"""
        while True:
            try:
                # Use timeout to prevent indefinite blocking
                channel_id, force = await asyncio.wait_for(
                    self.repost_queue.get(), timeout=30.0
                )
                
                # Skip if already processing this channel
                if channel_id in self.processing_channels:
                    continue
                    
                channel = self.bot.get_channel(channel_id)
                if channel:
                    self.processing_channels.add(channel_id)
                    try:
                        await self._repost_sticky_internal(channel, force)
                    finally:
                        self.processing_channels.discard(channel_id)
                        
                await asyncio.sleep(0.5)  # Small delay between reposts
            except asyncio.TimeoutError:
                continue  # Keep loop alive even if queue is empty
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Error processing repost queue: {e}")
                await asyncio.sleep(1)  # Brief pause before continuing
    
    @tasks.loop(minutes=10)
    async def cleanup_task(self):
        """Clean up old rate limit entries and orphaned sticky messages"""
        now = datetime.utcnow()
        
        # Clean rate limits
        for channel_id in list(self.rate_limits.keys()):
            while self.rate_limits[channel_id] and now - self.rate_limits[channel_id][0] > timedelta(seconds=5):
                self.rate_limits[channel_id].popleft()
            
            if not self.rate_limits[channel_id]:
                del self.rate_limits[channel_id]
        
        # Clean orphaned sticky message references
        orphaned_channels = []
        for channel_id in list(self.last_sticky_messages.keys()):
            channel = self.bot.get_channel(channel_id)
            if not channel:
                orphaned_channels.append(channel_id)
        
        for channel_id in orphaned_channels:
            del self.last_sticky_messages[channel_id]
    
    @tasks.loop(count=1)
    async def recovery_task(self):
        """Recover sticky message IDs after bot restart"""
        try:
            async for sticky in self.stickies.find():
                channel = self.bot.get_channel(sticky['channel_id'])
                if not channel:
                    continue
                
                # Look for recent messages that might be sticky messages
                try:
                    async for message in channel.history(limit=10):
                        if (message.author == self.bot.user and 
                            message.content == sticky['text']):
                            self.last_sticky_messages[channel.id] = message.id
                            break
                except (discord.Forbidden, discord.HTTPException):
                    continue
        except Exception as e:
            logging.error(f"Error in recovery task: {e}")
    
    @recovery_task.before_loop
    async def before_recovery_task(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)  # Wait a bit after bot is ready
    
    async def is_rate_limited(self, channel_id):
        """Check if channel is rate limited (max 2 reposts per 5 seconds)"""
        now = datetime.utcnow()
        rate_limit_queue = self.rate_limits[channel_id]
        
        # Remove old timestamps
        while rate_limit_queue and now - rate_limit_queue[0] > timedelta(seconds=5):
            rate_limit_queue.popleft()
        
        return len(rate_limit_queue) >= 2
    
    async def add_rate_limit(self, channel_id):
        """Add current timestamp to rate limit tracking"""
        self.rate_limits[channel_id].append(datetime.utcnow())
    
    async def get_sticky(self, guild_id, channel_id):
        """Get sticky config for a specific channel"""
        try:
            return await self.stickies.find_one({
                'guild_id': guild_id,
                'channel_id': channel_id
            })
        except Exception as e:
            logging.error(f"Database error getting sticky: {e}")
            return None
    
    async def set_sticky(self, guild_id, channel_id, text):
        """Set or update sticky for a channel"""
        try:
            await self.stickies.update_one(
                {'guild_id': guild_id, 'channel_id': channel_id},
                {
                    '$set': {
                        'guild_id': guild_id,
                        'channel_id': channel_id,
                        'text': text,
                        'last_repost': datetime.utcnow()
                    }
                },
                upsert=True
            )
            return True
        except Exception as e:
            logging.error(f"Database error setting sticky: {e}")
            return False
    
    async def remove_sticky(self, guild_id, channel_id):
        """Remove sticky for a channel"""
        try:
            result = await self.stickies.delete_one({
                'guild_id': guild_id,
                'channel_id': channel_id
            })
            return result.deleted_count > 0
        except Exception as e:
            logging.error(f"Database error removing sticky: {e}")
            return False
    
    async def _repost_sticky_internal(self, channel, force=False):
        """Internal method to repost sticky message"""
        if not force and await self.is_rate_limited(channel.id):
            return False
        
        sticky = await self.get_sticky(channel.guild.id, channel.id)
        if not sticky:
            return False
        
        try:
            # Delete previous sticky message if it exists
            if channel.id in self.last_sticky_messages:
                try:
                    old_msg = await channel.fetch_message(self.last_sticky_messages[channel.id])
                    await old_msg.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
                except Exception as e:
                    logging.error(f"Error deleting old sticky: {e}")
                finally:
                    # Clear the reference regardless of deletion success
                    del self.last_sticky_messages[channel.id]
            
            # Send new sticky message
            message = await channel.send(sticky['text'])
            self.last_sticky_messages[channel.id] = message.id
            
            # Update last repost time
            try:
                await self.stickies.update_one(
                    {'guild_id': channel.guild.id, 'channel_id': channel.id},
                    {'$set': {'last_repost': datetime.utcnow()}}
                )
            except Exception as e:
                logging.error(f"Database error updating repost time: {e}")
            
            if not force:  # Only add rate limit for non-forced reposts
                await self.add_rate_limit(channel.id)
            return True
            
        except discord.HTTPException as e:
            if e.status == 429:  # Rate limited
                retry_after = float(e.response.headers.get('Retry-After', 1))
                await asyncio.sleep(min(retry_after, 10))  # Cap retry delay at 10 seconds
                if not force:  # Only retry once for non-forced reposts
                    return await self._repost_sticky_internal(channel, True)
            else:
                logging.error(f"Failed to repost sticky in {channel.id}: {e}")
        except Exception as e:
            logging.error(f"Unexpected error reposting sticky in {channel.id}: {e}")
        
        return False
    
    async def repost_sticky(self, channel, force=False):
        """Queue a sticky repost to prevent race conditions"""
        try:
            # Check if there's actually a sticky configured
            sticky = await self.get_sticky(channel.guild.id, channel.id)
            if not sticky:
                return
                
            # Use put_nowait to avoid blocking if queue is full
            self.repost_queue.put_nowait((channel.id, force))
        except asyncio.QueueFull:
            logging.warning(f"Repost queue full, dropping request for channel {channel.id}")
        except Exception as e:
            logging.error(f"Error queuing repost: {e}")
    
    @commands.Cog.listener()
    async def on_message(self, message):
        """Repost sticky when user sends message"""
        if message.author.bot:
            return
            
        await self.repost_sticky(message.channel)
    
    @tasks.loop(minutes=15)  # Increased interval to reduce load
    async def periodic_repost(self):
        """Repost all stickies periodically"""
        try:
            count = 0
            async for sticky in self.stickies.find():
                channel = self.bot.get_channel(sticky['channel_id'])
                if channel:
                    await self.repost_sticky(channel, force=True)
                    count += 1
                    if count % 5 == 0:  # Pause every 5 channels
                        await asyncio.sleep(2)
                    else:
                        await asyncio.sleep(0.5)
        except Exception as e:
            logging.error(f"Error in periodic repost: {e}")
    
    @periodic_repost.before_loop
    async def before_periodic_repost(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)  # Wait longer before starting periodic reposts
    
    @cleanup_task.before_loop
    async def before_cleanup_task(self):
        await self.bot.wait_until_ready()
    
    @commands.command(name='stick')
    @commands.has_permissions(administrator=True)
    async def stick(self, ctx, *, text):
        """Create or update a sticky message in this channel"""
        # Validate message length
        if len(text) > 2000:
            await ctx.send("❌ Sticky message cannot exceed 2000 characters.", delete_after=10)
            return
        
        # Validate text content
        if not text.strip():
            await ctx.send("❌ Sticky message cannot be empty.", delete_after=10)
            return
        
        success = await self.set_sticky(ctx.guild.id, ctx.channel.id, text)
        if not success:
            await ctx.send("❌ Database error occurred. Please try again.", delete_after=10)
            return
        
        confirmation = await ctx.send("✅ Sticky message set!")
        await self.repost_sticky(ctx.channel, force=True)
        
        # Delete confirmation after delay
        try:
            await confirmation.delete(delay=5)
        except (discord.NotFound, discord.Forbidden):
            pass
    
    @commands.command(name='stickstop')
    @commands.has_permissions(administrator=True)
    async def stickstop(self, ctx):
        """Remove the sticky message from this channel"""
        sticky = await self.get_sticky(ctx.guild.id, ctx.channel.id)
        if not sticky:
            await ctx.send("❌ No sticky message found in this channel.", delete_after=10)
            return
        
        # Delete the last sticky message
        if ctx.channel.id in self.last_sticky_messages:
            try:
                old_msg = await ctx.channel.fetch_message(self.last_sticky_messages[ctx.channel.id])
                await old_msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            except Exception as e:
                logging.error(f"Error deleting sticky message: {e}")
            finally:
                del self.last_sticky_messages[ctx.channel.id]
        
        success = await self.remove_sticky(ctx.guild.id, ctx.channel.id)
        if success:
            confirmation = await ctx.send("✅ Sticky message removed!")
            try:
                await confirmation.delete(delay=5)
            except (discord.NotFound, discord.Forbidden):
                pass
        else:
            await ctx.send("❌ Database error occurred. Please try again.", delete_after=10)
    
    @commands.command(name='stickedit')
    @commands.has_permissions(administrator=True)
    async def stickedit(self, ctx, *, text):
        """Edit the sticky message in this channel"""
        # Validate message length
        if len(text) > 2000:
            await ctx.send("❌ Sticky message cannot exceed 2000 characters.", delete_after=10)
            return
        
        # Validate text content
        if not text.strip():
            await ctx.send("❌ Sticky message cannot be empty.", delete_after=10)
            return
        
        sticky = await self.get_sticky(ctx.guild.id, ctx.channel.id)
        if not sticky:
            await ctx.send("❌ No sticky message found in this channel. Use `.stick` to create one.", delete_after=10)
            return
        
        success = await self.set_sticky(ctx.guild.id, ctx.channel.id, text)
        if not success:
            await ctx.send("❌ Database error occurred. Please try again.", delete_after=10)
            return
        
        confirmation = await ctx.send("✅ Sticky message updated!")
        await self.repost_sticky(ctx.channel, force=True)
        
        # Delete confirmation after delay
        try:
            await confirmation.delete(delay=5)
        except (discord.NotFound, discord.Forbidden):
            pass
    
    @commands.command(name='stickreset')
    @commands.has_permissions(administrator=True)
    async def stickreset(self, ctx):
        """Remove all sticky messages from this server"""
        try:
            result = await self.stickies.delete_many({'guild_id': ctx.guild.id})
            
            # Clean up last sticky message tracking for this guild
            channels_to_remove = []
            for channel_id in list(self.last_sticky_messages.keys()):
                channel = self.bot.get_channel(channel_id)
                if channel and channel.guild.id == ctx.guild.id:
                    channels_to_remove.append(channel_id)
            
            for channel_id in channels_to_remove:
                del self.last_sticky_messages[channel_id]
            
            confirmation = await ctx.send(f"✅ Removed {result.deleted_count} sticky messages from this server.")
            try:
                await confirmation.delete(delay=10)
            except (discord.NotFound, discord.Forbidden):
                pass
            
        except Exception as e:
            logging.error(f"Database error in stickreset: {e}")
            await ctx.send("❌ Database error occurred. Please try again.", delete_after=10)
    
    @commands.command(name='sticklist')
    @commands.has_permissions(administrator=True)
    async def sticklist(self, ctx):
        """List all active sticky messages in this server"""
        try:
            stickies = []
            # Use database query filter instead of Python filtering
            async for sticky in self.stickies.find({'guild_id': ctx.guild.id}):
                channel = self.bot.get_channel(sticky['channel_id'])
                if channel:
                    preview = sticky['text'][:50] + "..." if len(sticky['text']) > 50 else sticky['text']
                    stickies.append(f"**#{channel.name}**: {preview}")
            
            if not stickies:
                await ctx.send("❌ No active sticky messages found in this server.", delete_after=10)
                return
            
            embed = discord.Embed(
                title="Active Sticky Messages",
                description="\n".join(stickies),
                color=0x00ff00
            )
            await ctx.send(embed=embed)
            
        except Exception as e:
            logging.error(f"Database error in sticklist: {e}")
            await ctx.send("❌ Database error occurred. Please try again.", delete_after=10)
    
    # Error handlers
    @stick.error
    @stickedit.error
    async def stick_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You need Administrator permissions to use this command.", delete_after=10)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Please provide text for the sticky message.", delete_after=10)
    
    @stickstop.error
    @stickreset.error
    @sticklist.error
    async def sticky_management_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You need Administrator permissions to use this command.", delete_after=10)

async def setup(bot):
    await bot.add_cog(StickyMessages(bot))
