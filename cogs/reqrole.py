import discord
import logging
import json
from discord.ext import commands
from discord.ext.commands import CommandNotFound, CommandOnCooldown, CommandRegistrationError

# Setting up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

EMBED_COLOR = 0x2f2136  # Constant embed color


class RoleManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reqrole_id = None  # Role required to assign/remove roles
        self.role_mappings = {}  # Custom role names mapped to role IDs
        self.log_channel_id = None  # Log channel ID for role changes
        self.role_assignment_limit = 5  # Default limit for custom roles per user
        self.load_data()

    def load_data(self):
        """Load role mappings from a file."""
        try:
            with open("role_mappings.json", "r") as f:
                data = json.load(f)
                self.reqrole_id = data.get("reqrole_id")
                self.role_mappings = data.get("role_mappings", {})
                self.log_channel_id = data.get("log_channel_id")
                self.role_assignment_limit = data.get("role_assignment_limit", 5)  # Set limit if provided
        except FileNotFoundError:
            logger.info("No existing role mappings file found. Starting fresh.")
        except json.JSONDecodeError as e:
            logger.error(f"Error loading JSON data: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")

    def save_data(self):
        """Save role mappings to a file."""
        data = {
            "reqrole_id": self.reqrole_id,
            "role_mappings": self.role_mappings,
            "log_channel_id": self.log_channel_id,
            "role_assignment_limit": self.role_assignment_limit  # Save role assignment limit
        }
        try:
            with open("role_mappings.json", "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.error(f"Failed to save data: {e}")

    async def has_reqrole(self, ctx):
        """Check if the user has the required role."""
        if self.reqrole_id is None:
            await ctx.send(embed=discord.Embed(description="<:sukoon_info:1323251063910043659> | The required role hasn't been set up yet!", color=EMBED_COLOR))
            return False
        if self.reqrole_id not in [role.id for role in ctx.author.roles]:
            await ctx.send(embed=discord.Embed(description="<:sukoon_cross:1322894630684983307> | You do not have the required role to use this command.", color=EMBED_COLOR))
            return False
        return True

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f'Logged in as {self.bot.user}')
        self.generate_dynamic_commands()

    @commands.command()
    async def setreqrole(self, ctx, role: discord.Role):
        """Set the required role for assigning/removing roles."""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send(embed=discord.Embed(description="<:sukoon_info:1323251063910043659> | You do not have permission to use this command.", color=EMBED_COLOR))
            return
        self.reqrole_id = role.id
        self.save_data()
        logger.info(f'Required role set to {role.name}.')
        await ctx.send(embed=discord.Embed(description=f"<a:sukoon_whitetick:1323992464058482729> | Required role set to {role.name}.", color=EMBED_COLOR))

    @commands.command()
    async def setrole(self, ctx, custom_name: str, role: discord.Role):
        """Map a custom role name to an existing role."""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send(embed=discord.Embed(description="<:sukoon_info:1323251063910043659> | You do not have permission to use this command.", color=EMBED_COLOR))
            return

        if not ctx.guild.me.guild_permissions.manage_roles:
            await ctx.send(embed=discord.Embed(description="<:sukoon_info:1323251063910043659> | I do not have permission to manage roles.", color=EMBED_COLOR))
            return

        self.role_mappings[custom_name] = role.id
        self.save_data()
        logger.info(f'Mapped custom role name "{custom_name}" to role {role.name}.')
        await ctx.send(embed=discord.Embed(description=f"<a:sukoon_whitetick:1323992464058482729> | Mapped custom role name \"{custom_name}\" to role {role.name}.", color=EMBED_COLOR))

        self.generate_dynamic_commands()

    @commands.command()
    async def setlogchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel where role assignment/removal logs will be sent."""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send(embed=discord.Embed(description="<:sukoon_info:1323251063910043659> | You do not have permission to use this command.", color=EMBED_COLOR))
            return
        self.log_channel_id = channel.id
        self.save_data()
        logger.info(f'Log channel set to {channel.name}.')
        await ctx.send(embed=discord.Embed(description=f"<a:sukoon_whitetick:1323992464058482729> | Log channel set to {channel.name}.", color=EMBED_COLOR))

    @commands.command()
    async def role(self, ctx):
        """Shows all bot commands."""
        embed = discord.Embed(title="Role Management Commands", color=EMBED_COLOR)
        embed.add_field(name=".setreqrole [role]", value="Set the role required for assigning/removing roles.", inline=False)
        embed.add_field(name=".setrole [custom_name] [@role]", value="Map a custom name to a role.", inline=False)
        embed.add_field(name=".setlogchannel [channel]", value="Set the log channel for role actions.", inline=False)
        embed.add_field(name=".[custom_name] [@user]", value="Assign/remove the mapped role to/from a user.", inline=False)
        embed.set_footer(text="Role Management Bot")
        await ctx.send(embed=embed)

    async def assign_or_remove_role(self, ctx, custom_name: str, member: discord.Member):
        """Assign or remove the mapped custom role from a user."""
        role_id = self.role_mappings.get(custom_name)
        if not role_id:
            await ctx.send(embed=discord.Embed(description=f"<:sukoon_info:1323251063910043659> | No role mapped to '{custom_name}'. Please map a role first.", color=EMBED_COLOR))
            return

        role = discord.utils.get(ctx.guild.roles, id=role_id)
        if not role:
            await ctx.send(embed=discord.Embed(description=f"<:sukoon_info:1323251063910043659> | The role mapped to '{custom_name}' no longer exists.", color=EMBED_COLOR))
            return

        if role.position >= ctx.guild.me.top_role.position:
            await ctx.send(embed=discord.Embed(description=f"<:sukoon_info:1323251063910043659> | I cannot assign/remove the role '{role.name}' as it is higher than or equal to my top role.", color=EMBED_COLOR))
            return

        if role in member.roles:
            await member.remove_roles(role)
            action = "removed"
        else:
            user_roles = [role.id for role in member.roles if role.id in self.role_mappings.values()]
            if len(user_roles) >= self.role_assignment_limit:
                await ctx.send(embed=discord.Embed(description=f"<:sukoon_info:1323251063910043659> | {member.mention} already has the maximum number of custom roles ({self.role_assignment_limit}).", color=EMBED_COLOR))
                return

            await member.add_roles(role)
            action = "assigned"

        await ctx.send(embed=discord.Embed(description=f"<a:sukoon_whitetick:1323992464058482729> | Successfully {action} the {custom_name} role to {member.mention}.", color=EMBED_COLOR))

        if self.log_channel_id:
            log_channel = self.bot.get_channel(self.log_channel_id)
            if log_channel:
                await log_channel.send(embed=discord.Embed(description=f"{ctx.author} {action} the {custom_name} role to {member.mention}.", color=EMBED_COLOR))

    async def dynamic_role_command(self, ctx, custom_name: str, member: discord.Member = None):
        """Dynamic command handler for assigning/removing roles."""
        if not await self.has_reqrole(ctx):
            return
        if not member:
            await ctx.send(embed=discord.Embed(description="<:sukoon_info:1323251063910043659> | Please mention a user.", color=EMBED_COLOR))
            return
        await self.assign_or_remove_role(ctx, custom_name, member)

    def generate_dynamic_commands(self):
        """Generate dynamic commands for each custom role."""
        for custom_name in list(self.role_mappings.keys()):
            if custom_name in self.bot.all_commands:
                logger.info(f"Command for '{custom_name}' already exists. Updating.")
                self.bot.remove_command(custom_name)

            async def command(ctx, member: discord.Member = None):
                await self.dynamic_role_command(ctx, custom_name, member)

            command.__name__ = f"role_{custom_name}"  # Ensuring unique command names
            command = commands.command()(command)
            logger.info(f"Registering command for role '{custom_name}'.")
            setattr(self, f"role_{custom_name}", command)
            self.bot.add_command(command)

    async def cog_load(self):
        """Load the cog and dynamically generate commands."""
        self.generate_dynamic_commands()

    @commands.command()
    async def set_role_limit(self, ctx, limit: int):
        """Set the maximum number of custom roles a user can have."""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send(embed=discord.Embed(description=" <:sukoon_info:1323251063910043659> | You do not have permission to use this command.", color=EMBED_COLOR))
            return
        if limit <= 0:
            await ctx.send(embed=discord.Embed(description="Role limit must be a positive integer.", color=EMBED_COLOR))
            return
        self.role_assignment_limit = limit
        self.save_data()
        await ctx.send(embed=discord.Embed(description=f"<a:sukoon_whitetick:1323992464058482729> | Role assignment limit set to {limit}.", color=EMBED_COLOR))

# Setup the cog
async def setup(bot):
    cog = RoleManagement(bot)
    await bot.add_cog(cog)
    await cog.cog_load()
