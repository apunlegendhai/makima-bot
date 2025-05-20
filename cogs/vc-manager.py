import discord
from discord.ext import commands
import asyncio
import logging
from typing import Optional, List, Dict, Tuple, Union
from discord.ui import Button, View

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('voice_manager')

class VoiceManager(commands.Cog):
    """
    Fast, safe voice-channel user management:
    • pull users in
    • push users out
    • kick everyone
    • mute everyone
    • lock voice channel
    • summon users via DM
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
        target: Optional[Union[discord.VoiceChannel, discord.StageChannel]]
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
        processed = len(members) - len(errors)
        return processed, errors

    async def _process_mute_batch(
        self,
        members: List[discord.Member],
        mute: bool
    ) -> Tuple[int, List[str]]:
        sem = asyncio.Semaphore(15)  # Increased for larger batches
        errors: List[str] = []

        async def mute_one(member: discord.Member):
            await asyncio.sleep(0.05)  # Reduced delay for faster processing
            async with sem:
                for attempt in range(1, 4):
                    try:
                        await member.edit(mute=mute)
                        return
                    except discord.HTTPException as e:
                        if e.status == 429 or getattr(e, 'code', None) == 429:
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

        await asyncio.gather(*(mute_one(m) for m in members))
        processed = len(members) - len(errors)
        return processed, errors

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

    @commands.command(name="mute")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def mute(self, ctx: commands.Context, confirm: str):
        """
        Mute everyone in your voice channel (except you/bots). Confirm: `mute all`
        """
        if confirm.lower() != "all":
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> To confirm, type: `mute all`")

        if not await self.check_admin_and_move_perms(ctx):
            return

        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("<:sukoon_info:1323251063910043659> Join a voice channel first.")

        vc = ctx.author.voice.channel
        if not await self.check_bot_permissions(ctx, vc):
            return

        members = [m for m in vc.members if not m.bot and m.id != ctx.author.id]
        if not members:
            return await ctx.send("<:sukoon_info:1323251063910043659> No one to mute.")

        lock = self.get_user_lock(ctx.author.id)
        if lock.locked():
            return await ctx.send("<a:heartspar:1335854160322498653> Hold on, operation in progress.")

        async with lock:
            msg = await ctx.send(f"<a:heartspar:1335854160322498653> Muting `{len(members)}` user(s)…")

            processed, errs = await self._process_mute_batch(members, True)

            await msg.edit(content=f"<a:sukoon_whitetick:1323992464058482729> Successfully Muted `{processed}/{len(members)}` users!")
            if errs:
                snippet = "\n".join(errs[:5]) + (f"\n…(+{len(errs)-5} more)" if len(errs)>5 else "")
                await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Issues:\n{snippet}")

    @commands.command(name="unmute")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def unmute(self, ctx: commands.Context, confirm: str):
        """
        Unmute everyone in your voice channel. Confirm: `unmute all`
        """
        if confirm.lower() != "all":
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> To confirm, type: `unmute all`")

        if not await self.check_admin_and_move_perms(ctx):
            return

        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("<:sukoon_info:1323251063910043659> Join a voice channel first.")

        vc = ctx.author.voice.channel
        if not await self.check_bot_permissions(ctx, vc):
            return

        members = [m for m in vc.members if not m.bot]
        if not members:
            return await ctx.send("<:sukoon_info:1323251063910043659> No one to unmute.")

        lock = self.get_user_lock(ctx.author.id)
        if lock.locked():
            return await ctx.send("<a:heartspar:1335854160322498653> Hold on, operation in progress.")

        async with lock:
            msg = await ctx.send(f"<a:heartspar:1335854160322498653> Unmuting `{len(members)}` user(s)…")

            processed, errs = await self._process_mute_batch(members, False)

            await msg.edit(content=f"<a:sukoon_whitetick:1323992464058482729> Successfully Unmuted `{processed}/{len(members)}` users!")
            if errs:
                snippet = "\n".join(errs[:5]) + (f"\n…(+{len(errs)-5} more)" if len(errs)>5 else "")
                await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Issues:\n{snippet}")

    @commands.command(name="lock")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def lock(self, ctx: commands.Context):
        """
        Lock your voice channel by setting user limit to current number of users.
        """
        if not ctx.author.guild_permissions.administrator and not ctx.author.guild_permissions.manage_channels:
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> You need Admin or Manage Channels permission.")

        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("<:sukoon_info:1323251063910043659> Join a voice channel first.")

        vc = ctx.author.voice.channel
        if not isinstance(vc, discord.VoiceChannel):
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> This command only works with voice channels, not stage channels.")

        # Check if bot has manage channels permission
        bot_member = ctx.guild.get_member(ctx.bot.user.id)
        if not bot_member:
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> I'm not in this guild properly.")

        bot_perms = vc.permissions_for(bot_member)
        if not bot_perms.manage_channels:
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> I need Manage Channels permission.")

        current_users = len([m for m in vc.members if not m.bot])
        if current_users == 0:
            current_users = 1  # Minimum lock of 1 user if channel is empty

        try:
            await vc.edit(user_limit=current_users)
            await ctx.send(f"<a:sukoon_whitetick:1323992464058482729> Voice channel `{vc.name}` locked at `{current_users}` users.")
        except discord.Forbidden:
            await ctx.send("<a:sukoon_reddot:1322894157794119732> I don't have permission to edit this channel.")
        except Exception as e:
            await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Failed to lock channel: {str(e)}")

    @commands.command(name="unlock")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def unlock(self, ctx: commands.Context):
        """
        Unlock your voice channel by removing user limit.
        """
        if not ctx.author.guild_permissions.administrator and not ctx.author.guild_permissions.manage_channels:
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> You need Admin or Manage Channels permission.")

        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("<:sukoon_info:1323251063910043659> Join a voice channel first.")

        vc = ctx.author.voice.channel
        if not isinstance(vc, discord.VoiceChannel):
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> This command only works with voice channels, not stage channels.")

        # Check if bot has manage channels permission
        bot_member = ctx.guild.get_member(ctx.bot.user.id)
        if not bot_member:
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> I'm not in this guild properly.")

        bot_perms = vc.permissions_for(bot_member)
        if not bot_perms.manage_channels:
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> I need Manage Channels permission.")

        try:
            await vc.edit(user_limit=0)
            await ctx.send(f"<a:sukoon_whitetick:1323992464058482729> Voice channel `{vc.name}` unlocked.")
        except discord.Forbidden:
            await ctx.send("<a:sukoon_reddot:1322894157794119732> I don't have permission to edit this channel.")
        except Exception as e:
            await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Failed to unlock channel: {str(e)}")

    class SummonView(View):
        def __init__(self, cog, user, target_channel):
            super().__init__(timeout=60)
            self.cog = cog
            self.user = user
            self.target_channel = target_channel
            self.response = None

        async def on_timeout(self):
            if self.response:
                await self.response.edit(content="<a:003_bel:1341822673247797319> Summon request timed out.", view=None)

        @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
        async def accept(self, interaction: discord.Interaction, button: Button):
            if interaction.user.id != self.user.id:
                return await interaction.response.send_message("This isn't for you!", ephemeral=True)

            await interaction.response.defer()

            if not self.target_channel or not isinstance(self.target_channel, (discord.VoiceChannel, discord.StageChannel)):
                return await interaction.followup.send("Target voice channel no longer exists.")

            try:
                # Check if user is already in a voice channel
                if self.user.voice and self.user.voice.channel:
                    try:
                        await self.user.move_to(self.target_channel)
                        if self.response:
                            self.stop()
                            await self.response.edit(content=f"<a:sukoon_whitetick:1323992464058482729> Moved to `{self.target_channel.name}`.", view=None)
                        return
                    except discord.Forbidden:
                        await interaction.followup.send("I don't have permission to move members between voice channels.")
                        return
                    except Exception as e:
                        await interaction.followup.send(f"Failed to move to voice channel: {e}")
                        return

                # If user is not in VC, handle invite process
                if isinstance(self.target_channel, discord.VoiceChannel):
                    try:
                        # Store original settings
                        original_settings = {
                            'user_limit': self.target_channel.user_limit,
                            'overwrites': self.target_channel.overwrites_for(self.user)
                        }

                        # Temporarily adjust channel settings for locked VCs
                        if self.target_channel.user_limit > 0:
                            await self.target_channel.edit(
                                user_limit=max(len(self.target_channel.members) + 1, self.target_channel.user_limit + 1)
                            )
                        
                        # Create new permissions that allow everything voice-related
                        allow_perms = discord.PermissionOverwrite(
                            connect=True,
                            speak=True,
                            stream=True,
                            use_voice_activation=True,
                            view_channel=True
                        )
                        
                        # Grant temporary permissions to the user
                        await self.target_channel.set_permissions(
                            self.user,
                            overwrite=allow_perms,
                            reason="Temporary access for summon"
                        )

                        # Store successful permission update
                        self.original_settings = original_settings
                    except discord.Forbidden:
                        await interaction.followup.send("I don't have permission to modify the voice channel settings.")
                        return
                    except Exception as e:
                        await interaction.followup.send(f"Failed to adjust channel permissions: {e}")
                        return

                # Generate invite link
                invite = await self.target_channel.create_invite(max_age=120, max_uses=1)
                await interaction.followup.send(f"Here's your invite link to join {self.target_channel.name}: {invite.url}")

                if self.response:
                    self.stop()
                    await self.response.edit(content=f"<a:sukoon_whitetick:1323992464058482729> A one-time invite link has been sent to you for `{self.target_channel.name}`.", view=None)

                # Wait for the user to join or for timeout
                try:
                    def check(member, before, after):
                        return (member.id == self.user.id and 
                                after.channel and after.channel.id == self.target_channel.id)
                    
                    await self.cog.bot.wait_for('voice_state_update', check=check, timeout=120)
                except asyncio.TimeoutError:
                    pass
                finally:
                    # Reset channel settings
                    if isinstance(self.target_channel, discord.VoiceChannel) and hasattr(self, 'original_settings'):
                        await self.target_channel.edit(user_limit=self.original_settings['user_limit'])
                        await self.target_channel.set_permissions(
                            self.user,
                            overwrite=self.original_settings['overwrites'],
                            reason="Restoring original permissions after summon"
                        )

            except Exception as e:
                await interaction.followup.send(f"Failed to set up channel access: {e}")

        @discord.ui.button(label="Reject", style=discord.ButtonStyle.red)
        async def reject(self, interaction: discord.Interaction, button: Button):
            if interaction.user.id != self.user.id:
                return await interaction.response.send_message("This isn't for you!", ephemeral=True)

            await interaction.response.defer()
            if self.response:
                self.stop()
                await self.response.edit(content="<a:sukoon_reddot:1322894157794119732> Summon declined.", view=None)
            await interaction.followup.send("You declined the summon.")

    @commands.command(name="summon")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def summon(self, ctx: commands.Context, user: discord.Member):
        """
        Summon a user to your voice channel via DM.
        Usage: .summon @user (must be used in your voice channel's text channel)
        """
        # Check if in a guild
        if not ctx.guild:
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> This command can only be used in a server.")

        # Check if command user is in a voice channel
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("<:sukoon_info:1323251063910043659> You must be in a voice channel to use this command.")

        # Check if both users are in the same guild
        mutual_guilds = [g for g in self.bot.guilds if g.get_member(ctx.author.id) and g.get_member(user.id)]
        if not mutual_guilds:
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> You don't share any servers with this user.")

        # Find the command user in a voice channel
        command_user_voice = None
        command_user_guild = None

        for guild in mutual_guilds:
            member = guild.get_member(ctx.author.id)
            if member and member.voice and member.voice.channel:
                command_user_voice = member.voice.channel
                command_user_guild = guild
                break

        if not command_user_voice:
            return await ctx.send("<:sukoon_info:1323251063910043659> Join a voice channel in a mutual server first.")

        # Get target member
        target_member = command_user_guild.get_member(user.id)

        # Store original VC settings if it's a voice channel
        original_settings = None
        if isinstance(command_user_voice, discord.VoiceChannel):
            original_settings = {
                'user_limit': command_user_voice.user_limit,
                'overwrites': command_user_voice.overwrites.copy()
            }

        # Check bot permissions for moving users
        bot_member = command_user_guild.get_member(self.bot.user.id)
        if not bot_member:
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> I'm not in this guild properly.")

        bot_perms = command_user_voice.permissions_for(bot_member)
        if not bot_perms.move_members:
            return await ctx.send("<a:sukoon_reddot:1322894157794119732> I need Move Members permission.")

        # Try to DM the target user
        view = self.SummonView(self, target_member, command_user_voice)
        try:
            dm = await target_member.create_dm()
            message = (f"<a:heartspar:1335854160322498653> {ctx.author} is summoning you to join their voice channel: **{command_user_voice.name}** "
                      f"in **{command_user_guild.name}**.\n"
                      f"{'<:sukoon_info:1323251063910043659> Please join a voice channel first to accept this invitation.' if not target_member.voice else ''}")
            response = await dm.send(message, view=view)
            view.response = response
            await ctx.send(f"<a:sukoon_whitetick:1323992464058482729> Summon request sent to {user.display_name}.")
        except discord.Forbidden:
            await ctx.send(f"<a:sukoon_reddot:1322894157794119732> I can't DM {user.display_name}. They may have DMs turned off.")
        except Exception as e:
            await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Error: {e}")

    @summon.error
    async def summon_error(self, ctx, error):
        await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Error: {error}")


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceManager(bot))
