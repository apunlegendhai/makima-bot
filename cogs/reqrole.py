import discord
import logging
import os
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import asyncio
import re
from typing import Dict, Optional, List, Any
import functools
import logging.handlers

# Load environment variables
load_dotenv()

# Enhanced logging configuration
logger = logging.getLogger('role_management_bot')
logger.setLevel(logging.INFO)
file_handler = logging.handlers.RotatingFileHandler(
    'bot_logs.log', 
    maxBytes=10*1024*1024,  # 10 MB
    backupCount=5
)
console_handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Configuration Constants
EMBED_COLOR = 0x2f2136
DEFAULT_ROLE_LIMIT = 5
MAX_ROLE_LIMIT = 15
MIN_ROLE_LIMIT = 1
EMOJI_SUCCESS = "<a:sukoon_whitetick:1323992464058482729>"
EMOJI_INFO = "<:sukoon_info:1323251063910043659>"

class RoleManagementConfig:
    """Immutable configuration for a guild's role management."""
    def __init__(self, 
                 guild_id: int, 
                 reqrole_id: Optional[int] = None, 
                 role_mappings: Dict[str, int] = None, 
                 log_channel_id: Optional[int] = None, 
                 role_assignment_limit: int = DEFAULT_ROLE_LIMIT):
        self.guild_id = guild_id
        self.reqrole_id = reqrole_id
        self.role_mappings = role_mappings or {}
        self.log_channel_id = log_channel_id
        self.role_assignment_limit = max(MIN_ROLE_LIMIT, min(role_assignment_limit, MAX_ROLE_LIMIT))

class RoleManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mongo_client = AsyncIOMotorClient(os.getenv("MONGO_URL"))
        self.db = self.mongo_client["reqrole"]
        self.config_collection = self.db["config"]

        # Thread-safe configuration cache with size limit
        self.guild_configs: Dict[int, RoleManagementConfig] = {}
        self.config_lock = asyncio.Lock()
        self.dynamic_commands: Dict[int, List[str]] = {}

        # Periodic cleanup and configuration loading
        self.bot.loop.create_task(self.periodic_config_cleanup())
        self.bot.loop.create_task(self.initial_config_load())

    @functools.lru_cache(maxsize=100)
    def sanitize_role_name(self, name: str) -> str:
        """Cached and consistent role name sanitization."""
        return re.sub(r'[^a-z0-9]', '', name.lower())

    async def initial_config_load(self):
        """Load configurations for all current guilds on bot startup."""
        try:
            for guild in self.bot.guilds:
                await self.load_guild_config(guild.id)
            logger.info(f"Loaded configurations for {len(self.bot.guilds)} guilds")
        except Exception as e:
            logger.error(f"Initial configuration load error: {e}")

    async def periodic_config_cleanup(self):
        """Periodically clean up configurations for guilds bot is no longer in."""
        while True:
            try:
                async with self.config_lock:
                    current_guild_ids = {guild.id for guild in self.bot.guilds}
                    # Remove configs for guilds bot is no longer in
                    for guild_id in list(self.guild_configs.keys()):
                        if guild_id not in current_guild_ids:
                            del self.guild_configs[guild_id]

                            # Remove associated dynamic commands
                            if guild_id in self.dynamic_commands:
                                for cmd_name in self.dynamic_commands[guild_id]:
                                    self.bot.remove_command(cmd_name)
                                del self.dynamic_commands[guild_id]

            except Exception as e:
                logger.error(f"Configuration cleanup error: {e}")

            # Run cleanup every 12 hours
            await asyncio.sleep(12 * 60 * 60)

    async def load_guild_config(self, guild_id: int) -> RoleManagementConfig:
        """
        Safely load or create guild configuration with comprehensive validation.
        """
        async with self.config_lock:
            # Check if configuration exists in cache
            if guild_id in self.guild_configs:
                return self.guild_configs[guild_id]

            # Fetch from database or create default
            try:
                config_data = await self.config_collection.find_one({"guild_id": guild_id}) or {}

                # Validate and sanitize configuration data
                config = RoleManagementConfig(
                    guild_id=guild_id,
                    reqrole_id=config_data.get("reqrole_id"),
                    role_mappings=config_data.get("role_mappings", {}),
                    log_channel_id=config_data.get("log_channel_id"),
                    role_assignment_limit=config_data.get("role_assignment_limit", DEFAULT_ROLE_LIMIT)
                )

                self.guild_configs[guild_id] = config
                return config

            except Exception as e:
                logger.error(f"Configuration loading error for guild {guild_id}: {e}")
                # Fallback to default configuration
                default_config = RoleManagementConfig(guild_id=guild_id)
                self.guild_configs[guild_id] = default_config
                return default_config

    async def save_guild_config(self, config: RoleManagementConfig):
        """
        Comprehensive configuration saving with advanced error handling.
        """
        try:
            async with self.config_lock:
                # Validate configuration before saving
                config_dict = {
                    "guild_id": config.guild_id,
                    "reqrole_id": config.reqrole_id,
                    "role_mappings": config.role_mappings,
                    "log_channel_id": config.log_channel_id,
                    "role_assignment_limit": config.role_assignment_limit
                }

                await self.config_collection.replace_one(
                    {"guild_id": config.guild_id}, 
                    config_dict, 
                    upsert=True
                )

                # Update in-memory cache
                self.guild_configs[config.guild_id] = config

                # Regenerate dynamic commands
                self.generate_dynamic_commands(config)

        except Exception as e:
            logger.error(f"Configuration save error for guild {config.guild_id}: {e}")

    def generate_dynamic_commands(self, config: RoleManagementConfig):
        """
        Advanced dynamic command generation with better error handling.
        """
        # Remove previous dynamic commands for this guild
        if config.guild_id in self.dynamic_commands:
            for cmd_name in self.dynamic_commands[config.guild_id]:
                self.bot.remove_command(cmd_name)
            del self.dynamic_commands[config.guild_id]

        self.dynamic_commands[config.guild_id] = []

        for custom_name, role_id in config.role_mappings.items():
            # Create a closure to capture the specific role_id and custom_name
            def create_dynamic_command(specific_role_id, specific_custom_name):
                async def dynamic_role_command(ctx, member: discord.Member = None):
                    # Validate required role
                    if not await self.check_required_role(ctx, config):
                        return

                    # Validate member
                    if not member:
                        await ctx.send(embed=discord.Embed(
                            description=f"{EMOJI_INFO} | Please mention a user to assign or remove the role.",
                            color=EMBED_COLOR))
                        return

                    # Find the role
                    role = ctx.guild.get_role(specific_role_id)
                    if not role:
                        await ctx.send(embed=discord.Embed(
                            description=f"{EMOJI_INFO} | The role no longer exists.",
                            color=EMBED_COLOR))
                        return

                    # Check role assignment limit
                    if not await self.check_role_assignment_limit(ctx, member, config):
                        return

                    # Perform role action
                    try:
                        action = "added" if role not in member.roles else "removed"
                        if action == "added":
                            await member.add_roles(role)
                        else:
                            await member.remove_roles(role)

                        # Send success message
                        await ctx.send(embed=discord.Embed(
                            description=f"{EMOJI_SUCCESS} | Role '{role.name}' has been {action} to {member.mention}.",
                            color=EMBED_COLOR))

                        # Log the action
                        await self.log_role_action(ctx, member, role, action, config)

                    except discord.Forbidden:
                        await ctx.send(embed=discord.Embed(
                            description=f"{EMOJI_INFO} | Insufficient permissions to manage roles.",
                            color=EMBED_COLOR))
                    except discord.HTTPException:
                        await ctx.send(embed=discord.Embed(
                            description=f"{EMOJI_INFO} | Failed to manage roles.",
                            color=EMBED_COLOR))

                return dynamic_role_command

            # Register dynamic command
            cmd = commands.command(name=custom_name)(create_dynamic_command(role_id, custom_name))
            self.bot.add_command(cmd)
            self.dynamic_commands[config.guild_id].append(custom_name)

    async def check_required_role(self, ctx, config: RoleManagementConfig) -> bool:
        """Check if user has the required role."""
        if not config.reqrole_id:
            await ctx.send(embed=discord.Embed(
                description=f"{EMOJI_INFO} | No required role set for this server.",
                color=EMBED_COLOR))
            return False

        if config.reqrole_id not in [role.id for role in ctx.author.roles]:
            await ctx.send(embed=discord.Embed(
                description=f"{EMOJI_INFO} | You lack the required role.",
                color=EMBED_COLOR))
            return False

        return True

    async def check_role_assignment_limit(self, ctx, member: discord.Member, config: RoleManagementConfig) -> bool:
        """Check if member can receive another role."""
        mapped_role_ids = list(config.role_mappings.values())
        current_mapped_roles = [r for r in member.roles if r.id in mapped_role_ids]

        if len(current_mapped_roles) >= config.role_assignment_limit:
            await ctx.send(embed=discord.Embed(
                description=f"{EMOJI_INFO} | {member.mention} has reached the role limit of {config.role_assignment_limit}.",
                color=EMBED_COLOR))
            return False
        return True

    async def log_role_action(self, ctx, member: discord.Member, role: discord.Role, action: str, config: RoleManagementConfig):
        """Log role actions to designated log channel."""
        if config.log_channel_id:
            log_channel = ctx.guild.get_channel(config.log_channel_id)
            if log_channel:
                await log_channel.send(embed=discord.Embed(
                    description=f"{ctx.author.mention} {action} role '{role.name}' for {member.mention}.",
                    color=EMBED_COLOR))

    # Fix for setting the required role
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setreqrole(self, ctx, role: discord.Role):
        """
        Sets the required role for using dynamic role commands.
        """
        try:
            # Load guild configuration
            config = await self.load_guild_config(ctx.guild.id)

            # Update the required role ID in the config
            config.reqrole_id = role.id
            await self.save_guild_config(config)

            # Provide confirmation to the admin
            await ctx.send(embed=discord.Embed(
                description=f"{EMOJI_SUCCESS} | Required role for managing roles has been set to **{role.name}**.",
                color=EMBED_COLOR
            ))

        except Exception as e:
            logger.error(f"Error in setreqrole command: {e}")
            await ctx.send(embed=discord.Embed(
                description=f"{EMOJI_INFO} | An error occurred while setting the required role. Please try again.",
                color=EMBED_COLOR
            ))

    # Improved validation for checking the required role
    async def check_required_role(self, ctx, config: RoleManagementConfig) -> bool:
        """
        Validates if the user has the required role to execute commands.
        """
        if not config.reqrole_id:
            await ctx.send(embed=discord.Embed(
                description=f"{EMOJI_INFO} | No required role has been set for this server. Please ask an admin to set one.",
                color=EMBED_COLOR
            ))
            return False

        required_role = ctx.guild.get_role(config.reqrole_id)
        if not required_role:
            await ctx.send(embed=discord.Embed(
                description=f"{EMOJI_INFO} | The required role set for this server no longer exists. Please ask an admin to update it.",
                color=EMBED_COLOR
            ))
            return False

        if required_role not in ctx.author.roles:
            await ctx.send(embed=discord.Embed(
                description=f"{EMOJI_INFO} | You lack the required role (**{required_role.name}**) to use this command.",
                color=EMBED_COLOR
            ))
            return False

        return True
        
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setrole(self, ctx, custom_name: str, role: discord.Role):
        """Map a custom role name to an existing role."""
        config = await self.load_guild_config(ctx.guild.id)
        sanitized_name = self.sanitize_role_name(custom_name)

        role_mappings = config.role_mappings.copy()
        role_mappings[sanitized_name] = role.id

        new_config = RoleManagementConfig(
            guild_id=config.guild_id,
            reqrole_id=config.reqrole_id,
            role_mappings=role_mappings,
            log_channel_id=config.log_channel_id,
            role_assignment_limit=config.role_assignment_limit
        )
        await self.save_guild_config(new_config)

        await ctx.send(embed=discord.Embed(
            description=f"{EMOJI_SUCCESS} | Mapped '{sanitized_name}' to {role.name}.",
            color=EMBED_COLOR))

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setlogchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel for logging role actions."""
        config = await self.load_guild_config(ctx.guild.id)
        new_config = RoleManagementConfig(
            guild_id=config.guild_id,
            reqrole_id=config.reqrole_id,
            role_mappings=config.role_mappings,
            log_channel_id=channel.id,
            role_assignment_limit=config.role_assignment_limit
        )
        await self.save_guild_config(new_config)
        await ctx.send(embed=discord.Embed(
            description=f"{EMOJI_SUCCESS} | Log channel set to {channel.name}.",
            color=EMBED_COLOR))

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setrolelimit(self, ctx, limit: int):
        """Set the maximum number of roles a user can have."""
        if limit < MIN_ROLE_LIMIT or limit > MAX_ROLE_LIMIT:
            await ctx.send(embed=discord.Embed(
                description=f"{EMOJI_INFO} | Role limit must be between {MIN_ROLE_LIMIT} and {MAX_ROLE_LIMIT}.",
                color=EMBED_COLOR))
            return

        config = await self.load_guild_config(ctx.guild.id)
        new_config = RoleManagementConfig(
            guild_id=config.guild_id,
            reqrole_id=config.reqrole_id,
            role_mappings=config.role_mappings,
            log_channel_id=config.log_channel_id,
            role_assignment_limit=limit
        )
        await self.save_guild_config(new_config)
        await ctx.send(embed=discord.Embed(
            description=f"{EMOJI_SUCCESS} | Role assignment limit set to {limit}.",
            color=EMBED_COLOR))

async def setup(bot):
    await bot.add_cog(RoleManagement(bot))
