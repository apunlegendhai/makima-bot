import discord
from discord.ext import commands
import logging
import os
from dotenv import load_dotenv
import asyncio
import aiohttp
import sys
import signal
import subprocess
import ast
import re
from typing import Optional, Dict, List, Set
from logging.handlers import TimedRotatingFileHandler
from pyfiglet import Figlet
from discord import HTTPException

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# Directory constants
LOGS_DIR = "logs"
DATABASE_DIR = "database"
COGS_DIR = "cogs"

def setup_directories() -> None:
    for directory in (LOGS_DIR, DATABASE_DIR, COGS_DIR):
        os.makedirs(directory, exist_ok=True)

def analyze_codebase() -> Set[str]:
    """Analyze all Python files to extract required packages"""
    packages = set()
    
    # Standard library modules to ignore
    stdlib_modules = {
        'os', 'sys', 'asyncio', 'logging', 'json', 'datetime', 'time', 'random',
        'typing', 'collections', 'itertools', 'functools', 'pathlib', 'urllib',
        'http', 'socket', 'ssl', 'hashlib', 'base64', 'uuid', 'pickle', 're',
        'math', 'statistics', 'decimal', 'fractions', 'sqlite3', 'csv', 'xml',
        'html', 'email', 'signal', 'threading', 'multiprocessing', 'subprocess',
        'ast', 'importlib', 'pkgutil', 'warnings', 'traceback', 'io', 'tempfile'
    }
    
    def extract_imports_from_file(filepath: str) -> Set[str]:
        """Extract import statements from a Python file"""
        file_packages = set()
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Parse AST to find imports
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            pkg = alias.name.split('.')[0]
                            if pkg not in stdlib_modules:
                                file_packages.add(pkg)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            pkg = node.module.split('.')[0]
                            if pkg not in stdlib_modules:
                                file_packages.add(pkg)
            except SyntaxError:
                # Fallback to regex if AST parsing fails
                import_patterns = [
                    r'^\s*import\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                    r'^\s*from\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+import'
                ]
                for pattern in import_patterns:
                    matches = re.findall(pattern, content, re.MULTILINE)
                    for match in matches:
                        if match not in stdlib_modules:
                            file_packages.add(match)
        except Exception as e:
            logging.warning(f"Could not analyze {filepath}: {e}")
        
        return file_packages
    
    # Analyze main file
    if os.path.exists(__file__):
        packages.update(extract_imports_from_file(__file__))
    
    # Analyze cogs directory
    if os.path.exists(COGS_DIR):
        for root, dirs, files in os.walk(COGS_DIR):
            for file in files:
                if file.endswith('.py') and not file.startswith('__'):
                    filepath = os.path.join(root, file)
                    packages.update(extract_imports_from_file(filepath))
    
    # Known package mappings for common discord bot packages
    package_mappings = {
        'discord': 'discord.py',
        'asyncpg': 'asyncpg',
        'aiosqlite': 'aiosqlite',
        'motor': 'motor',
        'pymongo': 'pymongo',
        'wavelink': 'wavelink',
        'youtube_dl': 'youtube-dl',
        'yt_dlp': 'yt-dlp',
        'PIL': 'Pillow',
        'cv2': 'opencv-python',
        'numpy': 'numpy',
        'pandas': 'pandas',
        'matplotlib': 'matplotlib',
        'seaborn': 'seaborn',
        'requests': 'requests',
        'beautifulsoup4': 'beautifulsoup4',
        'lxml': 'lxml',
        'psutil': 'psutil',
        'colorama': 'colorama',
        'tabulate': 'tabulate',
        'humanfriendly': 'humanfriendly'
    }
    
    # Map package names to pip install names
    pip_packages = set()
    for pkg in packages:
        pip_packages.add(package_mappings.get(pkg, pkg))
    
    return pip_packages

def create_requirements_file(packages: Set[str]) -> None:
    """Create requirements.txt with detected packages"""
    # Base requirements that are always needed
    base_requirements = [
        'discord.py>=2.3.0',
        'python-dotenv>=0.19.0',
        'aiohttp>=3.8.0',
        'pyfiglet>=0.8.0'
    ]
    
    # Combine with detected packages
    all_packages = set(base_requirements)
    for pkg in packages:
        if not any(pkg in req for req in base_requirements):
            all_packages.add(pkg)
    
    requirements_content = '\n'.join(sorted(all_packages))
    
    with open('requirements.txt', 'w', encoding='utf-8') as f:
        f.write(requirements_content)
    
    print(f"\033[32m✓ Created requirements.txt with {len(all_packages)} packages\033[0m")
    logging.info(f"Created requirements.txt with packages: {', '.join(sorted(all_packages))}")

def install_dependencies() -> bool:
    """Install packages from requirements.txt"""
    try:
        print("\033[33mInstalling dependencies...\033[0m")
        
        # Check if pip is available
        subprocess.run([sys.executable, '-m', 'pip', '--version'], 
                      check=True, capture_output=True)
        
        # Install requirements
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'],
            capture_output=True, text=True, timeout=300
        )
        
        if result.returncode == 0:
            print("\033[32m✓ Dependencies installed successfully\033[0m")
            logging.info("Dependencies installed successfully")
            return True
        else:
            print(f"\033[31m✗ Failed to install dependencies:\033[0m")
            print(result.stderr)
            logging.error(f"Failed to install dependencies: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print("\033[31m✗ Installation timed out\033[0m")
        logging.error("Package installation timed out")
        return False
    except Exception as e:
        print(f"\033[31m✗ Error installing dependencies: {e}\033[0m")
        logging.error(f"Error installing dependencies: {e}")
        return False

def setup_environment() -> bool:
    """Setup environment with dependency analysis and installation"""
    print("\033[36m" + "=" * 50 + "\033[0m")
    print("\033[33mAnalyzing codebase and setting up environment...\033[0m")
    print("\033[36m" + "=" * 50 + "\033[0m")
    
    try:
        # Analyze codebase for dependencies
        print("\033[33mScanning codebase for dependencies...\033[0m")
        detected_packages = analyze_codebase()
        
        if detected_packages:
            print(f"\033[32m✓ Detected {len(detected_packages)} packages: {', '.join(sorted(detected_packages))}\033[0m")
        
        # Create requirements.txt
        create_requirements_file(detected_packages)
        
        # Ask user if they want to install dependencies
        try:
            install_choice = input("\033[33mInstall dependencies now? (y/N): \033[0m").strip().lower()
            if install_choice in ['y', 'yes']:
                success = install_dependencies()
                if not success:
                    print("\033[33mYou can manually install dependencies later with: pip install -r requirements.txt\033[0m")
                return success
            else:
                print("\033[33mSkipping installation. Run 'pip install -r requirements.txt' when ready.\033[0m")
                return True
        except (KeyboardInterrupt, EOFError):
            print("\033[33m\nSkipping installation. Run 'pip install -r requirements.txt' when ready.\033[0m")
            return True
            
    except Exception as e:
        print(f"\033[31m✗ Error during environment setup: {e}\033[0m")
        logging.error(f"Error during environment setup: {e}")
        return False

def setup_logging() -> None:
    class MessageReceivedFilter(logging.Filter):
        def filter(self, record):
            return 'Message received:' not in record.getMessage()

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    handler = TimedRotatingFileHandler(
        os.path.join(LOGS_DIR, "bot.log"),
        when="midnight",
        backupCount=7,
        utc=True
    )
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    handler.addFilter(MessageReceivedFilter())
    logger.addHandler(handler)

def validate_environment() -> None:
    missing = []
    if not DISCORD_TOKEN:
        missing.append("DISCORD_TOKEN")
    if not WEBHOOK_URL:
        missing.append("WEBHOOK_URL")
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

def print_banner(bot_name: str = "Discord Bot") -> None:
    f = Figlet(font='slant')
    banner = f.renderText(bot_name)
    print("\033[36m" + banner + "\033[0m")
    print("\033[33m" + "=" * 50 + "\033[0m")
    print("\033[32m" + "Bot is starting up..." + "\033[0m")
    print("\033[33m" + "=" * 50 + "\033[0m\n")

class DiscordBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.presences = True
        intents.message_content = True

        super().__init__(command_prefix=".", intents=intents)
        self.session: Optional[aiohttp.ClientSession] = None
        self.command_locks: Dict[str, asyncio.Lock] = {}
        self._ready_once = False
        self._synced_commands: List[discord.app_commands.Command] = []
        self._shutdown_requested = False
        self._processed_messages: set = set()

    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession()
        await self.load_cogs()
        await asyncio.sleep(1)

        # Sync slash commands
        backoff = 1
        max_retries = 3
        retries = 0
        
        while retries < max_retries:
            try:
                self._synced_commands = await self.tree.sync()
                logging.info(f"Synced {len(self._synced_commands)} slash commands")
                for cmd in self._synced_commands:
                    logging.info(f"- /{cmd.name}: {cmd.description}")
                break
            except HTTPException as e:
                if e.status == 429:
                    logging.warning(f"Rate limited syncing commands; retry in {backoff}s")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)
                else:
                    logging.error(f"Failed to sync slash commands: {e}")
                    retries += 1
                    if retries >= max_retries:
                        break
                    await asyncio.sleep(backoff)

    async def process_commands(self, message):
        """Override to prevent duplicate command processing"""
        if message.author.bot:
            return
            
        # Prevent duplicate processing of the same message
        if message.id in self._processed_messages:
            return
            
        self._processed_messages.add(message.id)
        
        # Clean up old message IDs periodically
        if len(self._processed_messages) > 1000:
            # Keep only recent 500 entries
            recent_messages = list(self._processed_messages)[-500:]
            self._processed_messages = set(recent_messages)
        
        # Process the command normally
        await super().process_commands(message)

    async def on_ready(self):
        if self._ready_once:
            return
        self._ready_once = True

        # Clear screen & banner
        print("\033[2J\033[H")
        print_banner(self.user.name)

        # Log & print login
        logging.info(f"Logged in as {self.user.name} ({self.user.id})")
        print(f"\033[32mLogged in as {self.user.name} ({self.user.id})\033[0m\n")

        # Print the cached sync results
        print(f"\033[33mSynced {len(self._synced_commands)} slash commands:\033[0m")
        for cmd in self._synced_commands:
            print(f"\033[36m- /{cmd.name}\033[0m: {cmd.description}")
        print()

    async def close(self) -> None:
        if self.is_closed():
            return
            
        logging.info("Shutting down bot...")
        print("\n\033[33m" + "=" * 50 + "\033[0m")
        print("\033[31mBot is shutting down...\033[0m")
        print("\033[33m" + "=" * 50 + "\033[0m\n")

        if self.session and not self.session.closed:
            await self.session.close()

        await super().close()

    async def get_command_lock(self, user_id: int, command_name: str) -> asyncio.Lock:
        key = f"{user_id}:{command_name}"
        if key not in self.command_locks:
            self.command_locks[key] = asyncio.Lock()
        
        # Cleanup old locks periodically
        if len(self.command_locks) > 500:
            inactive_locks = [k for k, v in self.command_locks.items() if not v.locked()]
            for k in inactive_locks[:len(inactive_locks)//2]:
                self.command_locks.pop(k, None)
            
        return self.command_locks[key]

    async def load_cogs(self) -> None:
        if not os.path.isdir(COGS_DIR):
            logging.warning(f"No '{COGS_DIR}' directory found; skipping cog loading.")
            return

        failed_cogs = []
        for filename in os.listdir(COGS_DIR):
            if not filename.endswith('.py') or filename.startswith('__'):
                continue
            module = f"{COGS_DIR}.{filename[:-3]}"
            try:
                await self.load_extension(module)
                logging.info(f"Loaded cog {module}")
            except Exception as e:
                logging.error(f"Failed to load cog {module}: {e}")
                failed_cogs.append(module)
        
        if failed_cogs:
            logging.warning(f"Failed to load {len(failed_cogs)} cogs: {', '.join(failed_cogs)}")

    async def send_error_report(self, error_message: str) -> None:
        if not self.session or self.session.closed or self.is_closed():
            logging.warning("Cannot send error report: session not available")
            return
        try:
            async with self.session.post(WEBHOOK_URL, json={"content": error_message}) as resp:
                resp.raise_for_status()
        except Exception as e:
            logging.error(f"Failed to send error report: {e}")

# Add the ping command here as a standalone command
@commands.command()
@commands.cooldown(1, 3, commands.BucketType.user)
async def ping(ctx):
    bot = ctx.bot
    lock = await bot.get_command_lock(ctx.author.id, ctx.command.name)
    async with lock:
        await ctx.send(f'<a:sukoon_greendot:1322894177775783997> Latency: {bot.latency*1000:.2f}ms')

def setup_signal_handlers(bot: DiscordBot) -> None:
    def shutdown_handler(signum=None, frame=None):
        logging.info(f"Received shutdown signal: {signum}")
        bot._shutdown_requested = True
        if not bot.is_closed():
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(bot.close())
            except RuntimeError:
                pass
    
    if sys.platform != "win32":
        try:
            loop = asyncio.get_event_loop()
            loop.add_signal_handler(signal.SIGTERM, shutdown_handler)
            loop.add_signal_handler(signal.SIGINT, shutdown_handler)
        except NotImplementedError:
            signal.signal(signal.SIGTERM, shutdown_handler)
            signal.signal(signal.SIGINT, shutdown_handler)
    else:
        signal.signal(signal.SIGINT, shutdown_handler)

async def main():
    bot = None
    try:
        setup_directories()
        setup_logging()
        
        # Setup environment with dependency analysis
        if not setup_environment():
            print("\033[33mContinuing with existing environment...\033[0m")
        
        validate_environment()
    except ValueError as e:
        print(f"\033[31mStartup error: {e}\033[0m")
        sys.exit(1)

    try:
        bot = DiscordBot()
        
        # Add the ping command to the bot
        bot.add_command(ping)
        
        setup_signal_handlers(bot)

        async with bot:
            async def shutdown_checker():
                while not bot.is_closed():
                    if bot._shutdown_requested:
                        await bot.close()
                        break
                    await asyncio.sleep(1)
            
            await asyncio.gather(
                bot.start(DISCORD_TOKEN),
                shutdown_checker(),
                return_exceptions=True
            )
            
    except KeyboardInterrupt:
        logging.info("Received keyboard interrupt")
        if bot and not bot.is_closed():
            await bot.close()
    except Exception as e:
        logging.error(f"Fatal error in main: {e}")
        if bot and bot.session and not bot.session.closed and not bot.is_closed():
            try:
                await bot.send_error_report(f"Fatal error: {e}")
            except:
                pass
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\033[31mBot shutdown by keyboard interrupt\033[0m")
    except Exception as e:
        print(f"\n\033[31mFatal error during startup: {e}\033[0m")
        logging.error(f"Fatal error during startup: {e}")
        sys.exit(1)
