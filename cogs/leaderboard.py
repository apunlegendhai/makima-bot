import discord
from discord.ext import commands, tasks
from discord import app_commands
import motor.motor_asyncio
import os
import logging
import random
import colorsys
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# ------------------ MongoDB Database Logic ------------------
class MongoDatabase:
    def __init__(self, url: str, db_name: str):
        """
        :param url: MongoDB connection URL from environment variable MONGO_URL
        :param db_name: MongoDB database name from environment variable MONGO_DB_NAME
        """
        self.url = url
        self.db_name = db_name
        self.client = None
        self.db = None
        self.daily_stats = None
        self.user_stats = None

    async def setup(self):
        logging.info("Connecting to MongoDB ...")
        self.client = motor.motor_asyncio.AsyncIOMotorClient(self.url)
        self.db = self.client[self.db_name]

        self.daily_stats = self.db["daily_stats"]
        self.user_stats = self.db["user_stats"]

        # Create indexes for efficient queries
        await self.daily_stats.create_index(
            [("guild_id", 1), ("user_id", 1), ("date", 1)],
            unique=True
        )
        await self.user_stats.create_index(
            [("guild_id", 1), ("user_id", 1)],
            unique=True
        )
        logging.info(f"MongoDB setup complete for database: {self.db_name}")

    async def update_message_count(self, guild_id: str, user_id: str):
        # Use a datetime at midnight UTC
        today = datetime.combine(datetime.utcnow().date(), datetime.min.time())
        await self.daily_stats.update_one(
            {"guild_id": guild_id, "user_id": user_id, "date": today},
            {"$inc": {"messages": 1}},
            upsert=True
        )
        await self.user_stats.update_one(
            {"guild_id": guild_id, "user_id": user_id},
            {"$inc": {"total_messages": 1}},
            upsert=True
        )

    async def update_voice_time(self, guild_id: str, user_id: str, duration: float):
        today = datetime.combine(datetime.utcnow().date(), datetime.min.time())
        await self.daily_stats.update_one(
            {"guild_id": guild_id, "user_id": user_id, "date": today},
            {"$inc": {"voice_time": duration}},
            upsert=True
        )
        await self.user_stats.update_one(
            {"guild_id": guild_id, "user_id": user_id},
            {"$inc": {"total_voice_time": duration}},
            upsert=True
        )

    async def get_user_stats(self, guild_id: str, user_id: str):
        today = datetime.combine(datetime.utcnow().date(), datetime.min.time())
        user_doc = await self.user_stats.find_one(
            {"guild_id": guild_id, "user_id": user_id}
        )
        daily_doc = await self.daily_stats.find_one(
            {"guild_id": guild_id, "user_id": user_id, "date": today}
        )
        user_doc = user_doc or {}
        daily_doc = daily_doc or {}
        return {
            "total": user_doc.get("total_messages", 0),
            "daily": daily_doc.get("messages", 0),
            "voice_total": user_doc.get("total_voice_time", 0.0),
            "voice_daily": daily_doc.get("voice_time", 0.0)
        }

    async def get_last_14_days(self, guild_id: str, user_id: str):
        start_date = datetime.combine((datetime.utcnow().date() - timedelta(days=13)), datetime.min.time())
        cursor = self.daily_stats.find(
            {
                "guild_id": guild_id,
                "user_id": user_id,
                "date": {"$gte": start_date}
            }
        ).sort("date", 1)
        docs = await cursor.to_list(length=100)
        stats_map = {doc["date"]: doc for doc in docs}
        date_list, messages_list, voice_hours_list = [], [], []
        for i in range(14):
            d = start_date + timedelta(days=i)
            doc = stats_map.get(d, {})
            msgs = doc.get("messages", 0)
            voice_sec = doc.get("voice_time", 0.0)
            date_list.append(d)
            messages_list.append(msgs)
            voice_hours_list.append(voice_sec / 3600.0)
        return date_list, messages_list, voice_hours_list

    async def get_top_users(self, guild_id: str, offset: int, limit: int):
        cursor = self.user_stats.find({"guild_id": guild_id}) \
            .sort("total_messages", -1) \
            .skip(offset) \
            .limit(limit)
        return await cursor.to_list(length=limit)

    async def get_top_voice_users(self, guild_id: str, offset: int, limit: int):
        cursor = self.user_stats.find({"guild_id": guild_id}) \
            .sort("total_voice_time", -1) \
            .skip(offset) \
            .limit(limit)
        return await cursor.to_list(length=limit)

    async def reset_activity_for_guild(self, guild_id: str, activity: str):
        if activity == "message":
            await self.user_stats.update_many(
                {"guild_id": guild_id},
                {"$set": {"total_messages": 0}}
            )
            await self.daily_stats.update_many(
                {"guild_id": guild_id},
                {"$set": {"messages": 0}}
            )
        elif activity == "voice":
            await self.user_stats.update_many(
                {"guild_id": guild_id},
                {"$set": {"total_voice_time": 0.0}}
            )
            await self.daily_stats.update_many(
                {"guild_id": guild_id},
                {"$set": {"voice_time": 0.0}}
            )

    async def reset_activity_for_user(self, guild_id: str, user_id: str, activity: str):
        if activity == "message":
            await self.user_stats.update_one(
                {"guild_id": guild_id, "user_id": user_id},
                {"$set": {"total_messages": 0}}
            )
            await self.daily_stats.update_many(
                {"guild_id": guild_id, "user_id": user_id},
                {"$set": {"messages": 0}}
            )
        elif activity == "voice":
            await self.user_stats.update_one(
                {"guild_id": guild_id, "user_id": user_id},
                {"$set": {"total_voice_time": 0.0}}
            )
            await self.daily_stats.update_many(
                {"guild_id": guild_id, "user_id": user_id},
                {"$set": {"voice_time": 0.0}}
            )


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
                elif child.custom_id == "graphical_stats":
                    child.disabled = (self.current_mode == "graphical")

    @discord.ui.button(label="Messages", style=discord.ButtonStyle.secondary, custom_id="message_stats")
    async def message_stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            return await interaction.response.send_message("This button isn't for you.", ephemeral=True)
        self.current_mode = "message"
        self.update_buttons()
        stats = await self.cog.db.get_user_stats(str(interaction.guild.id), str(self.target.id))
        embed = self.cog.create_message_stats_embed(self.target, stats)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Voice", style=discord.ButtonStyle.secondary, custom_id="voice_stats")
    async def voice_stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            return await interaction.response.send_message("This button isn't for you.", ephemeral=True)
        self.current_mode = "voice"
        self.update_buttons()
        stats = await self.cog.db.get_user_stats(str(interaction.guild.id), str(self.target.id))
        embed = self.cog.create_voice_stats_embed(self.target, stats)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Graph", style=discord.ButtonStyle.secondary, custom_id="graphical_stats")
    async def graphical_stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            return await interaction.response.send_message("This button isn't for you.", ephemeral=True)
        self.current_mode = "graphical"
        self.update_buttons()
        embed, file = await self.cog.create_graphical_stats_embed(str(interaction.guild.id), self.target)
        await interaction.response.edit_message(embed=embed, view=self, attachments=[file])


# ------------------ Reset Activity Confirmation View ------------------
class ResetActivityConfirmationView(discord.ui.View):
    def __init__(self, activity: str, cog, interaction: discord.Interaction, member: discord.Member = None):
        super().__init__(timeout=30)
        self.activity = activity
        self.cog = cog
        self.original_interaction = interaction
        self.member = member

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_interaction.user.id:
            return await interaction.response.send_message("This confirmation isn't for you.", ephemeral=True)
        try:
            guild_id = str(self.original_interaction.guild.id)
            if self.member:
                await self.cog.db.reset_activity_for_user(guild_id, str(self.member.id), self.activity)
                action_taken = f"{self.activity.capitalize()} statistics for {self.member.display_name} have been reset."
            else:
                await self.cog.db.reset_activity_for_guild(guild_id, self.activity)
                action_taken = f"{self.activity.capitalize()} statistics for this server have been reset."
            result_embed = discord.Embed(
                title="Reset Complete",
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

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_interaction.user.id:
            return await interaction.response.send_message("This confirmation isn't for you.", ephemeral=True)
        result_embed = discord.Embed(
            title="Reset Cancelled",
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
        self.max_pages = 9

    @discord.ui.button(label="◀", style=discord.ButtonStyle.gray)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            if self.mode == 'm':
                embed = await self.cog.create_leaderboard_embed(self.current_page, str(interaction.guild.id))
            else:
                embed = await self.cog.create_voice_leaderboard_embed(self.current_page, str(interaction.guild.id))
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_pages:
            self.current_page += 1
            if self.mode == 'm':
                embed = await self.cog.create_leaderboard_embed(self.current_page, str(interaction.guild.id))
            else:
                embed = await self.cog.create_voice_leaderboard_embed(self.current_page, str(interaction.guild.id))
            if not embed or not embed.description or "No" in embed.description:
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
        MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
        MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "mybot")
        self.db = MongoDatabase(MONGO_URL, MONGO_DB_NAME)
        self.voice_sessions = {}  # (guild_id, user_id) -> datetime of voice join
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
        return self.color_pool.pop()

    def cog_unload(self):
        logging.info("Statistics cog unloading...")
        self.check_daily_reset.cancel()

    @tasks.loop(minutes=1)
    async def check_daily_reset(self):
        now = datetime.utcnow()
        if now.hour == 0 and now.minute == 0:
            logging.info("It's midnight UTC - daily maintenance tasks could run here if needed.")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        await self.db.update_message_count(str(message.guild.id), str(message.author.id))
        await self.bot.process_commands(message)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        guild_id = str(member.guild.id)
        user_id = str(member.id)
        if before.channel is None and after.channel is not None:
            self.voice_sessions[(guild_id, user_id)] = datetime.utcnow()
        elif before.channel is not None and after.channel is None:
            join_time = self.voice_sessions.pop((guild_id, user_id), None)
            if join_time:
                duration = (datetime.utcnow() - join_time).total_seconds()
                await self.db.update_voice_time(guild_id, user_id, duration)
        elif before.channel is not None and after.channel is not None and before.channel != after.channel:
            join_time = self.voice_sessions.pop((guild_id, user_id), None)
            if join_time:
                duration = (datetime.utcnow() - join_time).total_seconds()
                await self.db.update_voice_time(guild_id, user_id, duration)
            self.voice_sessions[(guild_id, user_id)] = datetime.utcnow()

    def create_message_stats_embed(self, target: discord.Member, stats: dict):
        embed = discord.Embed(
            title="Message Statistics",
            color=self.get_random_color(),
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="User", value=target.mention, inline=False)
        embed.add_field(name="All Time", value=f"{stats['total']:,}", inline=True)
        embed.add_field(name="Today", value=f"{stats['daily']:,}", inline=True)
        return embed

    def create_voice_stats_embed(self, target: discord.Member, stats: dict):
        embed = discord.Embed(
            title="Voice Statistics",
            color=self.get_random_color(),
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="User", value=target.mention, inline=False)
        embed.add_field(name="All Time", value=self.format_duration(stats["voice_total"]), inline=True)
        embed.add_field(name="Today", value=self.format_duration(stats["voice_daily"]), inline=True)
        return embed

    async def create_graphical_stats_embed(self, guild_id: str, target: discord.Member):
        date_list, messages_data, voice_hours_data = await self.db.get_last_14_days(guild_id, str(target.id))
        plt.style.use("dark_background")
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(date_list, messages_data, color="#FF69B4", linewidth=2, label="Messages")
        ax.fill_between(date_list, messages_data, color="#FF69B4", alpha=0.1)
        ax.plot(date_list, voice_hours_data, color="#32CD32", linewidth=2, label="Voice Hours")
        ax.fill_between(date_list, voice_hours_data, color="#32CD32", alpha=0.1)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
        plt.setp(ax.get_xticklabels(), rotation=30, ha='right')
        ax.grid(color='gray', linestyle='--', linewidth=0.5, alpha=0.5)
        ax.legend(loc="upper left", fontsize=10)
        ax.set_title(f"{target.display_name} | User Stats (14 Days)", fontsize=14)
        ax.set_ylabel("Count / Hours", fontsize=11)
        plt.text(0.5, -0.15,
                 "Server Lookback: Last 14 days — Timezone: UTC",
                 ha='center', va='center',
                 transform=ax.transAxes,
                 fontsize=9, color='gray')
        plt.text(0.95, 0.05,
                 "Powered by YourBot",
                 ha='right', va='center',
                 transform=ax.transAxes,
                 fontsize=9, color='gray', alpha=0.7)
        buffer = BytesIO()
        plt.tight_layout()
        plt.savefig(buffer, format="png", dpi=120)
        buffer.seek(0)
        plt.close(fig)
        file = discord.File(fp=buffer, filename="stats.png")
        stats = await self.db.get_user_stats(guild_id, str(target.id))
        embed = discord.Embed(
            title=(f"{target.display_name} | Today: {stats['daily']} msgs / "
                   f"{self.format_duration(stats['voice_daily'])} voice"),
            color=self.get_random_color(),
            timestamp=datetime.now()
        )
        embed.set_image(url="attachment://stats.png")
        return embed, file

    def format_duration(self, seconds: float):
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h {m}m {s}s"
        elif m:
            return f"{m}m {s}s"
        else:
            return f"{s}s"

    async def create_leaderboard_embed(self, page: int, guild_id: str):
        offset = page * 10
        docs = await self.db.get_top_users(guild_id, offset, 10)
        if not docs:
            return discord.Embed(
                title="Message Leaderboard",
                description="No data found!",
                color=self.get_random_color()
            )
        description = []
        start_rank = offset + 1
        for i, doc in enumerate(docs, start_rank):
            user_obj = self.bot.get_user(int(doc["user_id"]))
            total = doc.get("total_messages", 0)
            rank_str = f"#{i}"
            mention_str = user_obj.mention if user_obj else f"<@{doc['user_id']}>"
            description.append(f"`{rank_str}` {mention_str} - **{total:,}** messages")
        embed = discord.Embed(
            title="Message Leaderboard",
            description="\n".join(description),
            color=self.get_random_color(),
            timestamp=datetime.now()
        )
        embed.set_footer(text=f"Page {page+1}")
        return embed

    async def create_voice_leaderboard_embed(self, page: int, guild_id: str):
        offset = page * 10
        docs = await self.db.get_top_voice_users(guild_id, offset, 10)
        if not docs:
            return discord.Embed(
                title="Voice Leaderboard",
                description="No data found!",
                color=self.get_random_color()
            )
        description = []
        start_rank = offset + 1
        for i, doc in enumerate(docs, start_rank):
            user_obj = self.bot.get_user(int(doc["user_id"]))
            total_voice = doc.get("total_voice_time", 0.0)
            rank_str = f"#{i}"
            mention_str = user_obj.mention if user_obj else f"<@{doc['user_id']}>"
            description.append(f"`{rank_str}` {mention_str} - **{self.format_duration(total_voice)}**")
        embed = discord.Embed(
            title="Voice Leaderboard",
            description="\n".join(description),
            color=self.get_random_color(),
            timestamp=datetime.now()
        )
        embed.set_footer(text=f"Page {page+1}")
        return embed

    @commands.command(name='me')
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def message_count(self, ctx, member: discord.Member = None):
        """Shows message and voice stats with a 14-day graph toggle."""
        target = member or ctx.author
        stats = await self.db.get_user_stats(str(ctx.guild.id), str(target.id))
        embed = self.create_message_stats_embed(target, stats)
        view = StatsToggleView(self, target, initial_mode="message")
        await ctx.send(embed=embed, view=view)

    @message_count.error
    async def message_count_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Please wait {error.retry_after:.1f}s before using this command again.")
        else:
            logging.error(f"Error in 'me' command: {str(error)}")
            await ctx.send("An error occurred while fetching statistics.")

    @commands.command(name='lb')
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def leaderboard(self, ctx, mode: str = 'm'):
        """
        Shows a leaderboard.
        Usage: !lb m -> Message Leaderboard | !lb v -> Voice Leaderboard
        """
        mode = mode.lower()
        if mode not in ['m', 'v']:
            return await ctx.send("Please specify a valid leaderboard type: 'm' or 'v'.")
        if mode == 'm':
            embed = await self.create_leaderboard_embed(0, str(ctx.guild.id))
        else:
            embed = await self.create_voice_leaderboard_embed(0, str(ctx.guild.id))
        view = LeaderboardButtons(self, mode)
        await ctx.send(embed=embed, view=view)

    @leaderboard.error
    async def leaderboard_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Please wait {error.retry_after:.1f}s before using this command again.")
        else:
            logging.error(f"Error in leaderboard command: {str(error)}")
            await ctx.send("An error occurred while fetching the leaderboard.")

    @app_commands.command(name="reset-activity", description="Reset message or voice statistics for a server or a specific user.")
    @app_commands.describe(
        activity="Type of activity to reset: message or voice",
        member="Optional: The member whose stats to reset"
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
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        desc = (
            f"Are you sure you want to reset **{activity.value} statistics** for "
            f"{member.display_name if member else 'the entire server'}?\nThis action cannot be undone!"
        )
        confirm_embed = discord.Embed(
            title="Confirmation Required",
            description=desc,
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        view = ResetActivityConfirmationView(activity.value, self, interaction, member)
        await interaction.response.send_message(embed=confirm_embed, view=view, ephemeral=True)

    @reset_activity.error
    async def reset_activity_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need administrator permissions to use this command.", ephemeral=True)
        else:
            logging.error(f"Error in reset_activity command: {str(error)}")
            await interaction.response.send_message("An error occurred.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Statistics(bot))
    logging.info("Statistics cog has been added.")
