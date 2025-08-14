import discord
from discord.ext import commands
import aiosqlite
import os
import asyncio
from typing import Dict, List, Optional, Union

class RoleManager(commands.Cog, name="Role Management"):
    """Role management system with custom role names and required role for permissions"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db_folder = "database"
        self.db_path = os.path.join(self.db_folder, "reqrole.db")
        self.bot.loop.create_task(self.setup_database())
        
    async def setup_database(self):
        """Set up the database and tables if they don't exist"""
        # Create database directory if it doesn't exist
        if not os.path.exists(self.db_folder):
            os.makedirs(self.db_folder)
            
        async with aiosqlite.connect(self.db_path) as db:
            # Create table for required role (reqrole)
            await db.execute('''
                CREATE TABLE IF NOT EXISTS reqrole (
                    guild_id INTEGER PRIMARY KEY,
                    role_id INTEGER NOT NULL
                )
            ''')
            
            # Create table for custom role mappings
            await db.execute('''
                CREATE TABLE IF NOT EXISTS custom_roles (
                    guild_id INTEGER NOT NULL,
                    custom_name TEXT NOT NULL,
                    role_id INTEGER NOT NULL,
                    description TEXT,
                    PRIMARY KEY (guild_id, custom_name, role_id)
                )
            ''')
            
            # Create table for log channels
            await db.execute('''
                CREATE TABLE IF NOT EXISTS log_channels (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL
                )
            ''')
            
            await db.commit()
    
    async def get_reqrole(self, guild_id: int) -> Optional[int]:
        """Get the required role ID for a guild"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('SELECT role_id FROM reqrole WHERE guild_id = ?', (guild_id,)) as cursor:
                result = await cursor.fetchone()
                return result[0] if result else None
    
    async def get_custom_roles(self, guild_id: int, custom_name: str) -> List[int]:
        """Get the role IDs mapped to a custom name"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT role_id FROM custom_roles WHERE guild_id = ? AND custom_name = ?', 
                (guild_id, custom_name.lower())
            ) as cursor:
                results = await cursor.fetchall()
                return [result[0] for result in results] if results else []
    
    async def get_log_channel(self, guild_id: int) -> Optional[int]:
        """Get the log channel ID for a guild"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('SELECT channel_id FROM log_channels WHERE guild_id = ?', (guild_id,)) as cursor:
                result = await cursor.fetchone()
                return result[0] if result else None
    
    async def get_all_custom_roles(self, guild_id: int) -> Dict[str, List[Dict[str, Union[int, str]]]]:
        """Get all custom role mappings for a guild"""
        result = {}
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT custom_name, role_id, description FROM custom_roles WHERE guild_id = ?', 
                (guild_id,)
            ) as cursor:
                async for row in cursor:
                    custom_name, role_id, description = row
                    if custom_name not in result:
                        result[custom_name] = []
                    result[custom_name].append({
                        "role_id": role_id,
                        "description": description
                    })
        return result
    
    async def log_role_change(self, guild: discord.Guild, user: discord.Member, 
                             roles: List[discord.Role], action: str, mod: discord.Member = None):
        """Log role changes to the designated log channel"""
        log_channel_id = await self.get_log_channel(guild.id)
        if not log_channel_id:
            return
            
        log_channel = guild.get_channel(log_channel_id)
        if not log_channel:
            return
        
        # Get moderator info
        moderator_info = "System" if not mod else f"{mod.mention} ({mod.name}#{mod.discriminator} | {mod.id})"
        
        # Format roles with names and IDs
        role_info = []
        for role in roles:
            role_info.append(f"{role.name} (ID: {role.id})")
        role_text = "\n• ".join(role_info)
        
        # Create embed with detailed information
        embed = discord.Embed(
            title=f"Role {action}",
            description=f"**Roles were {action.lower()} from the user**",
            color=discord.Color.green() if action == "Added" else discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        
        # Add target user field
        embed.add_field(
            name="Target User",
            value=f"**Name:** {user.name}#{user.discriminator}\n"
                  f"**Mention:** {user.mention}\n"
                  f"**ID:** {user.id}",
            inline=False
        )
        
        # Add roles field
        embed.add_field(
            name=f"Roles {action}",
            value=f"• {role_text}",
            inline=False
        )
        
        # Add moderator field
        embed.add_field(
            name="Action Performed By",
            value=moderator_info,
            inline=False
        )
        
        # Add footer with timestamp
        embed.set_footer(text=f"Action ID: {discord.utils.utcnow().timestamp():.0f}")
        
        # Add user avatar
        embed.set_thumbnail(url=user.display_avatar.url)
        
        await log_channel.send(embed=embed)
    
    async def has_reqrole(self, ctx: commands.Context) -> bool:
        """Check if the user has the required role to manage roles"""
        if ctx.author.guild_permissions.administrator:
            return True
            
        reqrole_id = await self.get_reqrole(ctx.guild.id)
        if not reqrole_id:
            await ctx.send("No required role has been set up. Administrators can set it up using `.setupreqrole`.")
            return False
            
        reqrole = ctx.guild.get_role(reqrole_id)
        if not reqrole:
            await ctx.send("The configured required role no longer exists.")
            return False
            
        if reqrole not in ctx.author.roles:
            await ctx.send(f"You need the {reqrole.name} role to use this command.")
            return False
            
        return True
    
    @commands.command(name="setupreqrole")
    @commands.has_permissions(administrator=True)
    async def setup_reqrole(self, ctx, role: discord.Role):
        """Set up the required role for role management
        
        This role will be required to assign or remove roles from users.
        Only administrators can set this up.
        
        Usage: .setupreqrole @role
        Example: .setupreqrole @RoleManager
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'INSERT OR REPLACE INTO reqrole (guild_id, role_id) VALUES (?, ?)',
                (ctx.guild.id, role.id)
            )
            await db.commit()
        
        await ctx.send(f"Required role for role management has been set to {role.name}.")
    
    @commands.command(name="setrole")
    @commands.has_permissions(administrator=True)
    async def set_custom_role(self, ctx, custom_name: str, role: discord.Role, *, description: str = None):
        """Map a custom name to an existing role
        
        Creates a command with the custom name that can be used to assign the role.
        Only administrators can create these mappings.
        
        Usage: .setrole <custom_name> @role [description]
        Example: .setrole staff @StaffMember Staff members can moderate chat
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'INSERT OR REPLACE INTO custom_roles (guild_id, custom_name, role_id, description) VALUES (?, ?, ?, ?)',
                (ctx.guild.id, custom_name.lower(), role.id, description)
            )
            await db.commit()
        
        await ctx.send(f"Custom role '{custom_name}' has been mapped to {role.name}.")
    
    @commands.command(name="removerole")
    @commands.has_permissions(administrator=True)
    async def remove_custom_role(self, ctx, custom_name: str, role: discord.Role = None):
        """Remove a role mapping or all mappings for a custom name
        
        If a role is specified, only that role mapping will be removed.
        If no role is specified, all mappings for the custom name will be removed.
        
        Usage: .removerole <custom_name> [@role]
        Example: .removerole staff @StaffMember
        """
        async with aiosqlite.connect(self.db_path) as db:
            if role:
                await db.execute(
                    'DELETE FROM custom_roles WHERE guild_id = ? AND custom_name = ? AND role_id = ?',
                    (ctx.guild.id, custom_name.lower(), role.id)
                )
                await ctx.send(f"Removed mapping of '{custom_name}' to {role.name}.")
            else:
                await db.execute(
                    'DELETE FROM custom_roles WHERE guild_id = ? AND custom_name = ?',
                    (ctx.guild.id, custom_name.lower())
                )
                await ctx.send(f"Removed all mappings for custom role '{custom_name}'.")
            
            await db.commit()
    
    @commands.command(name="resetserver")
    @commands.has_permissions(administrator=True)
    async def reset_server(self, ctx):
        """Reset all role configurations for this server
        
        Deletes all reqrole settings, custom role mappings, and log channel configurations.
        Requires confirmation by typing 'confirm'.
        
        Usage: .resetserver
        """
        # Ask for confirmation
        confirm_msg = await ctx.send("⚠️ **WARNING**: This will delete ALL role configurations for this server. "
                                    "Type `confirm` within 30 seconds to proceed.")
        
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == "confirm"
        
        try:
            await self.bot.wait_for('message', check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await confirm_msg.edit(content="Server reset cancelled.")
            return
        
        # Delete all server data
        async with aiosqlite.connect(self.db_path) as db:
            # Delete reqrole
            await db.execute('DELETE FROM reqrole WHERE guild_id = ?', (ctx.guild.id,))
            
            # Delete custom roles
            await db.execute('DELETE FROM custom_roles WHERE guild_id = ?', (ctx.guild.id,))
            
            # Delete log channel
            await db.execute('DELETE FROM log_channels WHERE guild_id = ?', (ctx.guild.id,))
            
            await db.commit()
        
        await ctx.send("✅ All role configurations for this server have been reset.")
    
    @commands.command(name="deletemappedrole")
    @commands.has_permissions(administrator=True)
    async def delete_mapped_role(self, ctx, role_id: int):
        """Delete a specific role mapping by role ID
        
        Removes a role from all custom name mappings by its ID.
        Useful when the role no longer exists or needs to be removed from all mappings.
        
        Usage: .deletemappedrole <role_id>
        Example: .deletemappedrole 123456789012345678
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Check if the role exists in any mapping
            async with db.execute(
                'SELECT custom_name FROM custom_roles WHERE guild_id = ? AND role_id = ?',
                (ctx.guild.id, role_id)
            ) as cursor:
                mappings = await cursor.fetchall()
            
            if not mappings:
                await ctx.send(f"No mappings found for role ID {role_id}.")
                return
            
            # Delete the role from all mappings
            await db.execute(
                'DELETE FROM custom_roles WHERE guild_id = ? AND role_id = ?',
                (ctx.guild.id, role_id)
            )
            await db.commit()
        
        # Get the role object if it exists
        role = ctx.guild.get_role(role_id)
        role_name = role.name if role else f"ID:{role_id}"
        
        # Format the custom names
        custom_names = [f"`.{mapping[0]}`" for mapping in mappings]
        
        await ctx.send(f"✅ Deleted role {role_name} from the following mappings: {', '.join(custom_names)}")
    
    @commands.command(name="setlogchannel")
    @commands.has_permissions(administrator=True)
    async def set_log_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for role change logs
        
        All role assignments and removals will be logged to this channel.
        
        Usage: .setlogchannel #channel
        Example: .setlogchannel #role-logs
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'INSERT OR REPLACE INTO log_channels (guild_id, channel_id) VALUES (?, ?)',
                (ctx.guild.id, channel.id)
            )
            await db.commit()
        
        await ctx.send(f"Log channel has been set to {channel.mention}.")
    
    @commands.command(name="role", aliases=["roles"])
    async def show_role_commands(self, ctx):
        """Show all available role management commands
        
        Displays a list of all commands available in the role management system.
        
        Usage: .role
        """
        embed = discord.Embed(
            title="Role Management Commands",
            description="Here are all the available role management commands:",
            color=discord.Color.blue()
        )
        
        # Admin commands
        admin_cmds = [
            "`.setupreqrole @role` - Set the required role for role management",
            "`.setrole <custom_name> @role [description]` - Map a custom name to a role",
            "`.removerole <custom_name> [@role]` - Remove a role mapping or all mappings",
            "`.setlogchannel #channel` - Set the channel for role change logs",
            "`.clearroles @user` - Remove all custom roles from a user",
            "`.resetserver` - Reset all role configurations for the server",
            "`.deletemappedrole <role_id>` - Delete a specific role mapping by ID",
            "`.setupmultirole <custom_name> @role1 @role2...` - Map multiple roles to one name"
        ]
        embed.add_field(name="Admin Commands", value="\n".join(admin_cmds), inline=False)
        
        # User commands (with reqrole)
        user_cmds = [
            "`.role` - Show this help message",
            "`.rolelist` - List all custom role mappings",
            "`.roledesc <custom_name>` - Show description for a custom role",
            "`.custom_name @user` - Toggle a custom role for a user (e.g., `.staff @user`)"
        ]
        embed.add_field(name="Role Manager Commands", value="\n".join(user_cmds), inline=False)
        
        await ctx.send(embed=embed)
    
    @commands.command(name="rolelist")
    async def list_custom_roles(self, ctx):
        """List all custom role mappings in the server
        
        Shows all custom role names and the roles they are mapped to.
        
        Usage: .rolelist
        """
        custom_roles = await self.get_all_custom_roles(ctx.guild.id)
        
        if not custom_roles:
            await ctx.send("No custom roles have been set up yet.")
            return
        
        embed = discord.Embed(
            title="Custom Role Mappings",
            description="Here are all the custom role mappings:",
            color=discord.Color.blue()
        )
        
        for custom_name, roles in custom_roles.items():
            role_mentions = []
            for role_data in roles:
                role = ctx.guild.get_role(role_data["role_id"])
                if role:
                    role_mentions.append(f"{role.mention} (ID: {role.id})")
                else:
                    role_mentions.append(f"Unknown Role (ID: {role_data['role_id']})")
            
            if role_mentions:
                embed.add_field(
                    name=f"`.{custom_name}`",
                    value=", ".join(role_mentions),
                    inline=False
                )
        
        await ctx.send(embed=embed)
    
    @commands.command(name="roledesc")
    async def show_role_description(self, ctx, custom_name: str):
        """Show description for a custom role
        
        Displays the description for a custom role if one was provided.
        
        Usage: .roledesc <custom_name>
        Example: .roledesc staff
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT role_id, description FROM custom_roles WHERE guild_id = ? AND custom_name = ?',
                (ctx.guild.id, custom_name.lower())
            ) as cursor:
                roles = await cursor.fetchall()
        
        if not roles:
            await ctx.send(f"No custom role with the name '{custom_name}' exists.")
            return
        
        embed = discord.Embed(
            title=f"Role Description: {custom_name}",
            color=discord.Color.blue()
        )
        
        for role_id, description in roles:
            role = ctx.guild.get_role(role_id)
            if role:
                embed.add_field(
                    name=role.name,
                    value=description or "No description provided",
                    inline=False
                )
        
        await ctx.send(embed=embed)
    
    @commands.command(name="clearroles")
    @commands.has_permissions(administrator=True)
    async def clear_all_custom_roles(self, ctx, user: discord.Member):
        """Remove all custom roles from a user
        
        Removes all roles that have been mapped with custom names from the user.
        
        Usage: .clearroles @user
        Example: .clearroles @Username
        """
        custom_roles = await self.get_all_custom_roles(ctx.guild.id)
        removed_roles = []
        
        for custom_name, roles_data in custom_roles.items():
            for role_data in roles_data:
                role = ctx.guild.get_role(role_data["role_id"])
                if role and role in user.roles:
                    await user.remove_roles(role)
                    removed_roles.append(role)
        
        if removed_roles:
            await ctx.send(f"Removed {len(removed_roles)} custom roles from {user.mention}.")
            await self.log_role_change(ctx.guild, user, removed_roles, "Removed", ctx.author)
        else:
            await ctx.send(f"{user.mention} doesn't have any custom roles.")
    
    @commands.command(name="setupmultirole")
    @commands.has_permissions(administrator=True)
    async def setup_multi_role(self, ctx, custom_name: str, *roles: discord.Role):
        """Map multiple roles to a custom name at once
        
        Creates a command with the custom name that adds/removes multiple roles.
        
        Usage: .setupmultirole <custom_name> @role1 @role2...
        Example: .setupmultirole staff @Moderator @Helper
        """
        if not roles:
            await ctx.send("You must specify at least one role.")
            return
        
        async with aiosqlite.connect(self.db_path) as db:
            # First remove existing mappings
            await db.execute(
                'DELETE FROM custom_roles WHERE guild_id = ? AND custom_name = ?',
                (ctx.guild.id, custom_name.lower())
            )
            
            # Add new mappings
            for role in roles:
                await db.execute(
                    'INSERT INTO custom_roles (guild_id, custom_name, role_id) VALUES (?, ?, ?)',
                    (ctx.guild.id, custom_name.lower(), role.id)
                )
            
            await db.commit()
        
        role_mentions = ", ".join(role.mention for role in roles)
        await ctx.send(f"Custom role '{custom_name}' has been mapped to: {role_mentions}")
    
    @commands.Cog.listener()
    async def on_message(self, message):
        # Skip messages from bots and non-command messages
        if message.author.bot or not message.content.startswith('.'):
            return
            
        # Check if this might be a custom role command
        ctx = await self.bot.get_context(message)
        if ctx.command is not None or not hasattr(ctx, 'guild') or ctx.guild is None:
            return
            
        # Extract the command name without the prefix
        cmd = message.content.split()[0][1:].lower()
        
        # Check if the command exists as a custom role
        custom_roles = await self.get_custom_roles(ctx.guild.id, cmd)
        
        if custom_roles and len(message.mentions) == 1:
            # This is a custom role command
            # Check if user has admin or required role
            has_permission = False
            if ctx.author.guild_permissions.administrator:
                has_permission = True
            else:
                reqrole_id = await self.get_reqrole(ctx.guild.id)
                if reqrole_id:
                    reqrole = ctx.guild.get_role(reqrole_id)
                    if reqrole and reqrole in ctx.author.roles:
                        has_permission = True
            
            if not has_permission:
                # Get the reqrole name for better error message
                reqrole_id = await self.get_reqrole(ctx.guild.id)
                if reqrole_id:
                    reqrole = ctx.guild.get_role(reqrole_id)
                    role_name = reqrole.name if reqrole else "the required role"
                    await ctx.send(f"You need {role_name} to use this command.")
                else:
                    await ctx.send("You don't have permission to use this command.")
                return
            
            target_user = message.mentions[0]
            roles_to_toggle = []
            
            for role_id in custom_roles:
                role = ctx.guild.get_role(role_id)
                if role:
                    roles_to_toggle.append(role)
            
            if not roles_to_toggle:
                await ctx.send(f"No valid roles found for '{cmd}'.")
                return
            
            # Check if user has any of the roles
            has_any_role = any(role in target_user.roles for role in roles_to_toggle)
            
            if has_any_role:
                # Remove roles
                await target_user.remove_roles(*roles_to_toggle)
                await ctx.send(f"Removed {', '.join(role.name for role in roles_to_toggle)} from {target_user.mention}.")
                await self.log_role_change(ctx.guild, target_user, roles_to_toggle, "Removed", ctx.author)
            else:
                # Add roles
                await target_user.add_roles(*roles_to_toggle)
                await ctx.send(f"Added {', '.join(role.name for role in roles_to_toggle)} to {target_user.mention}.")
                await self.log_role_change(ctx.guild, target_user, roles_to_toggle, "Added", ctx.author)

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            # The on_message event will handle custom role commands
            pass

async def setup(bot):
    await bot.add_cog(RoleManager(bot))
