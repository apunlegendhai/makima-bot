import discord
from discord.ext import commands, tasks
import logging
import os
from dotenv import load_dotenv
from keep_alive import keep_alive  # Flask server to keep bot alive if needed
import asyncio

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not DISCORD_TOKEN:
    raise ValueError("No DISCORD_TOKEN found in .env file")

# Set up logging
logging.basicConfig(filename='bot.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Intents setup
intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # Enable Message Content Intent

bot = commands.Bot(command_prefix=".", intents=intents)

# List of cogs to load
cogs = [
    "cogs.status_changer",
    "cogs.dragmee",
    "cogs.confess",
    "cogs.afk_cog",
    "cogs.av",
    "cogs.giveaway",
    "cogs.key_generator",
    "cogs.purge",
    "cogs.reqrole",
    "cogs.stats",
    "cogs.steal",
    "cogs.sticky",
    "cogs.thread",
    "cogs.AvatarBannerUpdater",
    "cogs.autoresponder",
]

async def load_cogs():
    """Load all specified cogs."""
    for cog in cogs:
        try:
            if cog in bot.extensions:
                await bot.unload_extension(cog)
            await bot.load_extension(cog)
            logging.info(f"{cog} has been loaded.")
        except commands.errors.ExtensionNotFound:
            logging.error(f"{cog} not found. Ensure it is in the correct directory.")
        except commands.errors.ExtensionFailed as e:
            logging.error(f"Failed to load {cog}. Error: {e}")

@bot.event
async def on_ready():
    """When the bot is ready, print the bot info, sync commands, and list registered commands."""
    print(f'Logged in as {bot.user}')
    
    # Load cogs before syncing commands
    await load_cogs()

    # Sync slash commands with Discord
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Error syncing commands: {e}")
        logging.error(f"Error syncing commands: {e}")

    # Print all registered slash commands
    print("Registered slash commands:")
    for command in bot.tree.get_commands():
        print(f"- {command.name}")

@bot.event
async def on_message(message):
    """Handle invalid commands and process commands."""
    if message.content.startswith(".."):
        return  # Ignore incorrect prefixes
    await bot.process_commands(message)

@bot.event
async def on_command_error(ctx, error):
    """Custom error handling for commands."""
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Command not recognized. Use `.help` to see the available commands.")
        logging.warning(f"Command not found: {ctx.message.content}")
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"Command is on cooldown. Try again in {error.retry_after:.2f} seconds.")
    else:
        logging.error(f"Unexpected error in command {ctx.command}: {error}")
        raise error

@bot.event
async def on_error(event, *args, **kwargs):
    """Handle any unexpected errors."""
    logging.error(f"Unexpected error occurred: {event} | {args} | {kwargs}")
    print(f"Unexpected error occurred: {event}")

@bot.event
async def on_socket_response(msg):
    """Handle rate-limiting."""
    if msg.get('op') == 7:  # Detect rate-limiting (not guaranteed for this opcode)
        retry_after = msg.get('d', {}).get('retry_after')
        if retry_after:
            logging.warning(f"Rate-limited: Retrying after {retry_after} seconds.")
            await asyncio.sleep(retry_after)

# Latency Ping Command
@bot.command()
@commands.cooldown(1, 5, commands.BucketType.user)
async def ping(ctx):
    """A latency ping command."""
    latency = bot.latency  # Bot's latency in seconds
    await ctx.send(f'<a:sukoon_greendot:1322894177775783997> Latency is {latency * 1000:.2f}ms')

# Start Flask (if you want to keep the bot alive on platforms like Replit)
keep_alive()

# Graceful shutdown handling
async def shutdown():
    """Shut down the bot gracefully."""
    print("Shutting down bot...")
    await bot.close()

import signal
signal.signal(signal.SIGINT, lambda *_: asyncio.create_task(shutdown()))

# Start the bot
bot.run(DISCORD_TOKEN)
