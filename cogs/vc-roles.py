import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import aiosqlite
import logging
from typing import Dict, Optional, Set, Tuple
from contextlib import asynccontextmanager
import random
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Default database directory and path
DB_DIR = os.path.join("database")
DB_PATH = os.path.join(DB_DIR, "vc_roles.db")

class VCRoles(commands.Cog):
  """
  A Cog for automatically assigning roles when users join voice channels.
  Uses a single slash command with optional parameters.
  """

  def __init__(self, bot: commands.Bot):
      self.bot = bot
      self.vc_role_configs: Dict[int, Tuple[int, Optional[int]]] = {}  # guild_id -> (role_id, log_channel_id)
      self.db_pool = None
      self.operation_lock = asyncio.Lock()
      self.processing_users: Set[int] = set()  # Track users being processed

      # Ensure database directory exists
      os.makedirs(DB_DIR, exist_ok=True)

  @asynccontextmanager
  async def get_db_connection(self):
      """Context manager for database connections."""
      conn = None
      try:
          conn = await aiosqlite.connect(DB_PATH)
          yield conn
      except Exception as e:
          logger.error(f"Database connection error: {e}")
          raise
      finally:
          if conn:
              await conn.close()

  async def cog_load(self) -> None:
      """Initialize database and load configurations when cog is loaded."""
      try:
          await self._setup_database()
          await self._load_configurations()
          await self._cleanup_invalid_configs()
          if not self.check_role_validity.is_running():
              self.check_role_validity.start()
          if not self.periodic_role_sync.is_running():
              self.periodic_role_sync.start()
          logger.info("VCRoles cog loaded successfully")
      except Exception as e:
          logger.error(f"Failed to load VCRoles cog: {e}")
          raise

  async def _setup_database(self) -> None:
      """Initialize the SQLite database connection and tables."""
      try:
          async with self.get_db_connection() as db:
              await db.execute('''
                  CREATE TABLE IF NOT EXISTS vc_roles (
                      guild_id INTEGER PRIMARY KEY,
                      role_id INTEGER NOT NULL,
                      log_channel_id INTEGER
                  )
              ''')
              await db.commit()
              logger.info("Database setup completed")
      except Exception as e:
          logger.error(f"Database setup failed: {e}")
          raise

  async def _load_configurations(self) -> None:
      """Load all role configurations from the database."""
      try:
          async with self.get_db_connection() as db:
              async with db.execute("SELECT guild_id, role_id, log_channel_id FROM vc_roles") as cursor:
                  configs = await cursor.fetchall()
                  self.vc_role_configs = {
                      guild_id: (role_id, log_channel_id) 
                      for guild_id, role_id, log_channel_id in configs
                  }
                  logger.info(f"Loaded {len(self.vc_role_configs)} configurations")
      except Exception as e:
          logger.error(f"Failed to load configurations: {e}")
          self.vc_role_configs = {}

  async def _cleanup_invalid_configs(self) -> None:
      """Remove configurations for guilds the bot is no longer in."""
      invalid_guilds = []
      for guild_id in list(self.vc_role_configs.keys()):
          if not self.bot.get_guild(guild_id):
              invalid_guilds.append(guild_id)
      
      for guild_id in invalid_guilds:
          await self._delete_config(guild_id)
          logger.info(f"Cleaned up configuration for guild {guild_id}")

  async def cog_unload(self) -> None:
      """Close database connection when unloading the cog."""
      if self.check_role_validity.is_running():
          self.check_role_validity.cancel()
      if self.periodic_role_sync.is_running():
          self.periodic_role_sync.cancel()
      logger.info("VCRoles cog unloaded")

  async def _save_config(self, guild_id: int, role_id: int, log_channel_id: Optional[int] = None) -> bool:
      """Add or update a configuration in the database."""
      try:
          async with self.get_db_connection() as db:
              await db.execute(
                  "INSERT OR REPLACE INTO vc_roles (guild_id, role_id, log_channel_id) VALUES (?, ?, ?)",
                  (guild_id, role_id, log_channel_id)
              )
              await db.commit()
              return True
      except Exception as e:
          logger.error(f"Failed to save config for guild {guild_id}: {e}")
          return False

  async def _delete_config(self, guild_id: int) -> bool:
      """Remove a configuration from the database."""
      try:
          async with self.get_db_connection() as db:
              await db.execute("DELETE FROM vc_roles WHERE guild_id = ?", (guild_id,))
              await db.commit()
              self.vc_role_configs.pop(guild_id, None)
              return True
      except Exception as e:
          logger.error(f"Failed to delete config for guild {guild_id}: {e}")
          return False

  def _check_permissions(self, interaction: discord.Interaction) -> bool:
      if not interaction.guild or not isinstance(interaction.user, discord.Member):
          return False
      return interaction.user.guild_permissions.administrator

  async def _check_bot_permissions(self, guild: discord.Guild, role: discord.Role) -> bool:
      bot_member = guild.me
      if not bot_member or not bot_member.guild_permissions.manage_roles:
          return False
      return bot_member.top_role > role

  async def _log_action(self, guild: discord.Guild, member: discord.Member, role: discord.Role, action: str, log_channel_id: Optional[int]):
      """Log role changes to the specified logging channel."""
      if not log_channel_id:
          return
          
      channel = guild.get_channel(log_channel_id)
      if not channel or not isinstance(channel, discord.TextChannel):
          return
          
      try:
          embed = discord.Embed(
              title="Voice Channel Role Update",
              color=discord.Color.green() if action == "added" else discord.Color.red(),
              timestamp=datetime.utcnow()
          )
          embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=True)
          embed.add_field(name="Role", value=role.mention, inline=True)
          embed.add_field(name="Action", value=action.title(), inline=True)
          embed.set_thumbnail(url=member.display_avatar.url)
          embed.set_footer(text=f"Guild: {guild.name}")
          
          await channel.send(embed=embed)
      except Exception as e:
          logger.error(f"Failed to log action to channel {log_channel_id}: {e}")

  async def _apply_to_current_users(self, guild: discord.Guild, role: discord.Role, log_channel_id: Optional[int]) -> None:
      """Apply role to users currently in voice channels with improved performance."""
      tasks = []
      for vc in guild.voice_channels:
          for member in vc.members:
              if not member.bot and role not in member.roles:
                  tasks.append(self._add_role_with_retry(member, role, "Initial VC assignment", log_channel_id))
                  
      # Process in batches to avoid overwhelming the API
      batch_size = 5
      for i in range(0, len(tasks), batch_size):
          batch = tasks[i:i + batch_size]
          await asyncio.gather(*batch, return_exceptions=True)
          if i + batch_size < len(tasks):  # Don't delay after the last batch
              await asyncio.sleep(1)

  async def _add_role_with_retry(self, member: discord.Member, role: discord.Role, reason: str, log_channel_id: Optional[int] = None, max_retries: int = 3) -> bool:
      """Add role with exponential backoff retry logic."""
      for attempt in range(max_retries):
          try:
              await member.add_roles(role, reason=reason)
              if log_channel_id:
                  await self._log_action(member.guild, member, role, "added", log_channel_id)
              return True
          except discord.HTTPException as e:
              if e.status == 429:  # Rate limited
                  retry_after = getattr(e, 'retry_after', 2 ** attempt)
                  await asyncio.sleep(retry_after + random.uniform(0, 1))
              elif e.status in [403, 404]:  # Permission/Not found errors
                  logger.warning(f"Permission error adding role to {member}: {e}")
                  return False
              else:
                  wait_time = (2 ** attempt) + random.uniform(0, 1)
                  await asyncio.sleep(wait_time)
          except Exception as e:
              logger.error(f"Unexpected error adding role to {member}: {e}")
              if attempt == max_retries - 1:
                  return False
              await asyncio.sleep(2 ** attempt)
      return False

  async def _remove_role_with_retry(self, member: discord.Member, role: discord.Role, reason: str, log_channel_id: Optional[int] = None, max_retries: int = 3) -> bool:
      """Remove role with exponential backoff retry logic."""
      for attempt in range(max_retries):
          try:
              await member.remove_roles(role, reason=reason)
              if log_channel_id:
                  await self._log_action(member.guild, member, role, "removed", log_channel_id)
              return True
          except discord.HTTPException as e:
              if e.status == 429:  # Rate limited
                  retry_after = getattr(e, 'retry_after', 2 ** attempt)
                  await asyncio.sleep(retry_after + random.uniform(0, 1))
              elif e.status in [403, 404]:  # Permission/Not found errors
                  logger.warning(f"Permission error removing role from {member}: {e}")
                  return False
              else:
                  wait_time = (2 ** attempt) + random.uniform(0, 1)
                  await asyncio.sleep(wait_time)
          except Exception as e:
              logger.error(f"Unexpected error removing role from {member}: {e}")
              if attempt == max_retries - 1:
                  return False
              await asyncio.sleep(2 ** attempt)
      return False

  @app_commands.command(
      name="vc-role",
      description="Configure a role to be assigned when users join voice channels"
  )
  @app_commands.describe(
      role="The role to assign (leave empty to view current setting or use with remove=True to remove setting)",
      log_channel="Channel to log role changes (optional)",
      remove="Set to True to remove the current voice channel role configuration"
  )
  async def vc_role(
      self,
      interaction: discord.Interaction,
      role: Optional[discord.Role] = None,
      log_channel: Optional[discord.TextChannel] = None,
      remove: bool = False
  ) -> None:
      await interaction.response.defer(ephemeral=True)

      if not self._check_permissions(interaction):
          return await interaction.followup.send("❌ You need administrator permissions to use this command.", ephemeral=True)

      guild = interaction.guild
      if not guild:
          return await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)

      guild_id = guild.id

      async with self.operation_lock:
          # Remove existing configuration
          if remove:
              if guild_id not in self.vc_role_configs:
                  return await interaction.followup.send("ℹ️ No voice channel role is currently configured.", ephemeral=True)

              config = self.vc_role_configs.get(guild_id)
              role_id = config[0] if config else None
              
              if await self._delete_config(guild_id):
                  existing_role = guild.get_role(role_id) if role_id else None
                  mention = existing_role.mention if existing_role else "Unknown Role"
                  
                  # Clean up role from current users
                  if existing_role:
                      cleanup_tasks = []
                      for vc in guild.voice_channels:
                          for member in vc.members:
                              if not member.bot and existing_role in member.roles:
                                  cleanup_tasks.append(self._remove_role_with_retry(member, existing_role, "VC role removed"))
                      
                      if cleanup_tasks:
                          await asyncio.gather(*cleanup_tasks, return_exceptions=True)
                  
                  return await interaction.followup.send(
                      f"✅ Removed configuration. Users will no longer receive {mention}.", ephemeral=True
                  )
              else:
                  return await interaction.followup.send("❌ Failed to remove configuration.", ephemeral=True)

          # View current configuration
          if role is None and log_channel is None:
              config = self.vc_role_configs.get(guild_id)
              if not config:
                  return await interaction.followup.send(
                      "No voice channel role is configured.\nUse `/vc-role role:@RoleName` to set one.", ephemeral=True
                  )
              
              role_id, log_channel_id = config
              existing_role = guild.get_role(role_id)
              existing_log_channel = guild.get_channel(log_channel_id) if log_channel_id else None
              
              if existing_role:
                  embed = discord.Embed(
                      title="Voice Channel Role Configuration",
                      color=discord.Color.blue()
                  )
                  embed.add_field(
                      name="Role", 
                      value=existing_role.mention, 
                      inline=False
                  )
                  embed.add_field(
                      name="Log Channel", 
                      value=existing_log_channel.mention if existing_log_channel else "Not configured", 
                      inline=False
                  )
                  embed.add_field(
                      name="Usage",
                      value=(
                          "• To change role: `/vc-role role:@NewRole`\n"
                          "• To set log channel: `/vc-role log_channel:#channel`\n"
                          "• To remove setting: `/vc-role remove:True`"
                      ),
                      inline=False
                  )
                  return await interaction.followup.send(embed=embed, ephemeral=True)
              else:
                  # Clean up invalid role
                  await self._delete_config(guild_id)
                  return await interaction.followup.send(
                      "⚠️ The previously configured role no longer exists. Set a new one with `/vc-role role:@RoleName`",
                      ephemeral=True
                  )

          # Update configuration
          current_config = self.vc_role_configs.get(guild_id, (None, None))
          current_role_id, current_log_channel_id = current_config
          
          # Use existing values if not provided
          new_role_id = role.id if role else current_role_id
          new_log_channel_id = log_channel.id if log_channel else current_log_channel_id
          
          if not new_role_id:
              return await interaction.followup.send("❌ You must specify a role to configure.", ephemeral=True)
          
          # Get the role object for permission check
          target_role = guild.get_role(new_role_id)
          if not target_role:
              return await interaction.followup.send("❌ The specified role no longer exists.", ephemeral=True)
          
          # Check bot permissions for the role
          if not await self._check_bot_permissions(guild, target_role):
              return await interaction.followup.send(
                  "❌ I don't have permission to manage this role. Ensure my role is higher and I have Manage Roles permission.",
                  ephemeral=True
              )

          # Validate log channel permissions
          if new_log_channel_id:
              log_channel_obj = guild.get_channel(new_log_channel_id)
              if not log_channel_obj or not isinstance(log_channel_obj, discord.TextChannel):
                  return await interaction.followup.send("❌ Invalid log channel specified.", ephemeral=True)
              
              bot_perms = log_channel_obj.permissions_for(guild.me)
              if not (bot_perms.send_messages and bot_perms.embed_links):
                  return await interaction.followup.send(
                      "❌ I don't have permission to send messages or embed links in the specified log channel.",
                      ephemeral=True
                  )

          if await self._save_config(guild_id, new_role_id, new_log_channel_id):
              self.vc_role_configs[guild_id] = (new_role_id, new_log_channel_id)
              
              # Apply to current users if this is a new role or role change
              if role and (not current_role_id or role.id != current_role_id):
                  await self._apply_to_current_users(guild, target_role, new_log_channel_id)
              
              response_parts = [f"✅ Configuration saved! Users will receive {target_role.mention} when joining voice channels."]
              
              if new_log_channel_id:
                  log_channel_obj = guild.get_channel(new_log_channel_id)
                  response_parts.append(f"Role changes will be logged to {log_channel_obj.mention}.")
              
              return await interaction.followup.send("\n".join(response_parts), ephemeral=True)
          else:
              return await interaction.followup.send("❌ Failed to save configuration.", ephemeral=True)

  @app_commands.command(
      name="vc-role-sync",
      description="Manually trigger a role sync check for voice channel roles"
  )
  async def vc_role_sync(self, interaction: discord.Interaction) -> None:
      await interaction.response.defer(ephemeral=True)
      
      if not self._check_permissions(interaction):
          return await interaction.followup.send("❌ You need administrator permissions to use this command.", ephemeral=True)
      
      guild = interaction.guild
      if not guild:
          return await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
      
      guild_id = guild.id
      config = self.vc_role_configs.get(guild_id)
      
      if not config:
          return await interaction.followup.send("❌ No voice channel role is configured for this server.", ephemeral=True)
      
      role_id, log_channel_id = config
      role = guild.get_role(role_id)
      
      if not role:
          await self._delete_config(guild_id)
          return await interaction.followup.send("❌ The configured role no longer exists. Configuration has been cleaned up.", ephemeral=True)
      
      if not await self._check_bot_permissions(guild, role):
          return await interaction.followup.send("❌ I don't have permission to manage the configured role.", ephemeral=True)
      
      await interaction.followup.send("🔄 Starting manual role sync...", ephemeral=True)
      
      try:
          await self._sync_guild_roles(guild, role, log_channel_id)
          await interaction.edit_original_response(content="✅ Manual role sync completed successfully!")
      except Exception as e:
          logger.error(f"Manual sync failed for guild {guild_id}: {e}")
          await interaction.edit_original_response(content="❌ Role sync failed. Check bot logs for details.")

  @commands.Cog.listener()
  async def on_voice_state_update(
      self,
      member: discord.Member,
      before: discord.VoiceState,
      after: discord.VoiceState
  ) -> None:
      if member.bot or member.id in self.processing_users:
          return

      guild_id = member.guild.id
      config = self.vc_role_configs.get(guild_id)
      if not config:
          return

      role_id, log_channel_id = config
      role = member.guild.get_role(role_id)
      if not role:
          # Clean up invalid role
          await self._delete_config(guild_id)
          return

      # Prevent concurrent processing for the same user
      self.processing_users.add(member.id)
      try:
          # Check actual voice channel status
          was_in_vc = before.channel is not None
          is_in_vc = after.channel is not None
          user_has_role = role in member.roles

          if is_in_vc and not user_has_role:
              # User joined a VC and doesn't have role - add it
              await self._add_role_with_retry(member, role, "Voice channel role", log_channel_id)
          elif not is_in_vc and user_has_role and was_in_vc:
              # User left all VCs and has role - remove it
              await self._remove_role_with_retry(member, role, "Left voice channels", log_channel_id)
      
      finally:
          # Always remove from processing set
          self.processing_users.discard(member.id)

  @tasks.loop(minutes=15)
  async def periodic_role_sync(self) -> None:
      """Periodic check to ensure role assignments are correct."""
      try:
          sync_tasks = []
          
          for guild_id, (role_id, log_channel_id) in list(self.vc_role_configs.items()):
              guild = self.bot.get_guild(guild_id)
              if not guild:
                  continue
                  
              role = guild.get_role(role_id)
              if not role:
                  continue
              
              # Check permissions before processing
              if not await self._check_bot_permissions(guild, role):
                  continue
              
              sync_tasks.append(self._sync_guild_roles(guild, role, log_channel_id))
          
          # Process guilds in batches
          batch_size = 3
          for i in range(0, len(sync_tasks), batch_size):
              batch = sync_tasks[i:i + batch_size]
              await asyncio.gather(*batch, return_exceptions=True)
              if i + batch_size < len(sync_tasks):
                  await asyncio.sleep(2)
                  
          logger.info(f"Completed periodic role sync for {len(sync_tasks)} guilds")
          
      except Exception as e:
          logger.error(f"Error in periodic role sync: {e}")

  async def _sync_guild_roles(self, guild: discord.Guild, role: discord.Role, log_channel_id: Optional[int]) -> None:
      """Sync roles for a specific guild."""
      try:
          # Get all members currently in voice channels
          members_in_vc = set()
          for vc in guild.voice_channels:
              for member in vc.members:
                  if not member.bot:
                      members_in_vc.add(member.id)
          
          # Get all members with the VC role
          members_with_role = set()
          for member in role.members:
              if not member.bot:
                  members_with_role.add(member.id)
          
          # Find discrepancies
          should_have_role = members_in_vc - members_with_role
          should_not_have_role = members_with_role - members_in_vc
          
          sync_tasks = []
          
          # Add role to members who should have it
          for member_id in should_have_role:
              member = guild.get_member(member_id)
              if member and member_id not in self.processing_users:
                  sync_tasks.append(self._add_role_with_retry(member, role, "Periodic sync - add", log_channel_id))
          
          # Remove role from members who shouldn't have it
          for member_id in should_not_have_role:
              member = guild.get_member(member_id)
              if member and member_id not in self.processing_users:
                  sync_tasks.append(self._remove_role_with_retry(member, role, "Periodic sync - remove", log_channel_id))
          
          # Process in small batches to avoid rate limits
          if sync_tasks:
              batch_size = 3
              for i in range(0, len(sync_tasks), batch_size):
                  batch = sync_tasks[i:i + batch_size]
                  await asyncio.gather(*batch, return_exceptions=True)
                  if i + batch_size < len(sync_tasks):
                      await asyncio.sleep(1)
              
              logger.info(f"Synced {len(sync_tasks)} role changes for guild {guild.name}")
              
      except Exception as e:
          logger.error(f"Error syncing roles for guild {guild.name}: {e}")

  @tasks.loop(hours=12)
  async def check_role_validity(self) -> None:
      """Check for invalid roles and guilds, cleaning up as needed."""
      try:
          invalid_guilds = []
          
          for guild_id, (role_id, log_channel_id) in list(self.vc_role_configs.items()):
              guild = self.bot.get_guild(guild_id)
              if not guild:
                  invalid_guilds.append(guild_id)
                  continue
                  
              role = guild.get_role(role_id)
              if not role:
                  invalid_guilds.append(guild_id)
                  # Clean up role from users who might still have it
                  for vc in guild.voice_channels:
                      for member in vc.members:
                          if not member.bot:
                              # Try to remove any roles that match the old role_id
                              for user_role in member.roles:
                                  if user_role.id == role_id:
                                      await self._remove_role_with_retry(member, user_role, "Invalid role cleanup")
                                      break

          # Batch delete invalid configurations
          if invalid_guilds:
              try:
                  async with self.get_db_connection() as db:
                      await db.executemany(
                          "DELETE FROM vc_roles WHERE guild_id = ?",
                          [(guild_id,) for guild_id in invalid_guilds]
                      )
                      await db.commit()
                      
                  for guild_id in invalid_guilds:
                      self.vc_role_configs.pop(guild_id, None)
                      
                  logger.info(f"Cleaned up {len(invalid_guilds)} invalid configurations")
              except Exception as e:
                  logger.error(f"Failed to batch delete invalid configs: {e}")
                  
      except Exception as e:
          logger.error(f"Error in role validity check: {e}")

  @check_role_validity.before_loop
  async def before_check_role_validity(self) -> None:
      await self.bot.wait_until_ready()

  @periodic_role_sync.before_loop
  async def before_periodic_role_sync(self) -> None:
      await self.bot.wait_until_ready()

async def setup(bot: commands.Bot) -> None:
  await bot.add_cog(VCRoles(bot))
