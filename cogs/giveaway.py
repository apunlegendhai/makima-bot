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
ENTRIES_PER_PAGE  = 20  # Number of participants to show per page

class EntriesView(ui.View):
    """Persistent view for displaying giveaway entries with pagination."""

    def __init__(self, message_id: str, db_manager):
        super().__init__(timeout=None)
        self.message_id = message_id
        self.db = db_manager
        self.current_page = 0

    @ui.button(label="📊 Entries", style=ButtonStyle.secondary)
    async def entries_button(self, interaction: discord.Interaction, button: ui.Button):
        """Show entries for this giveaway."""
        button.custom_id = f"entries:{self.message_id}"
        await self.show_entries(interaction)

    async def show_entries(self, interaction: discord.Interaction, page: int = 0):
        """Display the entries embed with pagination."""
        try:
            await interaction.response.defer(ephemeral=True)

            # Get giveaway data
            giveaway = await self.db.giveaways_collection.find_one({
                'message_id': self.message_id
            })

            if not giveaway:
                await interaction.followup.send("Giveaway not found!", ephemeral=True)
                return

            # Get real participants
            real_participants = await self.db.participants_collection.find({
                'message_id': self.message_id
            }).to_list(length=None)

            # Get fake participants data
            fake_reaction_plan = await self.db.fake_reactions_collection.find_one({
                'message_id': self.message_id
            })

            # Collect all participant IDs
            all_participants = []

            # Add real participants
            for p in real_participants:
                user_id = p['user_id']
                if user_id != str(interaction.client.user.id):  # Exclude bot
                    all_participants.append({
                        'id': user_id,
                        'type': 'real'
                    })

            # Add fake participants if they exist (but treat them as real to hide fake nature)
            if fake_reaction_plan and 'fake_participants' in fake_reaction_plan:
                for fake_id in fake_reaction_plan['fake_participants']:
                    # Only add if not already in real participants
                    if not any(p['id'] == fake_id for p in all_participants):
                        all_participants.append({
                            'id': fake_id,
                            'type': 'real'  # Show as real to hide fake nature
                        })

            total_participants = len(all_participants)

            if total_participants == 0:
                embed = discord.Embed(
                    title="📊 Giveaway Entries",
                    description="No participants found for this giveaway.",
                    color=EMBED_COLOR
                )
                embed.add_field(name="Prize", value=giveaway.get('prize', 'Unknown'), inline=False)
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Calculate pagination
            total_pages = max(1, (total_participants + ENTRIES_PER_PAGE - 1) // ENTRIES_PER_PAGE)
            page = max(0, min(page, total_pages - 1))  # Ensure page is within bounds

            start_idx = page * ENTRIES_PER_PAGE
            end_idx = min(start_idx + ENTRIES_PER_PAGE, total_participants)
            page_participants = all_participants[start_idx:end_idx]

            # Create simple embed showing only participants
            embed = discord.Embed(
                title="Participants List",
                color=0x5865F2  # Beautiful Discord blue
            )

            # Add participants list with clean formatting
            participant_text = ""
            for i, participant in enumerate(page_participants, start=start_idx + 1):
                user_id = participant['id']

                # Handle fake participants - use original_user_id if available
                if participant.get('is_fake', False) and 'original_user_id' in participant:
                    display_user_id = participant['original_user_id']
                else:
                    display_user_id = user_id.split('_fake_')[0] if '_fake_' in str(user_id) else user_id

                # Try to get user info
                try:
                    user = interaction.client.get_user(int(display_user_id))
                    if user:
                        display_name = f"{user.display_name}"
                        username = f"@{user.name}"
                        participant_text += f"`{i:3d}.` **{display_name}** ({username})\n"
                    else:
                        participant_text += f"`{i:3d}.` User ID: {display_user_id}\n"
                except:
                    participant_text += f"`{i:3d}.` User ID: {display_user_id}\n"

            if participant_text:
                embed.description = participant_text

            # Clean footer with pagination info
            embed.set_footer(
                text=f"Page {page + 1} of {total_pages} | {total_participants} total participants"
            )

            # Create pagination view
            view = EntriesPaginationView(self.message_id, self.db, page, total_pages)

            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        except Exception as e:
            logging.error(f"Error showing entries: {e}")
            await interaction.followup.send("An error occurred while loading entries.", ephemeral=True)

class EntriesPaginationView(ui.View):
    """View for paginating through entries."""

    def __init__(self, message_id: str, db_manager, current_page: int, total_pages: int):
        super().__init__(timeout=300)  # 5 minute timeout for pagination
        self.message_id = message_id
        self.db = db_manager
        self.current_page = current_page
        self.total_pages = total_pages

        # Disable buttons if only one page
        if total_pages <= 1:
            self.first_page.disabled = True
            self.previous_page.disabled = True
            self.next_page.disabled = True
            self.last_page.disabled = True
        else:
            # Update button states
            self.first_page.disabled = current_page == 0
            self.previous_page.disabled = current_page == 0
            self.next_page.disabled = current_page >= total_pages - 1
            self.last_page.disabled = current_page >= total_pages - 1

    @ui.button(label="⏪", style=ButtonStyle.secondary)
    async def first_page(self, interaction: discord.Interaction, button: ui.Button):
        """Go to first page."""
        entries_view = EntriesView(self.message_id, self.db)
        await entries_view.show_entries(interaction, 0)

    @ui.button(label="◀️", style=ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: ui.Button):
        """Go to previous page."""
        entries_view = EntriesView(self.message_id, self.db)
        await entries_view.show_entries(interaction, max(0, self.current_page - 1))

    @ui.button(label="▶️", style=ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: ui.Button):
        """Go to next page."""
        entries_view = EntriesView(self.message_id, self.db)
        await entries_view.show_entries(interaction, min(self.total_pages - 1, self.current_page + 1))

    @ui.button(label="⏩", style=ButtonStyle.secondary)
    async def last_page(self, interaction: discord.Interaction, button: ui.Button):
        """Go to last page."""
        entries_view = EntriesView(self.message_id, self.db)
        await entries_view.show_entries(interaction, self.total_pages - 1)

class GiveawayEndedView(ui.View):
    """View with persistent buttons that work forever."""
    def __init__(self, participant_count: int, message_id: str, db_manager, bot):
        super().__init__(timeout=None)
        self.message_id = message_id
        self.db = db_manager
        self.bot = bot

    @ui.button(label="", style=ButtonStyle.gray, disabled=False, custom_id="count_persistent")
    async def count_button(self, interaction: discord.Interaction, button: ui.Button):
        """Button showing participant count - tells users giveaway ended."""
        await interaction.response.send_message(
            "🎉 This giveaway has ended! You can no longer participate, but you can check the entries to see all participants.",
            ephemeral=True
        )

    @ui.button(label="Entries", style=ButtonStyle.secondary, custom_id="entries_persistent")
    async def entries_button(self, interaction: discord.Interaction, button: ui.Button):
        """Show entries for this giveaway."""
        print(f"Entries button clicked for giveaway {self.message_id}")
        entries_view = EntriesView(self.message_id, self.db)
        await entries_view.show_entries(interaction)

    def update_count_button(self, participant_count: int):
        """Update the count button label with custom emoji and count."""
        # Use the party emoji since the custom emoji isn't accessible
        self.count_button.label = f"🎉 {participant_count}"

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

        # Logging setup - store logs in logs folder
        os.makedirs('logs', exist_ok=True)
        log_file = os.path.join('logs', 'giveaway_bot.log')
        self.logger = logging.getLogger('GiveawayBot')
        self.logger.handlers.clear()  # Clear any existing handlers

        # File handler
        file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(file_handler)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(console_handler)

        self.logger.setLevel(logging.INFO)
        self.logger.info("Giveaway bot initializing...")

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
        # Register persistent views for existing ended giveaways
        await self.register_persistent_views()
        # Start background tasks after DB initialization
        self.check_giveaways.start()
        self.process_fake_reactions.start()
        self._ready.set()

    async def register_persistent_views(self):
        """Register persistent views for all ended giveaways on bot startup."""
        try:
            ended_giveaways = await self.db.giveaways_collection.find({
                'status': 'ended'
            }).to_list(length=None)

            for giveaway in ended_giveaways:
                message_id = giveaway['message_id']

                # Get participant count for this giveaway
                participants = await self.db.participants_collection.find({
                    'message_id': message_id
                }).to_list(length=None)

                # Count real participants (excluding bot)
                real_count = len([p for p in participants if p['user_id'] != str(self.bot.user.id)])

                # Get fake participants count
                fake_plan = await self.db.fake_reactions_collection.find_one({
                    'message_id': message_id
                })
                fake_count = len(fake_plan.get('fake_participants', [])) if fake_plan else 0

                total_count = real_count + fake_count

                # Create and add persistent view
                view = GiveawayEndedView(total_count, message_id, self.db, self.bot)
                view.update_count_button(total_count)
                self.bot.add_view(view)

            print(f"Registered persistent views for {len(ended_giveaways)} ended giveaways")

        except Exception as e:
            self.logger.error(f"Error registering persistent views: {e}")

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
            self.logger.info(f"Starting to end giveaway {message_id}")

            giveaway = await self.db.giveaways_collection.find_one({
                'message_id': message_id,
                'status': 'active'
            })
            if not giveaway:
                self.logger.warning(f"Giveaway {message_id} not found or not active")
                return

            self.logger.info(f"Found giveaway {message_id}, fetching channel {giveaway['channel_id']}")
            channel = self.bot.get_channel(giveaway['channel_id'])
            if not channel:
                self.logger.error(f"Channel {giveaway['channel_id']} not found")
                await self.db.giveaways_collection.update_one(
                    {'message_id': message_id},
                    {'$set': {'status':'error','error':'Channel not found'}}
                )
                return

            try:
                self.logger.info(f"Fetching message {message_id}")
                message = await channel.fetch_message(int(message_id))
                self.logger.info(f"Successfully fetched message {message_id}")
            except discord.NotFound:
                self.logger.error(f"Message {message_id} not found")
                await self.db.giveaways_collection.update_one(
                    {'message_id': message_id},
                    {'$set': {'status':'error','error':'Message not found'}}
                )
                return
            except discord.Forbidden:
                self.logger.error(f"Permission denied for message {message_id}")
                await self.db.giveaways_collection.update_one(
                    {'message_id': message_id},
                    {'$set': {'status':'error','error':'Permission denied'}}
                )
                return

            if not await self.check_bot_permissions(channel):
                self.logger.error(f"Missing bot permissions in channel {giveaway['channel_id']}")
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

            mentions = []
            for w in winners:
                # If it's a fake ID, extract the original user ID
                user_id = w.split('_fake_')[0] if '_fake_' in w else w
                mentions.append(f"<@{user_id}>")
            if not mentions:
                mentions = ["No winners (no participants)."]

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
            # Count real and fake participants
            real_participants_count = len([p for p in valid if '_fake_' not in str(p)])
            fake_participants_count = len([p for p in valid if '_fake_' in str(p)])
            total_participants = real_participants_count + fake_participants_count

            # Create a view with persistent buttons
            view = GiveawayEndedView(total_participants, message_id, self.db, self.bot)
            view.update_count_button(total_participants)

            # Edit the message with the new embed and view, removing existing reactions
            self.logger.info(f"Clearing reactions and updating message for giveaway {message_id}")
            await message.clear_reactions()
            await message.edit(embed=embed, view=view)
            self.logger.info(f"Successfully updated message with buttons for giveaway {message_id}")

            if winners:
                self.logger.info(f"Announcing winners for giveaway {message_id}: {winners}")
                await message.reply(
                    f"{REACTION_EMOJI} Congratulations {', '.join(mentions)}! You won **{giveaway['prize']}**!"
                )

            self.logger.info(f"Updating database status to ended for giveaway {message_id}")
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
                self.logger.info(f"Cancelled fake reaction task for giveaway {message_id}")

            self.logger.info(f"Successfully ended giveaway {message_id}")

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
                'message_id': str(orig.id)
            })
            if not gw:
                return await ctx.send("Giveaway not found.", ephemeral=True)

            # If giveaway is still active, end it first
            if gw['status'] == 'active':
                await self.end_giveaway(str(orig.id))

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

            # Create a view with a grey button showing the participant count and entries button
            view = GiveawayEndedView(total_participants, str(orig.id), self.db, self.bot)
            view.update_count_button(total_participants)

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

                # Pick a random user ID from the available pool
                user_id = random.choice(member_ids)

                # Add them even if they're already in the list (this is fake data anyway)
                # No need to check for duplicates since we want the exact count specified
                # Create a unique fake user ID by appending a counter to avoid duplicates
                fake_user_id = f"{user_id}_fake_{total_reactions - remaining}"

                # Insert each fake reaction as a separate document
                await self.db.participants_collection.insert_one({
                    'message_id': message_id,
                    'user_id': fake_user_id,
                    'original_user_id': user_id,  # Keep track of the original user
                    'joined_at': int(now),
                    'is_fake': True
                })

                remaining -= 1
                self.logger.info(f"Added fake reaction {total_reactions - remaining}/{total_reactions} for user {user_id}")
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
        users: str
    ):
        """Forces specific users to win a giveaway. Mention users or provide comma-separated user IDs."""
        try:
            await interaction.response.defer(ephemeral=True)

            # Parse mentions and user IDs
            import re
            user_id_list = []

            # Extract user IDs from mentions (<@123456789>) and plain IDs
            mention_pattern = r'<@!?(\d+)>'
            mentions = re.findall(mention_pattern, users)
            user_id_list.extend(mentions)

            # Remove mentions from the string and split by commas for plain IDs
            users_cleaned = re.sub(mention_pattern, '', users)
            plain_ids = [uid.strip() for uid in users_cleaned.split(',') if uid.strip() and uid.strip().isdigit()]
            user_id_list.extend(plain_ids)

            # Remove duplicates and ensure all are strings
            user_id_list = list(set(str(uid) for uid in user_id_list))

            if not user_id_list:
                return await interaction.followup.send("Please mention users or provide valid user IDs.", ephemeral=True)

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
