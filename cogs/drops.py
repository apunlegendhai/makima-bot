import os
import sqlite3
import json
import discord
import logging
import asyncio
import aiosqlite
import threading
from typing import Optional, List, Tuple

from discord.ext import commands
from discord import app_commands, ui
from datetime import datetime, timezone, timedelta
from collections import deque, Counter
import pytz

# ─── UTILITY FUNCTIONS ─────────────────────────────────────────────────────────
def get_ist_time(dt: datetime = None) -> datetime:
    """Convert UTC datetime to Indian Standard Time"""
    if dt is None:
        dt = discord.utils.utcnow()
    ist = pytz.timezone('Asia/Kolkata')
    return dt.replace(tzinfo=pytz.UTC).astimezone(ist)

def format_time_ago(dt: datetime) -> str:
    """Format datetime as user-friendly relative time in IST"""
    ist_dt = get_ist_time(dt)
    now_ist = get_ist_time()
    diff = now_ist - ist_dt
    
    if diff.days > 0:
        if diff.days == 1:
            return f"ended yesterday at {ist_dt.strftime('%I:%M %p')}"
        elif diff.days <= 7:
            return f"ended {diff.days} days ago at {ist_dt.strftime('%I:%M %p')}"
        else:
            return f"ended on {ist_dt.strftime('%B %d at %I:%M %p')}"
    
    hours = diff.seconds // 3600
    minutes = (diff.seconds % 3600) // 60
    
    if hours > 0:
        return f"ended {hours} hour{'s' if hours != 1 else ''} ago at {ist_dt.strftime('%I:%M %p')}"
    elif minutes > 0:
        return f"ended {minutes} minute{'s' if minutes != 1 else ''} ago at {ist_dt.strftime('%I:%M %p')}"
    else:
        return f"ended at {ist_dt.strftime('%I:%M %p')}"

def format_timestamp(dt: datetime = None) -> str:
    """Format datetime as user-friendly timestamp in IST"""
    ist_dt = get_ist_time(dt)
    now_ist = get_ist_time()
    
    if ist_dt.date() == now_ist.date():
        return f"today at {ist_dt.strftime('%I:%M %p')}"
    elif ist_dt.date() == (now_ist - timedelta(days=1)).date():
        return f"yesterday at {ist_dt.strftime('%I:%M %p')}"
    elif (now_ist - ist_dt).days <= 7:
        return f"{ist_dt.strftime('%A at %I:%M %p')}"
    else:
        return f"{ist_dt.strftime('%B %d at %I:%M %p')}"

# ─── SETUP FILES & DB ───────────────────────────────────────────────────────────
DB_FOLDER = 'database'
DB_PATH   = os.path.join(DB_FOLDER, 'drops.db')
LOG_FOLDER = 'logs'
LOG_PATH   = os.path.join(LOG_FOLDER, 'drop_errors.log')

os.makedirs(DB_FOLDER, exist_ok=True)
os.makedirs(LOG_FOLDER, exist_ok=True)

# Initialize database synchronously
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS drops (
          id TEXT PRIMARY KEY,
          guild_id INTEGER,
          channel_id INTEGER,
          message_id INTEGER,
          host_id INTEGER,
          prize_name TEXT,
          winner_count INTEGER,
          winners TEXT,
          emoji TEXT,
          footer TEXT,
          completed INTEGER,
          created_at TEXT
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS cooldowns (
          user_id INTEGER PRIMARY KEY,
          last_win_at TEXT
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS claim_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          drop_id TEXT,
          user_id INTEGER,
          claimed_at TEXT,
          FOREIGN KEY(drop_id) REFERENCES drops(id)
        )
        """)
        conn.commit()

init_db()

# ─── LOGGER ────────────────────────────────────────────────────────────────────
logger = logging.getLogger('drop_system')
logger.setLevel(logging.ERROR)
if not logger.handlers:
    fh = logging.FileHandler(LOG_PATH)
    fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
    logger.addHandler(fh)

# ─── DATABASE HELPER ───────────────────────────────────────────────────────────
class DatabaseManager:
    def __init__(self):
        self._connection = None
        self._lock = asyncio.Lock()

    async def get_connection(self):
        """Get persistent database connection"""
        async with self._lock:
            if self._connection is None:
                self._connection = await aiosqlite.connect(DB_PATH)
            return self._connection

    async def close(self):
        """Close the database connection"""
        async with self._lock:
            if self._connection:
                await self._connection.close()
                self._connection = None

# Global database manager
db_manager = DatabaseManager()

# ─── UI COMPONENTS ─────────────────────────────────────────────────────────────
class DropModal(ui.Modal, title='Create Drop'):
    prize_name   = ui.TextInput(label='Prize Name', placeholder='Prize name...', required=True, max_length=100)
    winner_count = ui.TextInput(label='Winner Count', placeholder='1-10, default 1', required=False, max_length=2)
    custom_emoji = ui.TextInput(label='Custom Emoji', required=False, max_length=50)
    footer_text  = ui.TextInput(label='Footer Text', required=False, max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        # Verify admin permissions again
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Admin only.", ephemeral=True)

        await interaction.response.defer()
        try:
            # Validate and sanitize inputs
            prize_name = self.prize_name.value.strip()
            if not prize_name or len(prize_name) > 100:
                return await interaction.followup.send("Invalid prize name.", ephemeral=True)

            try:
                wc = int(self.winner_count.value) if self.winner_count.value else 1
                wc = max(1, min(10, wc))  # Clamp between 1-10
            except (ValueError, TypeError):
                wc = 1

            custom_emoji = self.custom_emoji.value.strip() if self.custom_emoji.value else None
            footer_text = self.footer_text.value.strip() if self.footer_text.value else None

            cog = interaction.client.get_cog('DropSystem')
            if cog:
                await cog.create_drop(
                    interaction=interaction,
                    prize_name=prize_name,
                    winner_count=wc,
                    custom_emoji=custom_emoji,
                    footer_text=footer_text
                )
            else:
                await interaction.followup.send("Drop system not available.", ephemeral=True)
        except Exception as e:
            logger.exception(f"Error in modal submission: {e}")
            await interaction.followup.send("Error creating drop.", ephemeral=True)

class DropButton(ui.View):
    def __init__(self, drop_id: str, emoji: str = None):
        super().__init__(timeout=None)
        self.drop_id = drop_id
        btn = ui.Button(
            label='Claim',
            style=discord.ButtonStyle.grey,
            emoji=emoji,
            custom_id=f'drop_claim_{drop_id}'
        )
        btn.callback = self.claim_callback
        self.add_item(btn)

    async def claim_callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog('DropSystem')
        if cog:
            await cog.handle_claim(interaction, self.drop_id)
        else:
            await interaction.response.send_message("Drop system not available.", ephemeral=True)

# ─── COG ───────────────────────────────────────────────────────────────────────
class DropSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.recent_claims = deque(maxlen=500)
        self.claim_locks = {}  # Per-drop claim locks
        self.lock = threading.Lock()  # For managing claim_locks dict
        self.bot.loop.create_task(self._restore_views())

    async def _restore_views(self):
        """Restore persistent views after bot restart"""
        await self.bot.wait_until_ready()

        try:
            db = await db_manager.get_connection()
            async with db.execute("SELECT id, emoji FROM drops WHERE completed=0") as cursor:
                async for row in cursor:
                    # Add persistent view
                    view = DropButton(drop_id=row[0], emoji=row[1])  # id, emoji
                    self.bot.add_view(view)
        except Exception as e:
            logger.exception(f"Error restoring views: {e}")

    @app_commands.command(name='drop', description='Create a new drop (Admin only)')
    async def drop(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Admin only.", ephemeral=True)
        await interaction.response.send_modal(DropModal())

    @app_commands.command(name='reset_cooldown', description='Reset cooldown for a user or entire server (Admin only)')
    @app_commands.describe(
        user='User to reset cooldown for (leave empty to reset entire server)',
        reset_server='Reset entire server cooldown'
    )
    async def reset_cooldown(self, interaction: discord.Interaction, user: discord.Member = None, reset_server: bool = False):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Admin only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        try:
            db = await db_manager.get_connection()

            if user:
                # Reset specific user
                await db.execute("DELETE FROM cooldowns WHERE user_id=?", (user.id,))
                await db.commit()
                await interaction.followup.send(f"✅ Cooldown reset for {user.mention}", ephemeral=True)
            elif reset_server:
                # Reset entire server
                # Get all users in this guild from cooldowns
                async with db.execute("""
                    SELECT DISTINCT user_id FROM cooldowns 
                    WHERE user_id IN (
                        SELECT DISTINCT user_id FROM claim_logs cl
                        JOIN drops d ON cl.drop_id = d.id
                        WHERE d.guild_id = ?
                    )
                """, (interaction.guild.id,)) as cursor:
                    guild_users = await cursor.fetchall()

                if guild_users:
                    user_ids = [row[0] for row in guild_users]
                    placeholders = ','.join('?' * len(user_ids))
                    await db.execute(f"DELETE FROM cooldowns WHERE user_id IN ({placeholders})", user_ids)
                    await db.commit()
                    await interaction.followup.send(f"✅ Reset cooldowns for {len(user_ids)} users in this server", ephemeral=True)
                else:
                    await interaction.followup.send("No cooldowns found for this server", ephemeral=True)
            else:
                await interaction.followup.send("Please specify a user or enable 'reset_server' to reset all cooldowns.", ephemeral=True)

        except Exception as e:
            logger.exception(f"Error resetting cooldown: {e}")
            await interaction.followup.send("Error resetting cooldown.", ephemeral=True)

    async def create_drop(self, interaction: discord.Interaction, prize_name: str, 
                         winner_count: int, custom_emoji: Optional[str], footer_text: Optional[str]):
        """Create a new drop"""
        drop_id = f"{interaction.guild.id}_{interaction.channel.id}_{int(discord.utils.utcnow().timestamp())}"
        now = discord.utils.utcnow()

        try:
            embed = discord.Embed(
                color=0x2f3136,
                description=f"<:sukoon_blackdot:1322894649488314378> Hosted by: {interaction.user.mention}\n<:sukoon_blackdot:1322894649488314378> winners: {winner_count}\n<:sukoon_blackdot:1322894649488314378> First Come First Serve! ⚡"
            )

            embed.set_author(
                name=prize_name, 
                icon_url=interaction.guild.icon.url if interaction.guild.icon else None
            )

            embed.set_footer(text=footer_text or f"Powered by {self.bot.user.name} • {format_timestamp(now)}")

            view = DropButton(drop_id=drop_id, emoji=custom_emoji)
            msg = await interaction.followup.send(
                content="<:sukoon_taaada:1324071825910792223> **DROPS** <:sukoon_taaada:1324071825910792223>",
                embed=embed, 
                view=view
            )

            # Add persistent view
            self.bot.add_view(view)

            # Store in database (removed expires_at column)
            db = await db_manager.get_connection()
            await db.execute("""
                INSERT INTO drops (
                  id, guild_id, channel_id, message_id, host_id,
                  prize_name, winner_count, winners, emoji, footer,
                  completed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """, (
                drop_id, interaction.guild.id, interaction.channel.id, msg.id,
                interaction.user.id, prize_name, winner_count, json.dumps([]),
                custom_emoji, footer_text, now.isoformat()
            ))
            await db.commit()

        except Exception as e:
            logger.exception(f"Error creating drop {drop_id}: {e}")
            try:
                await interaction.followup.send("Failed to create drop.", ephemeral=True)
            except:
                pass

    async def is_on_cooldown(self, user_id: int) -> Tuple[bool, Optional[timedelta]]:
        """Check if user is on cooldown"""
        try:
            db = await db_manager.get_connection()
            async with db.execute("SELECT last_win_at FROM cooldowns WHERE user_id=?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return False, None

                last = datetime.fromisoformat(row[0])
                end = last + timedelta(hours=24)
                now = discord.utils.utcnow()

                if now < end:
                    return True, end - now

                # Cooldown expired, remove it
                await db.execute("DELETE FROM cooldowns WHERE user_id=?", (user_id,))
                await db.commit()
                return False, None
        except Exception as e:
            logger.exception(f"Error checking cooldown for user {user_id}: {e}")
            return False, None

    def is_spam(self, user_id: int) -> bool:
        """Check if user is spamming claims"""
        now = discord.utils.utcnow()
        window = now - timedelta(minutes=1)

        # Clean old entries
        while self.recent_claims and self.recent_claims[0][0] < window:
            self.recent_claims.popleft()

        # Count recent claims from this user
        user_claims = sum(1 for _, uid in self.recent_claims if uid == user_id)
        return user_claims >= 5

    async def handle_claim(self, interaction: discord.Interaction, drop_id: str):
        """Handle a claim attempt"""
        await interaction.response.defer(ephemeral=True)

        # Get or create lock for this drop
        with self.lock:
            if drop_id not in self.claim_locks:
                self.claim_locks[drop_id] = asyncio.Lock()
            claim_lock = self.claim_locks[drop_id]

        async with claim_lock:
            try:
                # Check spam first (before DB operations)
                if self.is_spam(interaction.user.id):
                    return await interaction.followup.send("Too many claims; slow down!", ephemeral=True)

                db = await db_manager.get_connection()
                # Get drop info
                async with db.execute("SELECT * FROM drops WHERE id=?", (drop_id,)) as cursor:
                    drop = await cursor.fetchone()
                    if not drop or drop[10]:  # completed
                        return await interaction.followup.send("This drop is no longer active.", ephemeral=True)

                winners = json.loads(drop[7])  # winners
                if interaction.user.id in winners:
                    return await interaction.followup.send("You already won this drop.", ephemeral=True)

                # Check cooldown
                on_cd, left = await self.is_on_cooldown(interaction.user.id)
                if on_cd and left:
                    hrs, rem = divmod(int(left.total_seconds()), 3600)
                    mins, secs = divmod(rem, 60)
                    timestr = f"{hrs:02d}:{mins:02d}:{secs:02d}"
                    return await interaction.followup.send(f"Cooldown: {timestr}", ephemeral=True)

                # Process win - atomic transaction
                winners.append(interaction.user.id)
                completed = 1 if len(winners) >= drop[6] else 0  # winner_count
                now_iso = discord.utils.utcnow().isoformat()

                await db.execute("UPDATE drops SET winners=?, completed=? WHERE id=?",
                               (json.dumps(winners), completed, drop_id))
                await db.execute("REPLACE INTO cooldowns (user_id, last_win_at) VALUES (?, ?)",
                               (interaction.user.id, now_iso))
                await db.execute("INSERT INTO claim_logs (drop_id, user_id, claimed_at) VALUES (?, ?, ?)",
                               (drop_id, interaction.user.id, now_iso))
                await db.commit()

                # Send success message to user (removed the success message)
                # await interaction.followup.send("🎉 You successfully claimed the drop!", ephemeral=True)

                # Update embed and disable button if completed
                await self._update_drop_embed(drop, winners, completed)

                # Record claim for spam detection
                self.recent_claims.append((discord.utils.utcnow(), interaction.user.id))

                # Clean up lock if drop completed
                if completed:
                    with self.lock:
                        self.claim_locks.pop(drop_id, None)

            except Exception as e:
                logger.exception(f"Error in handle_claim for {drop_id}: {e}")
                await interaction.followup.send("Error processing claim.", ephemeral=True)

    async def _update_drop_embed(self, drop, winners: List[int], completed: bool = False):
        """Update the drop embed with current winners and disable button if completed"""
        try:
            chan = self.bot.get_channel(drop[2])  # channel_id
            if not chan:
                return

            msg = await chan.fetch_message(drop[3])  # message_id

            # Update embed with winner information
            if completed:
                winner_mentions = ' '.join(f'<@{uid}>' for uid in winners)
                embed = discord.Embed(
                    color=0x00ff00,  # Green for completed
                    description=f"* Claimed by: {winner_mentions}\n* Hosted by: <@{drop[4]}>\n* Lightning fast reflexes! ⚡"  # host_id
                )
            else:
                remaining_winners = drop[6] - len(winners)  # winner_count - current winners
                embed = discord.Embed(
                    color=0x2f3136,
                    description=f"<:sukoon_blackdot:1322894649488314378> Hosted by: <@{drop[4]}>\n<:sukoon_blackdot:1322894649488314378> lucky winners needed!: {remaining_winners}\n<:sukoon_blackdot:1322894649488314378> First Come First Serve! ⚡"  # host_id
                )

            embed.set_author(
                name=drop[5],  # prize_name
                icon_url=chan.guild.icon.url if chan.guild.icon else None
            )
            embed.set_footer(text=drop[9] or f"Powered by {chan.guild.me.display_name} • {format_timestamp(discord.utils.utcnow())}")
            # Add winners field with mentions
            if winners:
                mentions = " ".join(f"<@{uid}>" for uid in winners)
                winner_text = "🏆 Winner" if len(winners) == 1 else "🏆 Winners"
                embed.add_field(
                    name=f"{winner_text} ({len(winners)}/{drop[6]})",  # winner_count
                    value=mentions,
                    inline=False
                )

            # Create view - disable button if completed
            if completed:
                view = ui.View()
                view.add_item(ui.Button(
                    label='Claimed',
                    style=discord.ButtonStyle.grey,
                    disabled=True
                ))
                # Send completion message as reply to drop
                mentions = " ".join(f"<@{uid}>" for uid in winners)
                await msg.reply(f"<:sukoon_taaada:1324071825910792223> **{drop[5]}** has been claimed by {mentions}!")

                # Update embed to show "Ended" status
                now = discord.utils.utcnow()
                completed_time = discord.utils.utcnow()

                embed.color = 0x36393f  # Darker gray for ended drops
                embed.description = f"<:sukoon_blackdot:1322894649488314378> {format_time_ago(completed_time)}\n<:sukoon_redpoint:1322894737736339459> Winners: {mentions}\n<:sukoon_blackdot:1322894649488314378> Hosted by: <@{drop[4]}>"

                # Clear fields and set author to prize name with server icon
                embed.clear_fields()
                embed.set_author(
                    name=drop[5],  # prize_name
                    icon_url=chan.guild.icon.url if chan.guild.icon else None
                )
                embed.set_footer(text=drop[9] or f"Powered by {chan.guild.me.display_name} • {format_timestamp(completed_time)}")
            else:
                # Keep original view active
                view = DropButton(drop_id=drop[0], emoji=drop[8])  # id, emoji

            await msg.edit(embed=embed, view=view)

        except discord.NotFound:
            pass  # Message was deleted
        except Exception as e:
            logger.exception(f"Failed to update embed for drop {drop[0]}: {e}")

    @commands.hybrid_command(name='drop_stats', with_app_command=True,
                             description='Show stats for a specific drop')
    @app_commands.describe(drop_id='The ID of the drop to inspect')
    @commands.has_permissions(administrator=True)
    async def drop_stats(self, ctx: commands.Context, drop_id: str):
        try:
            db = await db_manager.get_connection()
            async with db.execute("SELECT * FROM drops WHERE id=?", (drop_id,)) as cursor:
                drop = await cursor.fetchone()
                if not drop:
                    return await ctx.reply(f"❌ Drop `{drop_id}` not found.", ephemeral=True)

            async with db.execute("SELECT * FROM claim_logs WHERE drop_id=?", (drop_id,)) as cursor:
                logs = await cursor.fetchall()
                total = len(logs)

            if total:
                created = datetime.fromisoformat(drop[11])  # created_at
                diffs = []
                for log in logs:
                    claimed = datetime.fromisoformat(log[3])  # claimed_at
                    diff = (claimed - created).total_seconds()
                    diffs.append(diff)

                avg = sum(diffs) / total
                avg_td = timedelta(seconds=avg)
                avg_str = f"{int(avg_td.total_seconds()//60)}m {int(avg_td.total_seconds()%60)}s"
            else:
                avg_str = "N/A"

            top5 = Counter(log[2] for log in logs).most_common(5)  # user_id
            top_str = "\n".join(f"<@{uid}> — {cnt}" for uid, cnt in top5) if top5 else "No winners yet."

            embed = discord.Embed(
                title=f"Stats for Drop `{drop_id}`",
                color=0x2f3136
            )
            embed.set_author(
                name="Drop Statistics",
                icon_url=ctx.guild.icon.url if ctx.guild.icon else None
            )
            embed.add_field(name="Prize", value=drop[5], inline=False)  # prize_name
            embed.add_field(name="Winners", value=f"{total}/{drop[6]}")  # winner_count
            embed.add_field(name="Avg. Time-to-Claim", value=avg_str)
            embed.add_field(name="Top Claimers", value=top_str, inline=False)

            await ctx.reply(embed=embed, ephemeral=True)

        except Exception as e:
            logger.exception(f"Error getting drop stats for {drop_id}: {e}")
            await ctx.reply("Error retrieving drop stats.", ephemeral=True)

    @commands.hybrid_command(name='drop_leaderboard', with_app_command=True,
                             description='Show the all-time top claimers across drops')
    @app_commands.describe(top_n='How many top users to show')
    @commands.has_permissions(administrator=True)
    async def drop_leaderboard(self, ctx: commands.Context, top_n: int = 10):
        try:
            top_n = max(1, min(50, top_n))  # Clamp between 1-50

            db = await db_manager.get_connection()
            async with db.execute("""
                SELECT user_id, COUNT(*) AS wins
                  FROM claim_logs
                 GROUP BY user_id
                 ORDER BY wins DESC
                 LIMIT ?
            """, (top_n,)) as cursor:
                results = await cursor.fetchall()

            if not results:
                return await ctx.reply("No wins logged yet.", ephemeral=True)

            lines = [f"<@{row[0]}> — {row[1]}" for row in results]
            embed = discord.Embed(
                title="🏆 Drop Leaderboard",
                description="\n".join(lines),
                color=0x2f3136
            )
            embed.set_author(
                name="Global Leaderboard",
                icon_url=ctx.guild.icon.url if ctx.guild.icon else None
            )
            await ctx.reply(embed=embed, ephemeral=True)

        except Exception as e:
            logger.exception(f"Error getting leaderboard: {e}")
            await ctx.reply("Error retrieving leaderboard.", ephemeral=True)

    def cog_unload(self):
        """Clean up when cog is unloaded"""
        # Clean up locks
        with self.lock:
            self.claim_locks.clear()
        # Close database connection
        asyncio.create_task(db_manager.close())

# ─── SETUP ─────────────────────────────────────────────────────────────────────
async def setup(bot):
    await bot.add_cog(DropSystem(bot))
