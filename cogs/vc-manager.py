import discord
from discord.ext import commands
import asyncio
import logging
import os
from typing import Optional, List, Dict, Union, Tuple, cast, Any

# Get logger for voice manager
logger = logging.getLogger('voice_manager')

class VoiceManager(commands.Cog, name="VoiceManager"):
    """
    A Cog for managing voice channel users with prefix commands.
    Provides commands to pull users from one channel to another,
    push users to a different channel, and kick all users from a channel.
    """

    def __init__(self, bot: commands.Bot, rate_limit_delay: float = 1.0, batch_size: Optional[int] = None):
        """
        Initialize the VoiceManager cog.

        Args:
            bot (commands.Bot): The Discord bot instance.
            rate_limit_delay (float, optional): Delay between voice operations in seconds. Defaults to 1.0.
            batch_size (Optional[int], optional): Maximum number of users to move in one batch. Defaults to None (no limit).
        """
        self.bot = bot
        self.rate_limit_delay = rate_limit_delay
        self.batch_size = batch_size
        self.user_locks: Dict[int, asyncio.Lock] = {}

    def get_user_lock(self, user_id: int) -> asyncio.Lock:
        """
        Get or create a lock for a specific user to prevent race conditions.

        Args:
            user_id (int): The Discord user ID.

        Returns:
            asyncio.Lock: The lock for the specified user.
        """
        if user_id not in self.user_locks:
            self.user_locks[user_id] = asyncio.Lock()
        return self.user_locks[user_id]

    async def move_members(self, members: List[discord.Member], target_channel: Union[discord.VoiceChannel, discord.StageChannel, None]) -> Tuple[int, List[str]]:
        """
        Move multiple members to a target voice channel with rate limiting.

        Args:
            members (List[discord.Member]): List of members to move.
            target_channel (Union[discord.VoiceChannel, discord.StageChannel, None]): The target voice channel, or None to disconnect.

        Returns:
            Tuple[int, List[str]]: A tuple containing the count of successfully moved members and a list of error messages.
        """
        moved_count = 0
        errors = []

        # If there are too many members, process them in chunks
        if self.batch_size is not None and len(members) > self.batch_size:
            logger.info(f"Processing {len(members)} members in batches of {self.batch_size}")
            total_members = len(members)
            
            # Process in batches to avoid overwhelming the Discord API
            for i in range(0, total_members, self.batch_size):
                batch = members[i:i+self.batch_size]
                batch_count, batch_errors = await self._process_member_batch(batch, target_channel)
                moved_count += batch_count
                errors.extend(batch_errors)
                
                # Add a short delay between batches to prevent rate limits
                if i + self.batch_size < total_members:
                    await asyncio.sleep(2.0)
                    
                    # Update the processing message if it takes a while
                    if len(members) > 2 * self.batch_size:
                        logger.info(f"Processed {min(i + self.batch_size, total_members)}/{total_members} members...")
        else:
            # Small enough to process in one batch
            moved_count, errors = await self._process_member_batch(members, target_channel)
            
        return moved_count, errors
        
    async def _process_member_batch(self, batch: List[discord.Member], target_channel: Union[discord.VoiceChannel, discord.StageChannel, None]) -> Tuple[int, List[str]]:
        """
        Process a single batch of members to move to a target channel.
        
        Args:
            batch (List[discord.Member]): Batch of members to move.
            target_channel (Union[discord.VoiceChannel, discord.StageChannel, None]): The target voice channel, or None to disconnect.
            
        Returns:
            Tuple[int, List[str]]: Count of members moved and list of errors.
        """
        moved_count = 0
        errors = []

        # Use an adaptive rate limit system
        base_delay = self.rate_limit_delay
        current_delay = base_delay
        consecutive_failures = 0
        max_consecutive_failures = 3

        for member in batch:
            try:
                # Wait before making the API call (pre-emptive rate limiting)
                await asyncio.sleep(current_delay)

                await member.move_to(target_channel)
                moved_count += 1

                # Reset failure counter on success
                if consecutive_failures > 0:
                    consecutive_failures = 0
                    # Gradually return to base delay if we had increased it
                    if current_delay > base_delay:
                        current_delay = max(base_delay, current_delay * 0.8)

            except discord.Forbidden:
                errors.append(f"Missing permissions to move {member.display_name}")
                consecutive_failures += 1

            except discord.HTTPException as e:
                errors.append(f"Failed to move {member.display_name}: {e}")
                consecutive_failures += 1

                # Check if this is a rate limit error and adapt
                if hasattr(e, 'code') and e.code == 429:  # Rate limit error code
                    # Double delay on rate limit, up to 3x the base
                    current_delay = min(current_delay * 2, base_delay * 3)
                    logger.warning(f"Rate limited when moving users. Increasing delay to {current_delay}s")

                    # Additional wait after hitting a rate limit
                    await asyncio.sleep(2.0)  # Increased wait time after rate limit

            except Exception as e:
                errors.append(f"Unexpected error moving {member.display_name}: {e}")
                logger.error(f"Error moving {member.id}: {str(e)}")
                consecutive_failures += 1

            # If we've had multiple consecutive failures, increase delay
            if consecutive_failures >= max_consecutive_failures:
                # Double the delay when hitting too many consecutive failures
                current_delay = min(current_delay * 2, base_delay * 3)
                logger.warning(f"Multiple consecutive failures when moving users. Increasing delay to {current_delay}s")
                consecutive_failures = 0  # Reset to prevent continuous increases

        return moved_count, errors

    async def check_admin_and_move_perms(self, ctx: commands.Context) -> bool:
        """
        Check if the user has Administrator OR Move Members permissions.
        Administrator should automatically grant all permissions.

        Args:
            ctx (commands.Context): The command context.

        Returns:
            bool: True if user has required permissions.

        Raises:
            commands.NoPrivateMessage: If used in DMs.
            commands.MissingPermissions: If missing required permissions.
        """
        if not ctx.guild:
            raise commands.NoPrivateMessage("This command cannot be used in private messages.")

        # Check if the user has Administrator OR Move Members permissions
        if not (ctx.author.guild_permissions.administrator or 
                ctx.author.guild_permissions.move_members):
            # Neither permission is present
            raise commands.MissingPermissions(["Administrator or Move Members"])
        
        return True

    async def check_bot_permissions(self, ctx: commands.Context, channel=None) -> Tuple[bool, str]:
        """
        Check if the bot has necessary permissions in the specified channel.
        If no channel is provided, checks permissions in the author's current voice channel.

        Args:
            ctx (commands.Context): The command context.
            channel (discord.VoiceChannel, optional): The channel to check permissions for.
                                                     Defaults to author's current voice channel.

        Returns:
            tuple: (bool, str) - Whether the bot has permissions and an error message if not.
        """
        # Get the channel to check
        check_channel = channel or (ctx.author.voice.channel if ctx.author.voice else None)
        if not check_channel:
            return False, "You need to be in a voice channel to use this command."

        # Get bot's permissions in the channel
        bot_member = ctx.guild.get_member(ctx.bot.user.id)
        bot_perms = check_channel.permissions_for(bot_member)

        # Check for required permissions
        missing_perms = []

        if not bot_perms.connect:
            missing_perms.append("Connect")

        if not bot_perms.move_members:
            missing_perms.append("Move Members")

        if not bot_perms.speak:  # Sometimes needed for connecting to stage channels
            missing_perms.append("Speak")

        # Return results
        if missing_perms:
            formatted_perms = ", ".join(missing_perms)
            return False, f"Bot is missing permissions in {check_channel.name}: {formatted_perms}"

        return True, ""

    async def get_voice_channel(self, ctx: commands.Context, channel_id: str) -> Tuple[Optional[Union[discord.VoiceChannel, discord.StageChannel]], Optional[str]]:
        """
        Try to get a voice channel from a given ID string.

        Args:
            ctx (commands.Context): The command context.
            channel_id (str): The ID of the voice channel to find.

        Returns:
            Tuple[Optional[Union[discord.VoiceChannel, discord.StageChannel]], Optional[str]]: 
                The channel object and an error message if any.
        """
        try:
            # Convert the ID to an integer
            channel_id_int = int(channel_id)

            # Get the channel
            channel = ctx.guild.get_channel(channel_id_int)

            # Check if it's a voice channel
            if not channel or not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                return None, "Could not find a voice channel with that ID."

            return channel, None

        except ValueError:
            return None, "Invalid channel ID. Please provide a valid channel ID."

    @commands.command(name="pull", aliases=["p"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.guild_only()
    @commands.check_any(
        commands.has_permissions(administrator=True),
        commands.has_permissions(move_members=True)
    )
    async def pull(self, ctx: commands.Context, *args) -> None:
        """
        Pull users to your current voice channel. Can pull all users from a channel or specific users.
        
        Usage:
        - `.pull 123456789012345678` (Pull all users from a voice channel by ID)
        - `.pull @user1 @user2` (Pull specific mentioned users)
        - `.pull 123456789012345678 987654321098765432` (Pull specific users by ID)

        Args:
            ctx (commands.Context): The command context.
            *args: One or more channel IDs, user mentions, or user IDs
        """
        # Check permissions first
        try:
            await self.check_admin_and_move_perms(ctx)
        except commands.MissingPermissions as e:
            await ctx.send("<a:sukoon_reddot:1322894157794119732> This command requires Administrator OR Move Members permission")
            return
        except commands.CheckFailure as e:
            await ctx.send(f"<a:sukoon_reddot:1322894157794119732> {str(e)}")
            return

        # Ensure we're working with a Member object, not a User
        if not isinstance(ctx.author, discord.Member):
            await ctx.send("<a:sukoon_reddot:1322894157794119732> This command can only be used in a server.")
            return

        user_id = ctx.author.id
        user_lock = self.get_user_lock(user_id)

        # Check if there's already an operation in progress for this user
        if user_lock.locked():
            await ctx.send("<a:heartspar:1335854160322498653> Please wait, you already have a voice operation in progress.")
            return

        async with user_lock:
            # Check if any arguments were provided
            if not args:
                await ctx.send("<a:sukoon_reddot:1322894157794119732> You need to specify either a channel ID or user mentions/IDs. Examples:\n"
                             "`.pull 123456789012345678` (Pull all from channel)\n"
                             "`.pull @user1 @user2` (Pull specific users)")
                return
            
            # Check if user is in a voice channel
            if not ctx.author.voice:
                await ctx.send("<a:sukoon_reddot:1322894157794119732> You need to be in a voice channel to use this command.")
                return

            target_channel = ctx.author.voice.channel

            # Check bot permissions for target channel
            has_target_perms, target_error = await self.check_bot_permissions(ctx, target_channel)
            if not has_target_perms:
                await ctx.send(f"<a:sukoon_reddot:1322894157794119732> {target_error}")
                return

            # First, try to interpret the first argument as a channel ID if it's the only argument
            if len(args) == 1:
                # Try to get a voice channel from the ID
                try:
                    # Convert the ID to an integer
                    channel_id_int = int(args[0])
                    
                    # Get the channel
                    source_channel = ctx.guild.get_channel(channel_id_int)
                    
                    # Check if it's a valid voice channel
                    if source_channel and isinstance(source_channel, (discord.VoiceChannel, discord.StageChannel)):
                        # It's a voice channel, so pull all members from it
                        # Check if source and target are the same
                        if source_channel.id == target_channel.id:
                            await ctx.send("<a:sukoon_reddot:1322894157794119732> Source and target channels are the same.")
                            return

                        # Check bot permissions for source channel
                        has_source_perms, source_error = await self.check_bot_permissions(ctx, source_channel)
                        if not has_source_perms:
                            await ctx.send(f"<a:sukoon_reddot:1322894157794119732> {source_error}")
                            return

                        # Get members to move (filter out bots)
                        members_to_move = [member for member in source_channel.members if not member.bot]

                        if not members_to_move:
                            await ctx.send(f"<:sukoon_info:1323251063910043659> No users found in {source_channel.name} to pull.")
                            return

                        # Send initial message
                        member_count = len(members_to_move)
                        if member_count > 10:
                            message = await ctx.send(f"<a:heartspar:1335854160322498653> Pulling {member_count} users from {source_channel.name} to {target_channel.name}. This may take some time...")
                        else:
                            message = await ctx.send(f"<a:heartspar:1335854160322498653> Pulling users from {source_channel.name} to {target_channel.name}...")

                        # Move members
                        moved_count, errors = await self.move_members(members_to_move, target_channel)

                        # Send feedback
                        if moved_count > 0:
                            await message.edit(content=f"<a:sukoon_whitetick:1323992464058482729> Successfully moved {moved_count}/{member_count} users to {target_channel.name}.")

                        if errors:
                            # Group similar errors to avoid long messages
                            unique_errors = list(set(errors))
                            if len(unique_errors) <= 3:
                                error_message = "\n".join(unique_errors)
                            else:
                                error_message = f"{len(errors)} errors occurred. Check the logs for details."

                            await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Some errors occurred:\n{error_message}")
                            for error in errors:
                                logger.warning(f"Pull command error (initiated by {ctx.author.id}): {error}")
                        
                        return
                except (ValueError, TypeError):
                    # Not a valid channel ID integer, treat as a user ID or mention
                    pass

            # If we get here, treat all arguments as user mentions or IDs
            members_to_move = []
            failed_ids = []

            for item in args:
                user_id = None
                
                # Check if it's a mention (e.g., <@123456789012345678>)
                if isinstance(item, str) and item.startswith('<@') and item.endswith('>'):
                    try:
                        user_id = int(item.strip('<@').strip('!').strip('>'))
                    except ValueError:
                        failed_ids.append(item)
                        continue
                # Check if it's a raw ID
                else:
                    try:
                        user_id = int(item)
                    except ValueError:
                        failed_ids.append(item)
                        continue

                # Get the member from the ID
                member = ctx.guild.get_member(user_id)
                if member and member.voice and member.voice.channel:
                    # The member is in a voice channel
                    source_channel = member.voice.channel
                    
                    # Skip if already in target channel
                    if source_channel.id == target_channel.id:
                        continue
                    
                    # Check bot permissions for source channel
                    has_source_perms, source_error = await self.check_bot_permissions(ctx, source_channel)
                    if not has_source_perms:
                        failed_ids.append(f"{member.display_name} ({source_error})")
                        continue
                    
                    members_to_move.append(member)
                else:
                    if member:
                        failed_ids.append(f"{member.display_name} (not in voice)")
                    else:
                        failed_ids.append(f"{item} (user not found)")

            if not members_to_move:
                await ctx.send("<a:sukoon_reddot:1322894157794119732> No valid users found to pull. Users must be in a voice channel.")
                if failed_ids:
                    await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Failed to find or pull: {', '.join(failed_ids[:5])}" + 
                                  (f" and {len(failed_ids) - 5} more..." if len(failed_ids) > 5 else ""))
                return

            # Send initial message
            member_count = len(members_to_move)
            message = await ctx.send(f"<a:heartspar:1335854160322498653> Pulling {member_count} user(s) to {target_channel.name}...")

            # Move members
            moved_count, errors = await self.move_members(members_to_move, target_channel)

            # Send feedback
            if moved_count > 0:
                await message.edit(content=f"<a:sukoon_whitetick:1323992464058482729> Successfully moved {moved_count}/{member_count} users to {target_channel.name}.")
            
            if failed_ids:
                await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Could not find or move: {', '.join(failed_ids[:5])}" + 
                              (f" and {len(failed_ids) - 5} more..." if len(failed_ids) > 5 else ""))

            if errors:
                # Group similar errors to avoid long messages
                unique_errors = list(set(errors))
                if len(unique_errors) <= 3:
                    error_message = "\n".join(unique_errors)
                else:
                    error_message = f"{len(errors)} errors occurred. Check the logs for details."

                await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Some errors occurred:\n{error_message}")
                for error in errors:
                    logger.warning(f"Pull command error (initiated by {ctx.author.id}): {error}")

    @commands.command(name="push", aliases=["ps"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.guild_only()
    @commands.check_any(
        commands.has_permissions(administrator=True),
        commands.has_permissions(move_members=True)
    )
    async def push(self, ctx: commands.Context, target_channel_id: str) -> None:
        """
        Push users from the command user's current voice channel to a target voice channel.

        Args:
            ctx (commands.Context): The command context.
            target_channel_id (str): The ID of the target voice channel to push users to.
        """
        # Check permissions first
        try:
            await self.check_admin_and_move_perms(ctx)
        except commands.MissingPermissions as e:
            await ctx.send("<a:sukoon_reddot:1322894157794119732> This command requires Administrator OR Move Members permission")
            return
        except commands.CheckFailure as e:
            await ctx.send(f"<a:sukoon_reddot:1322894157794119732> {str(e)}")
            return

        # Ensure we're working with a Member object, not a User
        if not isinstance(ctx.author, discord.Member):
            await ctx.send("<a:sukoon_reddot:1322894157794119732> This command can only be used in a server.")
            return

        user_id = ctx.author.id
        user_lock = self.get_user_lock(user_id)

        # Check if there's already an operation in progress for this user
        if user_lock.locked():
            await ctx.send("<a:heartspar:1335854160322498653> Please wait, you already have a voice operation in progress.")
            return

        async with user_lock:
            # Check if user is in a voice channel
            if not ctx.author.voice:
                await ctx.send("<a:sukoon_reddot:1322894157794119732> You need to be in a voice channel to use this command.")
                return

            source_channel = ctx.author.voice.channel

            # Get target channel from ID
            target_channel, error = await self.get_voice_channel(ctx, target_channel_id)
            if error:
                await ctx.send(f"<a:sukoon_reddot:1322894157794119732> {error}")
                return

            # Check if source and target are the same
            if source_channel.id == target_channel.id:
                await ctx.send("<a:sukoon_reddot:1322894157794119732> Source and target channels are the same.")
                return

            # Check bot permissions for both channels
            has_source_perms, source_error = await self.check_bot_permissions(ctx, source_channel)
            if not has_source_perms:
                await ctx.send(f"<a:sukoon_reddot:1322894157794119732> {source_error}")
                return

            has_target_perms, target_error = await self.check_bot_permissions(ctx, target_channel)
            if not has_target_perms:
                await ctx.send(f"<a:sukoon_reddot:1322894157794119732> {target_error}")
                return

            # Get members to move (exclude the command user and bots)
            members_to_move = [member for member in source_channel.members 
                               if member.id != ctx.author.id and not member.bot]

            if not members_to_move:
                await ctx.send(f"<:sukoon_info:1323251063910043659> No users found in {source_channel.name} to push.")
                return

            # Send initial message
            member_count = len(members_to_move)
            if member_count > 10:
                message = await ctx.send(f"<a:heartspar:1335854160322498653> Pushing {member_count} users from {source_channel.name} to {target_channel.name}. This may take some time...")
            else:
                message = await ctx.send(f"<a:heartspar:1335854160322498653> Pushing users from {source_channel.name} to {target_channel.name}...")

            # Move members
            moved_count, errors = await self.move_members(members_to_move, target_channel)

            # Send feedback
            if moved_count > 0:
                await message.edit(content=f"<a:sukoon_whitetick:1323992464058482729> Successfully moved {moved_count}/{member_count} users to {target_channel.name}.")

            if errors:
                # Group similar errors to avoid long messages
                unique_errors = list(set(errors))
                if len(unique_errors) <= 3:
                    error_message = "\n".join(unique_errors)
                else:
                    error_message = f"{len(errors)} errors occurred. Check the logs for details."

                await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Some errors occurred:\n{error_message}")
                for error in errors:
                    logger.warning(f"Push command error (initiated by {ctx.author.id}): {error}")

    @commands.command(name="kick", aliases=["k"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.guild_only()
    @commands.check_any(
        commands.has_permissions(administrator=True),
        commands.has_permissions(move_members=True)
    )
    async def kick(self, ctx: commands.Context, confirm: str, channel_id: str = None) -> None:
        """
        Disconnect all non-bot users from either the command user's current voice channel
        or a specified voice channel.

        Args:
            ctx (commands.Context): The command context.
            confirm (str): Confirmation string, must be 'all' to confirm the action.
            channel_id (str, optional): The ID of the voice channel to kick users from.
                                      If not provided, uses the author's current channel.
        """
        if confirm.lower() != "all":
            await ctx.send("<a:sukoon_reddot:1322894157794119732> To kick all users, use `.kick all` or `.kick all <channel_id>`")
            return

        # Check permissions first
        try:
            await self.check_admin_and_move_perms(ctx)
        except commands.MissingPermissions as e:
            await ctx.send("<a:sukoon_reddot:1322894157794119732> This command requires Administrator OR Move Members permission")
            return
        except commands.CheckFailure as e:
            await ctx.send(f"<a:sukoon_reddot:1322894157794119732> {str(e)}")
            return

        # Ensure we're working with a Member object, not a User
        if not isinstance(ctx.author, discord.Member):
            await ctx.send("<a:sukoon_reddot:1322894157794119732> This command can only be used in a server.")
            return

        user_id = ctx.author.id
        user_lock = self.get_user_lock(user_id)

        # Check if there's already an operation in progress for this user
        if user_lock.locked():
            await ctx.send("<a:heartspar:1335854160322498653> Please wait, you already have a voice operation in progress.")
            return

        async with user_lock:
            # Determine which voice channel to use
            if channel_id is not None:
                # Get target channel from ID
                voice_channel, error = await self.get_voice_channel(ctx, channel_id)
                if error:
                    await ctx.send(f"<a:sukoon_reddot:1322894157794119732> {error}")
                    return

                # If it's a remote channel and bot isn't there yet, we need to join it
                bot_member = ctx.guild.get_member(self.bot.user.id)
                bot_in_channel = False

                if hasattr(bot_member, 'voice') and bot_member.voice and bot_member.voice.channel:
                    if bot_member.voice.channel.id == voice_channel.id:
                        bot_in_channel = True
            else:
                # Check if user is in a voice channel
                if not ctx.author.voice:
                    await ctx.send("<a:sukoon_reddot:1322894157794119732> You need to be in a voice channel or specify a channel ID.")
                    return

                voice_channel = ctx.author.voice.channel
                bot_in_channel = True  # We don't need to join the user's channel

            # Check bot permissions for the voice channel
            has_perms, error = await self.check_bot_permissions(ctx, voice_channel)
            if not has_perms:
                await ctx.send(f"<a:sukoon_reddot:1322894157794119732> {error}")
                return

            # Get members to disconnect (exclude the command user and bots)
            members_to_kick = [member for member in voice_channel.members 
                               if member.id != ctx.author.id and not member.bot]

            if not members_to_kick:
                await ctx.send(f"<:sukoon_info:1323251063910043659> No users found in {voice_channel.name} to kick.")
                return

            # Connect to the channel if needed
            original_voice_state = None
            if not bot_in_channel:
                try:
                    # Save the bot's original voice state
                    bot_member = ctx.guild.get_member(self.bot.user.id)
                    if hasattr(bot_member, 'voice') and bot_member.voice and bot_member.voice.channel:
                        original_voice_state = bot_member.voice.channel

                    # Join the target voice channel
                    await voice_channel.connect()
                    await asyncio.sleep(1)  # Give a moment to establish the connection
                except Exception as e:
                    await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Error joining the voice channel: {str(e)}")
                    logger.error(f"Error joining voice channel {voice_channel.id}: {str(e)}")
                    return

            # Send initial message
            member_count = len(members_to_kick)
            if member_count > 10:
                message = await ctx.send(f"<a:heartspar:1335854160322498653> Disconnecting {member_count} users from {voice_channel.name}. This may take some time...")
            else:
                message = await ctx.send(f"<a:heartspar:1335854160322498653> Disconnecting {member_count} users from {voice_channel.name}...")

            # Use the common move_members method with None as the target (disconnects users)
            kicked_count, errors = await self.move_members(members_to_kick, None)

            # Leave the channel if we joined it
            if not bot_in_channel:
                try:
                    for vc in ctx.bot.voice_clients:
                        if vc.channel.id == voice_channel.id:
                            await vc.disconnect()
                            break

                    # Return to original channel if needed
                    if original_voice_state:
                        await original_voice_state.connect()
                except Exception as e:
                    logger.error(f"Error leaving voice channel {voice_channel.id}: {str(e)}")

            # Send feedback
            if kicked_count > 0:
                await message.edit(content=f"<a:sukoon_whitetick:1323992464058482729> Successfully disconnected {kicked_count}/{member_count} users from {voice_channel.name}.")

            if errors:
                # Group similar errors to avoid long messages
                unique_errors = list(set(errors))
                if len(unique_errors) <= 3:
                    error_message = "\n".join(unique_errors)
                else:
                    error_message = f"{len(errors)} errors occurred. Check the logs for details."

                await ctx.send(f"<a:sukoon_reddot:1322894157794119732> Some errors occurred:\n{error_message}")
                for error in errors:
                    logger.warning(f"Kick command error (initiated by {ctx.author.id}): {error}")



    @commands.command(name="vchelp", aliases=["vhelp", "voicehelp"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def voicehelp(self, ctx: commands.Context) -> None:
        """
        Show help information for voice manager commands.
        """
        embed = discord.Embed(
            title="Voice Manager Commands",
            description="Commands to manage users in voice channels",
            color=discord.Color.blue()
        )

        embed.add_field(
            name=".pull <channel_id or user_mentions/ids>",
            value="Pull users to your current voice channel. Works with channel IDs or user mentions/IDs.\n"
                  "Alias: `.p`\n"
                  "Examples: `.pull 123456789012345678` (all users from channel)\n"
                  "`.pull @user1 @user2` (specific users)\n"
                  "`.pull 123456789012345678 987654321098765432` (specific users by ID)",
            inline=False
        )

        embed.add_field(
            name=".push <target_channel_id>",
            value="Push all users from your current voice channel to a target voice channel.\n"
                  "Alias: `.ps`\n"
                  "Example: `.push 123456789012345678`",
            inline=False
        )



        embed.add_field(
            name=".kick all [channel_id]",
            value="Disconnect all users from your current voice channel or a specified channel.\n"
                  "Alias: `.k all`\n"
                  "Examples: `.kick all` (current channel) or `.kick all 123456789012345678` (specified channel)",
            inline=False
        )

        embed.add_field(
            name="How to get channel IDs",
            value="Right-click on a voice channel in Discord and select 'Copy ID'.\n"
                  "You must have Developer Mode enabled in Discord settings.",
            inline=False
        )

        embed.add_field(
            name="Moving Large Numbers of Members",
            value="All commands support moving large groups (20, 30, 40+ members) with automatic rate limiting to avoid Discord API limits.",
            inline=False
        )
            
        embed.set_footer(text="Administrator OR Move Members permission required. Bot must have Connect & Move Members permissions in all channels.")

        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """
        Handle command errors for this cog.

        Args:
            ctx (commands.Context): The command context.
            error (commands.CommandError): The error that occurred.
        """
        if hasattr(ctx.command, "qualified_name") and ctx.command.qualified_name in ["pull", "push", "kick", "vchelp"]:
            command_name = ctx.command.qualified_name

            if isinstance(error, commands.CommandOnCooldown):
                # Round up to the nearest second for better readability
                cooldown_time = int(error.retry_after) + (1 if error.retry_after % 1 > 0 else 0)
                await ctx.send(f"<a:heartspa:1363961371708096624> Please wait {cooldown_time}s before using the `{command_name}` command again.")
                return

            elif isinstance(error, commands.MissingPermissions):
                # Always show the required permissions, even if missing only one
                await ctx.send("<a:sukoon_reddot:1322894157794119732> This command requires Administrator OR Move Members permission")
                return

            elif isinstance(error, commands.MissingRequiredArgument):
                if command_name == "pull":
                    await ctx.send("<a:sukoon_reddot:1322894157794119732> You need to specify either a channel ID or user mentions/IDs. Examples:\n"
                                 "`.pull 123456789012345678` (Pull all from channel)\n"
                                 "`.pull @user1 @user2` (Pull specific users)")
                elif command_name == "push":
                    await ctx.send("<a:sukoon_reddot:1322894157794119732> You need to specify a target channel ID. Usage: `.push <target_channel_id>`")
                elif command_name == "kick":
                    await ctx.send("<a:sukoon_reddot:1322894157794119732> You need to confirm the action. Usage: `.kick all`")
                return

            elif isinstance(error, commands.NoPrivateMessage):
                await ctx.send("<a:sukoon_reddot:1322894157794119732> This command can only be used in a server.")
                return

            # Log other errors
            logger.error(f"Error in {command_name} command: {str(error)}")

async def setup(bot: commands.Bot) -> None:
    """
    Setup function to add the cog to the bot.

    Args:
        bot (commands.Bot): The Discord bot instance.
    """
    # Initialize with a batch size of 25 members and a rate limit delay of 1.2 seconds
    # This helps prevent Discord API rate limits when moving many members at once
    await bot.add_cog(VoiceManager(bot, rate_limit_delay=1.2, batch_size=25))
