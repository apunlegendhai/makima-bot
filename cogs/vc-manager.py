import discord
from discord.ext import commands
import asyncio
import logging
from typing import Optional, List, Dict, Tuple, Union

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('voice_manager')

class VoiceManager(commands.Cog):
    """
    Fast, safe voice-channel user management:
    • pull users in
    • push users out
    • kick everyone
    with exact rate-limit handling.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_locks: Dict[int, asyncio.Lock] = {}

    def get_user_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self.user_locks:
            self.user_locks[user_id] = asyncio.Lock()
        return self.user_locks[user_id]

    async def _join_channel(self, channel: Union[discord.VoiceChannel, discord.StageChannel]) -> Optional[discord.VoiceClient]:
        try:
            voice_client = await channel.connect()
            return voice_client
        except Exception as e:
            logger.error(f"Failed to join channel: {e}")
            return None

    async def _leave_channel(self, voice_client: Optional[discord.VoiceClient]) -> None:
        if voice_client and voice_client.is_connected():
            try:
                await voice_client.disconnect()
            except Exception as e:
                logger.error(f"Failed to leave channel: {e}")

    async def _process_member_batch(
        self,
        members: List[discord.Member],
        target: Union[discord.VoiceChannel, discord.StageChannel, None]
    ) -> Tuple[int, List[str]]:
        sem = asyncio.Semaphore(5)
        errors: List[str] = []

        async def move_one(member: discord.Member):
            async with sem:
                for attempt in range(1, 4):
                    try:
                        await member.move_to(target)
                        return
                    except discord.HTTPException as e:
                        if e.status == 429 or getattr(e, 'code', None) == 429:
                            # Use the actual retry_after value with a reasonable default
                            retry = getattr(e, 'retry_after', 1.0)
                            if not isinstance(retry, (int, float)) or retry <= 0:
                                retry = 1.0
                            logger.warning(
                                f"<a:heartspar:1335854160322498653> Rate-limit on {member.display_name}; "
                                f"waiting {retry:.1f}s (#{attempt})"
                            )
                            await asyncio.sleep(retry)
                            continue
                        errors.append(f"<a:sukoon_reddot:1322894157794119732> {member.display_name}: {e}")
                        return
                    except Exception as e:
                        errors.append(f"<a:sukoon_reddot:1322894157794119732> {member.display_name}: {e}")
                        return
                errors.append(f"<a:sukoon_reddot:1322894157794119732> {member.display_name}: failed after 3 tries")

        await asyncio.gather(*(move_one(m) for m in members))
        moved = len(members) - len(errors)
        return moved, errors

    async def check_admin_and_move_perms(self, ctx: commands.Context) -> bool:
        if not ctx.guild:
            await ctx.send("<a:sukoon_reddot:1322894157794119732> This command can't be used in DMs.")
            return False
        if not (ctx.author.guild_permissions.administrator or ctx.author.guild_permissions.move_members):
            await ctx.send("<a:sukoon_reddot:1322894157794119732> You need Admin or Move-Members permission.")
            return False
        return True

    async def check_bot_permissions(
        self, ctx: commands.Context, channel: discord.abc.GuildChannel
    ) -> bool:
        bot_member = ctx.guild.get_member(ctx.bot.user.id)
        if not bot_member:
            await ctx.send("<a:sukoon_reddot:1322894157794119732> I'm not in this guild properly.")
            return False

        bot_perms = channel.permissions_for(bot_member)
        missing = []
        if not bot_perms.connect:
            missing.append("Connect")
        if not bot_perms.move_members:
            missing.append("Move Members")
        if missing:
            await ctx.send(f"<a:sukoon_reddot:1322894157794119732> I need: {', '.join(missing)}.")
            return False
        return True

    async def get_voice_channel(
        self, ctx: commands.Context, channel_id: str
    ) -> Optional[Union[discord.VoiceChannel, discord.StageChannel]]:
        try:
            cid = int(channel_id)
        except ValueError:
            await ctx.send("<a:sukoon_reddot:1322894157794119732> Invalid channel ID.")
            return None
        channel = ctx.guild.get_channel(cid)
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            await ctx.send("<a:sukoon_reddot:1322894157794119732> Not a voice/stage channel.")
            return None
        return channel

    @commands.command(name="pull")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def pull(self, ctx: commands.Context, source: str = None, *more: str):
        """
        Pull everyone (or specific users) into your VC.
        """
        if not await self.check_admin_and_move_perms(ctx):
            return

        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("<:sukoon_info:1323251063910043659> Join a voice channel first.")

        target = ctx.author.voice.channel
        if not await self.check_bot_permissions(ctx, target):
            return

        members: List[discord.Member] = []
        src_channel = None
        if source and not more:
            src_channel = await self.get_voice_channel(ctx, source)
            if src_channel:
                if not await self.check_bot_permissions(ctx, src_channel):
                    return
                members = [m for m in src_channel.members if not m.bot]

        if not members:
            tokens = (source,) + more if source else ()
            for t in tokens:
                try:
                    uid = int(t.strip("<@!>"))
                    m = ctx.guild.get_member(uid)
                except (ValueError, TypeError):
                    m = None
                if m and m.voice and m.voice.channel:
                    members.append(m)
            if not members:
                return await ctx.send("<a:sukoon_reddot:1322894157794119732> No valid users to pull.")

        lock = self.get_user_lock(ctx.author.id)
        if lock.locked():
            return await ctx.send("<a:heartspar:1335854160322498653> Hold on, operation in progress.")

        async with lock:
            msg = await ctx.send(f"<a:heartspar:1335854160322498653> Pulling `{len(members)}` user(s)…")

            # Join source channel only if needed and valid
            voice_client = None
            if src_channel and members:
                voice_client = await self._join_channel(src_channel)

            moved, errs = await self._process_member_batch(members, target)

            # Ensure we're disconnecting from voice
            await self._leave_channel(voice_client)

            await msg.edit(content=f"<a:sukoon_whitetick:1323992464058482729> Successfully Pulled `{moved}/{len(members)}` users!")
            if errs:
                snippet = "\n".join(errs[:5]) + (f"\n…(+{len(errs)-5} more)" if len(errs)>5 else "")
                await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Issues:\n{snippet}")

    @commands.command(name="push")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def push(self, ctx: commands.Context, dest: str):
        """
        Push everyone from your VC to another.
        """
        if not await self.check_admin_and_move_perms(ctx):
            return

        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("<:sukoon_info:1323251063910043659> Join a voice channel first.")

        src = ctx.author.voice.channel

        tgt = await self.get_voice_channel(ctx, dest)
        if not tgt:
            return

        if src.id == tgt.id:
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> Source and target are the same.")

        if not await self.check_bot_permissions(ctx, src) or not await self.check_bot_permissions(ctx, tgt):
            return

        members = [m for m in src.members if not m.bot and m.id != ctx.author.id]
        if not members:
            return await ctx.send("<:sukoon_info:1323251063910043659> No one to push.")

        lock = self.get_user_lock(ctx.author.id)
        if lock.locked():
            return await ctx.send("<a:heartspar:1335854160322498653> Hold on, operation in progress.")

        async with lock:
            msg = await ctx.send(f"<a:heartspar:1335854160322498653> Pushing `{len(members)}` user(s)…")

            # Join source channel first
            voice_client = await self._join_channel(src)

            moved, errs = await self._process_member_batch(members, tgt)

            # Leave channel after operation
            await self._leave_channel(voice_client)

            await msg.edit(content=f"<a:sukoon_whitetick:1323992464058482729> Successfully Pushed `{moved}/{len(members)}` users!")
            if errs:
                snippet = "\n".join(errs[:5]) + (f"\n…(+{len(errs)-5} more)" if len(errs)>5 else "")
                await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Issues:\n{snippet}")

    @commands.command(name="kick")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def kick(self, ctx: commands.Context, confirm: str, channel_id: Optional[str] = None):
        """
        Disconnect everyone (except you/bots). Confirm: `kick all [channel_id]`
        """
        if confirm.lower() != "all":
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> To confirm, type: `kick all [channel_id]`")

        if not await self.check_admin_and_move_perms(ctx):
            return

        vc = None
        if channel_id:
            vc = await self.get_voice_channel(ctx, channel_id)
            if not vc or not await self.check_bot_permissions(ctx, vc):
                return
        else:
            if not ctx.author.voice or not ctx.author.voice.channel:
                return await ctx.send("<:sukoon_info:1323251063910043659> Join a voice channel first.")
            vc = ctx.author.voice.channel
            if not await self.check_bot_permissions(ctx, vc):
                return

        members = [m for m in vc.members if not m.bot and m.id != ctx.author.id]
        if not members:
            return await ctx.send("<:sukoon_info:1323251063910043659> No one to disconnect.")

        lock = self.get_user_lock(ctx.author.id)
        if lock.locked():
            return await ctx.send("<a:heartspar:1335854160322498653> Hold on, operation in progress.")

        async with lock:
            msg = await ctx.send(f"<a:heartspar:1335854160322498653> Disconnecting `{len(members)}` user(s)…")

            # Join channel if needed for permission validation
            voice_client = None
            if vc:
                voice_client = await self._join_channel(vc)

            moved, errs = await self._process_member_batch(members, None)

            # Leave channel after operation
            await self._leave_channel(voice_client)

            await msg.edit(content=f"<a:sukoon_whitetick:1323992464058482729> Successfully Disconnected `{moved}/{len(members)}` users!")
            if errs:
                snippet = "\n".join(errs[:5]) + (f"\n…(+{len(errs)-5} more)" if len(errs)>5 else "")
                await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Issues:\n{snippet}")

    @commands.command(name="vchelp")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def vchelp(self, ctx: commands.Context):
        embed = discord.Embed(
            title="🔊 Voice Manager Help",
            description="Fast & reliable voice-channel user management",
            color=discord.Color.blurple()
        )
        embed.add_field(
            name=".pull <channel_id|user_ids…>",
            value="📥 Pull all or specific users into your VC.",
            inline=False
        )
        embed.add_field(
            name=".push <target_channel_id>",
            value="📤 Push all users from your VC to another.",
            inline=False
        )
        embed.add_field(
            name=".kick all [channel_id]",
            value="🔌 Disconnect everyone (except you & bots).",
            inline=False
        )
        embed.set_footer(text="Requires Admin/Move-Members. I need Connect & Move-Members perms.")
        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceManager(bot))
