#!/usr/bin/env python3
"""
Discord Bot Setup Script
Scans cogs folder, generates requirements.txt, installs dependencies, and starts the bot.
"""

import os
import sys
import ast
import subprocess
import glob
from pathlib import Path


def extract_imports_from_file(file_path):
    """Extract import statements from a Python file."""
    imports = set()
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse the AST to find imports
        tree = ast.parse(content)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split('.')[0])
    
    except (SyntaxError, UnicodeDecodeError) as e:
        print(f"Warning: Could not parse {file_path}: {e}")
    
    return imports


def scan_entire_codebase(exclude_dirs=None):
    """Scan all Python files in the project directory."""
    if exclude_dirs is None:
        exclude_dirs = {'.git', '__pycache__', '.venv', 'venv', 'env', 'node_modules'}
    
    all_imports = set()
    file_count = 0
    
    print("Scanning entire codebase...")
    
    for root, dirs, files in os.walk('.'):
        # Skip excluded directories
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path)
                print(f"  - {relative_path}")
                imports = extract_imports_from_file(file_path)
                all_imports.update(imports)
                file_count += 1
    
    print(f"Scanned {file_count} Python files")
    return all_imports


def filter_external_packages(imports):
    """Filter out built-in modules and keep only external packages."""
    # Common built-in modules (not exhaustive but covers most cases)
    builtin_modules = {
        'os', 'sys', 'json', 'time', 'datetime', 'random', 'math', 'collections',
        'itertools', 'functools', 'operator', 're', 'urllib', 'http', 'socket',
        'threading', 'asyncio', 'logging', 'pathlib', 'typing', 'dataclasses',
        'enum', 'abc', 'copy', 'pickle', 'base64', 'hashlib', 'hmac', 'secrets',
        'uuid', 'csv', 'sqlite3', 'xml', 'html', 'email', 'mimetypes', 'tempfile',
        'shutil', 'glob', 'fnmatch', 'subprocess', 'signal', 'platform', 'io',
        'warnings', 'traceback', 'inspect', 'ast', 'dis', 'gc', 'weakref'
    }
    
    # Common Discord bot packages that should be included
    external_packages = set()
    
    for package in imports:
        if package not in builtin_modules:
            external_packages.add(package)
    
    return external_packages


def create_requirements_txt(packages):
    """Create requirements.txt file with the discovered packages."""
    # Common package mappings (import name -> pip name)
    package_mappings = {
        'discord': 'discord.py',
        'disnake': 'disnake',
        'nextcord': 'nextcord',
        'py_cord': 'py-cord',
        'wavelink': 'wavelink',
        'aiohttp': 'aiohttp',
        'requests': 'requests',
        'asyncpg': 'asyncpg',
        'motor': 'motor',
        'pymongo': 'pymongo',
        'youtube_dl': 'youtube-dl',
        'yt_dlp': 'yt-dlp',
        'PIL': 'Pillow',
        'cv2': 'opencv-python',
        'numpy': 'numpy',
        'pandas': 'pandas',
        'matplotlib': 'matplotlib',
        'psutil': 'psutil',
        'dotenv': 'python-dotenv'
    }
    
    requirements = []
    
    for package in sorted(packages):
        pip_name = package_mappings.get(package, package)
        requirements.append(pip_name)
    
    # Write requirements.txt
    with open('requirements.txt', 'w') as f:
        for req in requirements:
            f.write(f"{req}\n")
    
    print(f"Created requirements.txt with {len(requirements)} packages:")
    for req in requirements:
        print(f"  - {req}")


def install_dependencies():
    """Install dependencies from requirements.txt."""
    if not os.path.exists('requirements.txt'):
        print("No requirements.txt found, skipping dependency installation")
        return True
    
    print("Installing dependencies...")
    try:
        result = subprocess.run([
            sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'
        ], check=True, capture_output=True, text=True)
        
        print("Dependencies installed successfully!")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"Error installing dependencies: {e}")
        print(f"stdout: {e.stdout}")
        print(f"stderr: {e.stderr}")
        return False


def start_bot():
    """Start the Discord bot."""
    bot_file = 'bot.py'
    
    if not os.path.exists(bot_file):
        print(f"Error: {bot_file} not found!")
        return False
    
    print(f"Starting {bot_file}...")
    try:
        # Use subprocess.run with check=True to wait for the bot to finish
        # If you want the bot to run in the background, use subprocess.Popen instead
        subprocess.run([sys.executable, bot_file], check=True)
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"Error starting bot: {e}")
        return False
    except KeyboardInterrupt:
        print("\nBot stopped by user")
        return True


def main():
    """Main function to orchestrate the setup process."""
    print("=== Discord Bot Setup Script ===")
    
    # Step 1: Scan entire codebase
    print("\n1. Scanning entire codebase...")
    all_imports = scan_entire_codebase()
    
    # Step 2: Filter external packages
    external_packages = filter_external_packages(all_imports)
    
    if external_packages:
        print(f"\nFound {len(external_packages)} external packages")
        
        # Step 3: Create requirements.txt
        print("\n2. Creating requirements.txt...")
        create_requirements_txt(external_packages)
        
        # Step 4: Install dependencies
        print("\n3. Installing dependencies...")
        if not install_dependencies():
            print("Failed to install dependencies. Exiting.")
            sys.exit(1)
    else:
        print("No external packages found, skipping requirements.txt creation")
    
    # Step 5: Start the bot
    print("\n4. Starting Discord bot...")
    if not start_bot():
        print("Failed to start bot")
        sys.exit(1)

if __name__ == "__main__":
    main()
