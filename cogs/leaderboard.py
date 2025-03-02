import matplotlib
matplotlib.use('Agg')  # Use a non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO

import discord
from discord.ext import commands, tasks
from discord import app_commands
import motor.motor_asyncio
import os
import logging
import random
import colorsys
from datetime import datetime, timedelta
from dotenv import load_dotenv
import warnings

# Suppress warnings about missing glyph 4048 (Tibetan mark)
warnings.filterwarnings("ignore", message="Glyph 4048")

load_dotenv()  # Load environment variables from .env

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# ------------------ MongoDB Database Logic ------------------
class MongoDatabase:
    def __init__(self, url: str, db_name: str):
        """
        :param url: MongoDB connection URL
        :param db_name: MongoDB database name
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
        user_doc = await self.user_stats.find_one({"guild_id": guild_id, "user_id": user_id})
        daily_doc = await self.daily_stats.find_one({"guild_id": guild_id, "user_id": user_id, "date": today})
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
        cursor = self.daily_stats.find({
            "guild_id": guild_id,
            "user_id": user_id,
            "date": {"$gte": start_date}
        }).sort("date", 1)
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
            voice_hours_list.append(voice_sec / 3600.0)  # Convert seconds to hours
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
            await self.user_stats.update_many({"guild_id": guild_id}, {"$set": {"total_messages": 0}})
            await self.daily_stats.update_many({"guild_id": guild_id}, {"$set": {"messages": 0}})
        elif activity == "voice":
            await self.user_stats.update_many({"guild_id": guild_id}, {"$set": {"total_voice_time": 0.0}})
            await self.daily_stats.update_many({"guild_id": guild_id}, {"$set": {"voice_time": 0.0}})

    async def reset_activity_for_user(self, guild_id: str, user_id: str, activity: str):
        if activity == "message":
            await self.user_stats.update_one({"guild_id": guild_id, "user_id": user_id}, {"$set": {"total_messages": 0}})
            await self.daily_stats.update_many({"guild_id": guild_id, "user_id": user_id}, {"$set": {"messages": 0}})
        elif activity == "voice":
            await self.user_stats.update_one({"guild_id": guild_id, "user_id": user_id}, {"$set": {"total_voice_time": 0.0}})
            await self.daily_stats.update_many({"guild_id": guild_id, "user_id": user_id}, {"$set": {"voice_time": 0.0}})


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

    @discord.ui.button(emoji="<a:sukooon_cha:1344706814616273010>", style=discord.ButtonStyle.secondary, custom_id="message_stats")
    async def message_stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("This button isn't for you.", ephemeral=True)
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        self.current_mode = "message"
        self.update_buttons()
        stats = await self.cog.db.get_user_stats(str(interaction.guild.id), str(self.target.id))
        embed = self.cog.create_message_stats_embed(self.target, stats)
        await interaction.edit_original_response(embed=embed, view=self, attachments=[])

    @discord.ui.button(emoji="<:sukooon_voic:1344707189851295876>", style=discord.ButtonStyle.secondary, custom_id="voice_stats")
    async def voice_stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("This button isn't for you.", ephemeral=True)
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        self.current_mode = "voice"
        self.update_buttons()
        stats = await self.cog.db.get_user_stats(str(interaction.guild.id), str(self.target.id))
        embed = self.cog.create_voice_stats_embed(self.target, stats)
        await interaction.edit_original_response(embed=embed, view=self, attachments=[])

    @discord.ui.button(emoji="<:sukoon_statss:1344711129359847485>", style=discord.ButtonStyle.secondary, custom_id="graphical_stats")
    async def graphical_stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("This button isn't for you.", ephemeral=True)
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        self.current_mode = "graphical"
        self.update_buttons()
        embed, file = await self.cog.create_graphical_stats_embed(str(interaction.guild.id), self.target)
        await interaction.edit_original_response(embed=embed, view=self, attachments=[file] if file else [])


# ------------------ Reset Activity Confirmation View ------------------
class ResetActivityConfirmationView(discord.ui.View):
    def __init__(self, activity: str, cog, interaction: discord.Interaction, member: discord.Member = None):
        super().__init__(timeout=30)
        self.activity = activity
        self.cog = cog
        self.original_interaction = interaction
        self.member = member

    @discord.ui.button(emoji="<:sukoon_tick:1344600783257075815>", style=discord.ButtonStyle.secondary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message("This confirmation isn't for you.", ephemeral=True)
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        try:
            guild_id = str(self.original_interaction.guild.id)
            if self.member:
                await self.cog.db.reset_activity_for_user(guild_id, str(self.member.id), self.activity)
                if self.activity == "voice" and (guild_id, str(self.member.id)) in self.cog.voice_sessions:
                    self.cog.voice_sessions[(guild_id, str(self.member.id))] = datetime.utcnow()
                action_taken = f"<a:sukoon_whitetick:1344600976962748458> | {self.activity.capitalize()} statistics for {self.member.display_name} have been reset."
            else:
                await self.cog.db.reset_activity_for_guild(guild_id, self.activity)
                if self.activity == "voice":
                    for key in list(self.cog.voice_sessions.keys()):
                        if key[0] == guild_id:
                            self.cog.voice_sessions[key] = datetime.utcnow()
                action_taken = f"<a:sukoon_whitetick:1344600976962748458> | {self.activity.capitalize()} statistics for this server have been reset."
            result_embed = discord.Embed(
                description=action_taken,
                color=self.cog.get_random_color(),
                timestamp=datetime.now()
            )
            await interaction.followup.send(embed=result_embed, ephemeral=True)
        except Exception as e:
            logging.error(f"Error in reset activity confirmation: {str(e)}")
            await interaction.followup.send("<:sukoon_info:1344600840714846268> | An error occurred while resetting statistics.", ephemeral=True)
        finally:
            self.stop()

    @discord.ui.button(emoji="<:sukoon_cross:1344600813808390174>", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message("<:sukoon_info:1344600840714846268> | This confirmation isn't for you.", ephemeral=True)
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        result_embed = discord.Embed(
            description="<:sukoon_info:1344600840714846268> | No changes were made to the statistics.",
            color=self.cog.get_random_color(),
            timestamp=datetime.now()
        )
        await interaction.followup.send(embed=result_embed, ephemeral=True)
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

    @discord.ui.button(emoji="<:sukoon_left_arro:1345075074012676219>", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
            if self.mode == 'm':
                embed = await self.cog.create_leaderboard_embed(self.current_page, str(interaction.guild.id))
            else:
                embed = await self.cog.create_voice_leaderboard_embed(self.current_page, str(interaction.guild.id))
            await interaction.edit_original_response(embed=embed, view=self, attachments=[])
        else:
            await interaction.edit_original_response(view=self)

    @discord.ui.button(emoji="<:sukoon_right_arro:1345075121039216693>", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer()
        if self.current_page < self.max_pages:
            self.current_page += 1
            if self.mode == 'm':
                embed = await self.cog.create_leaderboard_embed(self.current_page, str(interaction.guild.id))
            else:
                embed = await self.cog.create_voice_leaderboard_embed(self.current_page, str(interaction.guild.id))
            if not embed or not embed.description or "No" in embed.description:
                self.current_page -= 1
                await interaction.edit_original_response(view=self)
            else:
                await interaction.edit_original_response(embed=embed, view=self, attachments=[])
        else:
            await interaction.edit_original_response(view=self)

    @discord.ui.button(emoji="<:sukoon_statss:1344711129359847485>", style=discord.ButtonStyle.secondary, custom_id="graphical_leaderboard")
    async def graphical_leaderboard_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer()
        embed, file = await self.cog.create_graphical_leaderboard_embed(self.current_page, str(interaction.guild.id), self.mode)
        if file:
            await interaction.edit_original_response(embed=embed, view=self, attachments=[file])
        else:
            await interaction.followup.send_message("Graphical data not available.", ephemeral=True)


# ------------------ Statistics Cog ------------------
class Statistics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
        MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "mybot")
        self.db = MongoDatabase(MONGO_URL, MONGO_DB_NAME)
        self.voice_sessions = {}  # (guild_id, user_id) -> datetime of voice join
        self.check_daily_reset.start()
        logging.info("Statistics cog initialized")

    async def cog_load(self):
        await self.db.setup()
        logging.info("Statistics cog database setup complete")

    def get_random_color(self):
        # Generate a new random color with broad variation in hue, saturation, and brightness.
        h = random.random()
        s = random.uniform(0.5, 1.0)
        v = random.uniform(0.7, 1.0)
        rgb = colorsys.hsv_to_rgb(h, s, v)
        return int(rgb[0]*255) << 16 | int(rgb[1]*255) << 8 | int(rgb[2]*255)

    def cog_unload(self):
        logging.info("Statistics cog unloading...")
        self.check_daily_reset.cancel()

    @tasks.loop(minutes=1)
    async def check_daily_reset(self):
        now = datetime.utcnow()
        if now.hour == 0 and now.minute == 0:
            logging.info("It's midnight UTC - daily maintenance tasks could run here if needed.")

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
        # Retrieve data for the last 14 days
        date_list, messages_data, voice_hours_data = await self.db.get_last_14_days(guild_id, str(target.id))
        # Retrieve total stats (to display in the subtitle)
        stats = await self.db.get_user_stats(guild_id, str(target.id))
        total_msgs = stats["total"]
        total_voice_hrs = stats["voice_total"] / 3600.0

        # ---- Chart Setup ----
        plt.style.use("dark_background")
        fig, ax1 = plt.subplots(figsize=(14, 7), dpi=300)
        fig.patch.set_facecolor("#1a1a1a")
        ax1.set_facecolor("#1a1a1a")
        ax1.grid(True, which='major', linestyle='--', linewidth=0.5, alpha=0.7)

        # Use random colors for both data series
        color_msgs = f"#{self.get_random_color():06x}"
        color_voice = f"#{self.get_random_color():06x}"
        # Improved line plotting with different markers
        ax1.plot(date_list, messages_data, color=color_msgs, linewidth=3, marker='D', markersize=8, label="Messages")
        ax1.fill_between(date_list, messages_data, color=color_msgs, alpha=0.3)
        ax1.set_ylabel("Messages", color=color_msgs, fontsize=14)
        ax1.tick_params(axis="y", labelcolor=color_msgs, labelsize=12)

        ax2 = ax1.twinx()
        ax2.plot(date_list, voice_hours_data, color=color_voice, linewidth=3, marker='^', markersize=8, label="Voice Hours")
        ax2.fill_between(date_list, voice_hours_data, color=color_voice, alpha=0.3)
        ax2.set_ylabel("Voice Hours", color=color_voice, fontsize=14)
        ax2.tick_params(axis="y", labelcolor=color_voice, labelsize=12)

        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
        ax1.xaxis.set_major_locator(mdates.DayLocator(interval=2))
        plt.setp(ax1.get_xticklabels(), rotation=30, ha='right', fontsize=12, color="white")
        ax1.set_xlabel("Date", fontsize=14, color="white")

        # Updated titles and subtitles
        ax1.set_title(
            f"{target.display_name} - User Stats\nMessages: {total_msgs:,} | Voice Hours: {total_voice_hrs:.2f}",
            fontsize=18, color="white", pad=20
        )

        fig.text(0.5, 0.02, "Server Lookback: Last 14 days — Timezone: UTC", ha="center", color="gray", fontsize=10)
        fig.text(0.95, 0.02, "Powered by Mercy", ha="right", color="gray", fontsize=10)

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc="best", facecolor="#1a1a1a", edgecolor='white', fontsize=12)

        plt.tight_layout()

        buffer = BytesIO()
        plt.savefig(buffer, format="png")
        buffer.seek(0)
        plt.close(fig)

        file = discord.File(fp=buffer, filename="stats.png")
        embed = discord.Embed(
            title=(f"{target.display_name} | Today: {stats['daily']} msgs / "
                   f"{self.format_duration(stats['voice_daily'])} voice"),
            color=self.get_random_color(),
            timestamp=datetime.now()
        )
        embed.set_image(url="attachment://stats.png")
        return embed, file

    async def create_graphical_leaderboard_embed(self, page: int, guild_id: str, mode: str):
        # For voice mode, we use 8 users to match the example; for messages, keep 10.
        offset = page * (8 if mode == 'v' else 10)
        if mode == 'm':
            docs = await self.db.get_top_users(guild_id, offset, 10)
            ylabel = "Messages"
            data_key = "total_messages"
        else:
            docs = await self.db.get_top_voice_users(guild_id, offset, 8)
            ylabel = "Voice Time (s)"
            data_key = "total_voice_time"

        if not docs:
            embed = discord.Embed(
                title="Leaderboard",
                description="No data found!",
                color=self.get_random_color()
            )
            return embed, None

        # Set up the figure with improved dark background
        plt.style.use("dark_background")
        fig, ax = plt.subplots(figsize=(10, 6), dpi=300)
        fig.patch.set_facecolor("#1a1a1a")
        ax.set_facecolor("#1a1a1a")
        ax.grid(True, which='major', color='gray', linestyle='--', linewidth=0.5, alpha=0.7)

        # Prepare data: extract usernames and aggregated values.
        user_names = []
        values = []
        for doc in docs:
            user_id = str(doc["user_id"])
            user = self.bot.get_user(int(user_id))
            name = user.display_name if user else f"<@{doc['user_id']}>"
            user_names.append(name)
            values.append(doc.get(data_key, 0))

        # Use numerical x positions for the categorical user names.
        x = range(len(user_names))
        random_color = f"#{self.get_random_color():06x}"
        ax.plot(x, values, color=random_color, marker='D', linestyle='-', linewidth=3, markersize=8)
        ax.fill_between(x, values, color=random_color, alpha=0.3)

        ax.set_xticks(x)
        ax.set_xticklabels(user_names, rotation=45, ha='right', fontsize=12, color="white")
        ax.set_xlabel("User", fontsize=14, color="white")
        ax.set_ylabel(ylabel, fontsize=14, color="white")

        title_line = f"Leaderboard - {ylabel}"
        subtitle_line = f"Page {page+1}"
        ax.set_title(f"{title_line}\n{subtitle_line}", fontsize=18, color="white", pad=20)

        fig.text(0.5, 0.02, "Aggregated data from leaderboard", ha="center", color="gray", fontsize=10)
        fig.text(0.95, 0.02, "Powered by Mercy", ha="right", color="gray", fontsize=10)

        plt.tight_layout()

        buffer = BytesIO()
        plt.savefig(buffer, format="png")
        buffer.seek(0)
        plt.close(fig)
        file = discord.File(fp=buffer, filename="leaderboard_graph.png")

        embed = discord.Embed(
            title="Leaderboard Graph",
            color=self.get_random_color(),
            timestamp=datetime.now()
        )
        embed.set_image(url="attachment://leaderboard_graph.png")
        embed.set_footer(text=f"Page {page+1}")
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

    @app_commands.command(name="reset-leaderboard", description="Reset message or voice statistics for a server or a specific user.")
    @app_commands.describe(
        activity="Type of activity to reset: message or voice",
        member="Optional: The member whose stats to reset"
    )
    @app_commands.choices(activity=[
        app_commands.Choice(name="Message", value="message"),
        app_commands.Choice(name="Voice", value="voice")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def reset_activity(self, interaction: discord.Interaction, activity: app_commands.Choice[str], member: discord.Member = None):
        if not interaction.guild:
            await interaction.response.send_message("<:sukoon_info:1344600840714846268> This command can only be used in a server.", ephemeral=True)
            return
        desc = (
            f"Are you sure you want to reset **{activity.value} statistics** for "
            f"{member.display_name if member else 'the entire server'}?\nThis action cannot be undone!"
        )
        confirm_embed = discord.Embed(
            title="<:sukoon_info:1344600840714846268> Confirmation Required",
            description=desc,
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        view = ResetActivityConfirmationView(activity.value, self, interaction, member)
        await interaction.response.send_message(embed=confirm_embed, view=view, ephemeral=True)


async def setup(bot):
    if "Statistics" in bot.cogs:
        logging.info("Statistics cog is already loaded; skipping duplicate load.")
        return
    await bot.add_cog(Statistics(bot))
    logging.info("Statistics cog has been added.")
