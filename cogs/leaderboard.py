import discord
from discord.ext import commands, tasks
from discord import ui, app_commands
from datetime import datetime
import colorsys
import random
import asyncio
import aiosqlite
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------ Database Logic ------------------
# The table now includes voice_total and voice_daily columns.
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
                    voice_total REAL DEFAULT 0,
                    voice_daily REAL DEFAULT 0,
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

    async def update_voice_time(self, guild_id: str, user_id: str, duration: float):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT voice_total, voice_daily FROM user_stats WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)
            )
            row = await cursor.fetchone()
            if row:
                voice_total, voice_daily = row
                await db.execute(
                    "UPDATE user_stats SET voice_total = ?, voice_daily = ? WHERE guild_id = ? AND user_id = ?",
                    (voice_total + duration, voice_daily + duration, guild_id, user_id)
                )
            else:
                await db.execute(
                    "INSERT INTO user_stats (guild_id, user_id, voice_total, voice_daily) VALUES (?, ?, ?, ?)",
                    (guild_id, user_id, duration, duration)
                )
            await db.commit()

    async def get_user_stats(self, guild_id: str, user_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT total, daily, voice_total, voice_daily FROM user_stats WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)
            )
            row = await cursor.fetchone()
            if row:
                total, daily, voice_total, voice_daily = row
                return {"total": total, "daily": daily, "voice_total": voice_total, "voice_daily": voice_daily}
            return {"total": 0, "daily": 0, "voice_total": 0, "voice_daily": 0}

    async def get_top_users(self, guild_id: str, offset: int, limit: int):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT user_id, total FROM user_stats WHERE guild_id = ? ORDER BY total DESC LIMIT ? OFFSET ?",
                (guild_id, limit, offset)
            )
            rows = await cursor.fetchall()
            return rows

    async def get_top_voice_users(self, guild_id: str, offset: int, limit: int):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT user_id, voice_total FROM user_stats WHERE guild_id = ? ORDER BY voice_total DESC LIMIT ? OFFSET ?",
                (guild_id, limit, offset)
            )
            rows = await cursor.fetchall()
            return rows

    async def reset_daily_stats_global(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE user_stats SET daily = 0, voice_daily = 0")
            await db.commit()

    async def reset_daily_stats_guild(self, guild_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE user_stats SET daily = 0, voice_daily = 0 WHERE guild_id = ?", (guild_id,))
            await db.commit()

    async def reset_all_stats_guild(self, guild_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM user_stats WHERE guild_id = ?", (guild_id,))
            await db.commit()

    async def reset_user_stats(self, guild_id: str, user_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM user_stats WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            await db.commit()

    # New methods to reset only message or voice stats:
    async def reset_message_stats_guild(self, guild_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE user_stats SET total = 0, daily = 0 WHERE guild_id = ?", (guild_id,))
            await db.commit()

    async def reset_voice_stats_guild(self, guild_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE user_stats SET voice_total = 0, voice_daily = 0 WHERE guild_id = ?", (guild_id,))
            await db.commit()

    async def reset_message_stats_user(self, guild_id: str, user_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE user_stats SET total = 0, daily = 0 WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            await db.commit()

    async def reset_voice_stats_user(self, guild_id: str, user_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE user_stats SET voice_total = 0, voice_daily = 0 WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            await db.commit()


# ------------------ Stats Toggle View ------------------
class StatsToggleView(discord.ui.View):
    def __init__(self, cog, target: discord.Member, initial_mode="message", timeout=180):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.target = target
        self.current_mode = initial_mode
        self.update_buttons()

    def update_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "message_stats":
                    child.disabled = (self.current_mode == "message")
                elif child.custom_id == "voice_stats":
                    child.disabled = (self.current_mode == "voice")

    @discord.ui.button(emoji="<a:sukooon_cha:1344706814616273010>", style=discord.ButtonStyle.secondary, custom_id="message_stats")
    async def message_stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            return await interaction.response.send_message("This button isn't for you.", ephemeral=True)
        self.current_mode = "message"
        self.update_buttons()
        stats = await self.cog.db.get_user_stats(str(interaction.guild.id), str(self.target.id))
        embed = self.cog.create_message_stats_embed(self.target, stats)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(emoji="<:sukooon_voic:1344707189851295876>", style=discord.ButtonStyle.secondary, custom_id="voice_stats")
    async def voice_stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            return await interaction.response.send_message("This button isn't for you.", ephemeral=True)
        self.current_mode = "voice"
        self.update_buttons()
        stats = await self.cog.db.get_user_stats(str(interaction.guild.id), str(self.target.id))
        embed = self.cog.create_voice_stats_embed(self.target, stats)
        await interaction.response.edit_message(embed=embed, view=self)


# ------------------ Reset Activity Confirmation View ------------------
class ResetActivityConfirmationView(discord.ui.View):
    def __init__(self, activity: str, cog, interaction: discord.Interaction, member: discord.Member = None):
        """
        :param activity: "message" or "voice"
        """
        super().__init__(timeout=30)
        self.activity = activity
        self.cog = cog
        self.original_interaction = interaction
        self.member = member

    @discord.ui.button(emoji="<:sukoon_tick:1344600783257075815>", style=discord.ButtonStyle.secondary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_interaction.user.id:
            return await interaction.response.send_message("This confirmation isn't for you.", ephemeral=True)
        try:
            guild_id = str(self.original_interaction.guild.id)
            if self.activity == "message":
                if self.member:
                    await self.cog.db.reset_message_stats_user(guild_id, str(self.member.id))
                    action_taken = f"Message statistics for {self.member.display_name} have been reset."
                else:
                    await self.cog.db.reset_message_stats_guild(guild_id)
                    action_taken = "Message statistics for this server have been reset."
            elif self.activity == "voice":
                if self.member:
                    await self.cog.db.reset_voice_stats_user(guild_id, str(self.member.id))
                    action_taken = f"Voice statistics for {self.member.display_name} have been reset."
                else:
                    await self.cog.db.reset_voice_stats_guild(guild_id)
                    action_taken = "Voice statistics for this server have been reset."
            else:
                return await interaction.response.send_message("Invalid reset action.", ephemeral=True)

            result_embed = discord.Embed(
                title="<a:sukoon_whitetick:1344600976962748458> Reset Complete",
                description=action_taken,
                color=self.cog.get_random_color(),
                timestamp=datetime.now()
            )
            await interaction.response.send_message(embed=result_embed, ephemeral=True)
        except Exception as e:
            logging.error(f"Error in reset activity confirmation: {str(e)}")
            await interaction.response.send_message("An error occurred while resetting statistics.", ephemeral=True)
        finally:
            self.stop()

    @discord.ui.button(emoji="<:sukoon_tick:1344600783257075815>", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_interaction.user.id:
            return await interaction.response.send_message("This confirmation isn't for you.", ephemeral=True)
        result_embed = discord.Embed(
            title="<:sukoon_tick:1344600783257075815> Reset Cancelled",
            description="No changes were made to the statistics.",
            color=self.cog.get_random_color(),
            timestamp=datetime.now()
        )
        await interaction.response.send_message(embed=result_embed, ephemeral=True)
        self.stop()


# ------------------ Leaderboard Buttons ------------------
class LeaderboardButtons(discord.ui.View):
    def __init__(self, cog, mode: str, timeout=180):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.mode = mode  # 'm' for messages, 'v' for voice
        self.current_page = 0
        self.users_per_page = 10
        self.max_pages = 9  # For up to 100 users (10 per page)

    @discord.ui.button(emoji="<:sukoon_left_arrow:1344204740405231727>", style=discord.ButtonStyle.gray)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            if self.mode == 'm':
                embed = await self.cog.create_leaderboard_embed(self.current_page, interaction.guild.id)
            else:
                embed = await self.cog.create_voice_leaderboard_embed(self.current_page, interaction.guild.id)
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(emoji="<:sukoon_right_arrow:1344204531520638987>", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_pages:
            self.current_page += 1
            if self.mode == 'm':
                embed = await self.cog.create_leaderboard_embed(self.current_page, interaction.guild.id)
            else:
                embed = await self.cog.create_voice_leaderboard_embed(self.current_page, interaction.guild.id)
            if not embed.description or "No" in embed.description:
                self.current_page -= 1
                await interaction.response.defer()
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()


# ------------------ Statistics Cog ------------------
class Statistics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = Database()
        self.voice_sessions = {}  # Tracks voice join times (key: (guild_id, user_id))
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

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        guild_id = str(member.guild.id)
        user_id = str(member.id)
        # User joins voice channel
        if before.channel is None and after.channel is not None:
            self.voice_sessions[(guild_id, user_id)] = datetime.now()
            logging.info(f"{member.name} joined voice channel at {datetime.now()}")
        # User leaves voice channel
        elif before.channel is not None and after.channel is None:
            join_time = self.voice_sessions.pop((guild_id, user_id), None)
            if join_time:
                duration = (datetime.now() - join_time).total_seconds()
                await self.db.update_voice_time(guild_id, user_id, duration)
                logging.info(f"{member.name} left voice channel, duration: {duration:.2f} seconds")
        # Switching channels (leave then join)
        elif before.channel is not None and after.channel is not None and before.channel != after.channel:
            join_time = self.voice_sessions.pop((guild_id, user_id), None)
            if join_time:
                duration = (datetime.now() - join_time).total_seconds()
                await self.db.update_voice_time(guild_id, user_id, duration)
                logging.info(f"{member.name} switched channels, previous duration: {duration:.2f} seconds")
            self.voice_sessions[(guild_id, user_id)] = datetime.now()

    def create_message_stats_embed(self, target: discord.Member, stats: dict):
        embed = discord.Embed(
            title="<a:003_bel:1344601355515330621> Message Statistics",
            color=self.get_random_color(),
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="User", value=target.mention, inline=False)
        embed.add_field(name="All Time", value=f"{stats['total']:,}", inline=True)
        embed.add_field(name="Today", value=f"{stats['daily']:,}", inline=True)
        return embed

    def create_voice_stats_embed(self, target: discord.Member, stats: dict):
        voice_total = stats.get("voice_total", 0)
        voice_daily = stats.get("voice_daily", 0)
        embed = discord.Embed(
            title="<a:003_bel:1344601355515330621> Voice Statistics",
            color=self.get_random_color(),
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="User", value=target.mention, inline=False)
        embed.add_field(name="All Time", value=f"{self.format_duration(voice_total)}", inline=True)
        embed.add_field(name="Today", value=f"{self.format_duration(voice_daily)}", inline=True)
        return embed

    def format_duration(self, seconds):
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h {m}m {s}s"
        elif m:
            return f"{m}m {s}s"
        else:
            return f"{s}s"

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
            title="<a:003_bel:1341822673247797319> Message Leaderboard",
            description="\n".join(description) if description else "No messages tracked yet!",
            color=self.get_random_color()
        )
        embed.set_footer(text=f"Page {page + 1}")
        embed.timestamp = datetime.now()
        return embed

    async def create_voice_leaderboard_embed(self, page: int = 0, guild_id: str = None):
        if not guild_id:
            return
        users = await self.db.get_top_voice_users(str(guild_id), page * 10, 10)
        description = []
        start_rank = page * 10 + 1
        for i, (user_id, voice_total) in enumerate(users, start_rank):
            user = self.bot.get_user(int(user_id))
            if user:
                rank_str = f"#{i:2d}"
                duration = self.format_duration(voice_total)
                description.append(f"``{rank_str}`` {user.mention} - **{duration}** voice time")
        embed = discord.Embed(
            title="<a:003_bel:1341822673247797319> Voice Leaderboard",
            description="\n".join(description) if description else "No voice data tracked yet!",
            color=self.get_random_color()
        )
        embed.set_footer(text=f"Page {page + 1}")
        embed.timestamp = datetime.now()
        return embed

    @commands.command(name='me')
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def message_count(self, ctx, member: discord.Member = None):
        """Shows message statistics for a user, with a button to toggle voice stats."""
        try:
            logging.info(f"Executing 'm' command for user {ctx.author.id}")
            target = member or ctx.author
            stats = await self.db.get_user_stats(str(ctx.guild.id), str(target.id))
            embed = self.create_message_stats_embed(target, stats)
            view = StatsToggleView(self, target, initial_mode="message")
            await ctx.send(embed=embed, view=view)
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

    @commands.command(name='lb')
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def leaderboard(self, ctx, mode: str = 'm'):
        """
        Shows a leaderboard.
        Use `.lb m` for the message leaderboard and `.lb v` for the voice leaderboard.
        """
        mode = mode.lower()
        if mode not in ['m', 'v']:
            return await ctx.send("Please specify a valid leaderboard type: `m` for messages or `v` for voice.")

        lock_key = f"{ctx.guild.id}:{ctx.command.name}:{mode}"
        if hasattr(self.bot, 'processing_commands') and self.bot.processing_commands.get(lock_key, False):
            logging.info(f"Skipping duplicate execution of {ctx.command.name} for guild {ctx.guild.id} with mode {mode}")
            return

        try:
            if hasattr(self.bot, 'processing_commands'):
                self.bot.processing_commands[lock_key] = True
                logging.info(f"Lock acquired for {ctx.command.name} - Guild: {ctx.guild.id}, Mode: {mode}")

            if mode == 'm':
                embed = await self.create_leaderboard_embed(0, ctx.guild.id)
            else:
                embed = await self.create_voice_leaderboard_embed(0, ctx.guild.id)

            view = LeaderboardButtons(self, mode)
            await ctx.send(embed=embed, view=view)
            logging.info(f"Successfully executed {ctx.command.name} for guild {ctx.guild.id} with mode {mode}")
        except Exception as e:
            logging.error(f"Error in {ctx.command.name} for guild {ctx.guild.id}: {str(e)}")
            raise
        finally:
            if hasattr(self.bot, 'processing_commands'):
                self.bot.processing_commands[lock_key] = False
                logging.info(f"Lock released for {ctx.command.name} - Guild: {ctx.guild.id}, Mode: {mode}")

    @leaderboard.error
    async def leaderboard_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            logging.info(f"Cooldown triggered for leaderboard - Guild: {ctx.guild.id} - Retry after: {error.retry_after:.1f}s")
            await ctx.send(f"Please wait {error.retry_after:.1f}s before using this command again.")
        else:
            logging.error(f"Error in leaderboard command: {str(error)}")
            await ctx.send("An error occurred while fetching the leaderboard.")

    # ------------------ New Slash Command for Resetting Activity ------------------
    @app_commands.command(name="reset-activity", description="Reset message or voice statistics for this server or a specific user.")
    @app_commands.describe(
        activity="Select the type of activity to reset: message or voice",
        member="The member whose statistics to reset (only needed when resetting a single user)"
    )
    @app_commands.choices(activity=[
        app_commands.Choice(name="Message", value="message"),
        app_commands.Choice(name="Voice", value="voice")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def reset_activity(
        self,
        interaction: discord.Interaction,
        activity: app_commands.Choice[str],
        member: discord.Member = None
    ):
        logging.info(f"Reset-activity command invoked by {interaction.user.id} - Activity: {activity.value}, Member: {member.id if member else 'None'}")
        if not interaction.guild:
            logging.warning(f"Reset-activity attempted outside guild context by {interaction.user.id}")
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if activity.value not in ["message", "voice"]:
            await interaction.response.send_message("Invalid activity type specified.", ephemeral=True)
            return

        description = (
            f"Are you sure you want to reset **{activity.value} statistics** for "
            f"{member.display_name if member else 'the entire server'}?\nThis action cannot be undone!"
        )

        confirm_embed = discord.Embed(
            title="<:sukoon_info:1344600840714846268> Confirmation Required",
            description=description,
            color=discord.Color.red(),
            timestamp=datetime.now()
        )

        view = ResetActivityConfirmationView(activity.value, self, interaction, member)
        await interaction.response.send_message(embed=confirm_embed, view=view, ephemeral=True)
        logging.info(f"Reset-activity confirmation prompt sent to {interaction.user.id}")

    @reset_activity.error
    async def reset_activity_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            logging.warning(f"Reset-activity permission denied for {interaction.user.id}")
            await interaction.response.send_message("You need administrator permissions to use this command.", ephemeral=True)
        else:
            logging.error(f"Error in reset_activity command: {str(error)}")
            await interaction.response.send_message("An error occurred.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Statistics(bot))
    logging.info("Statistics cog has been added.")
