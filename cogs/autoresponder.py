import discord
from discord.ext import commands
import json
import aiosqlite
from datetime import datetime
import random
import re
import os
import asyncio
from typing import Dict, Optional, List, Any
from discord import app_commands

class AutoResponderCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Ensure the database folder exists.
        self.db_folder = "database"
        os.makedirs(self.db_folder, exist_ok=True)
        self.db_path = os.path.join(self.db_folder, "autoresponder.db")

        # Caches:
        # Cache of all triggers for a guild (including global ones)
        self.triggers_cache: Dict[int, List[Dict]] = {}
        # Cooldown cache keyed by (trigger_id, channel_id) -> datetime of last activation
        self.cooldown_cache: Dict[tuple, datetime] = {}

        # Database connection (persistent)
        self.db: Optional[aiosqlite.Connection] = None

    async def setup_database(self):
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.executescript('''
            CREATE TABLE IF NOT EXISTS triggers (
                id INTEGER PRIMARY KEY,
                pattern TEXT NOT NULL,
                match_type TEXT NOT NULL,
                case_sensitive BOOLEAN DEFAULT 1,
                responses TEXT NOT NULL,
                cooldown INTEGER DEFAULT 0,
                channels TEXT,
                roles TEXT,
                blacklist_users TEXT,
                whitelist_users TEXT,
                creator_id INTEGER,
                created_at TIMESTAMP,
                guild_id INTEGER,
                is_global BOOLEAN DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY,
                trigger_id INTEGER,
                user_id INTEGER,
                channel_id INTEGER,
                timestamp TIMESTAMP,
                FOREIGN KEY (trigger_id) REFERENCES triggers (id)
            );
        ''')
        await self.db.commit()

    async def load_triggers_for_guild(self, guild_id: int) -> List[Dict[str, Any]]:
        if guild_id in self.triggers_cache:
            return self.triggers_cache[guild_id]

        triggers: List[Dict[str, Any]] = []
        try:
            async with self.db.execute(
                'SELECT * FROM triggers WHERE is_global = 1 OR guild_id = ?',
                (guild_id,)
            ) as cursor:
                async for row in cursor:
                    try:
                        trigger = {
                            'id': row[0],
                            'pattern': row[1],
                            'match_type': row[2],
                            'case_sensitive': row[3],
                            'responses': json.loads(row[4]),
                            'cooldown': row[5],
                            'channels': json.loads(row[6] or '[]'),
                            'roles': json.loads(row[7] or '[]'),
                            'blacklist_users': json.loads(row[8] or '[]'),
                            'whitelist_users': json.loads(row[9] or '[]'),
                            'creator_id': row[10],
                            'created_at': datetime.fromisoformat(row[11]),
                            'guild_id': row[12],
                            'is_global': bool(row[13])
                        }
                        triggers.append(trigger)
                    except Exception as e:
                        print(f"Error processing trigger row: {e}")
        except Exception as e:
            print(f"Error loading triggers for guild {guild_id}: {e}")

        self.triggers_cache[guild_id] = triggers
        return triggers

    def invalidate_triggers_cache(self, guild_id: int):
        if guild_id in self.triggers_cache:
            del self.triggers_cache[guild_id]

    async def get_trigger(self, pattern: str, guild_id: Optional[int] = None) -> Optional[Dict]:
        try:
            async with self.db.execute(
                'SELECT * FROM triggers WHERE pattern = ? AND (is_global = 1 OR guild_id = ?) LIMIT 1',
                (pattern, guild_id)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    trigger = {
                        'id': row[0],
                        'pattern': row[1],
                        'match_type': row[2],
                        'case_sensitive': row[3],
                        'responses': json.loads(row[4]),
                        'cooldown': row[5],
                        'channels': json.loads(row[6] or '[]'),
                        'roles': json.loads(row[7] or '[]'),
                        'blacklist_users': json.loads(row[8] or '[]'),
                        'whitelist_users': json.loads(row[9] or '[]'),
                        'creator_id': row[10],
                        'created_at': datetime.fromisoformat(row[11]),
                        'guild_id': row[12],
                        'is_global': bool(row[13])
                    }
                    return trigger
        except Exception as e:
            print(f"Error in get_trigger: {e}")
        return None

    @app_commands.command(name="add_trigger", description="Add a new trigger")
    @app_commands.describe(
        pattern="The pattern to match",
        response="The response to send",
        is_global="Whether the trigger should work across all servers"
    )
    @app_commands.choices(match_type=[
        app_commands.Choice(name=t, value=t) for t in ["exact", "partial", "regex"]
    ])
    async def add_trigger(self, interaction: discord.Interaction, pattern: str,
                          response: str, match_type: str = "exact", is_global: bool = False):
        if is_global and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Administrator permissions required for global triggers!", ephemeral=True)
            return

        try:
            query = 'SELECT id FROM triggers WHERE pattern = ? AND '
            query += 'is_global = 1' if is_global else 'guild_id = ?'
            params = (pattern,) if is_global else (pattern, interaction.guild.id)
            async with self.db.execute(query, params) as cursor:
                if await cursor.fetchone():
                    await interaction.response.send_message(
                        f"A trigger with pattern '{pattern}' already exists!", ephemeral=True)
                    return
        except Exception as e:
            print(f"Error checking duplicate triggers: {e}")

        try:
            await self.db.execute('''
                INSERT INTO triggers (
                    pattern, match_type, responses, creator_id, created_at, 
                    guild_id, is_global, channels, roles, blacklist_users, whitelist_users
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                pattern, match_type, 
                json.dumps([{'type': 'text', 'content': response}]),
                interaction.user.id, datetime.utcnow().isoformat(),
                None if is_global else interaction.guild.id, is_global,
                '[]', '[]', '[]', '[]'
            ))
            await self.db.commit()
            if is_global:
                self.triggers_cache = {}
            else:
                self.invalidate_triggers_cache(interaction.guild.id)
            await interaction.response.send_message(
                f"Trigger '{pattern}' added {'globally' if is_global else f'for server: {interaction.guild.name}'}!")
        except Exception as e:
            print(f"Error adding trigger: {e}")
            await interaction.response.send_message(
                "An error occurred while adding the trigger.", ephemeral=True)

    @app_commands.command(name="delete_trigger", description="Delete a trigger")
    async def delete_trigger(self, interaction: discord.Interaction, pattern: str):
        try:
            async with self.db.execute(
                'SELECT guild_id, is_global FROM triggers WHERE pattern = ?', (pattern,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    await interaction.response.send_message(
                        f"Trigger '{pattern}' not found!", ephemeral=True)
                    return

                guild_id, is_global = row
                if (is_global and not interaction.user.guild_permissions.administrator) or \
                   (not is_global and guild_id != interaction.guild.id):
                    await interaction.response.send_message(
                        "You don't have permission to delete this trigger!", ephemeral=True)
                    return

            await self.db.execute('DELETE FROM triggers WHERE pattern = ?', (pattern,))
            await self.db.commit()
            if is_global:
                self.triggers_cache = {}
            elif guild_id:
                self.invalidate_triggers_cache(guild_id)
            await interaction.response.send_message(f"Trigger '{pattern}' deleted!")
        except Exception as e:
            print(f"Error deleting trigger: {e}")
            await interaction.response.send_message("An error occurred while deleting the trigger.", ephemeral=True)

    @app_commands.command(name="list_triggers", description="List all triggers")
    async def list_triggers(self, interaction: discord.Interaction):
        try:
            triggers = await self.load_triggers_for_guild(interaction.guild.id)
            if not triggers:
                await interaction.response.send_message("No triggers found.")
                return

            embed = discord.Embed(
                title="Trigger List",
                color=discord.Color.blue(),
                description="📍 = Server-specific | 🌐 = Global"
            )
            for trig in triggers:
                scope_icon = '🌐' if trig['is_global'] else '📍'
                embed.add_field(
                    name=f"{scope_icon} {trig['pattern']}",
                    value=f"Match type: {trig['match_type']}",
                    inline=False
                )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            print(f"Error listing triggers: {e}")
            await interaction.response.send_message("An error occurred while listing triggers.", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        try:
            triggers = await self.load_triggers_for_guild(message.guild.id)
        except Exception as e:
            print(f"Error loading triggers in on_message: {e}")
            return

        for trigger in triggers:
            if not self._check_trigger(trigger, message):
                continue

            cooldown_seconds = trigger.get('cooldown', 0)
            key = (trigger['id'], message.channel.id)
            now = datetime.utcnow()
            if cooldown_seconds:
                last_trigger = self.cooldown_cache.get(key)
                if last_trigger and (now - last_trigger).total_seconds() < cooldown_seconds:
                    continue

            self.cooldown_cache[key] = now

            try:
                trigger_data = random.choice(trigger['responses'])
            except Exception as e:
                print(f"Error choosing a response: {e}")
                continue

            if trigger_data.get('type') == 'embed':
                response = discord.Embed(description=trigger_data.get('content', ''))
            else:
                response = trigger_data.get('content', '')

            try:
                await self.db.execute('''
                    INSERT INTO logs (trigger_id, user_id, channel_id, timestamp)
                    VALUES (?, ?, ?, ?)
                ''', (trigger['id'], message.author.id, message.channel.id,
                      datetime.utcnow().isoformat()))
                await self.db.commit()
            except Exception as e:
                print(f"Error logging trigger activation: {e}")

            try:
                if isinstance(response, discord.Embed):
                    await message.channel.send(embed=response)
                else:
                    await message.channel.send(response)
            except Exception as e:
                print(f"Error sending response: {e}")

        await self.bot.process_commands(message)

    def _check_trigger(self, trigger: Dict, message: discord.Message) -> bool:
        if trigger['channels'] and message.channel.id not in trigger['channels']:
            return False

        if trigger['roles'] and not any(role.id in trigger['roles'] for role in message.author.roles):
            return False

        if message.author.id in trigger['blacklist_users']:
            return False

        if trigger['whitelist_users'] and message.author.id not in trigger['whitelist_users']:
            return False

        return self.match_message(trigger, message.content)

    def match_message(self, trigger: Dict, message_content: str) -> bool:
        match_type = trigger['match_type']
        pattern = trigger['pattern']
        case_sensitive = trigger['case_sensitive']

        if not case_sensitive:
            pattern = pattern.lower()
            message_content = message_content.lower()

        if match_type == 'exact':
            return pattern == message_content
        elif match_type == 'partial':
            return pattern in message_content
        elif match_type == 'regex':
            try:
                return bool(re.search(pattern, message_content))
            except re.error:
                return False
        return False

    async def cog_unload(self):
        if self.db:
            try:
                await self.db.close()
            except RuntimeError as e:
                # This error can occur if the event loop is already closed.
                print(f"Error closing DB: {e}")

async def setup(bot: commands.Bot):
    cog = AutoResponderCog(bot)
    await cog.setup_database()
    await bot.add_cog(cog)
