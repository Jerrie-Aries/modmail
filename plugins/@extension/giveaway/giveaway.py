import asyncio
import math
import random
from datetime import datetime, timezone
from typing import List, Optional

import discord

from core import checks
from core.enums_ext import PermissionLevel
from core.ext import commands
from core.logging_ext import getLogger
from core.timeutils import human_timedelta, UserFriendlyTime

logger = getLogger(__name__)


# Parsing and Conversion
def format_time_remaining(giveaway_time):
    attrs = ["days", "hours", "minutes"]
    delta = {
        "days": math.floor(giveaway_time // 86400),
        "hours": math.floor(giveaway_time // 3600 % 24),
        "minutes": math.floor(giveaway_time // 60 % 60),
    }
    output = []
    for attr in attrs:
        value = delta.get(attr)
        if value:
            output.append(f"{value} {attr if value != 1 else attr[:-1]}")
    return " ".join(output) if output else "less than 1 minute"


# Checks
def can_execute_giveaway(context: commands.Context, destination: discord.TextChannel):
    ctx_perms = context.channel.permissions_for(context.me)
    attrs = [
        "send_messages",
        "read_message_history",
        "manage_messages",
        "embed_links",
        "add_reactions",
    ]
    all_perms = (getattr(ctx_perms, attr) for attr in attrs)
    if destination != context.channel:
        ch_perms = destination.permissions_for(context.me)
        all_perms = (*all_perms, *(getattr(ch_perms, attr) for attr in attrs))

    return all(all_perms)


# Session
class GiveawaySession:
    """
    Giveaway session.

    To run the giveaway session immediately, use `GiveawaySession.start` instead of
    instantiating directly.

    Attributes
    ----------
    bot : bot.ModmailBot
        The Modmail bot.
    giveaway_data : dict
        Giveaway object retrieved from database, or when starting the giveaway from command.
    channel_id : int
        The ID of channel where the giveaway embed was posted.
    guild_id : int
        The ID of the guild where the giveaway session is running.
    id : int
        The message ID of the giveaway embed.
    winners_count : int
        Numbers of giveaway winners to be choosen.
    ends : float
        Time the giveaway will end, in UTC timestamp format.
    message : discord.Message
        The giveaway message object. This will only be implemented if this class is instantiated
        using the `GiveawaySession.start`.
    """

    def __init__(self, bot, giveaway_data):
        """
        Parameters
        -----------
        bot : bot.ModmailBot
            The Modmail bot.
        giveaway_data : dict
            Giveaway object retrieved from database, or when starting the giveaway from command.
        """
        self.bot = bot
        self.data = giveaway_data
        self.channel_id: int = self.data.get("channel", int())
        self.guild_id: int = self.data.get("guild", int())
        self.id: int = self.data.get("message", int())
        self.giveaway_item: str = self.data.get("item", None)
        self.winners_count: int = self.data.get("winners", 1)
        self.ends: float = self.data.get("time", float())

        self.message: Optional[
            discord.Message
        ] = None  # Implemented in `handle_giveaway`

        self._task: Optional[asyncio.Task] = None
        self._stopped = False
        self._done = False

    @classmethod
    def start(cls, bot, giveaway_data) -> "GiveawaySession":
        """
        Create and start a giveaway session.

        This allows the session to manage the running and cancellation of its
        own tasks.

        Parameters
        ----------
        bot : bot.ModmailBot
            The Modmail bot.
        giveaway_data : dict
            Same as `GiveawaySession.data`.

        Returns
        -------
        GiveawaySession
            The new giveaway session being run.
        """
        session = cls(bot, giveaway_data)
        loop = bot.loop
        session._task = loop.create_task(session._handle_giveaway())
        session._task.add_done_callback(session._error_handler)
        return session

    def _error_handler(self, fut):
        """Catches errors in the session task."""
        try:
            fut.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"{type(exc).__name__}: {str(exc)}")
            if isinstance(exc, discord.NotFound) and "Unknown Message" in str(exc):
                # Probably message got deleted
                self.stop()
            else:
                logger.error(
                    "A giveaway session has encountered an error.\n", exc_info=exc
                )
                self.suspend()

    @property
    def channel(self) -> Optional[discord.TextChannel]:
        """
        The :class:`discord.TextChannel` object for this :class:`GiveawaySession`.

        Returns
        -------
        channel : discord.TextChannel or None
            The Channel object, or None.
        """
        return self.bot.get_channel(self.channel_id)

    @property
    def guild(self) -> Optional[discord.Guild]:
        """
        The :class:`discord.Guild` object for this :class:`GiveawaySession`.

        Returns
        -------
        guild : discord.Guild or None
            The Guild object, or None.
        """
        if self.message:
            return self.message.guild
        if self.channel:
            return self.channel.guild
        return self.bot.get_guild(self.guild_id)

    @property
    def stopped(self) -> bool:
        """
        Returns `True` if the giveaway session has been stopped, otherwise `False`.
        """
        return self._stopped

    @property
    def done(self) -> bool:
        """
        Checks whether the giveaway has ended.
        This will return `True` if the giveaway has ended, otherwise `False`.
        """
        return self._done

    def suspend(self):
        """Suspends the giveaway task."""
        self._stopped = True

    def stop(self):
        """Stops the giveaway session, and the `giveaway_end` event will be dispatched."""
        if self.stopped:
            return

        self._stopped = True
        self._done = True
        logger.debug(
            "Stopping giveaway session; channel `%s`, guild `%s`.",
            self.channel or f"<#{self.channel_id}>",
            self.guild or self.guild_id,
        )
        self.bot.dispatch("giveaway_end", self)

    def force_stop(self):
        """Cancel whichever tasks this session is running without dispatching the `giveaway_end` event."""
        self._stopped = True
        self._task.cancel()
        logger.debug(
            "Force stopping giveaway session; channel `%s`, guild `%s`.",
            self.channel or f"<#{self.channel_id}>",
            self.guild or self.guild_id,
        )

    def get_random_user(
        self, guild: discord.Guild, reacted_users: list
    ) -> List[Optional[int]]:
        """
        A method to get random users based on reactions on the giveaway embed.

        Also checks whether the member is present in the guild or is a bot.
        If the member is not in the guild, or the member is a bot, they will be removed from the list.

        Returns
        -------
        list : List[int or None]
            The list of unique IDs of selected winners, or an empty list if no winners selected
            in some way.
        """
        for member in list(
            reacted_users
        ):  # This is to remove bots and any None members
            if member.bot:
                reacted_users.remove(member)
                continue
            if isinstance(member, discord.User) or guild.get_member(member.id) is None:
                reacted_users.remove(member)

        win = []
        for _ in range(self.winners_count):
            if not reacted_users:
                break

            rnd = random.choice(reacted_users)
            reacted_users.remove(rnd)  # so this member won't get choosen again

            win.append(rnd.id)
            if len(win) == self.winners_count:
                break
        return win

    def embed_no_one_participated(self, message, winners=None) -> discord.Embed:
        if winners is None:
            winners = self.winners_count
        embed = message.embeds[0]
        embed.description = f"Giveaway has ended!\n\nSadly no one participated."
        embed.set_footer(
            text=f"{winners} {'winners' if winners > 1 else 'winner'} | Ended at"
        )
        return embed

    async def _handle_giveaway(self):
        """
        Task to handle this giveaway session. This task will loop each minute continuously until it is stopped, ends,
        or an error occurs in some way.
        """
        await self.bot.wait_for_connected()

        while True:
            if self.done or self.stopped:
                return
            if self.channel is None:
                self.stop()
                break

            if self.message is None:
                self.message = await self.channel.fetch_message(self.id)
            if (
                self.message is None
                or not self.message.embeds
                or self.message.embeds[0] is None
            ):
                self.stop()
                break

            now_utc = datetime.utcnow().replace(tzinfo=timezone.utc).timestamp()
            g_time = self.ends - now_utc

            if g_time <= 0:
                self.message = await self.channel.fetch_message(
                    self.message.id
                )  # update the message object
                if len(self.message.reactions) <= 0:
                    embed = self.embed_no_one_participated(self.message)
                    await self.message.edit(embed=embed)
                    self.stop()
                    break

                for reaction in self.message.reactions:
                    if reaction.emoji == "🎉":
                        reacted_users = await reaction.users().flatten()
                        if len(reacted_users) <= 1:
                            embed = self.embed_no_one_participated(self.message)
                            await self.message.edit(embed=embed)
                            del reacted_users, embed
                            self.stop()
                            break

                        winners = self.get_random_user(self.guild, reacted_users)

                        if not winners:
                            embed = self.embed_no_one_participated(
                                self.message, self.winners_count
                            )
                            await self.message.edit(embed=embed)
                            del reacted_users, embed
                            self.stop()
                            break

                        embed = self.message.embeds[0]
                        winners_text = " ".join(f"<@{winner}>" for winner in winners)

                        embed.description = f"Giveaway has ended!\n\n**{'Winners' if len(winners) > 1 else 'Winner'}:** {winners_text} "
                        embed.set_footer(
                            text=f"{len(winners)} {'winners' if len(winners) > 1 else 'winner'} | Ended at"
                        )
                        await self.message.edit(embed=embed)
                        await self.channel.send(
                            f"🎉 Congratulations {winners_text}, you have won **{self.giveaway_item}**!"
                        )
                        self.stop()
                        del winners_text, winners, reacted_users, embed
                        break

            else:
                time_remaining = format_time_remaining(g_time)

                embed = self.message.embeds[0]
                embed.description = (
                    f"React with 🎉 to enter the giveaway!\n\n"
                    f"Time Remaining: **{time_remaining}**"
                )
                await self.message.edit(embed=embed)
                await asyncio.sleep(60 if g_time > 60 else (30 if g_time > 30 else 10))

        return


# Actual Cog
class Giveaway(commands.Cog):
    """
    Host giveaways on your server.
    """

    def __init__(self, bot):
        """
        Parameters
        ----------
        bot : bot.ModmailBot
            The Modmail bot.
        """
        self.bot = bot
        self.db = bot.api.get_plugin_partition(self)
        self.active_giveaways: List["GiveawaySession"] = []
        self.bot.loop.create_task(self._set_giveaways_from_db())

    def cog_unload(self):
        for session in self.active_giveaways:
            session.force_stop()

    async def _set_giveaways_from_db(self):
        config = await self.db.find_one({"_id": "config"})
        if config is None:
            config = await self.db.find_one_and_update(
                {"_id": "config"},
                {"$set": {"giveaways": dict()}},
                upsert=True,
                return_document=True,
            )
        giveaways = config.get("giveaways", {})
        if not giveaways:
            return

        for message_id, giveaway in giveaways.items():
            is_running = self._get_giveaway_session(int(message_id))
            if is_running is not None:
                continue
            session = GiveawaySession.start(self.bot, giveaway)
            self.active_giveaways.append(session)

    async def _update_db(self):
        active_giveaways = {}
        for session in self.active_giveaways:
            if session.done or session.stopped:
                continue
            active_giveaways.update({str(session.id): session.data})

        await self.db.find_one_and_update(
            {"_id": "config"},
            {"$set": {"giveaways": active_giveaways}},
            upsert=True,
        )

    def generate_embed(self, description: str):
        embed = discord.Embed()
        embed.colour = self.bot.main_color
        embed.description = description
        embed.set_footer(text='To cancel, type "cancel".')

        return embed

    def is_giveaway_embed(self, embed: discord.Embed):
        if not embed.author or embed.author.name != "Giveaway":
            return False
        author_url = getattr(embed.author, "url")
        if not author_url:
            return False
        return author_url.split("/")[-1] == self.giveaway_string

    def _get_giveaway_session(self, message_id: int) -> GiveawaySession:
        return next(
            (session for session in self.active_giveaways if session.id == message_id),
            None,
        )

    @property
    def giveaway_string(self):
        return f"giveaway#bot_id={self.bot.user.id}"

    @commands.group(aliases=["g", "giveaways", "gaway"], invoke_without_command=True)
    @commands.guild_only()
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def giveaway(self, ctx: commands.Context):
        """
        Create / Stop Giveaways.
        """
        await ctx.send_help(ctx.command)

    @giveaway.command(aliases=["create", "c", "s"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def start(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Start a giveaway in interactive mode.
        """
        if not can_execute_giveaway(ctx, channel):
            ch_text = "this channel"
            if ctx.channel != channel:
                ch_text += f" and {channel.mention} channel"
            raise commands.BadArgument(
                "Need `SEND_MESSAGES`, `READ_MESSAGES`, `MANAGE_MESSAGES`, "
                f"`EMBED_LINKS`, and `ADD_REACTIONS` permissions in {ch_text}."
            )

        def check(msg: discord.Message):
            return (
                ctx.author == msg.author
                and ctx.channel == msg.channel
                and (len(msg.content) < 2048)
            )

        def cancel_check(msg: discord.Message):
            return msg.content == "cancel" or msg.content == f"{ctx.prefix}cancel"

        async def send_fail_embed(description="Cancelled."):
            embed = discord.Embed(color=self.bot.error_color, description=description)
            return await ctx.send(embed=embed)

        embed = discord.Embed(colour=0x00FF00)
        embed.set_author(
            name="Giveaway",
            url=f"https://discordapp.com/channels/{ctx.guild.id}/{channel.id}/{self.giveaway_string}",
        )

        await ctx.send(
            embed=self.generate_embed(
                f"Giveaway will be posted in {channel.mention}.\n\nWhat is the giveaway item?"
            )
        )
        try:
            giveaway_item = await self.bot.wait_for(
                "message", check=check, timeout=30.0
            )
        except asyncio.TimeoutError:
            return send_fail_embed("Time out.")
        if cancel_check(giveaway_item) is True:
            return await send_fail_embed()
        embed.title = giveaway_item.content
        await ctx.send(
            embed=self.generate_embed(
                f"Giveaway item:\n**{giveaway_item.content}**\n\nHow many winners are to be selected?"
            )
        )
        try:
            giveaway_winners = await self.bot.wait_for(
                "message", check=check, timeout=30.0
            )
        except asyncio.TimeoutError:
            return send_fail_embed("Time out.")
        if cancel_check(giveaway_winners) is True:
            return await send_fail_embed()

        try:
            giveaway_winners = int(giveaway_winners.content)
        except ValueError:
            raise commands.BadArgument(
                "Unable to parse giveaway winners to numbers, exiting."
            )

        if giveaway_winners <= 0:
            raise commands.BadArgument(
                "Giveaway can only be held with 1 or more winners. Cancelling command."
            )

        duration_syntax = (
            "Examples:\n"
            "`30m` or `30 minutes` = 30 minutes\n"
            "`2d` or `2days` or `2day` = 2 days\n"
            "`1mo` or `1 month` = 1 month\n"
            "`7 days 12 hours` or `7days12hours` (with/without spaces)\n"
            "`6d12h` (this syntax must be without spaces)\n"
        )
        await ctx.send(
            embed=self.generate_embed(
                f"**{giveaway_winners} {'winners' if giveaway_winners > 1 else 'winner'}** will be selected.\n"
                f"\nHow long will the giveaway last?\n\n{duration_syntax}"
            )
        )

        while True:
            try:
                giveaway_time = await self.bot.wait_for(
                    "message", check=check, timeout=30.0
                )
            except asyncio.TimeoutError:
                return send_fail_embed("Time out.")
            if cancel_check(giveaway_time) is True:
                return await send_fail_embed()

            try:
                ends_at = UserFriendlyTime().do_conversion(giveaway_time.content)
            except (commands.BadArgument, commands.CommandError):
                await ctx.send(
                    embed=discord.Embed(
                        description=(
                            "I was not able to parse the time properly. Please use the following syntax.\n\n"
                            f"{duration_syntax}"
                        ),
                        color=self.bot.error_color,
                    )
                )
                embed.set_footer(text='To cancel, type "cancel".')
                continue

            if (ends_at.dt.timestamp() - ends_at.now.timestamp()) <= 0:
                return await send_fail_embed(
                    "I was not able to parse the time properly. Exiting."
                )

            giveaway_time = ends_at.dt
            break

        reactions = ["✅", "❌"]
        confirm_message = await ctx.send(
            embed=discord.Embed(
                description=f"Giveaway will last for **{human_timedelta(giveaway_time)}**. Proceed?",
                color=self.bot.main_color,
            ).set_footer(text="React with ✅ to proceed, ❌ to cancel")
        )
        for emoji in reactions:
            await confirm_message.add_reaction(emoji)
            await asyncio.sleep(0.2)

        def reaction_check(reaction, user):
            return (
                user.id == ctx.author.id
                and reaction.message.id == confirm_message.id
                and reaction.emoji in reactions
            )

        try:
            reaction, user = await self.bot.wait_for(
                "reaction_add", check=reaction_check, timeout=15.0
            )
            if reaction.emoji == "✅":
                try:
                    await confirm_message.clear_reactions()
                except (discord.HTTPException, discord.Forbidden):
                    pass
            if reaction.emoji == "❌":
                await send_fail_embed()
                try:
                    await confirm_message.clear_reactions()
                except (discord.HTTPException, discord.Forbidden):
                    pass
                return
        except asyncio.TimeoutError:
            await confirm_message.clear_reactions()
            return

        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc).timestamp()
        giveaway_time_utc = giveaway_time.replace(tzinfo=timezone.utc).timestamp()
        time_left = giveaway_time_utc - now_utc
        time_remaining = format_time_remaining(time_left)
        embed.description = (
            f"React with 🎉 to enter the giveaway!\n\n"
            f"Time Remaining: **{time_remaining}**"
        )
        embed.add_field(name="Hosted by:", value=ctx.author.mention, inline=False)
        embed.set_footer(
            text=f"{giveaway_winners} {'winners' if giveaway_winners > 1 else 'winner'} | Ends at"
        )
        embed.timestamp = datetime.fromtimestamp(giveaway_time.timestamp())
        msg = await channel.send(embed=embed)

        await msg.add_reaction("🎉")

        giveaway_data = {
            "item": giveaway_item.content,
            "winners": giveaway_winners,
            "time": giveaway_time_utc,
            "guild": ctx.guild.id,
            "channel": channel.id,
            "message": msg.id,
        }
        await ctx.send(
            embed=discord.Embed(
                color=self.bot.main_color,
                description=f"Done! Giveaway embed has been posted in {channel.mention}!",
            )
        )
        session = GiveawaySession.start(self.bot, giveaway_data)
        self.active_giveaways.append(session)
        await self._update_db()

    @giveaway.command(aliases=["rroll"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def reroll(self, ctx: commands.Context, message_id: int, winners_count: int):
        """
        Reroll the giveaway.

        **Usage:**
        `{prefix}giveaway reroll <message_id> <winners_count>`

        __**Note:**__
        - This command must be run in the same channel where the message is.
        """

        # Don't roll if giveaway is active
        session = self._get_giveaway_session(message_id)
        if session is not None:
            raise commands.BadArgument("You can't reroll an active giveaway.")

        try:
            message = await ctx.channel.fetch_message(int(message_id))
        except discord.Forbidden:
            raise commands.BadArgument("No permission to read the history.")
        except discord.NotFound:
            raise commands.BadArgument("Message not found.")

        if message.author.id != self.bot.user.id:
            raise commands.BadArgument("The given message wasn't from me.")

        if not message.embeds or message.embeds[0] is None:
            raise commands.BadArgument(
                "The given message doesn't have an embed, it isn't related to a giveaway."
            )

        if not self.is_giveaway_embed(message.embeds[0]):
            raise commands.BadArgument("The given message isn't related to giveaway.")

        # giveaway dict to init the GiveawaySession, just pass in the `winners_count`for this purpose
        giveaway_obj = {"winners": winners_count}
        session = GiveawaySession(self.bot, giveaway_obj)

        if len(message.reactions) <= 0:
            embed = session.embed_no_one_participated(message)
            return await message.edit(embed=embed)

        for r in message.reactions:
            if r.emoji == "🎉":
                reactions = r
                reacted_users = await reactions.users().flatten()
                if len(reacted_users) <= 1:
                    embed = session.embed_no_one_participated(message)
                    await message.edit(embed=embed)
                    del reacted_users, embed
                    return

                winners = session.get_random_user(ctx.guild, reacted_users)

                if not winners:
                    raise commands.BadArgument(
                        "There is no legit guild member participated in that giveaway."
                    )

                embed = message.embeds[0]
                winners_text = ""
                for winner in winners:
                    winners_text += f"<@{winner}> "

                embed.description = f"Giveaway has ended!\n\n**{'Winners' if winners_count > 1 else 'Winner'}:** {winners_text}"
                embed.set_footer(
                    text=f"{winners_count} {'winners' if winners_count > 1 else 'winner'} | Ended at"
                )
                await message.edit(embed=embed)
                await ctx.channel.send(
                    f"🎉 Congratulations {winners_text}, you have won **{embed.title}**!"
                )
                del winners_text, winners, winners_count, reacted_users, embed
                return

    @giveaway.command(aliases=["stop"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def cancel(self, ctx: commands.Context, message_id: int):
        """
        Stop an active giveaway.

        **Usage:**
        `{prefix}giveaway stop <message_id>`
        """
        session = self._get_giveaway_session(message_id)
        if session is None:
            raise commands.BadArgument(
                "Unable to find an active giveaway with that ID!"
            )

        channel = self.bot.get_channel(session.channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise TypeError(
                f"Invalid type. Expected `TextChannel`, got `{type(channel).__name__}` instead."
            )

        try:
            message = await channel.fetch_message(int(message_id))
        except discord.Forbidden:
            raise commands.BadArgument("No permission to read the history.")
        except discord.NotFound:
            raise commands.BadArgument("Message not found.")

        if not message.embeds or message.embeds[0] is None:
            raise commands.BadArgument(
                "The given message doesn't have an embed, it isn't related to a giveaway."
            )

        embed = message.embeds[0]
        embed.description = "The giveaway has been cancelled."
        await message.edit(embed=embed)

        session.force_stop()
        self.active_giveaways.remove(session)
        await self._update_db()
        await ctx.send("Cancelled!")

    @commands.Cog.listener()
    async def on_giveaway_end(self, session: GiveawaySession):
        """
        A custom event that is dispatched when the giveaway session has ended.
        """
        if session in self.active_giveaways:
            self.active_giveaways.remove(session)
            await self._update_db()


async def setup(bot):
    await bot.add_cog(Giveaway(bot))
