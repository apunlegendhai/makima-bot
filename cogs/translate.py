import os
import logging
import asyncio
import random
import colorsys
from datetime import datetime
from functools import lru_cache
from typing import Tuple, Set

import discord
from discord.ext import commands
from discord import app_commands
from deep_translator import GoogleTranslator
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Simple in‑memory cache key generator for translate results
def _cache_key(text: str, src: str, tgt: str) -> Tuple[str, str, str]:
    return (text, src, tgt)


class TranslationCog(commands.Cog):
    """A single‑cog Discord bot for translating text EN⇄HI with extras."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        mongo_url = os.getenv("MONGO_URL")
        if not mongo_url:
            raise RuntimeError("MONGO_URL must be set in .env")
        self.mongo = MongoClient(mongo_url)
        self.db = self.mongo["translation_bot"]
        self.logs = self.db["translations"]
        self.session_count = 0
        self.used_colors: Set[int] = set()
        
        # Available languages in deep_translator
        self.langs = {
            "en": "English", "hi": "Hindi", "es": "Spanish", "fr": "French",
            "de": "German", "it": "Italian", "pt": "Portuguese", "ru": "Russian",
            "ja": "Japanese", "ko": "Korean", "zh-cn": "Chinese (Simplified)",
            "ar": "Arabic", "tr": "Turkish", "pl": "Polish", "nl": "Dutch",
            "sv": "Swedish", "da": "Danish", "no": "Norwegian", "fi": "Finnish",
            "cs": "Czech", "hu": "Hungarian", "ro": "Romanian",
            "bg": "Bulgarian", "hr": "Croatian", "sk": "Slovak"
        }
        
        # Reverse mapping for language codes
        self.lang_codes = {v.lower(): k for k, v in self.langs.items()}

        # per‑instance cache dict
        self._translation_cache: dict = {}

    async def detect_and_translate(self, text: str) -> Tuple[str, str, str]:
        """Detect source language, pick EN⇄HI, then translate (with retry)."""
        # 1) Try to detect language using deep_translator
        try:
            translator = GoogleTranslator(source='auto', target='en')
            detection = translator.detect(text)
            src = detection
            tgt = "hi" if src == "en" else "en"
        except Exception:
            # If detection fails, assume English
            src = "en"
            tgt = "hi"

        # 2) Check cache
        key = _cache_key(text, src, tgt)
        if key in self._translation_cache:
            cached_text, cached_src = self._translation_cache[key]
            return cached_text, cached_src, tgt

        # 3) Translate with up to 2 attempts
        for attempt in range(2):
            try:
                translator = GoogleTranslator(source=src, target=tgt)
                translated = translator.translate(text)
                self._translation_cache[key] = (translated, src)
                return translated, src, tgt
            except Exception as e:
                logger.warning(f"Translate attempt {attempt+1} failed: {e}")
                await asyncio.sleep(1)

        raise RuntimeError("Translation API unavailable")

    def generate_unique_color(self) -> int:
        """Generate a unique, non-repeating color with different shades."""
        while True:
            # Generate random HSV values
            hue = random.random()  # Random hue (0-1)
            saturation = random.uniform(0.5, 1.0)  # Medium to high saturation
            value = random.uniform(0.7, 1.0)  # Medium to high brightness
            
            # Convert HSV to RGB
            rgb = colorsys.hsv_to_rgb(hue, saturation, value)
            
            # Convert RGB to hex color integer
            color = int(rgb[0] * 255) << 16 | int(rgb[1] * 255) << 8 | int(rgb[2] * 255)
            
            # If we haven't used this color before, use it
            if color not in self.used_colors:
                self.used_colors.add(color)
                # Reset used colors if we have too many to prevent memory issues
                if len(self.used_colors) > 1000:
                    self.used_colors.clear()
                return color

    def build_embed(self, original: str, translated: str,
                    src: str, tgt: str, user: discord.Member) -> discord.Embed:
        # Create an embed with only the translated sentence
        emb = discord.Embed(
            description=f"**{translated}**",  # Made bigger with bold formatting
            color=self.generate_unique_color()
        )
        return emb

    def build_view(self, original: str, src: str, tgt: str) -> discord.ui.View:
        view = discord.ui.View(timeout=300)
        view.add_item(LanguageSelect(self, original, src, tgt))
        view.add_item(RetryButton(self, original))
        return view

    async def log(self, user_id: int, channel_id: int,
                  src: str, tgt: str, snippet: str):
        try:
            self.logs.insert_one({
                "user_id": user_id,
                "channel_id": channel_id,
                "timestamp": datetime.utcnow(),
                "src": src,
                "tgt": tgt,
                "snippet": snippet[:100]
            })
            self.session_count += 1
        except Exception as e:
            logger.error(f"Logging failed: {e}")

    @commands.command(aliases=["tl"])
    @commands.cooldown(2, 10, commands.BucketType.user)
    async def translate(self, ctx: commands.Context, *, text: str = None):
        """`.translate [text]` or reply to auto-translate."""
        if not text and ctx.message.reference:
            try:
                ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                text = ref.content
            except discord.NotFound:
                return await ctx.reply("❌ Could not fetch replied message.")

        if not text:
            return await ctx.reply("❌ Please provide text or reply to a message.")

        try:
            async with ctx.typing():
                translated, detected_src, tgt = await self.detect_and_translate(text)

            embed = self.build_embed(text, translated, detected_src, tgt, ctx.author)
            view = self.build_view(text, detected_src, tgt)
            await ctx.reply(embed=embed, view=view)
            await self.log(ctx.author.id, ctx.channel.id, detected_src, tgt, text)

        except Exception:
            err = discord.Embed(
                title="❌ Translation Failed",
                description="Please try again later.",
                color=0xFF0000
            )
            retry_view = discord.ui.View()
            retry_view.add_item(RetryButton(self, text))
            await ctx.reply(embed=err, view=retry_view)

    @app_commands.command(name="translate", description="Translate text to another language")
    @app_commands.describe(text="Text to translate")
    async def slash_translate(self, interaction: discord.Interaction, text: str):
        await interaction.response.defer()

        try:
            translated, detected_src, tgt = await self.detect_and_translate(text)
            embed = self.build_embed(text, translated, detected_src, tgt, interaction.user)
            view = self.build_view(text, detected_src, tgt)
            await interaction.followup.send(embed=embed, view=view)
            await self.log(interaction.user.id, interaction.channel_id or 0, detected_src, tgt, text)

        except Exception:
            err = discord.Embed(
                title="❌ Translation Failed",
                description="Please try again later.",
                color=0xFF0000
            )
            retry_view = discord.ui.View()
            retry_view.add_item(RetryButton(self, text))
            await interaction.followup.send(embed=err, view=retry_view)

    @app_commands.command(name="stats", description="View translation stats (admin only)")
    async def stats(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)

        top_langs = list(self.logs.aggregate([
            {"$group": {"_id": "$tgt", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 5}
        ]))
        top_users = list(self.logs.aggregate([
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 5}
        ]))

        emb = discord.Embed(
            title="📊 Translation Statistics",
            color=0x0099FF,
            timestamp=datetime.utcnow()
        )
        emb.add_field(
            name="Top 5 Target Languages",
            value="\n".join(
                f"{i+1}. {self.langs.get(lang['_id'], lang['_id']).title()}: {lang['count']}"
                for i, lang in enumerate(top_langs)
            ) or "No data",
            inline=False
        )
        emb.add_field(
            name="Top 5 Users",
            value="\n".join(
                f"{i+1}. <@{user['_id']}>: {user['count']}"
                for i, user in enumerate(top_users)
            ) or "No data",
            inline=False
        )
        emb.add_field(name="Session Translations", value=str(self.session_count), inline=True)
        await interaction.followup.send(embed=emb)


class LanguageSelect(discord.ui.Select):
    """Dropdown to choose any supported target language."""
    def __init__(self, cog: TranslationCog, original: str, src: str, current: str):
        self.cog = cog
        self.original = original
        self.src = src
        self.current = current

        options = [
            discord.SelectOption(label=name, value=code, default=(code == current))
            for code, name in cog.langs.items()
        ]
        super().__init__(
            placeholder="Choose a language…",
            min_values=1, max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        target = self.values[0]
        if target == self.current:
            return await interaction.response.send_message("<:sukoon_info:1323251063910043659> Already in that language!", ephemeral=True)

        await interaction.response.defer()
        try:
            translator = GoogleTranslator(source=self.src, target=target)
            translated = translator.translate(self.original)
            embed = self.cog.build_embed(self.original, translated, self.src, target, interaction.user)
            view = self.cog.build_view(self.original, self.src, target)
            await interaction.edit_original_response(embed=embed, view=view)
            await self.cog.log(interaction.user.id, interaction.channel_id or 0, self.src, target, self.original)
        except Exception:
            await interaction.followup.send("❌ Translation failed—please try again.", ephemeral=True)


class RetryButton(discord.ui.Button):
    """Button to retry a failed translation."""
    def __init__(self, cog: TranslationCog, original: str):
        super().__init__(label="<:sukoon_info:1323251063910043659> Retry", style=discord.ButtonStyle.secondary)
        self.cog = cog
        self.original = original

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            translated, src, tgt = await self.cog.detect_and_translate(self.original)
            embed = self.cog.build_embed(self.original, translated, src, tgt, interaction.user)
            view = self.cog.build_view(self.original, src, tgt)
            await interaction.edit_original_response(embed=embed, view=view)
            await self.cog.log(interaction.user.id, interaction.channel_id or 0, src, tgt, self.original)
        except Exception:
            await interaction.followup.send("❌ Still failing—please try later.", ephemeral=True)


async def setup(bot: commands.Bot):
    """Cog loader."""
    await bot.add_cog(TranslationCog(bot))
