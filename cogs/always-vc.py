import asyncio, json, os, discord
from discord.ext import commands

DB_DIR = "database"
DB_FILE = os.path.join(DB_DIR, "voice_targets.json")
SILENCE = b"\xF8\xFF\xFE"

def load_db():
    os.makedirs(DB_DIR, exist_ok=True)
    if not os.path.exists(DB_FILE): return {}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return {}

def save_db(data):
    os.makedirs(DB_DIR, exist_ok=True)
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, DB_FILE)

class Silence(discord.AudioSource):
    def read(self): return SILENCE
    def is_opus(self): return True

class Always(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = load_db()
        self.monitors = {}

    async def cog_load(self):
        await self.bot.wait_until_ready()
        # Auto-join for each guild with a saved target
        for gid_str, ch_id in list(self.db.items()):
            guild = self.bot.get_guild(int(gid_str))
            if not guild: continue
            ch = guild.get_channel(int(ch_id))
            if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
                await self.connect_and_play(guild, ch)

    async def connect_and_play(self, guild: discord.Guild, channel: discord.abc.GuildChannel):
        voice = guild.voice_client
        if voice and voice.is_connected():
            if voice.channel.id != channel.id:
                await voice.move_to(channel)
        else:
            voice = await channel.connect(self_deaf=True, reconnect=False)
        if not voice.is_playing():
            voice.play(Silence())
        # Start a tiny monitor if not running
        if guild.id not in self.monitors or self.monitors[guild.id].done():
            self.monitors[guild.id] = asyncio.create_task(self.monitor(guild.id, channel.id))

    async def monitor(self, guild_id: int, channel_id: int):
        # Very lightweight: every 5s, ensure we're connected and playing
        while True:
            await asyncio.sleep(5)
            # Target may have been removed
            if str(guild_id) not in self.db or int(self.db[str(guild_id)]) != channel_id:
                return
            guild = self.bot.get_guild(guild_id)
            if not guild: return
            ch = guild.get_channel(channel_id)
            if not isinstance(ch, (discord.VoiceChannel, discord.StageChannel)): return
            voice = guild.voice_client
            try:
                if not voice or not voice.is_connected():
                    await self.connect_and_play(guild, ch)
                elif not voice.is_playing():
                    voice.play(Silence())
            except Exception:
                # Ignore and try again next tick
                pass

    @commands.command(name="always")
    @commands.has_guild_permissions(manage_guild=True)
    async def always(self, ctx: commands.Context, channel_id: int = None):
        if not ctx.guild:
            return await ctx.reply("Use this in a server.")
        if channel_id is None:
            target = self.db.get(str(ctx.guild.id))
            return await ctx.reply(f"Current target: {target if target else 'None'}")
        ch = ctx.guild.get_channel(channel_id)
        if not isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
            return await ctx.reply("Invalid voice channel ID for this server.")
        self.db[str(ctx.guild.id)] = ch.id
        save_db(self.db)
        try:
            await self.connect_and_play(ctx.guild, ch)
            await ctx.reply(f"Set and joined: {ch.mention}. Will auto-rejoin after restarts.")
        except Exception as e:
            await ctx.reply(f"Failed to join: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(Always(bot))
