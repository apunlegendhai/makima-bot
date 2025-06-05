import discord
import logging
import os
import aiosqlite
from discord.ext import commands
from dotenv import load_dotenv
import asyncio
import re
from typing import Dict, Optional, List
import functools
import logging.handlers
import pathlib

# Create necessary directories
pathlib.Path("database").mkdir(exist_ok=True)
pathlib.Path("logs").mkdir(exist_ok=True)

# Load environment variables
load_dotenv()

# Enhanced logging configuration
logger = logging.getLogger('role_management_bot')
logger.setLevel(logging.INFO)
file_handler = logging.handlers.RotatingFileHandler(
    'logs/bot_logs.log',
    maxBytes=10 * 1024 * 1024,  # 10 MB
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
DB_PATH = "database/role_management.db"


class RoleManagementConfig:
    """
    Immutable configuration for a guild's role management.
    `role_mappings` maps each custom_name → a list of role IDs.
    """
    def __init__(
        self,
        guild_id: int,
        reqrole_id: Optional[int] = None,
        role_mappings: Dict[str, List[int]] = None,
        log_channel_id: Optional[int] = None,
        role_assignment_limit: int = DEFAULT_ROLE_LIMIT
    ):
        self.guild_id = guild_id
        self.reqrole_id = reqrole_id
        # Each custom_name → List of role IDs
        self.role_mappings = role_mappings or {}
        self.log_channel_id = log_channel_id
        # Ensure the limit stays within our min/max bounds
        self.role_assignment_limit = max(
            MIN_ROLE_LIMIT,
            min(role_assignment_limit, MAX_ROLE_LIMIT)
        )


class RoleManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = DB_PATH

        # Thread-safe config cache
        self.guild_configs: Dict[int, RoleManagementConfig] = {}
        self.config_lock = asyncio.Lock()

        # Keep track of dynamically created command names per guild
        self.dynamic_commands: Dict[int, List[str]] = {}

    async def initialize_database(self):
        """Initialize SQLite database with the required tables (including multi-role schema)."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # guild_config table
                await db.execute(f'''
                    CREATE TABLE IF NOT EXISTS guild_config (
                        guild_id INTEGER PRIMARY KEY,
                        reqrole_id INTEGER,
                        log_channel_id INTEGER,
                        role_assignment_limit INTEGER DEFAULT {DEFAULT_ROLE_LIMIT}
                    )
                ''')

                # role_mappings: (guild_id, custom_name, role_id) → composite PK
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS role_mappings (
                        guild_id INTEGER,
                        custom_name TEXT,
                        role_id INTEGER,
                        PRIMARY KEY (guild_id, custom_name, role_id),
                        FOREIGN KEY (guild_id)
                            REFERENCES guild_config(guild_id)
                            ON DELETE CASCADE
                    )
                ''')
                await db.commit()
                logger.info("Database initialized with multi-role schema.")
        except aiosqlite.Error as e:
            logger.error(f"SQLite error during database initialization: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during database initialization: {e}")
            raise

    @functools.lru_cache(maxsize=100)
    def sanitize_role_name(self, name: str) -> str:
        """Cached, consistent role name sanitization (lowercase alphanumerics only)."""
        return re.sub(r'[^a-z0-9]', '', name.lower())

    async def initial_config_load(self):
        """Load configurations for all guilds on startup and regenerate dynamic commands."""
        try:
            await asyncio.sleep(2)  # Give the DB a moment to initialize
            for guild in self.bot.guilds:
                config = await self.load_guild_config(guild.id)
                await self.generate_dynamic_commands(config)
            logger.info(
                f"Loaded configurations for {len(self.bot.guilds)} guild(s) and re-registered dynamic commands."
            )
        except Exception as e:
            logger.error(f"Initial configuration load error: {e}")

    async def periodic_config_cleanup(self):
        """
        Every 12 hours, remove dynamic commands for any guild the bot has left.
        (We keep the DB rows around but drop in-memory commands.)
        """
        while True:
            try:
                async with self.config_lock:
                    current_guild_ids = {guild.id for guild in self.bot.guilds}
                    for guild_id in list(self.dynamic_commands.keys()):
                        if guild_id not in current_guild_ids:
                            # Remove every command name we registered for that guild
                            for cmd_name in self.dynamic_commands[guild_id]:
                                try:
                                    self.bot.remove_command(cmd_name)
                                except Exception as cmd_err:
                                    logger.error(f"Error removing command {cmd_name}: {cmd_err}")
                            del self.dynamic_commands[guild_id]
            except Exception as e:
                logger.error(f"Configuration cleanup error: {e}")

            # Sleep 12 hours
            await asyncio.sleep(12 * 60 * 60)

    async def load_guild_config(self, guild_id: int) -> RoleManagementConfig:
        """
        Load—or create—a guild configuration. Checks in-memory cache first,
        then falls back to the database. If no row exists, insert a default.
        """
        # First, check the cache
        async with self.config_lock:
            if guild_id in self.guild_configs:
                return self.guild_configs[guild_id]

        # Not in cache, try the database
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Get the guild_config row
                async with db.execute(
                    "SELECT reqrole_id, log_channel_id, role_assignment_limit FROM guild_config WHERE guild_id = ?",
                    (guild_id,)
                ) as cursor:
                    row = await cursor.fetchone()

                # If no row, create a default config
                if not row:
                    try:
                        # Insert a default row
                        await db.execute(
                            "INSERT INTO guild_config (guild_id, role_assignment_limit) VALUES (?, ?)",
                            (guild_id, DEFAULT_ROLE_LIMIT)
                        )
                        await db.commit()
                        logger.info(f"Created default config for guild {guild_id}")
                        # Return a default config
                        config = RoleManagementConfig(guild_id=guild_id)
                    except aiosqlite.Error as e:
                        logger.error(f"Failed to create default config for guild {guild_id}: {e}")
                        # Return a default config without saving to DB
                        return RoleManagementConfig(guild_id=guild_id)
                else:
                    # We have a row, get the role mappings
                    reqrole_id, log_channel_id, role_assignment_limit = row
                    role_mappings = {}

                    try:
                        # Get all role mappings for this guild
                        async with db.execute(
                            "SELECT custom_name, role_id FROM role_mappings WHERE guild_id = ?",
                            (guild_id,)
                        ) as cursor:
                            async for mapping_row in cursor:
                                custom_name, role_id = mapping_row
                                if custom_name not in role_mappings:
                                    role_mappings[custom_name] = []
                                role_mappings[custom_name].append(role_id)
                    except aiosqlite.Error as e:
                        logger.error(f"Failed to load role mappings for guild {guild_id}: {e}")
                        # Continue with empty role mappings
                        role_mappings = {}

                    # Create the config object
                    config = RoleManagementConfig(
                        guild_id=guild_id,
                        reqrole_id=reqrole_id,
                        role_mappings=role_mappings,
                        log_channel_id=log_channel_id,
                        role_assignment_limit=role_assignment_limit
                    )

            # Cache the config
            async with self.config_lock:
                self.guild_configs[guild_id] = config

            # Generate dynamic commands for this guild
            await self.generate_dynamic_commands(config)

            return config
        except aiosqlite.Error as e:
            logger.error(f"Database error loading guild config for {guild_id}: {e}")
            # Return a default config as fallback
            return RoleManagementConfig(guild_id=guild_id)
        except Exception as e:
            logger.error(f"Unexpected error loading guild config for {guild_id}: {e}")
            # Return a default config as fallback
            return RoleManagementConfig(guild_id=guild_id)

    async def save_guild_config(self, config: RoleManagementConfig):
        """
        Save (insert or update) guild_config row and all role_mappings.
        Then regenerate dynamic commands with the fresh configuration.
        """
        try:
            async with self.config_lock:
                async with aiosqlite.connect(self.db_path) as db:
                    try:
                        # Upsert guild_config
                        await db.execute(
                            """
                            INSERT INTO guild_config (
                                guild_id,
                                reqrole_id,
                                log_channel_id,
                                role_assignment_limit
                            ) VALUES (?, ?, ?, ?)
                            ON CONFLICT(guild_id) DO UPDATE SET
                                reqrole_id = excluded.reqrole_id,
                                log_channel_id = excluded.log_channel_id,
                                role_assignment_limit = excluded.role_assignment_limit
                            """,
                            (
                                config.guild_id,
                                config.reqrole_id,
                                config.log_channel_id,
                                config.role_assignment_limit
                            )
                        )

                        # Delete existing role_mappings for this guild
                        await db.execute(
                            "DELETE FROM role_mappings WHERE guild_id = ?",
                            (config.guild_id,)
                        )

                        # Insert each (custom_name, role_id) pair
                        for custom_name, role_id_list in config.role_mappings.items():
                            for rid in role_id_list:
                                await db.execute(
                                    "INSERT INTO role_mappings (guild_id, custom_name, role_id) VALUES (?, ?, ?)",
                                    (config.guild_id, custom_name, rid)
                                )

                        await db.commit()
                        logger.info(f"Successfully saved config for guild {config.guild_id}")
                    except aiosqlite.Error as e:
                        # Rollback transaction on error
                        await db.rollback()
                        logger.error(f"Database error saving config for guild {config.guild_id}: {e}")
                        raise

                # Update cache
                self.guild_configs[config.guild_id] = config

                # Regenerate dynamic commands from this updated config
                await self.generate_dynamic_commands(config)

        except aiosqlite.Error as e:
            logger.error(f"SQLite error saving config for guild {config.guild_id}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error saving config for guild {config.guild_id}: {e}")

    async def generate_dynamic_commands(self, config: RoleManagementConfig):
        """
        For each `custom_name` in config.role_mappings (with a list of role IDs),
        create a single command named `<custom_name>` that toggles all of those roles at once.
        Each command is "locked" to the guild that created it.
        """
        guild_id = config.guild_id

        # Remove previously registered commands for this guild
        if guild_id in self.dynamic_commands:
            for cmd_name in self.dynamic_commands[guild_id]:
                try:
                    self.bot.remove_command(cmd_name)
                except Exception as e:
                    logger.error(f"Error removing command {cmd_name}: {e}")
            del self.dynamic_commands[guild_id]

        self.dynamic_commands[guild_id] = []
        built_in_commands = {cmd.name for cmd in self.bot.commands}

        # Create a command for each custom_name → list_of_role_ids
        for custom_name, role_id_list in config.role_mappings.items():
            if custom_name in built_in_commands:
                logger.warning(
                    f"Skipping dynamic command '{custom_name}' as it conflicts with a built-in command."
                )
                continue

            # Create a new command function
            async def dynamic_role_command(ctx, member: discord.Member = None, *, _role_ids=role_id_list, _cmd_name=custom_name, _owner_guild_id=guild_id):
                # *** PER-SERVER LOCK: if run outside its "home" guild, do nothing. ***
                if not ctx.guild or ctx.guild.id != _owner_guild_id:
                    return

                # Always fetch a fresh config at runtime
                current_config = await self.load_guild_config(ctx.guild.id)

                # 1) Required-role check
                if not await self.check_required_role(ctx, current_config):
                    return

                # 2) Make sure a member was mentioned
                if not member:
                    await ctx.send(
                        embed=discord.Embed(
                            description=(
                                f"{EMOJI_INFO} | Please mention a user to assign or remove "
                                f"the '{_cmd_name}' role group."
                            ),
                            color=EMBED_COLOR
                        )
                    )
                    return

                # 3) Resolve each role object from its ID
                roles_to_toggle = []
                for rid in _role_ids:
                    r = ctx.guild.get_role(rid)
                    if r:
                        roles_to_toggle.append(r)

                if not roles_to_toggle:
                    await ctx.send(
                        embed=discord.Embed(
                            description=(
                                f"{EMOJI_INFO} | None of the roles mapped to '{_cmd_name}' exist anymore."
                            ),
                            color=EMBED_COLOR
                        )
                    )
                    return

                await self._process_role_toggle(ctx, member, roles_to_toggle, _cmd_name, current_config)

            # Create a proper Command object
            cmd = commands.Command(
                dynamic_role_command,
                name=custom_name,
                help=f"Toggle the '{custom_name}' role group for a member."
            )
            
            # Add the command to the bot
            self.bot.add_command(cmd)
            self.dynamic_commands[guild_id].append(custom_name)
            logger.info(f"Registered dynamic command: {custom_name} for guild {guild_id}")
            
    async def _process_role_toggle(self, ctx, member, roles_to_toggle, cmd_name, config):
        """Process role toggling for a member based on the command."""
        # Determine which roles the member already has
        member_current_roles = {r.id for r in member.roles}
        want_to_remove_all = all(r.id in member_current_roles for r in roles_to_toggle)

        if want_to_remove_all:
            # Remove ALL mapped roles
            try:
                await member.remove_roles(*roles_to_toggle)
                await ctx.send(
                    embed=discord.Embed(
                        description=(
                            f"{EMOJI_SUCCESS} | Removed all roles in '{cmd_name}' from {member.mention}."
                        ),
                        color=EMBED_COLOR
                    )
                )
                # Log each removal separately
                for role in roles_to_toggle:
                    await self.log_role_action(ctx, member, role, "removed", config)
            except discord.Forbidden:
                await ctx.send(
                    embed=discord.Embed(
                        description=(
                            f"{EMOJI_INFO} | I don't have permission to remove those roles."
                        ),
                        color=EMBED_COLOR
                    )
                )
            except discord.HTTPException:
                await ctx.send(
                    embed=discord.Embed(
                        description=(
                            f"{EMOJI_INFO} | Something went wrong while removing roles."
                        ),
                        color=EMBED_COLOR
                    )
                )
        else:
            # We will add whichever roles the member does *not* already have
            new_roles = [r for r in roles_to_toggle if r.id not in member_current_roles]

            # Check assignment-limit
            mapped_role_ids = [rid for sublist in config.role_mappings.values() for rid in sublist]
            current_mapped_roles = [r for r in member.roles if r.id in mapped_role_ids]
            already_count = len(current_mapped_roles)
            to_add_count = len(new_roles)

            if already_count + to_add_count > config.role_assignment_limit:
                await ctx.send(
                    embed=discord.Embed(
                        description=(
                            f"{EMOJI_INFO} | {member.mention} already has {already_count} mapped roles, "
                            f"and adding {to_add_count} more would exceed the limit of "
                            f"{config.role_assignment_limit}."
                        ),
                        color=EMBED_COLOR
                    )
                )
                return

            # Add all "new_roles" at once
            try:
                await member.add_roles(*new_roles)
                await ctx.send(
                    embed=discord.Embed(
                        description=(
                            f"{EMOJI_SUCCESS} | Added all roles in '{cmd_name}' to {member.mention}."
                        ),
                        color=EMBED_COLOR
                    )
                )
                # Log each addition separately
                for role in new_roles:
                    await self.log_role_action(ctx, member, role, "added", config)
            except discord.Forbidden:
                await ctx.send(
                    embed=discord.Embed(
                        description=(
                            f"{EMOJI_INFO} | I don't have permission to assign those roles."
                        ),
                        color=EMBED_COLOR
                    )
                )
            except discord.HTTPException:
                await ctx.send(
                    embed=discord.Embed(
                        description=(
                            f"{EMOJI_INFO} | Something went wrong while assigning roles."
                        ),
                        color=EMBED_COLOR
                    )
                )

    async def check_required_role(self, ctx, config: RoleManagementConfig) -> bool:
        """
        Ensures the invoker has the required “reqrole” (if one is set).
        If none is set, or if the role no longer exists, or if the user lacks it,
        we send an info message and return False.
        """
        if not config.reqrole_id:
            await ctx.send(
                embed=discord.Embed(
                    description=(
                        f"{EMOJI_INFO} | No required role has been set for this server. "
                        f"Please ask an admin to set one."
                    ),
                    color=EMBED_COLOR
                )
            )
            return False

        required_role = ctx.guild.get_role(config.reqrole_id)
        if not required_role:
            await ctx.send(
                embed=discord.Embed(
                    description=(
                        f"{EMOJI_INFO} | The required role set for this server no longer exists. "
                        f"Please ask an admin to update it."
                    ),
                    color=EMBED_COLOR
                )
            )
            return False

        if required_role not in ctx.author.roles:
            await ctx.send(
                embed=discord.Embed(
                    description=(
                        f"{EMOJI_INFO} | You lack the required role (**{required_role.name}**) to use this command."
                    ),
                    color=EMBED_COLOR
                )
            )
            return False

        return True

    async def log_role_action(
        self,
        ctx,
        member: discord.Member,
        role: discord.Role,
        action: str,
        config: RoleManagementConfig
    ):
        """If a log channel is set, post a message whenever a role is added/removed."""
        if config.log_channel_id:
            log_channel = ctx.guild.get_channel(config.log_channel_id)
            if log_channel:
                await log_channel.send(
                    embed=discord.Embed(
                        description=(
                            f"{ctx.author.mention} {action} role '{role.name}' for {member.mention}."
                        ),
                        color=EMBED_COLOR
                    )
                )

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setreqrole(self, ctx, role: discord.Role):
        """
        /setreqrole @Role
        Defines the role that members must have in order to run any dynamic commands.
        """
        try:
            config = await self.load_guild_config(ctx.guild.id)
            new_config = RoleManagementConfig(
                guild_id=config.guild_id,
                reqrole_id=role.id,
                role_mappings=config.role_mappings,
                log_channel_id=config.log_channel_id,
                role_assignment_limit=config.role_assignment_limit
            )
            await self.save_guild_config(new_config)
            await ctx.send(
                embed=discord.Embed(
                    description=(
                        f"{EMOJI_SUCCESS} | Required role for managing roles has been set to **{role.name}**."
                    ),
                    color=EMBED_COLOR
                )
            )
        except Exception as e:
            logger.error(f"Error in setreqrole command: {e}")
            await ctx.send(
                embed=discord.Embed(
                    description=(
                        f"{EMOJI_INFO} | An error occurred while setting the required role. Please try again."
                    ),
                    color=EMBED_COLOR
                )
            )

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setrole(self, ctx, custom_name: str, *roles: discord.Role):
        """
        /setrole <custom_name> @RoleA @RoleB ...
    
        Map a custom name to one or more roles. When someone runs:
          .<custom_name> @member
        it will toggle _all_ of those roles at once (but only in this guild).
        """
        if not roles:
            await ctx.send(
                embed=discord.Embed(
                    description=(
                        f"{EMOJI_INFO} | You must mention at least one role to map to '{custom_name}'."
                    ),
                    color=EMBED_COLOR
                )
            )
            return

        config = await self.load_guild_config(ctx.guild.id)
        sanitized_name = self.sanitize_role_name(custom_name)
        if not sanitized_name:
            await ctx.send(
                embed=discord.Embed(
                    description=(
                        f"{EMOJI_INFO} | Custom name must contain at least one alphanumeric character."
                    ),
                    color=EMBED_COLOR
                )
            )
            return

        built_in_commands = {cmd.name for cmd in self.bot.commands}
        if sanitized_name in built_in_commands:
            await ctx.send(
                embed=discord.Embed(
                    description=(
                        f"{EMOJI_INFO} | '{sanitized_name}' conflicts with a built-in command. "
                        f"Please choose a different name."
                    ),
                    color=EMBED_COLOR
                )
            )
            return

        role_id_list = [role.id for role in roles]
        role_mappings = config.role_mappings.copy()
        role_mappings[sanitized_name] = role_id_list

        new_config = RoleManagementConfig(
            guild_id=config.guild_id,
            reqrole_id=config.reqrole_id,
            role_mappings=role_mappings,
            log_channel_id=config.log_channel_id,
            role_assignment_limit=config.role_assignment_limit
        )
        await self.save_guild_config(new_config)

        role_names = ", ".join(r.name for r in roles)
        await ctx.send(
            embed=discord.Embed(
                description=(
                    f"{EMOJI_SUCCESS} | Mapped '{sanitized_name}' to roles: {role_names}."
                ),
                color=EMBED_COLOR
            )
        )

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def delrole(self, ctx, custom_name: str):
        """
        /delrole <custom_name>
    
        Deletes the mapping for this custom_name from the current server.
        All roles under that name are unmapped, and the dynamic command is removed.
        """
        config = await self.load_guild_config(ctx.guild.id)
        sanitized_name = self.sanitize_role_name(custom_name)

        # If the sanitized name isn't in the mapping, inform
        if sanitized_name not in config.role_mappings:
            await ctx.send(
                embed=discord.Embed(
                    description=(
                        f"{EMOJI_INFO} | No mapping found for '{sanitized_name}'."
                    ),
                    color=EMBED_COLOR
                )
            )
            return

        # Remove that single key
        role_mappings = config.role_mappings.copy()
        del role_mappings[sanitized_name]

        new_config = RoleManagementConfig(
            guild_id=config.guild_id,
            reqrole_id=config.reqrole_id,
            role_mappings=role_mappings,
            log_channel_id=config.log_channel_id,
            role_assignment_limit=config.role_assignment_limit
        )
        await self.save_guild_config(new_config)

        await ctx.send(
            embed=discord.Embed(
                description=(
                    f"{EMOJI_SUCCESS} | Deleted mapping for '{sanitized_name}'."
                ),
                color=EMBED_COLOR
            )
        )

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def resetroles(self, ctx):
        """
        /resetroles
    
        Deletes _all_ role mappings in the current server. After this, no dynamic commands exist
        until you run /setrole again.
        """
        config = await self.load_guild_config(ctx.guild.id)

        if not config.role_mappings:
            await ctx.send(
                embed=discord.Embed(
                    description=(
                        f"{EMOJI_INFO} | There are no role mappings to reset."
                    ),
                    color=EMBED_COLOR
                )
            )
            return

        # Wipe every key
        new_config = RoleManagementConfig(
            guild_id=config.guild_id,
            reqrole_id=config.reqrole_id,
            role_mappings={},  # empty dict = no mappings
            log_channel_id=config.log_channel_id,
            role_assignment_limit=config.role_assignment_limit
        )
        await self.save_guild_config(new_config)

        await ctx.send(
            embed=discord.Embed(
                description=(
                    f"{EMOJI_SUCCESS} | All mapped roles have been cleared in this server."
                ),
                color=EMBED_COLOR
            )
        )

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setlogchannel(self, ctx, channel: discord.TextChannel):
        """
        /setlogchannel #channel
        Designates which text channel will receive role‐change logs.
        """
        config = await self.load_guild_config(ctx.guild.id)
        new_config = RoleManagementConfig(
            guild_id=config.guild_id,
            reqrole_id=config.reqrole_id,
            role_mappings=config.role_mappings,
            log_channel_id=channel.id,
            role_assignment_limit=config.role_assignment_limit
        )
        await self.save_guild_config(new_config)
        await ctx.send(
            embed=discord.Embed(
                description=(
                    f"{EMOJI_SUCCESS} | Log channel set to **{channel.name}**."
                ),
                color=EMBED_COLOR
            )
        )

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setrolelimit(self, ctx, limit: int):
        """
        /setrolelimit <n>
        Adjusts how many “mapped” roles a single member may have at once.
        """
        if limit < MIN_ROLE_LIMIT or limit > MAX_ROLE_LIMIT:
            await ctx.send(
                embed=discord.Embed(
                    description=(
                        f"{EMOJI_INFO} | Role limit must be between {MIN_ROLE_LIMIT} and {MAX_ROLE_LIMIT}."
                    ),
                    color=EMBED_COLOR
                )
            )
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
        await ctx.send(
            embed=discord.Embed(
                description=(
                    f"{EMOJI_SUCCESS} | Role assignment limit set to {limit}."
                ),
                color=EMBED_COLOR
            )
        )


async def setup(bot):
    # Create the cog instance
    cog = RoleManagement(bot)
    
    # Initialize database and load configs before adding the cog
    await cog.initialize_database()
    await cog.initial_config_load()
    
    # Add the cog to the bot
    await bot.add_cog(cog)
    
    # Start the periodic cleanup as a background task
    # Use bot.loop directly since we're in an async context
    bot.loop.create_task(cog.periodic_config_cleanup())
