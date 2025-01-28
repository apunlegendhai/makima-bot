import asyncio
import discord
from discord.ext import commands
import json
import aiosqlite
from datetime import datetime
import random
import re
from typing import List, Dict, Optional
from discord import app_commands


class AutoResponderCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = 'autoresponder.db'
        self.cache = {}
        asyncio.create_task(self.init_db())

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS triggers (
                    id INTEGER PRIMARY KEY,
                    pattern TEXT NOT NULL,
                    match_type TEXT NOT NULL,
                    case_sensitive BOOLEAN,
                    responses TEXT NOT NULL,
                    cooldown INTEGER DEFAULT 0,
                    channels TEXT,
                    roles TEXT,
                    blacklist_users TEXT,
                    whitelist_users TEXT,
                    creator_id INTEGER,
                    created_at TIMESTAMP
                )
            ''')
            await db.execute('''
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY,
                    trigger_id INTEGER,
                    user_id INTEGER,
                    channel_id INTEGER,
                    timestamp TIMESTAMP,
                    FOREIGN KEY (trigger_id) REFERENCES triggers (id)
                )
            ''')
            await db.commit()

    async def get_trigger(self, pattern: str) -> Optional[Dict]:
        if pattern in self.cache:
            return self.cache[pattern]

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('SELECT * FROM triggers WHERE pattern = ?',
                                  (pattern, )) as cursor:
                row = await cursor.fetchone()
                if row:
                    trigger = {
                        'id': row[0],
                        'pattern': row[1],
                        'match_type': row[2],
                        'case_sensitive': row[3],
                        'responses': json.loads(row[4]),
                        'cooldown': row[5],
                        'channels': json.loads(row[6]) if row[6] else [],
                        'roles': json.loads(row[7]) if row[7] else [],
                        'blacklist_users':
                        json.loads(row[8]) if row[8] else [],
                        'whitelist_users':
                        json.loads(row[9]) if row[9] else [],
                        'creator_id': row[10],
                        'created_at': datetime.fromisoformat(row[11])
                    }
                    self.cache[pattern] = trigger
                    return trigger
        return None

    @app_commands.command(name="add_trigger", description="Add a new trigger")
    @app_commands.describe(pattern="The pattern to match.", response="The response to send.")
    @app_commands.choices(
        match_type=[
            app_commands.Choice(name="exact", value="exact"),
            app_commands.Choice(name="partial", value="partial"),
            app_commands.Choice(name="regex", value="regex"),
        ]
    )
    async def add_trigger(self, interaction: discord.Interaction, pattern: str, response: str, match_type: str = "exact"):
        """
        Add a new trigger with basic options.
        Match types:
        - 'exact': The trigger must match the entire message exactly (e.g., 'hello' == 'hello')
        - 'partial': The trigger will match if the pattern is contained within the message (e.g., 'hello' in 'say hello')
        - 'regex': The trigger will match if the pattern is a valid regular expression (e.g., 'h.*o' matches 'hello')

        Match type is optional and defaults to 'exact'.
        """
        trigger_data = {
            'pattern': pattern,
            'match_type': match_type,
            'case_sensitive': True,
            'responses': [{
                'type': 'text',
                'content': response
            }],
            'cooldown': 0
        }

        await self.add_trigger_to_db(trigger_data)
        await interaction.response.send_message(f"Trigger '{pattern}' added successfully!")


    async def add_trigger_to_db(self, trigger_data: dict):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                '''
                INSERT INTO triggers (
                    pattern, match_type, case_sensitive, responses, cooldown,
                    channels, roles, blacklist_users, whitelist_users,
                    creator_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
                (
                    trigger_data['pattern'],
                    trigger_data['match_type'],
                    trigger_data['case_sensitive'],
                    json.dumps(trigger_data['responses']),
                    trigger_data['cooldown'],
                    json.dumps(trigger_data.get('channels', [])),
                    json.dumps(trigger_data.get('roles', [])),
                    json.dumps(trigger_data.get('blacklist_users', [])),
                    json.dumps(trigger_data.get('whitelist_users', [])),
                    1,  # Example: Use creator_id as 1 for now
                    datetime.utcnow().isoformat()))
            await db.commit()

    @app_commands.command(name="delete_trigger",
                          description="Delete a trigger")
    async def delete_trigger(self, interaction: discord.Interaction,
                             pattern: str):
        """Delete a trigger"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('DELETE FROM triggers WHERE pattern = ?',
                             (pattern, ))
            await db.commit()

        if pattern in self.cache:
            del self.cache[pattern]

        await interaction.response.send_message(
            f"Trigger '{pattern}' deleted successfully!")

    @app_commands.command(name="list_triggers",
                          description="List all triggers")
    async def list_triggers(self, interaction: discord.Interaction):
        """List all triggers"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                    'SELECT pattern, match_type FROM triggers') as cursor:
                triggers = await cursor.fetchall()

        if not triggers:
            await interaction.response.send_message("No triggers found.")
            return

        embed = discord.Embed(title="Trigger List", color=discord.Color.blue())
        for pattern, match_type in triggers:
            embed.add_field(name=pattern,
                            value=f"Match type: {match_type}",
                            inline=False)

        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('SELECT * FROM triggers') as cursor:
                async for row in cursor:
                    trigger = await self.get_trigger(row[1])

                    if not self.match_trigger(trigger, message.content):
                        continue

                    # Check permissions and restrictions
                    if trigger[
                            'channels'] and message.channel.id not in trigger[
                                'channels']:
                        continue

                    if trigger['roles'] and not any(
                            role.id in trigger['roles']
                            for role in message.author.roles):
                        continue

                    if message.author.id in trigger['blacklist_users']:
                        continue

                    if trigger[
                            'whitelist_users'] and message.author.id not in trigger[
                                'whitelist_users']:
                        continue

                    # Select and create response
                    response_data = random.choice(trigger['responses'])
                    response = await self.create_response(
                        response_data, message)

                    # Log trigger usage
                    await db.execute(
                        '''
                        INSERT INTO logs (trigger_id, user_id, channel_id, timestamp)
                        VALUES (?, ?, ?, ?)
                    ''', (trigger['id'], message.author.id, message.channel.id,
                          datetime.utcnow().isoformat()))
                    await db.commit()

                    # Send response immediately (no waiting)
                    if isinstance(response, str):
                        await message.channel.send(response)
                    else:
                        await message.channel.send(embed=response)

    def match_trigger(self, trigger, message_content: str) -> bool:
        """Check if the message matches the trigger pattern."""
        pattern = trigger['pattern']
        match_type = trigger['match_type']
        case_sensitive = trigger['case_sensitive']

        if not case_sensitive:
            message_content = message_content.lower()
            pattern = pattern.lower()

        if match_type == 'exact':
            return message_content == pattern
        elif match_type == 'partial':
            return pattern in message_content
        elif match_type == 'regex':
            try:
                return bool(re.search(pattern, message_content))
            except re.error:
                return False
        return False

    async def create_response(self, response_data: dict,
                              message: discord.Message):
        if response_data['type'] == 'text':
            return response_data['content']

        elif response_data['type'] == 'embed':
            embed = discord.Embed(description=response_data['content'])
            return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoResponderCog(bot))
