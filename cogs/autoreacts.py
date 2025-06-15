import discord
from discord.ext import commands
import aiosqlite
import asyncio
import random
import time
import os
from typing import Optional
import logging

logger = logging.getLogger(__name__)

class AutoReactsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "database/autoreacts.db"
        self.custom_emojis = []
        self.default_emojis = ["😀", "😂", "😍", "🤔", "👍", "👎", "❤️", "🔥", "💯", "🎉",
                              "😎", "🤗", "😱", "🙄", "😴", "🤪", "😇", "🤯", "🥳", "😤",
                              "🤓", "😋", "😜", "🤐", "😏", "🥺", "🤤", "😪", "🤑", "🤠"]
        self.cooldowns = {}
        
        os.makedirs("database", exist_ok=True)
        self.load_emojis()
        asyncio.create_task(self.init_db())
    
    def is_valid_custom_emoji(self, emoji_str: str) -> bool:
        """Check if the emoji string is a valid custom emoji format."""
        # Check for both animated and static emoji formats
        # Animated: <a:name:id>
        # Static: <:name:id>
        return (emoji_str.startswith('<:') or emoji_str.startswith('<a:')) and emoji_str.endswith('>')
    
    def load_emojis(self):
        try:
            with open("emojis.txt", "r", encoding="utf-8") as f:
                # Filter and validate custom emojis
                self.custom_emojis = []
                for line in f:
                    emoji = line.strip()
                    if emoji:
                        if self.is_valid_custom_emoji(emoji):
                            self.custom_emojis.append(emoji)
                        elif len(emoji) == 1 or emoji in self.default_emojis:
                            # It's a unicode emoji
                            self.custom_emojis.append(emoji)
                        else:
                            logger.warning(f"Skipping invalid emoji format: {emoji}")
                            
            logger.info(f"Loaded {len(self.custom_emojis)} custom emojis from emojis.txt")
            animated_count = sum(1 for e in self.custom_emojis if e.startswith('<a:'))
            static_count = sum(1 for e in self.custom_emojis if e.startswith('<:'))
            unicode_count = len(self.custom_emojis) - animated_count - static_count
            logger.info(f"Emoji breakdown - Animated: {animated_count}, Static: {static_count}, Unicode: {unicode_count}")
        except FileNotFoundError:
            logger.info("No custom emojis file found")
            self.custom_emojis = []
        
        logger.info(f"Total default emojis available: {len(self.default_emojis)}")
    
    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    enabled BOOLEAN DEFAULT FALSE,
                    cooldown INTEGER DEFAULT 10,
                    reaction_count INTEGER DEFAULT 20
            )
            """)
            await db.commit()
    
    async def get_guild_settings(self, guild_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT enabled, cooldown, reaction_count FROM guild_settings WHERE guild_id = ?",
                (guild_id,)
            )
            result = await cursor.fetchone()
            
            if result:
                return {"enabled": bool(result[0]), "cooldown": result[1], "reaction_count": result[2]}
            else:
                await db.execute("""
                    INSERT INTO guild_settings (guild_id, enabled, cooldown, reaction_count)
                    VALUES (?, FALSE, 10, 20)
                """, (guild_id,))
                await db.commit()
                return {"enabled": False, "cooldown": 10, "reaction_count": 20}
    
    async def update_guild_setting(self, guild_id: int, **kwargs):
        async with aiosqlite.connect(self.db_path) as db:
            current = await self.get_guild_settings(guild_id)
            
            enabled = kwargs.get('enabled', current['enabled'])
            cooldown = kwargs.get('cooldown', current['cooldown'])
            reaction_count = kwargs.get('reaction_count', current['reaction_count'])
            
            await db.execute("""
                INSERT OR REPLACE INTO guild_settings (guild_id, enabled, cooldown, reaction_count)
                VALUES (?, ?, ?, ?)
            """, (guild_id, enabled, cooldown, reaction_count))
            await db.commit()
    
    def is_on_cooldown(self, guild_id: int, cooldown_time: int) -> bool:
        if guild_id not in self.cooldowns:
            return False
        return (time.time() - self.cooldowns[guild_id]) < cooldown_time
    
    def set_cooldown(self, guild_id: int):
        self.cooldowns[guild_id] = time.time()
    
    async def add_reaction_safely(self, message, emoji):
        """Safely add a reaction with proper error handling."""
        try:
            await message.add_reaction(emoji)
            logger.debug(f"Added reaction {emoji} to message {message.id}")
            return True
        except discord.Forbidden:
            logger.warning(f"Bot lacks permission to add reaction {emoji}")
            return False
        except discord.NotFound:
            logger.warning(f"Emoji {emoji} not found or message was deleted")
            return False
        except discord.HTTPException as e:
            if e.code == 10014:  # Unknown Emoji
                logger.warning(f"Unknown emoji {emoji} - bot might not have access to this custom emoji")
            else:
                logger.error(f"HTTP error adding reaction {emoji}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error adding reaction {emoji}: {e}")
            return False

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        
        if not (message.mention_everyone or message.content.count("@everyone") > 0 or message.content.count("@here") > 0):
            return
        
        try:
            settings = await self.get_guild_settings(message.guild.id)
            logger.info(f"Processing mention in guild {message.guild.id} (#{message.channel.name})")
            logger.info(f"Guild settings: {settings}")
            
            if not settings["enabled"]:
                logger.info(f"Auto-reactions disabled for guild {message.guild.id}")
                return
            
            if self.is_on_cooldown(message.guild.id, settings["cooldown"]):
                logger.info(f"Guild {message.guild.id} is on cooldown")
                return
            
            self.set_cooldown(message.guild.id)
            
            target_reactions = settings["reaction_count"]
            logger.info(f"Adding {target_reactions} reactions")
            
            # Try custom emojis first
            successful_reactions = 0
            if self.custom_emojis:
                # Separate animated and static emojis
                animated_emojis = [e for e in self.custom_emojis if e.startswith('<a:')]
                static_emojis = [e for e in self.custom_emojis if e.startswith('<:')]
                unicode_emojis = [e for e in self.custom_emojis if not e.startswith('<')]
                
                # Try to use a mix of animated and static emojis
                available_emojis = []
                if animated_emojis:
                    available_emojis.extend(random.sample(animated_emojis, min(target_reactions // 2, len(animated_emojis))))
                if static_emojis:
                    available_emojis.extend(random.sample(static_emojis, min(target_reactions // 2, len(static_emojis))))
                if unicode_emojis and len(available_emojis) < target_reactions:
                    available_emojis.extend(random.sample(unicode_emojis, min(target_reactions - len(available_emojis), len(unicode_emojis))))
                
                random.shuffle(available_emojis)
                for emoji in available_emojis[:target_reactions]:
                    if await self.add_reaction_safely(message, emoji):
                        successful_reactions += 1
                        await asyncio.sleep(0.2)
            
            # If we need more reactions or custom emojis failed, use default emojis
            if successful_reactions < target_reactions:
                remaining = target_reactions - successful_reactions
                default_emojis = random.sample(self.default_emojis, min(remaining, len(self.default_emojis)))
                for emoji in default_emojis:
                    if await self.add_reaction_safely(message, emoji):
                        await asyncio.sleep(0.2)
                        
        except Exception as e:
            logger.error(f"Error in auto-react listener: {e}", exc_info=True)
    
    @discord.app_commands.command(name="autoreacts", description="Configure automatic reactions")
    @discord.app_commands.describe(
        action="Action to perform",
        cooldown="Cooldown in seconds (5-300)",
        reactions="Number of reactions (1-20)"
    )
    @discord.app_commands.choices(action=[
        discord.app_commands.Choice(name="enable", value="enable"),
        discord.app_commands.Choice(name="disable", value="disable"),
        discord.app_commands.Choice(name="status", value="status"),
        discord.app_commands.Choice(name="config", value="config")
    ])
    async def autoreacts(self, interaction: discord.Interaction, action: str, 
                        cooldown: Optional[int] = None, reactions: Optional[int] = None):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "❌ You need `Manage Server` permission to use this command.", 
                ephemeral=True
            )
            return
        
        guild_id = interaction.guild.id
        
        if action == "enable":
            await self.update_guild_setting(guild_id, enabled=True)
            await interaction.response.send_message(
                "✅ Auto-reactions enabled for @everyone/@here pings!", 
                ephemeral=True
            )
        
        elif action == "disable":
            await self.update_guild_setting(guild_id, enabled=False)
            await interaction.response.send_message(
                "❌ Auto-reactions disabled.", 
                ephemeral=True
            )
        
        elif action == "config":
            updates = {}
            
            if cooldown is not None:
                if 5 <= cooldown <= 300:
                    updates['cooldown'] = cooldown
                else:
                    await interaction.response.send_message(
                        "❌ Cooldown must be between 5-300 seconds.", 
                        ephemeral=True
                    )
                    return
            
            if reactions is not None:
                if 1 <= reactions <= 20:
                    updates['reaction_count'] = reactions
                else:
                    await interaction.response.send_message(
                        "❌ Reaction count must be between 1-20.", 
                        ephemeral=True
                    )
                    return
            
            if updates:
                await self.update_guild_setting(guild_id, **updates)
                config_text = []
                if 'cooldown' in updates:
                    config_text.append(f"Cooldown: {updates['cooldown']}s")
                if 'reaction_count' in updates:
                    config_text.append(f"Reactions: {updates['reaction_count']}")
                
                await interaction.response.send_message(
                    f"✅ Updated: {', '.join(config_text)}", 
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ Please specify cooldown and/or reactions to update.", 
                    ephemeral=True
                )
        
        elif action == "status":
            settings = await self.get_guild_settings(guild_id)
            
            status = "✅ Enabled" if settings["enabled"] else "❌ Disabled"
            cooldown_val = settings["cooldown"]
            reaction_count = settings["reaction_count"]
            
            embed = discord.Embed(
                title="🤖 Auto-Reactions Configuration",
                color=discord.Color.green() if settings["enabled"] else discord.Color.red()
            )
            embed.add_field(name="Status", value=status, inline=False)
            embed.add_field(name="Cooldown", value=f"{cooldown_val} seconds", inline=True)
            embed.add_field(name="Reactions per message", value=str(reaction_count), inline=True)
            embed.add_field(name="Available Emojis", value=str(len(self.emojis)), inline=True)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(AutoReactsCog(bot))
