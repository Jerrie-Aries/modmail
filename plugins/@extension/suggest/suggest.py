import asyncio
import datetime

import discord

from core import checks
from core.enums_ext import PermissionLevel
from core.ext import commands
from core.views.paginator import EmbedPaginatorSession

UPVOTE_EMOJI = "🔼"
DOWNVOTE_EMOJI = "🔽"


# This is for poll command.
def to_emoji(c):
    base = 0x1F1E6
    return chr(base + c)


class Suggest(commands.Cog):
    """
    Want to suggest something? Use this command and your suggestion will be sent to a designated channel.

    __**Note:**__
    - The `suggest <suggestion>` command can be used globally.
    That means whichever server the command is executed from, the suggestions always will be sent to the set channel.
    """

    default_config = {
        "channel": str(int()),
        "enabled": False,
        "numbers": int(),
    }

    def __init__(self, bot):
        """
        Parameters
        ----------
        bot : bot.ModmailBot
            The Modmail bot.
        """
        self.bot = bot
        self.db = bot.api.get_plugin_partition(self)

        self.banlist = dict()

        self.bot.loop.create_task(self.set_banlist())

    async def get_config(self):
        config = await self.db.find_one({"_id": "config"})
        if config is None:
            default_config = {"suggest": self.default_config.copy()}
            config = await self.db.find_one_and_update(
                {"_id": "config"},
                {"$set": {"suggest": default_config}},
                upsert=True,
                return_document=True,
            )
        return config

    async def update_db(self, item: dict, config: bool = False, banlist: bool = False):
        """
        Update the database. Either `config` or `banlist` parameter must be set to True to specify
        which document you want to update. Otherwise ValueError will be raised.
        """
        if not config and not banlist:
            raise ValueError("Neither `config` nor `banlist` was set to True.")

        if config:
            await self.db.find_one_and_update(
                {"_id": "config"},
                {"$set": {"suggest": item}},
                upsert=True,
            )
            return
        if banlist:
            await self.db.find_one_and_update(
                {"_id": "mod"},
                {"$set": {"banlist": item}},
                upsert=True,
            )
            return

    async def set_banlist(self):
        mod = await self.db.find_one({"_id": "mod"})

        if mod is None:
            return

        self.banlist = mod["banlist"]

    @commands.group(invoke_without_command=True, extras={"add_slash_option": True})
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def suggest(self, ctx: commands.Context, *, suggestion):
        """
        Send your suggestion to a designated channel.

        **Example**:
        `{prefix}suggest Let's have a Trivia night!`

        **See also:**
        - `{prefix}suggest config`
        - `{prefix}suggest mod`
        """
        if str(ctx.author.id) in self.banlist:
            await ctx.send(
                embed=discord.Embed(
                    color=self.bot.error_color,
                    title=f"You are currently blocked, {ctx.author.name}#{ctx.author.discriminator}.",
                    description=f"Reason: {self.banlist[str(ctx.author.id)]}",
                )
            )
            return

        db_config = await self.get_config()

        config = db_config["suggest"]
        enabled = config["enabled"]
        if enabled is False:
            raise commands.BadArgument("Suggest command is disabled.")

        channel = self.bot.get_channel(int(config["channel"]))
        if channel is None or not isinstance(channel, discord.TextChannel):
            raise commands.BadArgument(
                "Suggestion channel hasn't been set.\n\n"
                "To set a suggestion channel, use command:\n"
                "- `{}suggest config channel <channel>`".format(self.bot.prefix)
            )

        numbers = config["numbers"]
        embed = discord.Embed(
            title=f"Suggestion #{numbers + 1}", description=suggestion, color=0x546E7A
        )
        embed.set_author(
            name=f"{ctx.author.name}", icon_url=ctx.author.display_avatar.url
        )
        suggestion_embed = await channel.send(embed=embed)
        emojis = [UPVOTE_EMOJI, DOWNVOTE_EMOJI]

        for emoji in emojis:
            await suggestion_embed.add_reaction(emoji)
            await asyncio.sleep(0.2)

        config["numbers"] += 1
        await self.update_db(config, config=True)
        await ctx.message.add_reaction("\N{WHITE HEAVY CHECK MARK}")

    @suggest.group(name="config", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def suggest_config(self, ctx: commands.Context):
        """
        Suggest command configurations.
        """
        await ctx.send_help(ctx.command)

    @suggest_config.command(name="channel")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def suggest_config_channel(
        self, ctx: commands.Context, channel: discord.TextChannel = None
    ):
        """
        Set or change the channel where the suggestions will be posted.

        **Examples:**
        - `{prefix}suggest config channel #suggestions`
        - `{prefix}suggest config channel suggestions`
        - `{prefix}suggest config channel 515085600047628288`

        Leave the `channel` empty to get the current set channel.
        """
        db_config = await self.get_config()
        config = db_config["suggest"]
        if channel is None:
            channel_id = int(config["channel"])
            if not channel_id:
                raise commands.BadArgument("Suggestion channel hasn't been set.")

            channel = self.bot.get_channel(channel_id)
            if not channel:
                raise commands.BadArgument(f'Channel "{channel_id}" not found.')

            desc = f"The suggestion channel was set to {channel.mention}."
            embed = discord.Embed(color=self.bot.main_color, description=desc)
            await ctx.send(embed=embed)
            return

        config["channel"] = str(channel.id)
        await self.update_db(config, config=True)
        embed = discord.Embed(
            description=f"Set suggestion channel to {channel.mention}.",
            color=discord.Color.green(),
        )
        embed.set_author(name="Success!")
        await ctx.send(embed=embed)

    @suggest_config.command(name="enable")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def suggest_config_enable(self, ctx: commands.Context, mode: bool = None):
        """
        Enable or disable the suggest command.

        **Usage:**
        - `{prefix}suggest config enabled True`
        - `{prefix}suggest config enabled False`

        Leave the `mode` empty to get the current set configuration.
        """
        db_config = await self.get_config()
        config = db_config["suggest"]
        if mode is None:
            mode = config["enabled"]
            embed = discord.Embed(
                color=self.bot.main_color,
                description=f"Suggestion command is currently "
                + ("enabled." if mode else "disabled."),
            )
            await ctx.send(embed=embed)
            return

        config["enabled"] = mode
        await self.update_db(config, config=True)
        embed = discord.Embed(
            description=("Enabled" if mode else "Disabled") + " the suggest command.",
            color=discord.Color.green(),
        )
        embed.set_author(name="Success!")
        await ctx.send(embed=embed)

    @suggest_config.command(name="reset")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def suggest_config_reset(self, ctx: commands.Context):
        """
        Reset the configuration settings to default value.
        """
        default_config = self.default_config.copy()
        await self.update_db(default_config, config=True)
        embed = discord.Embed(
            description=f"Configuration settings has been reset to default.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @suggest.group(name="mod", usage="<option>", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def suggest_mod(self, ctx: commands.Context):
        """
        Block or unblock people from using the suggest command.

        `user` may be a user name, mention or ID.
        `reason` is optional.

        **Examples:**
        Block a user:
        - `{prefix}suggest mod block @User Spamming suggestion channel`
        - `{prefix}suggest mod ban 750783082713579634`
        Unblock a user:
        - `{prefix}suggest mod unblock @User`
        - `{prefix}suggest mod unban 750783082713579634`
        """
        await ctx.send_help(ctx.command)

    @suggest_mod.command(name="block", aliases=["ban"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def suggest_mod_block(
        self, ctx: commands.Context, user: discord.User, *, reason=None
    ):
        """
        Block a user from using the suggest command.
        """
        if str(user.id) in self.banlist:
            embed = discord.Embed(
                colour=self.bot.error_color,
                title=f"{user.name}#{user.discriminator} is already blocked.",
                description=f"Reason: {self.banlist[str(user.id)]}",
            )
        else:
            reason = reason
            if reason is None:
                reason = "Reason not specified."
            self.banlist[str(user.id)] = reason
            embed = discord.Embed(
                colour=self.bot.main_color,
                title=f"{user.name}#{user.discriminator} is now blocked.",
                description=f"Reason: {reason}",
            )
            await self.update_db(self.banlist, banlist=True)

        await ctx.send(embed=embed)

    @suggest_mod.command(name="unblock", aliases=["unban"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def suggest_mod_unblock(self, ctx: commands.Context, user: discord.User):
        """
        Unblock a user from using the suggest command.
        """
        if str(user.id) not in self.banlist:
            embed = discord.Embed(
                colour=self.bot.error_color,
                title=f"{user.name}#{user.discriminator} is not blocked.",
                description=f"Reason: {self.banlist[str(user.id)]}",
            )
        else:
            self.banlist.pop(str(user.id))
            embed = discord.Embed(
                colour=self.bot.main_color,
                title=f"{user.name}#{user.discriminator} is now unblocked.",
            )
            await self.update_db(self.banlist, banlist=True)

        await ctx.send(embed=embed)

    @suggest_mod.command(name="blocked", aliases=["blocklist", "banlist"])
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def suggest_mod_blocked(self, ctx: commands.Context):
        """
        Get the list of members that have been blocked from using suggestion command.
        """
        if not self.banlist:
            embed = discord.Embed(
                color=self.bot.main_color,
                title="Blocked members",
                description="There are no blocked members.",
            )
            await ctx.send(embed=embed)
            return

        def base_embed(continued=False, description=None):
            embed = discord.Embed(color=self.bot.main_color)
            embed.description = description if description is not None else ""
            embed.title = "Blocked members"
            if continued:
                embed.title += " (Continued)"
            return embed

        embeds = [base_embed()]
        entries = 0
        embed = embeds[0]
        num = 1

        for _id, _ in self.banlist.items():
            user = ctx.guild.get_member(int(_id))
            if user:
                line = f"{num}. {user.name}#{user.discriminator} = `{user.id}`\n"
            else:
                line = f"{num}. {_id}"

            if entries == 25:
                embed = base_embed(continued=True, description=line)
                embeds.append(embed)
                entries = 1
            else:
                embed.description += line
                entries += 1
            num += 1

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    # Poll command.
    @commands.group(name="poll", usage="<option>", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def poll(self, ctx: commands.Context):
        """Easily create Polls."""
        await ctx.send_help(ctx.command)

    @poll.command()
    @commands.guild_only()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def start(self, ctx: commands.Context, *, question):
        """
        Interactively creates a poll with the following questions.

        **Example:**
        - `{prefix}poll start This is a poll.`

        Then wait for the next question and type in your poll choices you would like to add.
        After you're done, type `done` to publish the poll.

        **Note(s):**
        - You must have 2 or more choices (up to 20) added to publish the poll.
        """
        perms = ctx.channel.permissions_for(ctx.me)
        if not perms.add_reactions:
            raise commands.BadArgument("Need `ADD_REACTIONS` permission.")

        def check(m):
            return (
                m.author == ctx.author
                and m.channel == ctx.channel
                and len(m.content) <= 100
            )

        async def delete_user_input(message):
            try:
                await message.delete()
            except discord.Forbidden:
                pass

        # a list of messages to delete when we're all done
        messages = [ctx.message]
        answers = []
        cancel = False
        index = 0
        setup_msg = None
        while index < 20:
            embed = discord.Embed(
                color=self.bot.main_color,
            )
            if index < 1:
                embed.description = (
                    "What is the poll option/choice you would like to add?\n\n"
                )
            else:
                embed.description = (
                    "Is there any more option/choice you would like to add?\n\n"
                )

            if answers:
                embed.description += f"Current option(s):\n"
                ans = (
                    "\n".join(f"{keycap} - {content}" for keycap, content in answers)
                    + "\n\n"
                )
                embed.description += ans
            if len(answers) <= 1:
                embed.set_footer(text='Type "cancel" to cancel.')
            else:
                embed.set_footer(
                    text='Type "done" to publish the poll, or "cancel" to cancel.'
                )
            if setup_msg is None:
                setup_msg = await ctx.send(embed=embed)
            else:
                await setup_msg.edit(embed=embed)

            try:
                entry: discord.Message = await self.bot.wait_for(
                    "message", check=check, timeout=60.0
                )
            except asyncio.TimeoutError:
                break
            if entry.clean_content.lower() == "done":
                if len(answers) < 2:
                    await ctx.send(
                        embed=discord.Embed(
                            color=self.bot.error_color,
                            description="You must have 2 or more poll options to publish it.",
                        ),
                        delete_after=5,
                    )
                    await delete_user_input(entry)
                    continue
                await delete_user_input(entry)
                break
            if entry.clean_content.lower() == "cancel":
                cancel = True
                await ctx.send("Cancelled.", delete_after=5)
                await delete_user_input(entry)
                break

            answers.append((to_emoji(index), entry.clean_content))
            await delete_user_input(entry)
            index += 1
        if setup_msg is not None:
            messages.append(setup_msg)
        try:
            await ctx.channel.delete_messages(messages)
        except (Exception, discord.Forbidden):
            pass  # oh well
        if cancel:
            return

        answer = "\n".join(f"{keycap} - {content}" for keycap, content in answers)
        embed = discord.Embed(
            color=discord.Color.green(),
            timestamp=datetime.datetime.utcnow(),
            description=f"**{question}**\n\n{answer}",
        )
        embed.set_thumbnail(
            url="https://cdn.discordapp.com/attachments/804336471313743883/804340547958734868/vote.png",
        )
        embed.set_author(name=ctx.author, icon_url=ctx.author.display_avatar.url)
        embed.set_footer(text="React below to vote")
        poll = await ctx.send(embed=embed)
        for emoji, _ in answers:
            await poll.add_reaction(emoji)
            await asyncio.sleep(0.2)

    @poll.command()
    @commands.guild_only()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def quick(self, ctx: commands.Context, *questions_and_choices: str):
        """
        Makes a poll quickly.
        The first argument is the question and the rest are the choices.
        For example: `?poll quick "Green or Light Green?" Green "Light Green"`

        Or it can be a simple yes or no poll, like:
        `?poll quick "Do you watch Anime?"`
        """

        if len(questions_and_choices) == 0:
            return await ctx.send_help(ctx.command)
        if len(questions_and_choices) == 2:
            raise commands.BadArgument("You need at least 2 choices.")
        elif len(questions_and_choices) > 21:
            raise commands.BadArgument("You can only have up to 20 choices.")

        perms = ctx.channel.permissions_for(ctx.me)
        if not perms.add_reactions:
            raise commands.BadArgument("Need Add Reactions permissions.")
        try:
            await ctx.message.delete()
        except (Exception, discord.Forbidden):
            pass
        question = questions_and_choices[0]

        if len(questions_and_choices) == 1:
            embed = discord.Embed(
                color=self.bot.main_color,
                timestamp=datetime.datetime.utcnow(),
                description=f"**{question}**",
            )
            embed.set_author(name=ctx.author, icon_url=ctx.author.display_avatar.url)
            poll = await ctx.send(embed=embed)

            # Original reactions.
            reactions = ["👍", "👎"]

            # New reactions (animated).
            # reactions = [animated_emojis["tick_green"], animated_emojis["tick_red"]]
            for emoji in reactions:
                await poll.add_reaction(emoji)
                await asyncio.sleep(0.2)

        else:
            choices = [
                (to_emoji(e), v) for e, v in enumerate(questions_and_choices[1:])
            ]

            body = "\n".join(f"{key} - {c}" for key, c in choices)
            embed = discord.Embed(
                color=self.bot.main_color,
                timestamp=datetime.datetime.utcnow(),
                description=f"**{question}**\n{body}",
            )
            embed.set_author(name=ctx.author, icon_url=ctx.author.display_avatar.url)
            poll = await ctx.send(embed=embed)
            for emoji, _ in choices:
                await poll.add_reaction(emoji)
                await asyncio.sleep(0.2)

    @commands.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def vote(self, ctx: commands.Context, *, question):
        """
        Create a simple vote question.
        """
        embed = discord.Embed(
            title="Vote", description=question, color=discord.Color.green()
        )
        embed.set_footer(text="React below to vote")
        vote_embed = await ctx.send(embed=embed)
        emojis = ["✅", "❌"]

        for emoji in emojis:
            await vote_embed.add_reaction(emoji)
            await asyncio.sleep(0.2)

        try:
            await ctx.message.delete()
        except (discord.Forbidden, Exception):
            pass


async def setup(bot):
    await bot.add_cog(Suggest(bot))
