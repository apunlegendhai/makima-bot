import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import logging
import aiosqlite
from typing import Dict, Optional

# Default database and log directories
DB_DIR = os.path.join("database")
LOG_DIR = os.path.join("logs")
DB_PATH = os.path.join(DB_DIR, "vc_roles.db")
LOG_PATH = os.path.join(LOG_DIR, "vc_roles.log")

class VCRoles(commands.Cog):
    """
    A Cog for automatically assigning roles when users join voice channels.
    Uses a single slash command with optional parameters.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.vc_role_configs: Dict[int, int] = {}  # guild_id -> role_id cache
        self.db = None  # Database connection

        # Ensure directories exist
        os.makedirs(DB_DIR, exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)

        # Set up logging
        self.logger = logging.getLogger('VCRolesBot')
        if not any(isinstance(h, logging.FileHandler) and h.baseFilename.endswith('vc_roles.log')
                   for h in self.logger.handlers):
            handler = logging.FileHandler(LOG_PATH)
            handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
            self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

    async def cog_load(self) -> None:
        """Initialize database and load configurations when cog is loaded."""
        await self._setup_database()
        await self._load_configurations()
        if not self.check_role_validity.is_running():
            self.check_role_validity.start()

    async def _setup_database(self) -> None:
        """Initialize the SQLite database connection and tables."""
        try:
            # Create tables if they don't exist
            self.db = await aiosqlite.connect(DB_PATH)
            
            # Create tables
            await self.db.execute('''
                CREATE TABLE IF NOT EXISTS vc_roles (
                    guild_id INTEGER PRIMARY KEY,
                    role_id INTEGER NOT NULL
                )
            ''')
            await self.db.commit()
            
            self.logger.info("Database setup complete")
        except Exception as e:
            self.logger.error(f"Database setup error: {e}")
            
    async def _load_configurations(self) -> None:
        """Load all role configurations from the database."""
        try:
            # Load all configurations
            async with self.db.execute("SELECT guild_id, role_id FROM vc_roles") as cursor:
                async for row in cursor:
                    guild_id, role_id = row
                    self.vc_role_configs[guild_id] = role_id
                    
            self.logger.info(f"Loaded {len(self.vc_role_configs)} VC role configurations")
        except Exception as e:
            self.logger.error(f"Failed to load configurations: {e}")
            self.vc_role_configs = {}

    async def cog_unload(self) -> None:
        """Close database connection when unloading the cog."""
        if self.check_role_validity.is_running():
            self.check_role_validity.cancel()
        
        if self.db:
            await self.db.close()
            self.logger.info("Database connection closed")

    async def _save_config(self, guild_id: int, role_id: int) -> None:
        """Add or update a configuration in the database."""
        try:
            await self.db.execute(
                "INSERT OR REPLACE INTO vc_roles (guild_id, role_id) VALUES (?, ?)",
                (guild_id, role_id)
            )
            await self.db.commit()
        except Exception as e:
            self.logger.error(f"Failed to save configuration: {e}")
            
    async def _delete_config(self, guild_id: int) -> None:
        """Remove a configuration from the database."""
        try:
            await self.db.execute("DELETE FROM vc_roles WHERE guild_id = ?", (guild_id,))
            await self.db.commit()
        except Exception as e:
            self.logger.error(f"Failed to delete configuration: {e}")

    def _check_permissions(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False
        return interaction.user.guild_permissions.administrator

    async def _check_bot_permissions(self, guild: discord.Guild, role: discord.Role) -> bool:
        bot_member = guild.me
        if not bot_member or not bot_member.guild_permissions.manage_roles:
            return False
        return bot_member.top_role > role

    async def _apply_to_current_users(self, guild: discord.Guild, role: discord.Role) -> None:
        added = 0
        errors = 0
        for vc in guild.voice_channels:
            for member in vc.members:
                if not member.bot and role not in member.roles:
                    try:
                        await member.add_roles(role, reason="Initial VC assignment")
                        added += 1
                        await asyncio.sleep(0.1)  # Rate limit to avoid hitting Discord API limits
                    except Exception as e:
                        errors += 1
                        self.logger.error(f"Error assigning role to {member.display_name}: {e}")
        
        if added:
            self.logger.info(f"Assigned {role.name} to {added} existing users in {guild.name}")
        if errors:
            self.logger.warning(f"Failed to assign role to {errors} users in {guild.name}")

    @app_commands.command(
        name="vc-role",
        description="Configure a role to be assigned when users join voice channels"
    )
    @app_commands.describe(
        role="The role to assign (leave empty to view current setting or use with remove=True to remove setting)",
        remove="Set to True to remove the current voice channel role configuration"
    )
    async def vc_role(
        self,
        interaction: discord.Interaction,
        role: Optional[discord.Role] = None,
        remove: bool = False
    ) -> None:
        """Single command to configure, view, or remove VC role settings"""
        # Defer the response to have more time to process
        await interaction.response.defer(ephemeral=True)
        
        # Check permissions
        if not self._check_permissions(interaction):
            return await interaction.followup.send("❌ You need administrator permissions to use this command.", ephemeral=True)
        
        # Ensure guild context
        guild = interaction.guild
        if not guild:
            return await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
        
        guild_id = guild.id
        
        # REMOVE: Remove existing configuration
        if remove:
            if guild_id not in self.vc_role_configs:
                return await interaction.followup.send("ℹ️ No voice channel role is currently configured.", ephemeral=True)
            
            # Get role info for the response message
            role_id = self.vc_role_configs[guild_id]
            existing_role = guild.get_role(role_id)
            role_mention = existing_role.mention if existing_role else "Unknown Role"
            
            # Remove from config
            del self.vc_role_configs[guild_id]
            await self._delete_config(guild_id)
            
            return await interaction.followup.send(
                f"✅ Voice channel role configuration removed successfully. Users will no longer receive {role_mention} when joining voice channels.", 
                ephemeral=True
            )
        
        # VIEW: Show current configuration
        if role is None:
            role_id = self.vc_role_configs.get(guild_id)
            if not role_id:
                return await interaction.followup.send(
                    "No voice channel role is currently configured for this server.\n\n"
                    "To set up automatic role assignment, use `/vc-role role:@RoleName`", 
                    ephemeral=True
                )
                
            existing_role = guild.get_role(role_id)
            if existing_role:
                embed = discord.Embed(
                    title="Voice Channel Role Configuration",
                    description=f"Users who join voice channels will receive: {existing_role.mention}\n\n"
                              f"• To change this role: `/vc-role role:@NewRole`\n"
                              f"• To remove this setting: `/vc-role remove:True`",
                    color=discord.Color.blue()
                )
                return await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                return await interaction.followup.send(
                    "⚠️ The previously configured role no longer exists on this server.\n"
                    "Please set a new role with `/vc-role role:@RoleName`", 
                    ephemeral=True
                )
        
        # SET: Configure new role
        # Check bot permissions for the role
        if not await self._check_bot_permissions(guild, role):
            return await interaction.followup.send(
                "❌ I don't have permission to manage this role. Make sure:\n"
                "1. My role is higher than the target role in the server settings\n"
                "2. I have the 'Manage Roles' permission", 
                ephemeral=True
            )
        
        # Save the new configuration
        self.vc_role_configs[guild_id] = role.id
        await self._save_config(guild_id, role.id)
        
        # Apply to current voice users
        await self._apply_to_current_users(guild, role)
        
        return await interaction.followup.send(
            f"✅ Configuration saved!\n\n"
            f"Role: {role.mention}\n\n"
            f"Users will automatically receive this role when joining voice channels and lose it when leaving.",
            ephemeral=True
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ) -> None:
        """Event listener for voice state changes"""
        if member.bot:
            return
        
        guild_id = member.guild.id
        role_id = self.vc_role_configs.get(guild_id)
        
        if not role_id:
            return
            
        role = member.guild.get_role(role_id)
        if not role:
            # Role doesn't exist anymore - clean up the invalid configuration
            self.logger.warning(f"Configured role {role_id} no longer exists in guild {guild_id}, removing configuration")
            del self.vc_role_configs[guild_id]
            await self._delete_config(guild_id)
            return
        
        try:
            # Handle direct channel-to-channel moves
            if before.channel and after.channel:
                # User is still in voice, ensure they have the role
                if role not in member.roles:
                    await member.add_roles(role, reason="In voice channel")
                    self.logger.info(f"Added {role.name} to {member.display_name} during channel move in {member.guild.name}")
                return
                
            # User left all voice channels
            if before.channel and not after.channel and role in member.roles:
                await member.remove_roles(role, reason="Left all voice channels")
                self.logger.info(f"Removed {role.name} from {member.display_name} in {member.guild.name}")
            
            # User joined a voice channel from nothing
            elif not before.channel and after.channel and role not in member.roles:
                await member.add_roles(role, reason="Joined voice channel")
                self.logger.info(f"Added {role.name} to {member.display_name} in {member.guild.name}")
                
        except discord.Forbidden:
            self.logger.error(f"Missing permissions to manage roles for {member.display_name} in {member.guild.name}")
        except discord.HTTPException as e:
            self.logger.error(f"Discord API error when managing roles: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected error managing roles: {e}")

    @tasks.loop(hours=12)
    async def check_role_validity(self) -> None:
        """Periodic check that configured roles still exist and remove invalid configurations"""
        self.logger.info("Running role validity check")
        invalid_guilds = []
        
        for guild_id, role_id in list(self.vc_role_configs.items()):
            guild = self.bot.get_guild(guild_id)
            
            # Check if we're still in the guild
            if not guild:
                self.logger.warning(f"Bot is no longer in guild {guild_id}, marking for removal")
                invalid_guilds.append(guild_id)
                continue
                
            # Check if the role still exists
            if not guild.get_role(role_id):
                self.logger.warning(f"Configured role {role_id} no longer exists in guild {guild_id}, marking for removal")
                invalid_guilds.append(guild_id)
        
        # Clean up invalid configurations
        for guild_id in invalid_guilds:
            try:
                del self.vc_role_configs[guild_id]
                await self._delete_config(guild_id)
                self.logger.info(f"Removed invalid configuration for guild {guild_id}")
            except Exception as e:
                self.logger.error(f"Error removing invalid configuration for guild {guild_id}: {e}")

    @check_role_validity.before_loop
    async def before_check_role_validity(self) -> None:
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VCRoles(bot))
