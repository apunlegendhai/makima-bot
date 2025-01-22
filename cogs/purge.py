import discord
import logging
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import asyncio
from typing import List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class Purge(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.MAX_RETRY_ATTEMPTS = 3
        self.MESSAGE_AGE_LIMIT = 14  # days
        self.BACKOFF_MULTIPLIER = 1.5

    async def fetch_messages_with_backoff(self, channel, limit: int) -> List[discord.Message]:
        """Fetch messages with exponential backoff for rate limits."""
        messages = []
        base_delay = 1.0
        current_delay = base_delay
        attempt = 0

        while len(messages) < limit and attempt < self.MAX_RETRY_ATTEMPTS:
            try:
                async for message in channel.history(limit=limit - len(messages)):
                    messages.append(message)
                break  # If successful, exit the loop
            except discord.HTTPException as e:
                if e.status == 429:  # Rate limit hit
                    retry_after = float(e.response.headers.get('Retry-After', current_delay))
                    logging.warning(f"Rate limited while fetching messages. Waiting {retry_after} seconds.")
                    await asyncio.sleep(retry_after)
                    current_delay *= self.BACKOFF_MULTIPLIER
                    attempt += 1
                else:
                    raise  # Re-raise non-rate-limit errors

        return messages

    async def process_messages(self, channel, amount: int, filter_type: str = "all", user: Optional[discord.Member] = None) -> Tuple[List[discord.Message], int]:
        """Processes messages based on the filter criteria with rate limit handling."""
        messages = []
        too_old_count = 0

        try:
            all_messages = await self.fetch_messages_with_backoff(channel, amount)

            for msg in all_messages:
                if (datetime.now(timezone.utc) - msg.created_at).days <= self.MESSAGE_AGE_LIMIT:
                    if filter_type == "bots" and msg.author.bot:
                        messages.append(msg)
                    elif filter_type == "humans" and not msg.author.bot:
                        messages.append(msg)
                    elif filter_type == "user" and user and msg.author == user:
                        messages.append(msg)
                    elif filter_type == "all":
                        messages.append(msg)
                else:
                    too_old_count += 1

            return messages, too_old_count
        except discord.HTTPException as e:
            logging.error(f"Error fetching messages: {e}")
            raise

    async def delete_messages_with_retry(self, channel, messages: List[discord.Message], max_retries: int = 3) -> Tuple[bool, str]:
        """Attempts to delete messages with retry logic for rate limits."""
        if not messages:
            return False, "<:sukoon_info:1323251063910043659> | No messages to delete."

        if len(messages) == 1:
            try:
                await messages[0].delete()
                return True, "<a:sukoon_whitetick:1323992464058482729> | Successfully deleted 1 message."
            except discord.HTTPException as e:
                return False, f"<:sukoon_info:1323251063910043659> | Failed to delete message: {str(e)}"

        current_delay = 1.0
        for attempt in range(max_retries):
            try:
                await channel.delete_messages(messages)
                return True, f"<a:sukoon_whitetick:1323992464058482729> | Successfully deleted {len(messages)} messages."
            except discord.errors.HTTPException as e:
                if e.status == 429 and attempt < max_retries - 1:
                    retry_after = float(e.response.headers.get('Retry-After', current_delay))
                    logging.warning(f"Rate limited while deleting messages. Waiting {retry_after} seconds.")
                    await asyncio.sleep(retry_after)
                    current_delay *= self.BACKOFF_MULTIPLIER
                    continue
                return False, str(e)
            except Exception as e:
                return False, str(e)

        return False, "<:sukoon_info:1323251063910043659> | Max retry attempts reached."

    @commands.command(name="purge", help="Purge messages (usage: .purge <amount/bots/humans/@user>)")
    @commands.has_permissions(manage_messages=True)
    async def purge_prefix(self, ctx, arg: str = None):
        if not arg:
            await ctx.send("<:sukoon_info:1323251063910043659> | Please specify the number of messages to delete or a filter type (bots/humans/@user).")
            return

        amount = None
        filter_type = "all"
        user = None

        if arg.isdigit():
            amount = min(int(arg), 100)
        elif arg.lower() in ["bots", "humans"]:
            amount = 50  # Default amount for filter types
            filter_type = arg.lower()
        elif arg.startswith("<@") and arg.endswith(">"):
            try:
                user_id = int(arg.strip("<@!>"))
                user = await ctx.guild.fetch_member(user_id)
                amount = 50  # Default amount for user filter
                filter_type = "user"
            except Exception:
                await ctx.send("<:sukoon_info:1323251063910043659> | Invalid user mention. Please try again.", delete_after=3)
                return
        else:
            await ctx.send("<:sukoon_info:1323251063910043659> | Invalid argument. Use a number (1-100), 'bots', 'humans', or @user.", delete_after=3)
            return

        await ctx.message.delete()

        try:
            messages, too_old_count = await self.process_messages(ctx.channel, amount, filter_type, user)
        except discord.HTTPException as e:
            await ctx.send(f"Error fetching messages: {e}", delete_after=5)
            return

        if not messages:
            await ctx.send(
                f"<:sukoon_info:1323251063910043659> | No messages found to delete. {too_old_count} messages were too old (>{self.MESSAGE_AGE_LIMIT} days).",
                delete_after=5
            )
            return

        success, message = await self.delete_messages_with_retry(ctx.channel, messages)

        embed = discord.Embed(
            description=f"{'' if success else ''} {message}",
            color=discord.Color.green() if success else discord.Color.red()
        )

        if too_old_count > 0:
            embed.add_field(
                name="Note",
                value=f"{too_old_count} messages were too old to delete (>{self.MESSAGE_AGE_LIMIT} days old)"
            )

        await ctx.send(embed=embed, delete_after=5)

    # [Slash command implementation remains the same as before]

async def setup(bot):
    await bot.add_cog(Purge(bot))