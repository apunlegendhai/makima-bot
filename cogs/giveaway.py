import discord
import random
from datetime import datetime
from discord.ext import commands, tasks
from discord import ButtonStyle, ui
import logging
from logging.handlers import RotatingFileHandler
import asyncio
from typing import List, Dict
import pytz
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Constants
REACTION_EMOJI    = "<:sukoon_taaada:1324071825910792223>"
DOT_EMOJI         = "<:sukoon_blackdot:1322894649488314378>"
RED_DOT_EMOJI     = "<:sukoon_redpoint:1322894737736339459>"
EMBED_COLOR       = 0x2f3136
CLEANUP_INTERVAL  = 60  # seconds

class GiveawayEndedView(ui.View):
    """View with a disabled button showing participant count."""
    def __init__(self, participant_count: int):
        super().__init__(timeout=None)
        self.add_item(ui.Button(
            style=ButtonStyle.gray,
            label=f"🎉 {participant_count}",
            disabled=True
        ))

class DatabaseManager:
    """Manages MongoDB interactions."""
    def __init__(self, mongo_uri: str, database_name: str):
        self.client = AsyncIOMotorClient(mongo_uri)
        self.db = self.client[database_name]
        self.giveaways_collection      = self.db['giveaways']
        self.participants_collection   = self.db['participants']
        self.fake_reactions_collection = self.db['fake_reactions']

    async def init(self):
        await self.giveaways_collection.create_index('message_id', unique=True)
        await self.participants_collection.create_index(
            [('message_id', 1), ('user_id', 1)], unique=True
        )
        await self.fake_reactions_collection.create_index('message_id')

    def close(self):
        if self.client:
            self.client.close()

class Giveaway(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        mongo_uri     = os.getenv('MONGO_URL')
        database_name = os.getenv('MONGO_DATABASE', 'giveaway_bot')

        # Logging
        log_file = os.getenv('LOG_FILE', 'giveaway_logs.log')
        self.logger = logging.getLogger('GiveawayBot')
        handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3)
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.WARNING)

        # DB and tasks
        self.db  = DatabaseManager(mongo_uri, database_name)
        self._ready = asyncio.Event()
        self._checking_lock = asyncio.Lock()
        
        # Timezone - Using Indian Standard Time (IST)
        self.timezone = 'Asia/Kolkata'

        # Track running fake-reaction tasks
        self.active_fake_reaction_tasks: Dict[str, asyncio.Task] = {}

    async def cog_load(self):
        await self.db.init()
        # Start background tasks after DB initialization
        self.check_giveaways.start()
        self.process_fake_reactions.start()
        self._ready.set()

    def cog_unload(self):
        self.check_giveaways.cancel()
        self.process_fake_reactions.cancel()
        for task in self.active_fake_reaction_tasks.values():
            task.cancel()
        self.db.close()

    async def check_bot_permissions(self, channel: discord.abc.GuildChannel):
        perms = channel.permissions_for(channel.guild.me)
        needed = {'send_messages', 'embed_links', 'add_reactions', 'read_message_history'}
        return all(getattr(perms, p, False) for p in needed)

    @tasks.loop(seconds=CLEANUP_INTERVAL)
    async def check_giveaways(self):
        """Ends giveaways whose end_time has passed."""
        await self._ready.wait()
        async with self._checking_lock:
            now = int(datetime.utcnow().timestamp())
            active = await self.db.giveaways_collection.find({
                'end_time': {'$lte': now}, 'status': 'active'
            }).to_list(length=None)
            for gw in active:
                await self.end_giveaway(gw['message_id'])

    @tasks.loop(minutes=1)
    async def process_fake_reactions(self):
        """On bot restart, resume any in-progress fake-reaction plans."""
        await self._ready.wait()
        try:
            plans = await self.db.fake_reactions_collection.find({'status': 'active'}).to_list(length=None)
            for plan in plans:
                mid = plan['message_id']
                if mid in self.active_fake_reaction_tasks:
                    continue

                gw = await self.db.giveaways_collection.find_one({'message_id': mid, 'status': 'active'})
                if not gw:
                    await self.db.fake_reactions_collection.update_one(
                        {'message_id': mid},
                        {'$set': {'status':'cancelled','cancelled_at':int(datetime.utcnow().timestamp())}}
                    )
                    continue

                channel = self.bot.get_channel(plan['channel_id'])
                if not channel or not isinstance(channel, discord.TextChannel):
                    continue

                members = [str(m.id) for m in channel.guild.members if not m.bot]
                if not members:
                    continue

                remaining = plan.get('remaining_reactions', 0)
                end_time  = plan.get('end_time', 0)
                if remaining > 0 and end_time > datetime.utcnow().timestamp():
                    task = asyncio.create_task(
                        self.add_fake_reactions(mid, members, remaining, end_time)
                    )
                    self.active_fake_reaction_tasks[mid] = task

        except Exception as e:
            self.logger.error(f"process_fake_reactions error: {e}")

    @discord.app_commands.command(name="giveaway", description="Start a new giveaway")
    @discord.app_commands.guild_only()
    @discord.app_commands.default_permissions(administrator=True)
    async def start_giveaway(
        self,
        interaction: discord.Interaction,
        duration: str,
        winners: int,
        prize: str
    ):
        """Starts a new giveaway."""
        try:
            await interaction.response.defer(ephemeral=True)
            if not await self.check_bot_permissions(interaction.channel):
                return await interaction.followup.send("I need proper permissions.", ephemeral=True)

            if not 1 <= winners <= 20:
                raise ValueError("Winners must be between 1 and 20.")

            units = {"s":1,"m":60,"h":3600,"d":86400}
            unit = duration[-1].lower()
            if unit not in units or not duration[:-1].isdigit():
                raise ValueError("Use number + s/m/h/d (e.g., 1h).")
            secs = int(duration[:-1]) * units[unit]
            if not 30 <= secs <= 2592000:
                raise ValueError("Duration must be between 30s and 30d.")

            end_ts = int(datetime.utcnow().timestamp() + secs)

            await interaction.channel.send("**<:sukoon_taaada:1324071825910792223> GIVEAWAY <:sukoon_taaada:1324071825910792223>**")

            # Convert UTC timestamp to local time with proper timezone formatting
            end_dt = datetime.utcfromtimestamp(end_ts).replace(tzinfo=pytz.utc)
            local  = end_dt.astimezone(pytz.timezone(self.timezone))
            
            # Format the time properly (without leading zero in hours)
            time_part = local.strftime("%I:%M %p").lstrip("0")
            
            # Create the full formatted time string
            formatted = (
                f"{local.strftime('%A')} at {time_part}"
                if local.date() > datetime.utcnow().date()
                else f"Today at {time_part}"
            )

            # Create embed without participant count (will be shown via reactions)
            embed = discord.Embed(
                description=(
                    f"{DOT_EMOJI} Ends: <t:{end_ts}:R>\n"
                    f"{DOT_EMOJI} Hosted by: {interaction.user.mention}"
                ),
                color=EMBED_COLOR
            )
            embed.set_author(name=prize, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
            embed.set_footer(text=f"Ends at • {formatted}")

            message = await interaction.channel.send(embed=embed)
            await message.add_reaction(REACTION_EMOJI)

            doc = {
                'message_id': str(message.id),
                'channel_id': interaction.channel.id,
                'end_time': end_ts,
                'winners_count': winners,
                'prize': prize,
                'status': 'active',
                'host_id': interaction.user.id,
                'created_at': int(datetime.utcnow().timestamp()),
                'winner_ids': [],
                'forced_winner_ids': []
            }
            await self.db.giveaways_collection.insert_one(doc)
            await interaction.followup.send("Giveaway started!", ephemeral=True)

        except ValueError as e:
            await interaction.followup.send(f"Error: {e}", ephemeral=True)
        except Exception as e:
            self.logger.error(f"Error starting giveaway: {e}")
            await interaction.followup.send("Unexpected error.", ephemeral=True)

    async def end_giveaway(self, message_id: str):
        """Ends an active giveaway and announces winners."""
        try:
            giveaway = await self.db.giveaways_collection.find_one({
                'message_id': message_id,
                'status': 'active'
            })
            if not giveaway:
                return

            channel = self.bot.get_channel(giveaway['channel_id'])
            if not channel:
                await self.db.giveaways_collection.update_one(
                    {'message_id': message_id},
                    {'$set': {'status':'error','error':'Channel not found'}}
                )
                return

            try:
                message = await channel.fetch_message(int(message_id))
            except discord.NotFound:
                await self.db.giveaways_collection.update_one(
                    {'message_id': message_id},
                    {'$set': {'status':'error','error':'Message not found'}}
                )
                return
            except discord.Forbidden:
                await self.db.giveaways_collection.update_one(
                    {'message_id': message_id},
                    {'$set': {'status':'error','error':'Permission denied'}}
                )
                return

            if not await self.check_bot_permissions(channel):
                await self.db.giveaways_collection.update_one(
                    {'message_id': message_id},
                    {'$set': {'status':'error','error':'Missing permissions'}}
                )
                return

            parts = await self.db.participants_collection.find({
                'message_id': message_id
            }).to_list(length=None)
            valid = [p['user_id'] for p in parts if p['user_id'] != str(self.bot.user.id)]

            forced_winners = giveaway.get('forced_winner_ids', [])
            winners = []
            if forced_winners:
                # Add any forced winners that aren't already in participants
                for forced_id in forced_winners:
                    if forced_id not in valid:
                        valid.append(forced_id)
                
                # Start with the forced winners
                winners = forced_winners.copy()
                
                # If we need more winners than forced ones
                cnt = giveaway.get('winners_count', 1)
                if cnt > len(forced_winners):
                    # Get other participants excluding forced winners
                    others = [u for u in valid if u not in forced_winners]
                    if others:
                        # Add random winners to fill remaining spots
                        winners += random.sample(others, min(len(others), cnt - len(forced_winners)))
            else:
                # No forced winners, pick randomly
                cnt = giveaway.get('winners_count', 1)
                winners = random.sample(valid, min(len(valid), cnt)) if valid else []

            mentions = [f"<@{w}>" for w in winners] if winners else ["No winners (no participants)."]

            # Format end time for ended giveaway
            end_dt = datetime.utcnow().replace(tzinfo=pytz.utc)
            local  = end_dt.astimezone(pytz.timezone(self.timezone))
            # Remove leading zero from hour
            time_part = local.strftime("%I:%M %p").lstrip("0")
            # Format date as MM/DD/YY
            date_part = local.strftime("%m/%d/%y")
            formatted = f"{date_part}, {time_part}"

            # Create an embed without the participant count line since we'll use a button for that
            embed = discord.Embed(
                description=(
                    f"{DOT_EMOJI} Ended: <t:{int(datetime.utcnow().timestamp())}:R>\n"
                    f"{RED_DOT_EMOJI} Winners: {', '.join(mentions)}\n"
                    f"{DOT_EMOJI} Hosted by: <@{giveaway['host_id']}>"
                ),
                color=EMBED_COLOR
            )
            embed.set_author(name=giveaway['prize'], icon_url=channel.guild.icon.url if channel.guild.icon else None)
            embed.set_footer(text=f"Ended at • {formatted}")
            
            # Calculate total participants (real + fake)
            real_participants_count = len(valid)
            fake_reactions_plan = await self.db.fake_reactions_collection.find_one({'message_id': message_id})
            fake_count = fake_reactions_plan.get('total_reactions', 0) if fake_reactions_plan else 0
            total_participants = real_participants_count + fake_count
            
            # Create a view with a grey button showing the participant count
            view = GiveawayEndedView(total_participants)
            
            # Edit the message with the new embed and view, removing existing reactions
            await message.clear_reactions()
            await message.edit(embed=embed, view=view)

            if winners:
                await message.reply(
                    f"{REACTION_EMOJI} Congratulations {', '.join(mentions)}! You won **{giveaway['prize']}**!"
                )

            await self.db.giveaways_collection.update_one(
                {'message_id': message_id},
                {'$set': {
                    'status':'ended',
                    'winner_ids': winners,
                    'ended_at': int(datetime.utcnow().timestamp())
                }}
            )

            task = self.active_fake_reaction_tasks.pop(message_id, None)
            if task:
                task.cancel()

        except Exception as e:
            self.logger.error(f"Error ending giveaway {message_id}: {e}")
            await self.db.giveaways_collection.update_one(
                {'message_id': message_id},
                {'$set': {'status':'error','error':str(e)}}
            )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        gw = await self.db.giveaways_collection.find_one({
            'message_id': str(payload.message_id),
            'status': 'active'
        })
        if not gw or str(payload.emoji) != REACTION_EMOJI:
            return
        await self.db.participants_collection.update_one(
            {'message_id': str(payload.message_id), 'user_id': str(payload.user_id)},
            {'$set': {
                'message_id': str(payload.message_id),
                'user_id': str(payload.user_id),
                'joined_at': int(datetime.utcnow().timestamp())
            }},
            upsert=True
        )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        gw = await self.db.giveaways_collection.find_one({'message_id': str(payload.message_id)})
        if not gw or str(payload.emoji) != REACTION_EMOJI:
            return
        await self.db.participants_collection.delete_one({
            'message_id': str(payload.message_id),
            'user_id': str(payload.user_id)
        })

    @commands.command(name="reroll")
    @commands.has_permissions(manage_guild=True)
    async def reroll_giveaway(self, ctx):
        if not ctx.message.reference or not ctx.message.reference.message_id:
            return await ctx.send("Reply to a giveaway message to reroll.", ephemeral=True)
        try:
            if not await self.check_bot_permissions(ctx.channel):
                return await ctx.send("Missing permissions.", ephemeral=True)

            orig = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            if not orig.embeds or not orig.embeds[0].author:
                return await ctx.send("Not a giveaway message.", ephemeral=True)

            gw = await self.db.giveaways_collection.find_one({
                'message_id': str(orig.id), 'status': 'ended'
            })
            if not gw:
                return await ctx.send("Giveaway hasn't ended or cannot be rerolled.", ephemeral=True)

            prev = gw.get('winner_ids', [])
            parts = await self.db.participants_collection.find({
                'message_id': str(orig.id)
            }).to_list(None)
            valid = [str(p['user_id']) for p in parts if str(p['user_id']) != str(self.bot.user.id)]
            valid = [u for u in valid if u not in prev]
            if not valid:
                return await ctx.send("No participants left for reroll.", ephemeral=True)

            cnt = gw.get('winners_count', 1)
            new = random.sample(valid, min(len(valid), cnt))
            mentions = [f"<@{u}>" for u in new]

            # Format reroll time consistently
            now = datetime.utcnow().replace(tzinfo=pytz.utc)
            local = now.astimezone(pytz.timezone(self.timezone))
            # Remove leading zero from hour
            time_part = local.strftime("%I:%M %p").lstrip("0")
            # Format date as MM/DD/YY
            date_part = local.strftime("%m/%d/%y")
            formatted = f"{date_part}, {time_part}"

            # Create an embed without the participant count for reroll too
            embed = discord.Embed(
                description=(
                    f"{DOT_EMOJI} Rerolled: <t:{int(datetime.utcnow().timestamp())}:R>\n"
                    f"{RED_DOT_EMOJI} Winners: {', '.join(mentions)}\n"
                    f"{DOT_EMOJI} Hosted by: <@{gw['host_id']}>"
                ),
                color=EMBED_COLOR
            )
            embed.set_author(name=gw['prize'], icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
            embed.set_footer(text=f"Rerolled at • {formatted}")
            
            # Get participant count for the button
            parts = await self.db.participants_collection.find({'message_id': str(orig.id)}).to_list(None)
            real_count = len([p for p in parts if not p.get('is_fake', False)])
            fake_count = len([p for p in parts if p.get('is_fake', False)])
            total_participants = real_count + fake_count
            
            # Create a view with a grey button showing the participant count
            view = GiveawayEndedView(total_participants)
            
            # Edit the message with the new embed and view
            await orig.edit(embed=embed, view=view)

            await self.db.giveaways_collection.update_one(
                {'message_id': str(orig.id)},
                {'$set': {
                    'winner_ids': new,
                    'rerolled_at': int(datetime.utcnow().timestamp()),
                    'rerolled_by': ctx.author.id
                }}
            )

            await ctx.send(f"{REACTION_EMOJI} Congratulations {', '.join(mentions)}! You won **{gw['prize']}**!")
        except Exception as e:
            self.logger.error(f"Error rerolling: {e}")
            await ctx.send(f"Error rerolling: {e}", ephemeral=True)

    @discord.app_commands.command(
        name="fill_giveaway",
        description="Gradually fill a giveaway with fake reactions"
    )
    @discord.app_commands.guild_only()
    @discord.app_commands.default_permissions(administrator=True)
    async def fill_giveaway(
        self,
        interaction: discord.Interaction,
        message_id: str,
        total_fake_reactions: int,
        duration_in_minutes: int
    ):
        try:
            await interaction.response.defer(ephemeral=True)
            if not (1 <= total_fake_reactions <= 1000):
                return await interaction.followup.send(
                    "Total fake reactions must be 1–1000.", ephemeral=True
                )
            if not (1 <= duration_in_minutes <= 10080):
                return await interaction.followup.send(
                    "Duration must be 1–10080 minutes.", ephemeral=True
                )

            gw = await self.db.giveaways_collection.find_one({
                'message_id': message_id, 'status': 'active'
            })
            if not gw:
                return await interaction.followup.send("Not an active giveaway.", ephemeral=True)

            if message_id in self.active_fake_reaction_tasks:
                self.active_fake_reaction_tasks[message_id].cancel()

            channel = self.bot.get_channel(gw['channel_id'])
            try:
                message = await channel.fetch_message(int(message_id))
            except:
                return await interaction.followup.send(
                    "Couldn't fetch giveaway message.", ephemeral=True
                )

            members = [str(m.id) for m in channel.guild.members if not m.bot]
            if not members:
                return await interaction.followup.send(
                    "No valid members.", ephemeral=True
                )

            end_time = datetime.utcnow().timestamp() + duration_in_minutes*60
            plan = {
                'message_id': message_id,
                'channel_id': gw['channel_id'],
                'total_reactions': total_fake_reactions,
                'remaining_reactions': total_fake_reactions,
                'end_time': end_time,
                'created_by': interaction.user.id,
                'created_at': datetime.utcnow().timestamp(),
                'status': 'active'
            }
            await self.db.fake_reactions_collection.update_one(
                {'message_id': message_id}, {'$set': plan}, upsert=True
            )

            task = asyncio.create_task(
                self.add_fake_reactions(message_id, members, total_fake_reactions, end_time)
            )
            self.active_fake_reaction_tasks[message_id] = task

            await interaction.followup.send(
                f"Started fake fill: {total_fake_reactions} over {duration_in_minutes} min.",
                ephemeral=True
            )
        except Exception as e:
            self.logger.error(f"fill_giveaway error: {e}")
            await interaction.followup.send(f"Error: {e}", ephemeral=True)

    async def add_fake_reactions(
        self,
        message_id: str,
        member_ids: List[str],
        total_reactions: int,
        end_time: float
    ):
        """Adds fake reactions by updating the embed’s participant count."""
        try:
            gw = await self.db.giveaways_collection.find_one({'message_id': message_id})
            channel = self.bot.get_channel(gw['channel_id'])
            message = await channel.fetch_message(int(message_id))
            embed = message.embeds[0]

            remaining = total_reactions
            while remaining > 0 and not asyncio.current_task().cancelled():
                now = datetime.utcnow().timestamp()
                if now >= end_time:
                    break

                active = await self.db.giveaways_collection.find_one({
                    'message_id': message_id, 'status': 'active'
                })
                if not active:
                    break

                parts = await self.db.participants_collection.find({
                    'message_id': message_id
                }).to_list(None)
                existing = {p['user_id'] for p in parts}
                available = [mid for mid in member_ids if mid not in existing]
                if not available:
                    available = member_ids.copy()

                user_id = random.choice(available)
                await self.db.participants_collection.update_one(
                    {'message_id': message_id, 'user_id': user_id},
                    {'$set': {
                        'message_id': message_id,
                        'user_id': user_id,
                        'joined_at': int(now),
                        'is_fake': True
                    }},
                    upsert=True
                )

                remaining -= 1
                await self.db.fake_reactions_collection.update_one(
                    {'message_id': message_id},
                    {'$set': {'remaining_reactions': remaining}}
                )

                total_count = await self.db.participants_collection.count_documents({
                    'message_id': message_id
                })
                # Update embed without showing participant count
                embed.description = (
                    f"{DOT_EMOJI} Ends: <t:{gw['end_time']}:R>\n"
                    f"{DOT_EMOJI} Hosted by: <@{gw['host_id']}>"
                )
                await message.edit(embed=embed)

                avg = max((end_time - now) / max(1, remaining), 1)
                delay = random.uniform(avg*0.5, avg*1.5)
                if now + delay > end_time:
                    break
                await asyncio.sleep(delay)

            await self.db.fake_reactions_collection.update_one(
                {'message_id': message_id},
                {'$set': {
                    'status':'completed',
                    'completed_at': datetime.utcnow().timestamp(),
                    'remaining_reactions': 0
                }}
            )
        except asyncio.CancelledError:
            await self.db.fake_reactions_collection.update_one(
                {'message_id': message_id},
                {'$set': {
                    'status':'cancelled',
                    'cancelled_at': datetime.utcnow().timestamp()
                }}
            )
        except Exception as e:
            self.logger.error(f"add_fake_reactions error for {message_id}: {e}")
            await self.db.fake_reactions_collection.update_one(
                {'message_id': message_id},
                {'$set': {'status':'error','error':str(e)}}
            )
        finally:
            self.active_fake_reaction_tasks.pop(message_id, None)

    @discord.app_commands.command(
        name="force_winner",
        description="Force specific users to win a giveaway (comma-separated user IDs)"
    )
    @discord.app_commands.guild_only()
    @discord.app_commands.default_permissions(administrator=True)
    async def force_winner(
        self,
        interaction: discord.Interaction,
        message_id: str,
        user_ids: str
    ):
        """Forces specific users to win a giveaway. Provide comma-separated list of user IDs."""
        try:
            await interaction.response.defer(ephemeral=True)
            
            # Parse the comma-separated user IDs
            # Remove spaces, accept multiple formats like "123, 456" or "123,456" or even "123 456"
            user_ids = user_ids.replace(' ', '') if user_ids else ''
            user_id_list = [uid.strip() for uid in user_ids.split(',') if uid.strip()]
            
            if not user_id_list:
                return await interaction.followup.send("Please provide at least one valid user ID.", ephemeral=True)
            
            # Validate all user IDs are numbers
            valid_ids = []
            for uid in user_id_list:
                try:
                    valid_ids.append(str(int(uid)))  # Convert to int and back to string to ensure clean format
                except ValueError:
                    return await interaction.followup.send(f"Invalid user ID: {uid}. All IDs must be valid numbers.", ephemeral=True)
            
            # Use the validated list
            user_id_list = valid_ids
                
            # Find the active giveaway
            gw = await self.db.giveaways_collection.find_one({
                'message_id': message_id, 'status': 'active'
            })
            if not gw:
                return await interaction.followup.send("Not an active giveaway.", ephemeral=True)

            # Get the channel
            channel = self.bot.get_channel(int(gw['channel_id']))
            if not channel:
                return await interaction.followup.send("Couldn't find the channel.", ephemeral=True)

            # Try to get the message to verify it exists
            try:
                message = await channel.fetch_message(int(message_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return await interaction.followup.send("Couldn't fetch giveaway message.", ephemeral=True)

            # Verify all users exist
            for uid in user_id_list:
                try:
                    user = await self.bot.fetch_user(int(uid))
                    if not user:
                        return await interaction.followup.send(f"Couldn't find user with ID: {uid}", ephemeral=True)
                except discord.NotFound:
                    # Allow forcing winner with any valid ID, even if not in server
                    pass
                except Exception as e:
                    return await interaction.followup.send(f"Error checking user {uid}: {str(e)}", ephemeral=True)

            # Update the database with the array of forced winners
            await self.db.giveaways_collection.update_one(
                {'message_id': message_id},
                {'$set': {'forced_winner_ids': user_id_list}}
            )
            
            # Add all users as participants
            for uid in user_id_list:
                await self.db.participants_collection.update_one(
                    {'message_id': message_id, 'user_id': uid},
                    {'$set': {
                        'message_id': message_id,
                        'user_id': uid,
                        'joined_at': int(datetime.utcnow().timestamp()),
                        'is_forced': True
                    }},
                    upsert=True
                )
            
            # Update the message for confirmation
            embed = message.embeds[0]
            if not embed:
                return await interaction.followup.send("No embed found in giveaway message.", ephemeral=True)
                
            # Success - notify user
            mentions = [f"<@{uid}>" for uid in user_id_list]
            count = len(user_id_list)
            await interaction.followup.send(
                f"{count} user{'s' if count > 1 else ''} ({', '.join(mentions)}) {'have' if count > 1 else 'has'} been set as forced {'winners' if count > 1 else 'winner'} for this giveaway.", 
                ephemeral=True
            )
        except Exception as e:
            self.logger.error(f"force_winner error: {e}")
            await interaction.followup.send(f"Error setting forced winner: {str(e)}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Giveaway(bot))
