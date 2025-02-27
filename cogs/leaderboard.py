import discord
from discord.ext import commands, tasks
from discord import ui, app_commands
from datetime import datetime
import colorsys
import random
import asyncio
import aiosqlite
import logging

#Added logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------ Database Logic ------------------
# Stores statistics per server using a composite primary key (guild_id, user_id).

class Database:
    def __init__(self, db_path="database/statistics.db"):
        self.db_path = db_path

    async def setup(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_stats (
                    guild_id TEXT,
                    user_id TEXT,
                    total INTEGER DEFAULT 0,
                    daily INTEGER DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            await db.commit()

    async def update_message_count(self, guild_id: str, user_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT total, daily FROM user_stats WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)
            )
            row = await cursor.fetchone()
            if row:
                total, daily = row
                await db.execute(
                    "UPDATE user_stats SET total = ?, daily = ? WHERE guild_id = ? AND user_id = ?",
                    (total + 1, daily + 1, guild_id, user_id)
                )
            else:
                await db.execute(
                    "INSERT INTO user_stats (guild_id, user_id, total, daily) VALUES (?, ?, ?, ?)",
                    (guild_id, user_id, 1, 1)
                )
            await db.commit()

    async def get_user_stats(self, guild_id: str, user_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT total, daily FROM user_stats WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)
            )
            row = await cursor.fetchone()
            if row:
                total, daily = row
                return {"total": total, "daily": daily}
            return {"total": 0, "daily": 0}

    async def get_top_users(self, guild_id: str, offset: int, limit: int):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT user_id, total FROM user_stats WHERE guild_id = ? ORDER BY total DESC LIMIT ? OFFSET ?",
                (guild_id, limit, offset)
            )
            rows = await cursor.fetchall()
            return rows

    async def reset_daily_stats_global(self):
        """Reset daily counts for all guilds."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE user_stats SET daily = 0")
            await db.commit()

    async def reset_daily_stats_guild(self, guild_id: str):
        """Reset today's counts for a specific guild."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE user_stats SET daily = 0 WHERE guild_id = ?", (guild_id,))
            await db.commit()

    async def reset_all_stats_guild(self, guild_id: str):
        """Reset all statistics for a specific guild."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM user_stats WHERE guild_id = ?", (guild_id,))
            await db.commit()

    async def reset_user_stats(self, guild_id: str, user_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM user_stats WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            await db.commit()


# ------------------ Leaderboard Buttons ------------------

class LeaderboardButtons(discord.ui.View):
    def __init__(self, cog, timeout=180):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.current_page = 0
        self.users_per_page = 10
        self.max_pages = 9  # For up to 100 users (10 per page)

    @discord.ui.button(emoji="<:sukoon_left_arrow:1344204740405231727>", style=discord.ButtonStyle.gray)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            embed = await self.cog.create_leaderboard_embed(self.current_page, interaction.guild.id)
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(emoji="<:sukoon_right_arrow:1344204531520638987>", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_pages:
            self.current_page += 1
            embed = await self.cog.create_leaderboard_embed(self.current_page, interaction.guild.id)
            if not embed.description or embed.description == "No messages tracked yet!":
                self.current_page -= 1
                await interaction.response.defer()
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()

# ------------------ Reset Confirmation View ------------------
# Uses interactive buttons to confirm/cancel the reset.

class ResetConfirmationView(discord.ui.View):
    def __init__(self, action: str, cog, interaction: discord.Interaction, member: discord.Member = None):
        super().__init__(timeout=30)
        self.action = action
        self.cog = cog
        self.original_interaction = interaction
        self.member = member

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message("This confirmation isn't for you.", ephemeral=True)
            return
        try:
            if self.action == "all":
                await self.cog.db.reset_all_stats_guild(str(self.original_interaction.guild.id))
                action_taken = "All message statistics for this server have been reset."
            elif self.action == "user" and self.member:
                # Use the existing database connection to delete the specific user's stats
                async with aiosqlite.connect(self.cog.db.db_path) as db:
                    await db.execute(
                        "DELETE FROM user_stats WHERE guild_id = ? AND user_id = ?",
                        (str(self.original_interaction.guild.id), str(self.member.id))
                    )
                    await db.commit()
                action_taken = f"Message statistics for {self.member.display_name} have been reset."
            else:
                await interaction.response.send_message("Invalid reset action.", ephemeral=True)
                return

            result_embed = discord.Embed(
                title="✅ Reset Complete",
                description=action_taken,
                color=self.cog.get_random_color(),
                timestamp=datetime.now()
            )
            await interaction.response.send_message(embed=result_embed, ephemeral=True)
        except Exception as e:
            logging.error(f"Error in reset confirmation: {str(e)}")
            await interaction.response.send_message("An error occurred while resetting statistics.", ephemeral=True)
        finally:
            self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message("This confirmation isn't for you.", ephemeral=True)
            return
        result_embed = discord.Embed(
            title="❌ Reset Cancelled",
            description="No changes were made to the statistics.",
            color=self.cog.get_random_color(),
            timestamp=datetime.now()
        )
        await interaction.response.send_message(embed=result_embed, ephemeral=True)
        self.stop()

# ------------------ Statistics Cog ------------------

class Statistics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = Database()
        self.color_pool = []
        self.check_daily_reset.start()
        logging.info("Statistics cog initialized")

    async def cog_load(self):
        await self.db.setup()
        logging.info("Statistics cog database setup complete")

    def generate_color_pool(self):
        colors = set()
        while len(colors) < 30:
            h = random.random()
            s = random.uniform(0.5, 1.0)
            v = random.uniform(0.8, 1.0)
            rgb = colorsys.hsv_to_rgb(h, s, v)
            color = int(rgb[0] * 255) << 16 | int(rgb[1] * 255) << 8 | int(rgb[2] * 255)
            colors.add(color)
        self.color_pool = list(colors)
        random.shuffle(self.color_pool)
        logging.info(f"Generated new color pool with {len(self.color_pool)} colors")

    def get_random_color(self):
        if not self.color_pool:
            self.generate_color_pool()
        color = self.color_pool.pop()
        logging.info(f"Using color: {hex(color)} ({len(self.color_pool)} colors remaining)")
        return color

    def cog_unload(self):
        logging.info("Statistics cog unloading...")
        self.check_daily_reset.cancel()

    @tasks.loop(minutes=1)
    async def check_daily_reset(self):
        now = datetime.now()
        if now.hour == 0 and now.minute == 0:
            await self.db.reset_daily_stats_global()
            logging.info("Daily stats have been reset globally!")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        await self.db.update_message_count(str(message.guild.id), str(message.author.id))

    @commands.command(name='m')
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def message_count(self, ctx, member: discord.Member = None):
        """Shows message statistics for a user"""
        try:
            logging.info(f"Executing 'm' command for user {ctx.author.id}")
            target = member or ctx.author
            stats = await self.db.get_user_stats(str(ctx.guild.id), str(target.id))

            embed = discord.Embed(
                title="📊 Message Statistics",
                color=self.get_random_color()
            )
            embed.set_thumbnail(url=target.display_avatar.url)
            embed.add_field(name="User", value=target.mention, inline=False)
            embed.add_field(name="Total Messages", value=f"{stats['total']:,}", inline=True)
            embed.add_field(name="Messages Today", value=f"{stats['daily']:,}", inline=True)
            embed.timestamp = datetime.now()

            await ctx.send(embed=embed)
            logging.info(f"Successfully executed 'm' command for user {ctx.author.id}")

        except Exception as e:
            logging.error(f"Error in 'm' command for user {ctx.author.id}: {str(e)}")
            await ctx.send("An error occurred while fetching message statistics.")

    @message_count.error
    async def message_count_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Please wait {error.retry_after:.1f}s before using this command again.")
            logging.info(f"Cooldown triggered for 'm' command - User: {ctx.author.id}")
        else:
            logging.error(f"Error in 'm' command: {str(error)}")
            await ctx.send("An error occurred while fetching message statistics.")

    async def create_leaderboard_embed(self, page: int = 0, guild_id: str = None):
        if not guild_id:
            return
        users = await self.db.get_top_users(str(guild_id), page * 10, 10)
        description = []
        start_rank = page * 10 + 1
        for i, (user_id, messages) in enumerate(users, start_rank):
            user = self.bot.get_user(int(user_id))
            if user:
                rank_str = f"#{i:2d}"
                description.append(f"``{rank_str}`` {user.mention} - **{messages:,}** messages")
        embed = discord.Embed(
            title="<a:003_bel:1341822673247797319> Server Leaderboard",
            description="\n".join(description) if description else "No messages tracked yet!",
            color=self.get_random_color()
        )
        embed.set_footer(text=f"Page {page + 1}")
        embed.timestamp = datetime.now()
        return embed

    @commands.command(name='lb')
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def leaderboard(self, ctx):
        lock_key = f"{ctx.guild.id}:{ctx.command.name}"
        if hasattr(self.bot, 'processing_commands') and self.bot.processing_commands.get(lock_key, False):
            logging.info(f"Skipping duplicate execution of {ctx.command.name} for guild {ctx.guild.id}")
            return

        try:
            if hasattr(self.bot, 'processing_commands'):
                self.bot.processing_commands[lock_key] = True
                logging.info(f"Lock acquired for {ctx.command.name} - Guild: {ctx.guild.id}")

            embed = await self.create_leaderboard_embed(0, ctx.guild.id)
            view = LeaderboardButtons(self)
            await ctx.send(embed=embed, view=view)
            logging.info(f"Successfully executed {ctx.command.name} for guild {ctx.guild.id}")
        except Exception as e:
            logging.error(f"Error in {ctx.command.name} for guild {ctx.guild.id}: {str(e)}")
            raise
        finally:
            if hasattr(self.bot, 'processing_commands'):
                self.bot.processing_commands[lock_key] = False
                logging.info(f"Lock released for {ctx.command.name} - Guild: {ctx.guild.id}")

    @leaderboard.error
    async def leaderboard_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            logging.info(f"Cooldown triggered for {ctx.command.name} - Guild: {ctx.guild.id} - Retry after: {error.retry_after:.1f}s")
            await ctx.send(f"Please wait {error.retry_after:.1f}s before using this command again.")
        else:
            logging.error(f"Error in {ctx.command.name}: {str(error)}")
            await ctx.send("An error occurred while fetching the leaderboard.")

    # ------------------ Slash Command: /reset-xp ------------------
    @app_commands.command(name="reset-xp", description="Reset message statistics for this server or a specific user.")
    @app_commands.describe(
        action="Select what to reset: 'all' resets entire server, 'user' resets specific member",
        member="The member whose statistics to reset (only needed when action is 'user')"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="All Server Stats", value="all"),
        app_commands.Choice(name="Single User Stats", value="user")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def reset_xp(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        member: discord.Member = None
    ):
        logging.info(f"Reset-xp command invoked by {interaction.user.id} - Action: {action.value}, Member: {member.id if member else 'None'}")

        if not interaction.guild:
            logging.warning(f"Reset-xp attempted outside guild context by {interaction.user.id}")
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if action.value == "user" and not member:
            logging.warning(f"Reset-xp 'user' action attempted without member by {interaction.user.id}")
            await interaction.response.send_message("Please specify a member when using the 'user' option.", ephemeral=True)
            return

        description = (
            f"Are you sure you want to reset statistics for "
            f"**{member.display_name if action.value == 'user' else 'the entire server'}**?\n"
            "This action cannot be undone!"
        )

        confirm_embed = discord.Embed(
            title="⚠️ Confirmation Required",
            description=description,
            color=discord.Color.red(),
            timestamp=datetime.now()
        )

        # Pass both the action and member to the confirmation view
        view = ResetConfirmationView(action.value, self, interaction, member)
        await interaction.response.send_message(embed=confirm_embed, view=view, ephemeral=True)
        logging.info(f"Reset-xp confirmation prompt sent to {interaction.user.id}")

    @reset_xp.error
    async def reset_xp_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            logging.warning(f"Reset-xp permission denied for {interaction.user.id}")
            await interaction.response.send_message("You need administrator permissions to use this command.", ephemeral=True)
        else:
            logging.error(f"Error in reset_xp command: {str(error)}")
            await interaction.response.send_message("An error occurred.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Statistics(bot))
    logging.info("Statistics cog has been added.")
