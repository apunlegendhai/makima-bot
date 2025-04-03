import discord
from discord.ext import commands
from discord.ui import Button, View
from datetime import datetime, timedelta
from typing import List
import backoff
import logging
import aiosqlite
import os
import asyncio
import random

logger = logging.getLogger('discord')

class SnipeView(View):
    def __init__(self, cog, ctx: commands.Context, messages: List[dict], timeout: float = 7 * 24 * 60 * 60):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.messages = messages
        self.current_page = 0
        self.message = None
        self.update_buttons()

    def update_buttons(self):
        try:
            self.clear_items()
            prev_button = Button(
                emoji="<:sukoon_left_arrow:1344204740405231727>",
                style=discord.ButtonStyle.secondary,
                disabled=self.current_page == 0
            )
            prev_button.callback = self.previous_page
            self.add_item(prev_button)

            counter_button = Button(
                label=f"Page {self.current_page + 1}/{len(self.messages)}",
                style=discord.ButtonStyle.secondary,
                disabled=True
            )
            self.add_item(counter_button)

            next_button = Button(
                emoji="<:sukoon_right_arrow:1344204531520638987>",
                style=discord.ButtonStyle.secondary,
                disabled=self.current_page >= len(self.messages) - 1
            )
            next_button.callback = self.next_page
            self.add_item(next_button)
        except Exception as e:
            logger.error(f"Error updating buttons: {type(e).__name__}: {e}")
            raise

    async def previous_page(self, interaction: discord.Interaction):
        try:
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message("You cannot use these controls!", ephemeral=True)
                return

            if self.current_page > 0:
                self.current_page -= 1
                self.update_buttons()
                embed = await self.cog.create_snipe_embed(self.ctx, self.messages[self.current_page])
                await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            logger.error(f"Error handling previous page: {type(e).__name__}: {e}")

    async def next_page(self, interaction: discord.Interaction):
        try:
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message("You cannot use these controls!", ephemeral=True)
                return

            if self.current_page < len(self.messages) - 1:
                self.current_page += 1
                self.update_buttons()
                embed = await self.cog.create_snipe_embed(self.ctx, self.messages[self.current_page])
                await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            logger.error(f"Error handling next page: {type(e).__name__}: {e}")

    async def on_timeout(self):
        try:
            if self.message:
                for item in self.children:
                    item.disabled = True
                await self.message.edit(view=self)
        except Exception as e:
            logger.error(f"Error handling view timeout: {type(e).__name__}: {e}")

class Snipe(commands.Cog):
    _loaded = False

    def __init__(self, bot: commands.Bot):
        if Snipe._loaded:
            logger.error("Snipe cog is already loaded! Duplicate loading prevented.")
            return
        Snipe._loaded = True

        self.bot = bot
        self.db_path = "database/deleted_messages.db"
        self.max_age = timedelta(days=7)
        self.connected = False
        self.cleanup_task = None
        self._ensure_db_folder()
        asyncio.create_task(self._init_db())

        self.embed_colors = [
            0xFF6B6B, 0x4ECDC4, 0x45B7D1, 0x96CEB4, 0xFF9F1C, 0x2D3047,
            0xD4A373, 0x588B8B, 0xFF7F51, 0x9B5DE5, 0x00BBF9, 0xFEE440,
            0xF15BB5, 0x9B2226, 0x006D77, 0xFCAF58, 0x4EA8DE, 0x8AC926,
            0xAA8B56, 0x9381FF, 0xFF70A6, 0x43AA8B, 0x277DA1, 0xF94144,
            0x90BE6D, 0xF8961E, 0xF9C74F, 0x577590, 0xB5838D, 0x495057
        ]

    def _ensure_db_folder(self):
        os.makedirs("database", exist_ok=True)

    async def _migrate_db_if_needed(self):
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Check if author_id column exists
                cursor = await db.execute("PRAGMA table_info(deleted_messages)")
                columns = await cursor.fetchall()
                column_names = [column[1] for column in columns]
                
                if "author_id" not in column_names:
                    logger.info("Migrating database to add author_id column")
                    # SQLite doesn't support ADD COLUMN with NOT NULL constraint without default value
                    await db.execute("ALTER TABLE deleted_messages ADD COLUMN author_id INTEGER")
                    await db.commit()
                    logger.info("Database migration completed successfully")
        except Exception as e:
            logger.error(f"Error during database migration: {e}")

    async def _init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS deleted_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    content TEXT,
                    author TEXT NOT NULL,
                    author_id INTEGER,
                    deleted_at TIMESTAMP NOT NULL,
                    attachments TEXT
                )
            ''')
            await db.commit()

        # Migrate database if needed
        await self._migrate_db_if_needed()

        if not self.cleanup_task:
            self.cleanup_task = self.bot.loop.create_task(self._periodic_cleanup())

    async def _periodic_cleanup(self):
        while True:
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    cutoff_date = datetime.utcnow() - self.max_age
                    await db.execute(
                        'DELETE FROM deleted_messages WHERE deleted_at < ?',
                        (cutoff_date.isoformat(),)
                    )
                    await db.commit()
            except Exception as e:
                logger.error(f"Error during periodic cleanup: {e}")
            await asyncio.sleep(3600)

    @backoff.on_exception(
        backoff.expo,
        (discord.ConnectionClosed, discord.GatewayNotFound, discord.HTTPException),
        max_tries=8,
        max_time=300,
        on_backoff=lambda details: logger.warning(
            f"Connection attempt failed. Retrying in {details['wait']:.1f} seconds. Attempt {details['tries']}/8"
        )
    )
    async def connect_with_backoff(self, token: str) -> None:
        if not self.connected:
            try:
                await self.bot.start(token)
                self.connected = True
            except Exception as e:
                logger.error(f"Failed to connect after all retries: {str(e)}")
                raise

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        safe_attachments = [
            att.url for att in message.attachments
            if att.url.startswith('https://cdn.discordapp.com/')
        ]
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    'INSERT INTO deleted_messages (channel_id, content, author, author_id, deleted_at, attachments) VALUES (?, ?, ?, ?, ?, ?)',
                    (message.channel.id, message.content, message.author.name, message.author.id, datetime.utcnow().isoformat(), 
                     ','.join(safe_attachments) if safe_attachments else None)
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Error storing deleted message: {e}")

    async def create_snipe_embed(self, ctx: commands.Context, deleted_msg: dict) -> discord.Embed:
        deleted_at = datetime.fromisoformat(deleted_msg['deleted_at'])
        time_diff = datetime.utcnow() - deleted_at
        readable_time = (
            (f"{time_diff.days}d " if time_diff.days > 0 else "") +
            (f"{time_diff.seconds // 3600}h " if time_diff.seconds >= 3600 else "") +
            (f"{(time_diff.seconds % 3600) // 60}m " if time_diff.seconds >= 60 else "") +
            f"{time_diff.seconds % 60}s"
        ).strip()

        member = None
        author_name = deleted_msg['author'].lower()
        
        # Handle both int and None types for author_id
        author_id = None
        if 'author_id' in deleted_msg and deleted_msg['author_id'] is not None:
            try:
                author_id = int(deleted_msg['author_id'])
            except (ValueError, TypeError):
                author_id = None
        
        try:
            if author_id:
                member = ctx.guild.get_member(author_id)
            
            if not member:
                member = discord.utils.get(ctx.guild.members, name=deleted_msg['author'])
            if not member:
                member = discord.utils.get(ctx.guild.members, display_name=deleted_msg['author'])
            if not member:
                async for guild_member in ctx.guild.fetch_members(limit=1000):
                    if guild_member.name.lower() == author_name or guild_member.display_name.lower() == author_name:
                        member = guild_member
                        break
        except Exception as e:
            logger.error(f"Unexpected error during member lookup: {type(e).__name__}: {e}")

        # Get author mention
        author_mention = deleted_msg['author']
        if member:
            author_mention = member.mention
        elif author_id:
            author_mention = f"<@{author_id}>"

        # Format content
        content = deleted_msg['content'] or "*No content*"
        
        # Prepare content with any attachments
        content_section = content
        if deleted_msg['attachments']:
            attachments = deleted_msg['attachments'].split(',')
            if len(attachments) > 1:
                attachment_list = "\n".join([f"[Attachment {i+1}]({url})" for i, url in enumerate(attachments)])
                content_section += f"\n\n**Attachments:**\n{attachment_list}"

        embed = discord.Embed(
            title="Deleted Msgs",
            color=random.choice(self.embed_colors)
        )
        
        # Add fields exactly as requested
        embed.add_field(name="author mention", value=author_mention, inline=False)
        embed.add_field(name="deleted at", value=f"{readable_time}", inline=False)
        
        # Add the content section
        embed.add_field(name="content", value=content_section, inline=False)
        
        # Set the user's avatar as thumbnail
        if member and member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)
        elif author_id:
            # Fallback to default avatar url pattern if we only have the ID
            embed.set_thumbnail(url=f"https://cdn.discordapp.com/avatars/{author_id}/avatar.png")
        
        # Set the first attachment as image if there's only one
        if deleted_msg['attachments']:
            attachments = deleted_msg['attachments'].split(',')
            if len(attachments) == 1:
                embed.set_image(url=attachments[0])
        
        # Set footer with requester info
        formatted_time = datetime.utcnow().strftime('%H:%M:%S')
        footer_text = f"requested by {ctx.author.name} | at {formatted_time}"
        footer_icon = ctx.author.display_avatar.url if ctx.author.display_avatar else None
        embed.set_footer(text=footer_text, icon_url=footer_icon)
        
        return embed

    @commands.command(name='snipe')
    @commands.has_permissions(administrator=True)
    async def snipe(self, ctx: commands.Context) -> None:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    'SELECT * FROM deleted_messages WHERE channel_id = ? ORDER BY deleted_at DESC LIMIT 10',
                    (ctx.channel.id,)
                )
                deleted_msgs = await cursor.fetchall()

                if not deleted_msgs:
                    embed = discord.Embed(
                        title="No Messages Found",
                        description="No recently deleted messages found in this channel!",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return

                current_time = datetime.utcnow()
                valid_msgs = [
                    msg for msg in deleted_msgs
                    if (current_time - datetime.fromisoformat(msg['deleted_at'])) <= self.max_age
                ]

                if not valid_msgs:
                    embed = discord.Embed(
                        title="Messages Too Old",
                        description=f"All deleted messages are older than {self.max_age.days} days!",
                        color=discord.Color.orange()
                    )
                    await ctx.send(embed=embed)
                    return

                first_embed = await self.create_snipe_embed(ctx, valid_msgs[0])
                view = SnipeView(self, ctx, valid_msgs)
                sent_message = await ctx.reply(embed=first_embed, view=view)
                view.message = sent_message
                # Added custom emoji reaction here
                await ctx.message.add_reaction("<a:sukoon_whitetick:1344600976962748458>")
        except aiosqlite.Error as e:
            logger.error(f"Database error in snipe command: {e}")
            error_embed = discord.Embed(
                title="Database Error",
                description="An error occurred while accessing the message database.",
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)
        except Exception as e:
            logger.error(f"Unexpected error in snipe command: {type(e).__name__}: {e}")
            error_embed = discord.Embed(
                title="Error",
                description="An unexpected error occurred while retrieving deleted messages.",
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)

    @snipe.error
    async def snipe_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            return
        else:
            logger.error(f"Command error: {type(error).__name__}: {error}")
            await ctx.send("An error occurred while executing the command.")

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Snipe(bot))
