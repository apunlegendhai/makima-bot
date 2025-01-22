import discord
from discord.ext import commands, tasks
import json
import asyncio
import logging
import os

# Set up logging to log only errors
logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

class StickyBot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sticky_data = self.load_sticky_data()
        self.lock = asyncio.Lock()  # Ensure thread-safe operations
        self.sticky_task.start()

    def load_sticky_data(self):
        """Load sticky message data from storage with error handling."""
        try:
            if not os.path.exists('sticky_data.json'):
                with open('sticky_data.json', 'w') as file:
                    json.dump({}, file)  # Create the file if it doesn't exist
            with open('sticky_data.json', 'r') as file:
                return json.load(file)
        except FileNotFoundError:
            logging.error("sticky_data.json not found. Creating a new file.")
            return {}
        except PermissionError:
            logging.error("Permission error when trying to read sticky_data.json.")
            return {}
        except json.JSONDecodeError:
            logging.error("Error decoding sticky_data.json. The file might be corrupted.")
            os.remove('sticky_data.json')  # Remove the corrupted file
            return {}  # Return an empty dictionary to initialize fresh data

    def save_sticky_data(self):
        """Save sticky message data to storage with error handling."""
        try:
            with open('sticky_data.json', 'w') as file:
                json.dump(self.sticky_data, file, indent=4)
        except Exception as e:
            logging.error(f"Failed to save sticky data: {e}")

    async def has_permissions(self, ctx):
        """Check if the bot has necessary permissions in the channel."""
        permissions = ctx.channel.permissions_for(ctx.guild.me)
        if not permissions.send_messages or not permissions.manage_messages or not permissions.read_messages:
            await ctx.send("The bot lacks necessary permissions (Send Messages, Manage Messages, Read Messages) in this channel.")
            return False
        return True

    @commands.command()
    async def stick(self, ctx, *, message):
        """Start or overwrite a sticky message in the channel."""
        if not ctx.author.guild_permissions.manage_messages:
            await ctx.send("You do not have permission to manage sticky messages.")
            return

        if not await self.has_permissions(ctx):
            return

        async with self.lock:  # Prevent concurrent modifications
            channel_id = str(ctx.channel.id)
            if channel_id in self.sticky_data:
                existing_message = self.sticky_data[channel_id]['message']
                confirmation_message = await ctx.send(
                    f"A sticky message already exists: `{existing_message}`.\n"
                    "React with ✅ to overwrite it with the new message or ❌ to keep the old message."
                )
                await confirmation_message.add_reaction('✅')
                await confirmation_message.add_reaction('❌')

                def reaction_check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in ['✅', '❌'] and reaction.message.id == confirmation_message.id

                try:
                    reaction, user = await self.bot.wait_for('reaction_add', timeout=30.0, check=reaction_check)
                    if str(reaction.emoji) == '❌':
                        await ctx.send("Keeping the old sticky message.")
                        return
                except asyncio.TimeoutError:
                    await ctx.send("You didn't react in time. Sticky message setup canceled.")
                    return

            self.sticky_data[channel_id] = {
                'message': message,
                'last_posted': None,
                'last_message_id': None
            }
            self.save_sticky_data()
            await ctx.send(f"Sticky message set for this channel: {message}")

    @commands.command()
    async def stickstop(self, ctx):
        """Stop the sticky message in the channel."""
        if not ctx.author.guild_permissions.manage_messages:
            await ctx.send("You do not have permission to stop the sticky message.")
            return

        if not await self.has_permissions(ctx):
            return

        async with self.lock:
            channel_id = str(ctx.channel.id)
            if channel_id in self.sticky_data:
                self.sticky_data.pop(channel_id, None)
                self.save_sticky_data()
                await ctx.send("Sticky message stopped.")
            else:
                await ctx.send("There is no sticky message in this channel.")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Repost sticky message when a new message is sent in the channel."""
        if message.author == self.bot.user:
            return

        channel_id = str(message.channel.id)
        async with self.lock:
            if channel_id in self.sticky_data:
                data = self.sticky_data[channel_id]
                last_message_id = data.get('last_message_id')

                if last_message_id:
                    try:
                        # Try to fetch the previous sticky message by its ID
                        last_message = await message.channel.fetch_message(last_message_id)
                        # Check if the last message is from the bot and delete it
                        if last_message.author == self.bot.user:
                            await last_message.delete()
                            logging.error(f"Deleted previous sticky message with ID: {last_message_id}")
                    except discord.NotFound:
                        logging.error(f"Sticky message with ID {last_message_id} not found for deletion.")
                    except discord.Forbidden:
                        logging.error(f"Bot does not have permission to delete message with ID {last_message_id}.")
                    except Exception as e:
                        logging.error(f"Error deleting sticky message with ID {last_message_id}: {str(e)}")

                # Send the new sticky message
                sticky_message = await message.channel.send(data['message'])
                # Update the sticky data with the new message ID and timestamp
                self.sticky_data[channel_id]['last_message_id'] = sticky_message.id
                self.sticky_data[channel_id]['last_posted'] = discord.utils.utcnow().isoformat()
                self.save_sticky_data()
                logging.error(f"Posted new sticky message in channel {channel_id}: {data['message']}")

    @tasks.loop(seconds=300)
    async def sticky_task(self):
        """Task to repost sticky messages every 5 minutes."""
        logging.error("Sticky task is running.")
        async with self.lock:
            for channel_id, data in list(self.sticky_data.items()):
                channel = self.bot.get_channel(int(channel_id))
                if not channel:
                    self.sticky_data.pop(channel_id)
                    self.save_sticky_data()
                    continue

                try:
                    # Only repost if the last message has been deleted
                    last_message_id = data.get('last_message_id')
                    if last_message_id:
                        try:
                            last_message = await channel.fetch_message(last_message_id)
                            if last_message.author == self.bot.user:
                                await last_message.delete()
                                logging.error(f"Deleted previous sticky message in channel {channel_id}.")
                        except discord.NotFound:
                            pass  # The message doesn't exist anymore

                    # Check if it's time to update or repost the sticky message
                    sticky_message = await channel.send(data['message'])
                    self.sticky_data[channel_id]['last_message_id'] = sticky_message.id
                    self.sticky_data[channel_id]['last_posted'] = discord.utils.utcnow().isoformat()
                    self.save_sticky_data()

                except discord.Forbidden:
                    logging.error(f"Failed to post sticky message in channel {channel_id}. Missing permissions.")
                except Exception as e:
                    logging.error(f"Error posting sticky message in channel {channel_id}: {str(e)}")

    @sticky_task.before_loop
    async def before_sticky_task(self):
        await self.bot.wait_until_ready()

    async def cog_unload(self):
        """Stop the sticky task loop when the bot shuts down."""
        if self.sticky_task.is_running():
            self.sticky_task.cancel()
        await self.sticky_task

# Add the cog to the bot
async def setup(bot):
    await bot.add_cog(StickyBot(bot))
    logging.error("StickyBot cog loaded successfully.")
