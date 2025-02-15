   await asyncio.sleep(0.5)  # Reduced delay between batches

        # Process remaining members
        if current_batch:
            await self._process_member_batch(current_batch, role, config, current_time)

    async def _process_member_batch(self, members, role, config, current_time):
        """Process a batch of members for role updates"""
        for member in members:
            try:
                guild_id = config['guild_id']
                member_key = f"{guild_id}:{member.id}"
                current_has_role = role in member.roles

                # Handle offline members
                if member.status == discord.Status.offline:
                    if current_has_role:
                        await member.remove_roles(role, reason="Member went offline")
                        await self.log_role_change(guild_id, config['log_channel_id'], member, role, "removed")
                        self.rate_limits[guild_id][member.id] = int(current_time)
                        self.member_role_states[member_key] = False
                    continue

                # Check custom status for active members
                status_text = None
                if member.activity and isinstance(member.activity, discord.CustomActivity):
                    status_text = member.activity.name or ""

                should_have_role = bool(status_text and config['status_word'].lower() in status_text.lower())

                if should_have_role != current_has_role:
                    try:
                        if should_have_role:
                            await member.add_roles(role, reason="Status match detected")
                            await self.log_role_change(guild_id, config['log_channel_id'], member, role, "added")
                        else:
                            await member.remove_roles(role, reason="Status no longer matches")
                            await self.log_role_change(guild_id, config['log_channel_id'], member, role, "removed")

                        self.rate_limits[guild_id][member.id] = int(current_time)
                        self.member_role_states[member_key] = should_have_role

                    except discord.Forbidden:
                        logger.error(f"Missing permissions to update role for {member.name}")
                    except discord.HTTPException as e:
                        logger.error(f"HTTP error updating role for {member.name}: {e}")
                    except Exception as e:
                        logger.error(f"Failed to update role for member {member.name}: {e}")

            except Exception as e:
                logger.error(f"Error processing member {member.name}: {e}")

            await asyncio.sleep(0.1)  # Small delay between members within batch

    async def setup_database(self):
        """Initialize the SQLite database"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS guild_configs (
                        guild_id INTEGER PRIMARY KEY,
                        guild_name TEXT,
                        status_word TEXT,
                        role_id INTEGER,
                        log_channel_id INTEGER
                    )
                ''')
                await db.commit()
                logger.info("Database setup completed successfully")
        except Exception as e:
            logger.error(f"Database setup failed: {e}")
            raise

    @app_commands.command(name="vanity-setup")
    @app_commands.default_permissions(administrator=True)
    async def setup(
        self,
        interaction: discord.Interaction,
        status_word: str,
        role: discord.Role,
        log_channel: discord.TextChannel
    ):
        """Setup the vanity role monitoring configuration for this server"""
        try:
            await interaction.response.defer(ephemeral=True)

            if not interaction.guild.me.guild_permissions.manage_roles:
                embed = discord.Embed(
                    title="❌ Permission Required",
                    description="I don't have permission to manage roles!",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Missing Permissions", icon_url=self.bot.user.display_avatar.url)
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            if role >= interaction.guild.me.top_role:
                embed = discord.Embed(
                    title="⚠️ Role Hierarchy Issue",
                    description="I cannot manage this role as it's higher than my highest role!",
                    color=discord.Color.red()
                )
                embed.set_footer(text="Role Hierarchy Error", icon_url=self.bot.user.display_avatar.url)
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO guild_configs 
                    (guild_id, guild_name, status_word, role_id, log_channel_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        interaction.guild_id,
                        interaction.guild.name,
                        status_word,
                        role.id,
                        log_channel.id
                    )
                )
                await db.commit()

            embed = discord.Embed(
                title="✨ Setup Complete!",
                description=(
                    f"Configuration saved successfully, {interaction.user.mention}!\n"
                    "Your status role system is now ready to use."
                ),
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.set_footer(text="Status Role Bot • Configuration Saved", icon_url=self.bot.user.display_avatar.url)
            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f"Configuration updated for guild: {interaction.guild.name}")

        except Exception as e:
            logger.error(f"Setup command error: {e}")
            embed = discord.Embed(
                title="❌ Setup Failed",
                description=(
                    "An error occurred while saving your configuration.\n"
                    "Please try setting up the status role again."
                ),
                color=discord.Color.red()
            )
            embed.set_footer(text="Error • Setup Failed", icon_url=self.bot.user.display_avatar.url)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="vanity-check")
    async def help(self, interaction: discord.Interaction):
        """Display help information about vanity role management"""
        embed = discord.Embed(
            title="🎭 Vanity Role Bot Guide",
            color=discord.Color.blue(),
            description="Automatically manage roles based on user status!"
        )

        embed.add_field(
            name="📝 Setup Command",
            value=(
                "**/vanity-setup** (Admin only)\n"
                "Configure the vanity role system with:\n"
                "• `status_word`: Keyword to track in status\n"
                "• `role`: Role to assign/remove\n"
                "• `log_channel`: Channel for notifications"
            ),
            inline=False
        )

        embed.set_footer(text="Made with ❤️ | Vanity Role Bot", icon_url=self.bot.user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def log_role_change(self, guild_id: int, channel_id: int, user: discord.Member, role: discord.Role, action: str):
        """Log role changes with detailed embed and proper timestamp"""
        try:
            channel = self.bot.get_channel(channel_id)
            if channel:
                # Set appropriate title and color based on action
                title = "Vanity Added!" if action == "added" else "Vanity Removed!"
                color = discord.Color.from_str('#2f3136')  # Discord dark theme color

                # Create embed with proper styling
                embed = discord.Embed(
                    title=title,
                    description=(
                        f"<a:sukoon_white_bo:1335856241011855430> {user.mention}! You've been granted the {role.name} role. "
                        "Enjoy your time with us and make the most out of your stay!"
                    ) if action == "added" else (
                        f"<a:sukoon_yflower:1323990499660664883> {user.mention} Your {role.name} role has been removed. "
                        "We'll miss you in that role, but we hope to see you back soon!"
                    ),
                    color=color,
                    timestamp=datetime.utcnow()  # Add timestamp to embed for discord's relative time
                )

                # Set user's avatar as thumbnail
                embed.set_thumbnail(url=user.display_avatar.url)

                guild = self.bot.get_guild(guild_id)

                # Set footer with server icon and relative timestamp
                footer_text = f"{guild.name}"
                footer_icon = guild.icon.url if guild.icon else self.bot.user.display_avatar.url
                embed.set_footer(text=footer_text, icon_url=footer_icon)

                await channel.send(embed=embed)
                logger.info(f"Role change logged for user {user.id} in guild {guild_id}")

        except Exception as e:
            logger.error(f"Failed to log role change in guild {guild_id}: {e}")

async def setup(bot):
    await bot.add_cog(StatusManager(bot))
    logger.info("StatusManager cog loaded")
