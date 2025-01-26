import discord
import logging
from discord.ext import commands
from discord.ext.commands import CommandNotFound, CommandOnCooldown, CommandRegistrationError
from pymongo import MongoClient
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# MongoDB setup
MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    raise ValueError("Missing MongoDB URL in environment variables.")
mongo_client = MongoClient(MONGO_URL)
db = mongo_client["role_management"]

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

EMBED_COLOR = 0x2f2136  # Default embed color


class RoleManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        logger.info("RoleManagement cog has been loaded.")

    async def has_reqrole(self, ctx, guild_data):
        """Check if the user has the required role."""
        reqrole_id = guild_data.get("reqrole_id")
        if not reqrole_id:
            await ctx.send(embed=discord.Embed(description="The required role hasn't been set up yet!", color=EMBED_COLOR))
            return False
        if reqrole_id not in [role.id for role in ctx.author.roles]:
            await ctx.send(embed=discord.Embed(description="You do not have the required role to use this command.", color=EMBED_COLOR))
            return False
        return True

    async def get_guild_data(self, guild_id):
        """Retrieve or create default data for a guild."""
        guild_data = db.guilds.find_one({"guild_id": guild_id})
        if not guild_data:
            guild_data = {
                "guild_id": guild_id,
                "reqrole_id": None,
                "role_mappings": {},
                "log_channel_id": None,
                "role_assignment_limit": 5,
            }
            db.guilds.insert_one(guild_data)
        return guild_data

    async def update_guild_data(self, guild_id, data):
        """Update guild data in the database."""
        db.guilds.update_one({"guild_id": guild_id}, {"$set": data})

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Logged in as {self.bot.user}")

    @commands.command()
    async def setreqrole(self, ctx, role: discord.Role):
        """Set the required role for assigning/removing roles."""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send(embed=discord.Embed(description="You do not have permission to use this command.", color=EMBED_COLOR))
            return
        guild_data = await self.get_guild_data(ctx.guild.id)
        await self.update_guild_data(ctx.guild.id, {"reqrole_id": role.id})
        await ctx.send(embed=discord.Embed(description=f"Required role set to {role.name}.", color=EMBED_COLOR))

    @commands.command()
    async def setrole(self, ctx, custom_name: str, role: discord.Role):
        """Map a custom role name to an existing role."""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send(embed=discord.Embed(description="You do not have permission to use this command.", color=EMBED_COLOR))
            return

        guild_data = await self.get_guild_data(ctx.guild.id)
        role_mappings = guild_data["role_mappings"]
        role_mappings[custom_name] = role.id
        await self.update_guild_data(ctx.guild.id, {"role_mappings": role_mappings})
        await ctx.send(embed=discord.Embed(description=f"Mapped custom role name \"{custom_name}\" to role {role.name}.", color=EMBED_COLOR))

    @commands.command()
    async def reset_roles(self, ctx):
        """Reset all mapped roles in the server."""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send(embed=discord.Embed(description="You do not have permission to use this command.", color=EMBED_COLOR))
            return

        await self.update_guild_data(ctx.guild.id, {"role_mappings": {}})
        await ctx.send(embed=discord.Embed(description="All role mappings have been reset.", color=EMBED_COLOR))

    @commands.command()
    async def setlogchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel for logging role assignments."""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send(embed=discord.Embed(description="You do not have permission to use this command.", color=EMBED_COLOR))
            return

        await self.update_guild_data(ctx.guild.id, {"log_channel_id": channel.id})
        await ctx.send(embed=discord.Embed(description=f"Log channel set to {channel.name}.", color=EMBED_COLOR))

    @commands.command()
    async def set_role_limit(self, ctx, limit: int):
        """Set the maximum number of custom roles a user can have."""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send(embed=discord.Embed(description="You do not have permission to use this command.", color=EMBED_COLOR))
            return

        await self.update_guild_data(ctx.guild.id, {"role_assignment_limit": limit})
        await ctx.send(embed=discord.Embed(description=f"Role assignment limit set to {limit}.", color=EMBED_COLOR))


async def setup(bot):
    await bot.add_cog(RoleManagement(bot))
