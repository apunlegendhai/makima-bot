import discord
import aiohttp
import logging
from discord.ext import commands
import io
import asyncio

# Configure logging
logging.basicConfig(level=logging.INFO)

# Embed color constant
EMBED_COLOR = discord.Color.from_rgb(47, 49, 54)  # #2f3136

class StealEmoji(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    @commands.command(name="steal")
    @commands.has_permissions(manage_emojis_and_stickers=True)  # Only users with the 'Manage Emojis and Stickers' permission can use this command
    async def steal(self, ctx):
        """Handles stealing emojis and stickers from a referenced message."""
        if not ctx.message.reference:
            return await ctx.send("You must reply to a message containing an emoji or sticker.")

        replied_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        if not replied_message:
            return await ctx.send("Could not fetch the referenced message.")

        # Process stickers or emojis
        if replied_message.stickers:
            await self.steal_sticker(ctx, replied_message)
        elif emojis := self.extract_emojis(replied_message):
            await self.steal_emoji(ctx, emojis)
        else:
            await ctx.send("No emoji or sticker found in the referenced message.")

    async def steal_sticker(self, ctx, message):
        """Handles stealing stickers with processing and success message."""
        sticker = message.stickers[0]
        if not ctx.author.guild_permissions.manage_emojis_and_stickers or not ctx.guild.me.guild_permissions.manage_emojis_and_stickers:
            return await ctx.send("Insufficient permissions to manage stickers.")

        sticker_url = sticker.url.replace("cdn.discordapp.com", "media.discordapp.net")
        embed = discord.Embed(
            description="<a:sukoon_loading:1322897472338526240> **Processing** to Steal Sticker...",
            color=EMBED_COLOR
        )
        processing_message = await ctx.send(embed=embed)

        async with self.session.get(sticker_url) as resp:
            if resp.status != 200:
                await processing_message.edit(content="Failed to fetch sticker.")
                return

            sticker_data = await resp.read()
            sticker_name = sticker.name.replace(" ", "_")
            file_extension = "png" if sticker.format in [discord.StickerFormatType.png, discord.StickerFormatType.apng] else "json"

            sticker_file = io.BytesIO(sticker_data)
            try:
                # Check if we can add the sticker or if the maximum limit is reached
                new_sticker = await ctx.guild.create_sticker(
                    name=sticker_name, description="Stolen by StealEmoji", emoji=":smile:",
                    file=discord.File(sticker_file, filename=f"{sticker_name}.{file_extension}")
                )

                # Send the sticker directly as a message
                await processing_message.delete()  # Remove the processing embed
                success_message = await ctx.send(f"Sticker Added!")
                await ctx.send(stickers=[new_sticker])  # Send the sticker directly to the channel

            except discord.HTTPException as e:
                # Handle specific error code for max stickers reached
                if "Maximum number of stickers reached" in str(e):
                    await processing_message.edit(content="Maximum number of stickers reached. Unable to add sticker.")
                    await asyncio.sleep(5)  # Auto-delete after 5 seconds
                    await processing_message.delete()  # Delete the bot's message

                else:
                    await self.handle_bot_error(ctx, f"Failed to add sticker: {e}")

            finally:
                sticker_file.close()

    async def steal_emoji(self, ctx, emojis):
        """Handles stealing emojis with processing and success message."""
        embed = discord.Embed(
            description="<a:sukoon_loading:1322897472338526240> **Processing** to Steal Emojis...",
            color=EMBED_COLOR
        )
        processing_message = await ctx.send(embed=embed)

        total = len(emojis)
        added = 0
        for emoji in emojis:
            emoji_url = f"https://cdn.discordapp.com/emojis/{emoji.split(':')[2][:-1]}.{'gif' if emoji.startswith('<a:') else 'png'}"
            result = await self.add_emoji(ctx, emoji_url, emoji.split(":")[1][:-1])
            if result:
                added += 1

        # Successfully added emojis
        success_embed = discord.Embed(
            description=f"<a:stolen_success:1322894423755063316> Successfully created **{added}/{total}** Emojis",
            color=EMBED_COLOR
        )
        await processing_message.edit(embed=success_embed)

    def extract_emojis(self, message):
        """Extract custom emojis from a message."""
        return [word for word in message.content.split() if word.startswith("<:") or word.startswith("<a:")]

    async def add_emoji(self, ctx, emoji_url, name):
        """Add emoji to the server."""
        if len(emoji_url) > 256000:  # Check file size
            return await ctx.send("Emoji file too large.")

        async with self.session.get(emoji_url) as resp:
            if resp.status != 200:
                return await ctx.send(f"Failed to fetch emoji: HTTP {resp.status}")
            image_data = await resp.read()

        guild = ctx.guild
        if not guild.me.guild_permissions.manage_emojis:
            return await ctx.send("I lack the necessary permissions to manage emojis.")

        name = await self.get_unique_emoji_name(name, guild)
        try:
            new_emoji = await guild.create_custom_emoji(name=name, image=image_data)
            return new_emoji
        except discord.HTTPException as e:
            return await ctx.send(f"Error creating emoji: {e}")

    async def get_unique_emoji_name(self, name, guild):
        """Generate a unique name for the emoji."""
        existing_names = {emoji.name for emoji in guild.emojis}
        unique_name = name
        counter = 1
        while unique_name in existing_names:
            unique_name = f"{name}_{counter}"
            counter += 1
        return unique_name

    def cog_unload(self):
        """Close aiohttp session when the cog is unloaded."""
        self.bot.loop.create_task(self.session.close())

    async def handle_bot_error(self, ctx, error_message):
        """Handle bot-specific errors like 'Maximum number of stickers reached'."""
        # Send the error message
        error_message_sent = await ctx.send(error_message)

        # Auto delete the error message after 5 seconds
        await asyncio.sleep(5)
        await error_message_sent.delete()

    @steal.error
    async def steal_error(self, ctx, error):
        """Handle errors for the steal command."""
        if isinstance(error, commands.MissingPermissions):
            error_msg = "You do not have the required permissions to use this command."
        elif isinstance(error, commands.MissingRole):
            error_msg = "You do not have the required role to use this command."
        elif isinstance(error, commands.CheckFailure):
            error_msg = "You are not authorized to use this command."
        else:
            error_msg = f"An unexpected error occurred: {error}"

        # Send error message
        error_message = await ctx.send(error_msg)

        # Auto delete the error message after 5 seconds
        await asyncio.sleep(5)
        await error_message.delete()

async def setup(bot):
    await bot.add_cog(StealEmoji(bot))
