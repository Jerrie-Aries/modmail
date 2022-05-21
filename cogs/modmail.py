from __future__ import annotations

import re
from datetime import datetime, timezone
from itertools import zip_longest
from typing import Iterable, Optional, Union, TYPE_CHECKING

import discord
from dateutil import parser
from discord.utils import escape_markdown
from natural.date import duration

from core import checks
from core.ext import commands
from core.enums_ext import DMDisabled, PermissionLevel
from core.errors import LinkMessageError
from core.logging_ext import getLogger
from core.timeutils import UserFriendlyTime, human_timedelta
from core.utils import *
from core.views.paginator import EmbedPaginatorSession

if TYPE_CHECKING:
    from bot import ModmailBot
    from core.types_ext.raw_data import ThreadLogPayload

logger = getLogger(__name__)


class Modmail(commands.Cog):
    """Commands directly related to Modmail functionality."""

    def __init__(self, bot: ModmailBot):
        """
        Parameters
        ----------
        bot : ModmailBot
            The Modmail bot.
        """
        self.bot: ModmailBot = bot

    @commands.command()
    @trigger_typing
    @checks.has_permissions(PermissionLevel.OWNER)
    async def setup(self, ctx: commands.Context):
        """
        Sets up a server for Modmail.

        You only need to run this command once after configuring Modmail.
        """

        if ctx.guild != self.bot.modmail_guild:
            return await ctx.send(
                f"You can only setup in the Modmail guild: {self.bot.modmail_guild}."
            )

        if self.bot.main_category is not None:
            logger.debug("Can't re-setup server, main_category is found.")
            return await ctx.send(f"{self.bot.modmail_guild} is already set up.")

        if self.bot.modmail_guild is None:
            embed = discord.Embed(
                title="Error",
                description="Modmail functioning guild not found.",
                color=self.bot.error_color,
            )
            return await ctx.send(embed=embed)

        overwrites = {
            self.bot.modmail_guild.default_role: discord.PermissionOverwrite(
                read_messages=False
            ),
            self.bot.modmail_guild.me: discord.PermissionOverwrite(read_messages=True),
        }

        for level in PermissionLevel:
            if level <= PermissionLevel.REGULAR:
                continue
            permissions = self.bot.config["level_permissions"].get(level.name, [])
            for perm in permissions:
                perm = int(perm)
                if perm == -1:
                    key = self.bot.modmail_guild.default_role
                else:
                    key = self.bot.modmail_guild.get_member(perm)
                    if key is None:
                        key = self.bot.modmail_guild.get_role(perm)
                if key is not None:
                    logger.info("Granting %s access to Modmail category.", key.name)
                    overwrites[key] = discord.PermissionOverwrite(read_messages=True)

        category = await self.bot.modmail_guild.create_category(
            name="Modmail", overwrites=overwrites
        )

        await category.edit(position=0)

        log_channel = await self.bot.modmail_guild.create_text_channel(
            name="bot-logs", category=category
        )

        embed = discord.Embed(
            title="Friendly Reminder",
            description=f"You may use the `{self.bot.prefix}config set log_channel_id "
            "<channel-id>` command to set up a custom log channel, then you can delete this default "
            f"{log_channel.mention} log channel.",
            color=self.bot.main_color,
        )

        embed.add_field(
            name="Thanks for using our bot!",
            value="If you like what you see, consider giving the "
            "[repo a star](https://github.com/kyb3r/modmail) :star: and if you are "
            "feeling extra generous, buy us coffee on [Patreon](https://patreon.com/kyber) :heart:!",
        )

        embed.set_footer(
            text=f'Type "{self.bot.prefix}help" for a complete list of commands.'
        )
        await log_channel.send(embed=embed)

        self.bot.config["main_category_id"] = category.id
        self.bot.config["log_channel_id"] = log_channel.id

        await self.bot.config.update()
        await ctx.send(
            "**Successfully set up server.**\n"
            "Consider setting permission levels to give access to roles "
            "or users the ability to use Modmail.\n\n"
            f"Type:\n- `{self.bot.prefix}permissions` and `{self.bot.prefix}permissions add` "
            "for more info on setting permissions.\n"
            f"- `{self.bot.prefix}config help` for a list of available customizations."
        )

        if (
            not self.bot.config["command_permissions"]
            and not self.bot.config["level_permissions"]
        ):
            await self.bot.update_perms(PermissionLevel.REGULAR, -1)
            for owner_id in self.bot.bot_owner_ids:
                await self.bot.update_perms(PermissionLevel.OWNER, owner_id)

    @commands.group(aliases=["snippets"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def snippet(self, ctx: commands.Context, *, name: str.lower = None):
        """
        Create pre-defined messages for use in threads.

        When `{prefix}snippet` is used by itself, this will retrieve a list of snippets that are currently set.
        `{prefix}snippet-name` will show what the snippet point to.

        To create a snippet:
        - `{prefix}snippet add snippet-name A pre-defined text.`

        You can use your snippet in a thread channel with `{prefix}snippet-name`, the message "A pre-defined text." will be sent to the recipient.

        Currently, there is not a built-in anonymous snippet command; however, a workaround is available using `{prefix}alias`.
        Here is how:
        - `{prefix}alias add snippet-name anonreply A pre-defined anonymous text.`

        See also `{prefix}alias`.
        """

        if name is not None:
            val = self.bot.snippets.get(name)
            if val is None:
                embed = create_not_found_embed(
                    name, self.bot.snippets.keys(), "Snippet"
                )
            else:
                embed = discord.Embed(
                    title=f'Snippet - "{name}":',
                    description=val,
                    color=self.bot.main_color,
                )
            return await ctx.send(embed=embed)

        if not self.bot.snippets:
            embed = discord.Embed(
                color=self.bot.error_color,
                description="You dont have any snippets at the moment.",
            )
            embed.set_footer(
                text=f'Check "{self.bot.prefix}help snippet add" to add a snippet.'
            )
            embed.set_author(name="Snippets", icon_url=ctx.guild.icon.url)
            return await ctx.send(embed=embed)

        embeds = []

        for i, names in enumerate(
            zip_longest(*(iter(sorted(self.bot.snippets)),) * 15)
        ):
            description = format_description(i, names)
            embed = discord.Embed(color=self.bot.main_color, description=description)
            embed.set_author(name="Snippets", icon_url=ctx.guild.icon.url)
            embeds.append(embed)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @snippet.command(name="raw")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def snippet_raw(self, ctx: commands.Context, *, name: str.lower):
        """
        View the raw content of a snippet.
        """
        val = self.bot.snippets.get(name)
        if val is None:
            embed = create_not_found_embed(name, self.bot.snippets.keys(), "Snippet")
        else:
            val = truncate(escape_code_block(val), 2048 - 7)
            embed = discord.Embed(
                title=f'Raw snippet - "{name}":',
                description=f"```\n{val}```",
                color=self.bot.main_color,
            )

        return await ctx.send(embed=embed)

    @snippet.command(name="add")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def snippet_add(
        self, ctx: commands.Context, name: str.lower, *, value: commands.clean_content
    ):
        """
        Add a snippet.

        Simply to add a snippet, do: ```
        {prefix}snippet add hey hello there :)
        ```
        then when you type `{prefix}hey`, "hello there :)" will get sent to the recipient.

        To add a multi-word snippet name, use quotes: ```
        {prefix}snippet add "two word" this is a two word snippet.
        ```
        """
        if self.bot.get_command(name):
            embed = discord.Embed(
                title="Error",
                color=self.bot.error_color,
                description=f"A command with the same name already exists: `{name}`.",
            )
            return await ctx.send(embed=embed)

        elif name in self.bot.snippets:
            embed = discord.Embed(
                title="Error",
                color=self.bot.error_color,
                description=f"Snippet `{name}` already exists.",
            )
            return await ctx.send(embed=embed)

        if name in self.bot.aliases:
            embed = discord.Embed(
                title="Error",
                color=self.bot.error_color,
                description=f"An alias that shares the same name exists: `{name}`.",
            )
            return await ctx.send(embed=embed)

        if len(name) > 120:
            embed = discord.Embed(
                title="Error",
                color=self.bot.error_color,
                description="Snippet names cannot be longer than 120 characters.",
            )
            return await ctx.send(embed=embed)

        self.bot.snippets[name] = str(value)
        await self.bot.config.update()

        embed = discord.Embed(
            title="Added snippet",
            color=self.bot.main_color,
            description="Successfully created snippet.",
        )
        return await ctx.send(embed=embed)

    @snippet.command(name="remove", aliases=["del", "delete"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def snippet_remove(self, ctx: commands.Context, *, name: str.lower):
        """Remove a snippet."""

        if name in self.bot.snippets:
            embed = discord.Embed(
                title="Removed snippet",
                color=self.bot.main_color,
                description=f"Snippet `{name}` is now deleted.",
            )
            self.bot.snippets.pop(name)
            await self.bot.config.update()
        else:
            embed = create_not_found_embed(name, self.bot.snippets.keys(), "Snippet")
        await ctx.send(embed=embed)

    @snippet.command(name="edit")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def snippet_edit(self, ctx: commands.Context, name: str.lower, *, value):
        """
        Edit a snippet.

        To edit a multi-word snippet name, use quotes: ```
        {prefix}snippet edit "two word" this is a new two word snippet.
        ```
        """
        if name in self.bot.snippets:
            self.bot.snippets[name] = value
            await self.bot.config.update()

            embed = discord.Embed(
                title="Edited snippet",
                color=self.bot.main_color,
                description=f'`{name}` will now send "{value}".',
            )
        else:
            embed = create_not_found_embed(name, self.bot.snippets.keys(), "Snippet")
        await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.MODERATOR)
    @checks.is_modmail_thread()
    async def move(
        self,
        ctx: commands.Context,
        category: discord.CategoryChannel,
        *,
        specifics: str = None,
    ):
        """
        Move a thread to another category.

        `category` may be a category ID, mention, or name.
        `specifics` is a string which takes in arguments on how to perform the move. Ex: "silently"
        """
        modmail_thread = ctx.modmail_thread
        silent = False

        if specifics:
            silent_words = ["silent", "silently"]
            silent = any(word in silent_words for word in specifics.split())

        await modmail_thread.channel.edit(category=category, sync_permissions=True)

        if self.bot.config["thread_move_notify"] and not silent:
            embed = discord.Embed(
                title=self.bot.config["thread_move_title"],
                description=self.bot.config["thread_move_response"],
                color=self.bot.main_color,
            )
            await modmail_thread.recipient.send(embed=embed)

        if self.bot.config["thread_move_notify_mods"]:
            mention = self.bot.config["mention"]
            await modmail_thread.channel.send(f"{mention}, thread has been moved.")

        sent_emoji, _ = await self.bot.retrieve_emoji()
        await self.bot.add_reaction(ctx.message, sent_emoji)

    async def send_scheduled_close_message(
        self, ctx: commands.Context, after, silent=False
    ):
        human_delta = human_timedelta(after.dt)

        silent = "*silently* " if silent else ""

        embed = discord.Embed(
            title="Scheduled close",
            description=f"This thread will close {silent}in {human_delta}.",
            color=self.bot.error_color,
        )

        if after.arg and not silent:
            embed.add_field(name="Message", value=after.arg)

        embed.set_footer(text="Closing will be cancelled if a thread message is sent.")
        embed.timestamp = after.dt.replace(tzinfo=timezone.utc)

        await ctx.send(embed=embed)

    @commands.command(usage="[after] [close message]")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def close(self, ctx: commands.Context, *, after: UserFriendlyTime = None):
        """
        Close the current thread.

        Close after a period of time:
        - `{prefix}close in 5 hours`
        - `{prefix}close 2m30s`

        Custom close messages:
        - `{prefix}close 2 hours The issue has been resolved.`
        - `{prefix}close We will contact you once we find out more.`

        Silently close a thread (no message)
        - `{prefix}close silently`
        - `{prefix}close in 10m silently`

        Stop a thread from closing:
        - `{prefix}close cancel`
        """

        modmai_thread = ctx.modmail_thread

        close_after = int(after.difference().total_seconds()) if after else 0
        close_message = after.arg if after else None
        silent = str(close_message).lower() in {"silent", "silently"}
        cancel = str(close_message).lower() == "cancel"

        if cancel:

            if (
                modmai_thread.close_task is not None
                or modmai_thread.auto_close_task is not None
            ):
                await modmai_thread.cancel_closure(all=True)
                embed = discord.Embed(
                    color=self.bot.error_color,
                    description="Scheduled close has been cancelled.",
                )
            else:
                embed = discord.Embed(
                    color=self.bot.error_color,
                    description="This thread has not already been scheduled to close.",
                )

            return await ctx.send(embed=embed)

        if after and after.dt > after.now:
            await self.send_scheduled_close_message(ctx, after, silent)

        await modmai_thread.close(
            closer=ctx.author,
            after=close_after,
            close_message=close_message,
            silent=silent,
        )

    @staticmethod
    def parse_user_or_role(ctx: commands.Context, user_or_role):
        mention = None
        if user_or_role is None:
            mention = ctx.author.mention
        elif hasattr(user_or_role, "mention"):
            mention = user_or_role.mention
        elif user_or_role in {"here", "everyone", "@here", "@everyone"}:
            mention = "@" + user_or_role.lstrip("@")
        return mention

    @commands.command(aliases=["alert"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def notify(
        self,
        ctx: commands.Context,
        *,
        user_or_role: Union[discord.Role, User, str.lower, None] = None,
    ):
        """
        Notify a user or role when the next thread message received.

        Once a thread message is received, `user_or_role` will be pinged once.

        Leave `user_or_role` empty to notify yourself.
        `@here` and `@everyone` can be substituted with `here` and `everyone`.
        `user_or_role` may be a user ID, mention, name. role ID, mention, name, "everyone", or "here".
        """
        mention = self.parse_user_or_role(ctx, user_or_role)
        if mention is None:
            raise commands.BadArgument(f"{user_or_role} is not a valid user or role.")

        modmai_thread = ctx.modmail_thread

        if str(modmai_thread.id) not in self.bot.config["notification_squad"]:
            self.bot.config["notification_squad"][str(modmai_thread.id)] = []

        mentions = self.bot.config["notification_squad"][str(modmai_thread.id)]

        if mention in mentions:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=f"{mention} is already going to be mentioned.",
            )
        else:
            mentions.append(mention)
            await self.bot.config.update()
            embed = discord.Embed(
                color=self.bot.main_color,
                description=f"{mention} will be mentioned on the next message received.",
            )
        return await ctx.send(embed=embed)

    @commands.command(aliases=["unalert"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def unnotify(
        self,
        ctx: commands.Context,
        *,
        user_or_role: Union[discord.Role, User, str.lower, None] = None,
    ):
        """
        Un-notify a user, role, or yourself from a thread.

        Leave `user_or_role` empty to un-notify yourself.
        `@here` and `@everyone` can be substituted with `here` and `everyone`.
        `user_or_role` may be a user ID, mention, name, role ID, mention, name, "everyone", or "here".
        """
        mention = self.parse_user_or_role(ctx, user_or_role)
        if mention is None:
            mention = f"`{user_or_role}`"

        modmai_thread = ctx.modmail_thread

        if str(modmai_thread.id) not in self.bot.config["notification_squad"]:
            self.bot.config["notification_squad"][str(modmai_thread.id)] = []

        mentions = self.bot.config["notification_squad"][str(modmai_thread.id)]

        if mention not in mentions:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=f"{mention} does not have a pending notification.",
            )
        else:
            mentions.remove(mention)
            await self.bot.config.update()
            embed = discord.Embed(
                color=self.bot.main_color,
                description=f"{mention} will no longer be notified.",
            )
        return await ctx.send(embed=embed)

    @commands.command(aliases=["sub"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def subscribe(
        self,
        ctx: commands.Context,
        *,
        user_or_role: Union[discord.Role, User, str.lower, None] = None,
    ):
        """
        Notify a user, role, or yourself for every thread message received.

        You will be pinged for every thread message received until you unsubscribe.

        Leave `user_or_role` empty to subscribe yourself.
        `@here` and `@everyone` can be substituted with `here` and `everyone`.
        `user_or_role` may be a user ID, mention, name, role ID, mention, name, "everyone", or "here".
        """
        mention = self.parse_user_or_role(ctx, user_or_role)
        if mention is None:
            raise commands.BadArgument(f"{user_or_role} is not a valid user or role.")

        modmai_thread = ctx.modmail_thread

        if str(modmai_thread.id) not in self.bot.config["subscriptions"]:
            self.bot.config["subscriptions"][str(modmai_thread.id)] = []

        mentions = self.bot.config["subscriptions"][str(modmai_thread.id)]

        if mention in mentions:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=f"{mention} is already subscribed to this thread.",
            )
        else:
            mentions.append(mention)
            await self.bot.config.update()
            embed = discord.Embed(
                color=self.bot.main_color,
                description=f"{mention} will now be notified of all messages received.",
            )
        return await ctx.send(embed=embed)

    @commands.command(aliases=["unsub"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def unsubscribe(
        self,
        ctx: commands.Context,
        *,
        user_or_role: Union[discord.Role, User, str.lower, None] = None,
    ):
        """
        Unsubscribe a user, role, or yourself from a thread.

        Leave `user_or_role` empty to unsubscribe yourself.
        `@here` and `@everyone` can be substituted with `here` and `everyone`.
        `user_or_role` may be a user ID, mention, name, role ID, mention, name, "everyone", or "here".
        """
        mention = self.parse_user_or_role(ctx, user_or_role)
        if mention is None:
            mention = f"`{user_or_role}`"

        modmai_thread = ctx.modmail_thread

        if str(modmai_thread.id) not in self.bot.config["subscriptions"]:
            self.bot.config["subscriptions"][str(modmai_thread.id)] = []

        mentions = self.bot.config["subscriptions"][str(modmai_thread.id)]

        if mention not in mentions:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=f"{mention} is not subscribed to this thread.",
            )
        else:
            mentions.remove(mention)
            await self.bot.config.update()
            embed = discord.Embed(
                color=self.bot.main_color,
                description=f"{mention} is now unsubscribed from this thread.",
            )
        return await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def nsfw(self, ctx: commands.Context):
        """Flags a Modmail thread as NSFW (not safe for work)."""
        if not isinstance(ctx.channel, discord.TextChannel):
            return
        channel: discord.TextChannel = ctx.channel
        await channel.edit(nsfw=True)
        sent_emoji, _ = await self.bot.retrieve_emoji()
        await self.bot.add_reaction(ctx.message, sent_emoji)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def sfw(self, ctx: commands.Context):
        """Flags a Modmail thread as SFW (safe for work)."""
        if not isinstance(ctx.channel, discord.TextChannel):
            return
        channel: discord.TextChannel = ctx.channel
        await channel.edit(nsfw=False)
        sent_emoji, _ = await self.bot.retrieve_emoji()
        await self.bot.add_reaction(ctx.message, sent_emoji)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def msglink(self, ctx: commands.Context, message_id: int):
        """
        Retrieves the link to a message in the current thread.
        """
        try:
            message = await ctx.modmail_thread.recipient.fetch_message(message_id)
        except discord.NotFound:
            embed = discord.Embed(
                color=self.bot.error_color,
                description="Message not found or no longer exists.",
            )
        else:
            embed = discord.Embed(
                color=self.bot.main_color, description=message.jump_url
            )
        await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def loglink(self, ctx: commands.Context):
        """Retrieves the link to the current thread's logs."""
        log_link = await self.bot.api.get_log_link(ctx.channel.id)
        await ctx.send(
            embed=discord.Embed(color=self.bot.main_color, description=log_link)
        )

    def format_log_embeds(self, logs: Iterable[ThreadLogPayload], avatar_url: str):
        embeds = []
        logs = tuple(logs)
        title = f"Total Results Found ({len(logs)})"

        for entry in logs:
            created_at = parser.parse(entry["created_at"])
            if created_at.tzinfo is None:
                # to support older log
                created_at = created_at.replace(tzinfo=timezone.utc)

            prefix = self.bot.config["log_url_prefix"].strip("/")
            if prefix == "NONE":
                prefix = ""
            log_url = f"{self.bot.config['log_url'].strip('/')}{'/' + prefix if prefix else ''}/{entry['key']}"

            username = entry["recipient"]["name"] + "#"
            username += entry["recipient"]["discriminator"]

            embed = discord.Embed(color=self.bot.main_color, timestamp=created_at)
            embed.set_author(
                name=f"{title} - {username}", icon_url=avatar_url, url=log_url
            )
            embed.url = log_url
            embed.add_field(
                name="Created", value=duration(created_at, now=discord.utils.utcnow())
            )
            closer = entry.get("closer")
            if closer is None:
                closer_msg = "Unknown"
            else:
                closer_msg = f"<@{closer['id']}>"
            embed.add_field(name="Closed By", value=closer_msg)

            if entry["recipient"]["id"] != entry["creator"]["id"]:
                embed.add_field(name="Created by", value=f"<@{entry['creator']['id']}>")

            embed.add_field(
                name="Preview", value=format_preview(entry["messages"]), inline=False
            )

            if closer is not None:
                # BUG: Currently, logviewer can't display logs without a closer.
                embed.add_field(name="Link", value=log_url)
            else:
                logger.debug("Invalid log entry: no closer.")
                embed.add_field(name="Log Key", value=f"`{entry['key']}`")

            embed.set_footer(text="Recipient ID: " + str(entry["recipient"]["id"]))
            embeds.append(embed)
        return embeds

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def logs(self, ctx: commands.Context, *, user: User = None):
        """
        Get previous Modmail thread logs of a member.

        Leave `user` blank when this command is used within a thread channel to show logs for the current recipient.
        `user` may be a user ID, mention, or name.
        """

        await ctx.typing()

        if not user:
            modmai_thread = ctx.modmail_thread
            if not modmai_thread:
                raise commands.MissingRequiredArgument(
                    ctx.command.clean_params.get("user")
                )
            user = modmai_thread.recipient or await self.bot.fetch_user(
                modmai_thread.id
            )

        default_avatar = "https://cdn.discordapp.com/embed/avatars/0.png"
        icon_url = getattr(user.avatar, "url", default_avatar)

        logs = await self.bot.api.get_user_logs(user.id)

        if not any(not log["open"] for log in logs):
            embed = discord.Embed(
                color=self.bot.error_color,
                description="This user does not have any previous logs.",
            )
            return await ctx.send(embed=embed)

        logs = reversed([log for log in logs if not log["open"]])

        embeds = self.format_log_embeds(logs, avatar_url=icon_url)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @logs.command(name="closed-by", aliases=["closeby"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def logs_closed_by(self, ctx: commands.Context, *, user: User = None):
        """
        Get all logs closed by the specified user.

        If no `user` is provided, the user will be the person who sent this command.
        `user` may be a user ID, mention, or name.
        """
        user = user if user is not None else ctx.author

        entries = await self.bot.api.search_closed_by(user.id)
        embeds = self.format_log_embeds(entries, avatar_url=self.bot.guild.icon.url)

        if not embeds:
            embed = discord.Embed(
                color=self.bot.error_color,
                description="No log entries have been found for that query.",
            )
            return await ctx.send(embed=embed)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @logs.command(name="delete", aliases=["wipe"])
    @checks.has_permissions(PermissionLevel.OWNER)
    async def logs_delete(self, ctx: commands.Context, key_or_link: str):
        """
        Wipe a log entry from the database.

        To clear all logs, use command `{prefix}logs delete all`.
        Plase note, this operation cannot be undone. Use with caution.
        """
        if key_or_link == "all":
            await self.bot.api.delete_all_logs()

            embed = discord.Embed(
                title="Success",
                description=f"All logs have been successfully deleted.",
                color=self.bot.main_color,
            )
            return await ctx.send(embed=embed)

        key = key_or_link.split("/")[-1]

        success = await self.bot.api.delete_log_entry(key)

        if not success:
            embed = discord.Embed(
                title="Error",
                description=f"Log entry `{key}` not found.",
                color=self.bot.error_color,
            )
        else:
            embed = discord.Embed(
                title="Success",
                description=f"Log entry `{key}` successfully deleted.",
                color=self.bot.main_color,
            )

        await ctx.send(embed=embed)

    @logs.command(name="responded")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def logs_responded(self, ctx: commands.Context, *, user: User = None):
        """
        Get all logs where the specified user has responded at least once.

        If no `user` is provided, the user will be the person who sent this command.
        `user` may be a user ID, mention, or name.
        """
        user = user if user is not None else ctx.author

        entries = await self.bot.api.get_responded_logs(user.id)

        embeds = self.format_log_embeds(entries, avatar_url=self.bot.guild.icon.url)

        if not embeds:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=f"{getattr(user, 'mention', user.id)} has not responded to any threads.",
            )
            return await ctx.send(embed=embed)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @logs.command(name="search", aliases=["find"])
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def logs_search(
        self, ctx: commands.Context, limit: Optional[int] = None, *, query
    ):
        """
        Retrieve all logs that contain messages with your query.

        Provide a `limit` to specify the maximum number of logs the bot should find.
        """

        await ctx.typing()

        entries = await self.bot.api.search_by_text(query, limit)

        embeds = self.format_log_embeds(entries, avatar_url=self.bot.guild.icon.url)

        if not embeds:
            embed = discord.Embed(
                color=self.bot.error_color,
                description="No log entries have been found for that query.",
            )
            return await ctx.send(embed=embed)

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def reply(self, ctx: commands.Context, *, msg: str = ""):
        """
        Reply to a Modmail thread.

        Supports attachments and images as well as automatically embedding image URLs.
        """
        ctx.message.content = msg
        async with ctx.typing():
            await ctx.modmail_thread.reply(ctx.message)

    @commands.command(aliases=["formatreply"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def freply(self, ctx: commands.Context, *, msg: str = ""):
        """
        Reply to a Modmail thread with variables.

        Works just like `{prefix}reply`, however with the addition of three variables:
          - `{{channel}}` - the `discord.TextChannel` object
          - `{{recipient}}` - the `discord.User` object of the recipient
          - `{{author}}` - the `discord.User` object of the author

        Supports attachments and images as well as automatically embedding image URLs.
        """
        msg = self.bot.formatter.format(
            msg,
            channel=ctx.channel,
            recipient=ctx.modmail_thread.recipient,
            author=ctx.message.author,
        )
        ctx.message.content = msg
        async with ctx.typing():
            await ctx.modmail_thread.reply(ctx.message)

    @commands.command(aliases=["anonreply", "anonymousreply"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def areply(self, ctx: commands.Context, *, msg: str = ""):
        """
        Reply to a thread anonymously.

        You can edit the anonymous user's name, avatar and tag using the config command.

        Edit the `anon_username`, `anon_avatar_url`
        and `anon_tag` config variables to do so.
        """
        ctx.message.content = msg
        async with ctx.typing():
            await ctx.modmail_thread.reply(ctx.message, anonymous=True)

    @commands.command(aliases=["plainreply"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def preply(self, ctx: commands.Context, *, msg: str = ""):
        """
        Reply to a Modmail thread with a plain message.

        Supports attachments and images as well as automatically embedding image URLs.
        """
        ctx.message.content = msg
        async with ctx.typing():
            await ctx.modmail_thread.reply(ctx.message, plain=True)

    @commands.command(aliases=["plainanonreply", "plainanonymousreply"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def pareply(self, ctx: commands.Context, *, msg: str = ""):
        """
        Reply to a Modmail thread with a plain message and anonymously.

        Supports attachments and images as well as automatically embedding image URLs.
        """
        ctx.message.content = msg
        async with ctx.typing():
            await ctx.modmail_thread.reply(ctx.message, anonymous=True, plain=True)

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def note(self, ctx: commands.Context, *, msg: str):
        """
        Take a note about the current thread.

        Useful for noting context.
        """
        ctx.message.content = msg
        async with ctx.typing():
            msg = await ctx.modmail_thread.note(ctx.message)
            await msg.pin()

    @note.command(name="persistent", aliases=["persist"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def note_persistent(self, ctx: commands.Context, *, msg: str):
        """
        Take a persistent note about the current user.
        """
        ctx.message.content = msg
        async with ctx.typing():
            msg = await ctx.modmail_thread.note(ctx.message, persistent=True)
            await msg.pin()
        await self.bot.api.create_note(
            recipient=ctx.modmail_thread.recipient,
            message=ctx.message,
            message_id=msg.id,
        )

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def edit(
        self, ctx: commands.Context, message_id: Optional[int] = None, *, message: str
    ):
        """
        Edit a message that was sent using the reply or anonreply command.

        If no `message_id` is provided, the last message sent by a staff will be edited.

        Note: Attachments **cannot** be edited.
        """
        modmai_thread = ctx.modmail_thread

        try:
            await modmai_thread.edit_message(ctx.author, message_id, message)
        except LinkMessageError:
            return await ctx.send(
                embed=discord.Embed(
                    title="Failed",
                    description="Cannot find a message to edit.",
                    color=self.bot.error_color,
                )
            )

        sent_emoji, _ = await self.bot.retrieve_emoji()
        await self.bot.add_reaction(ctx.message, sent_emoji)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def contact(
        self,
        ctx: commands.Context,
        user: Union[discord.Member, discord.User],
        category: Union[discord.CategoryChannel, str] = None,
        *,
        option: str = "",
    ):
        """
        Create a thread with a specified member.

        `user` may be a user ID, mention, or name.
        `category`, if specified, may be a category ID, mention, or name.
        `option` can be `silent` or `silently`.

        __**Note:**__
        - If `category` is specified, the thread will be created in that specified category instead.
        """

        valid_options = ("silent", "silently")
        silent = False
        if not category:
            category = None
        elif isinstance(category, discord.CategoryChannel):
            if option and option in valid_options:
                silent = True
            elif option:
                raise commands.BadArgument(
                    f'"{option}" is not a valid argument for parameter `option`.'
                )
        else:
            # if we reach here that means the category conversion has failed
            # and all the arguments here are strings
            # so we have to manually parse them
            args = " ".join(v for v in (category, option)).split()
            if args[-1].lower() in valid_options:
                silent = True
                args.pop()

            args = " ".join(args)
            if args:
                converter = commands.CategoryChannelConverter()
                try:
                    category = await converter.convert(ctx, args)
                except commands.ChannelNotFound:
                    raise
            else:
                category = None

        if user.bot:
            embed = discord.Embed(
                color=self.bot.error_color,
                description="Cannot start a thread with a bot.",
            )
            return await ctx.send(embed=embed, delete_after=3)

        exists = await self.bot.thread_manager.find(recipient=user)
        if exists:
            desc = "A thread for this user already exists"
            if exists.channel:
                desc += f" in {exists.channel.mention}"
            desc += "."
            embed = discord.Embed(color=self.bot.error_color, description=desc)
            await ctx.channel.send(embed=embed, delete_after=3)

        else:
            if ctx.message:
                manual_trigger = (
                    f"{ctx.channel.id}-{ctx.message.id}"
                    != self.bot.config.get("contact_panel_message")
                )
            else:
                manual_trigger = True
            creator = ctx.author if manual_trigger else user
            if await self.bot.is_blocked(user):
                if not manual_trigger:  # react to contact
                    return

                ref = f"{user.mention} is" if creator != user else "You are"
                raise commands.BadArgument(
                    f"{ref} currently blocked from contacting {self.bot.user.name}."
                )

            thread = await self.bot.thread_manager.create(
                recipient=user,
                creator=creator,
                category=category,
                manual_trigger=manual_trigger,
            )
            if thread.cancelled:
                return

            if self.bot.config["dm_disabled"] in (
                DMDisabled.NEW_THREADS,
                DMDisabled.ALL_THREADS,
            ):
                logger.info("Contacting user %s when Modmail DM is disabled.", user)

            if not silent:
                if creator.id == user.id:
                    description = "You have opened a Modmail thread."
                else:
                    _creator = creator.name
                    if self.bot.config.get("thread_contact_anonymously"):
                        _creator = self.bot.config["anon_username"]
                        if _creator is None:
                            tag = self.bot.config["mod_tag"]
                            _creator = (
                                tag if tag is not None else str(ctx.author.top_role)
                            )
                    description = f"{_creator} has opened a Modmail thread."
                em = discord.Embed(
                    title="New Thread",
                    description=description,
                    color=self.bot.main_color,
                    timestamp=discord.utils.utcnow(),
                )
                em.set_footer(icon_url=creator.display_avatar.url)
                await user.send(embed=em)

            embed = discord.Embed(
                title="Created Thread",
                description=f"Thread started by {creator.mention} for {user.mention}.",
                color=self.bot.main_color,
            )
            await thread.wait_until_ready()
            await thread.channel.send(embed=embed)

            if manual_trigger:
                _embed = discord.Embed(
                    description=f"Thread started in {thread.channel.mention} "
                    f"by {creator.mention} for {user.mention}.",
                    color=self.bot.main_color,
                )
                await ctx.send(embed=_embed)

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.MODERATOR)
    @trigger_typing
    async def blocked(self, ctx: commands.Context):
        """Retrieve a list of blocked users."""

        embeds = [
            discord.Embed(
                title="Blocked Users", color=self.bot.main_color, description=""
            )
        ]

        users = []
        now = discord.utils.utcnow()
        blocked_users = list(self.bot.blocked_users.items())
        to_update = False
        for id_, reason in blocked_users:
            # parse "reason" and check if block is expired
            end_time = re.search(r"until ([^`]+?)\.$", reason)
            if end_time is None:
                # backwards compat
                end_time = re.search(r"%([^%]+?)%", reason)
                if end_time is not None:
                    logger.warning(
                        r"Deprecated time message for user %s, block and unblock again to update.",
                        id_,
                    )

            if end_time is not None:
                after = (
                    datetime.fromisoformat(end_time.group(1)).replace(
                        tzinfo=timezone.utc
                    )
                    - now
                ).total_seconds()
                if after <= 0:
                    # No longer blocked
                    self.bot.blocked_users.pop(str(id_))
                    logger.debug("No longer blocked, user %s.", id_)
                    to_update = True
                    continue

            user = self.bot.get_user(int(id_))
            if user:
                users.append((user.mention, reason))
            else:
                try:
                    user = await self.bot.fetch_user(id_)
                    users.append((user.mention, reason))
                except discord.NotFound:
                    users.append((id_, reason))

        if users:
            embed = embeds[0]

            for mention, reason in users:
                line = mention + f" - {reason or 'No Reason Provided'}\n"
                if len(embed.description) + len(line) > 2048:
                    embed = discord.Embed(
                        title="Blocked Users (Continued)",
                        color=self.bot.main_color,
                        description=line,
                    )
                    embeds.append(embed)
                else:
                    embed.description += line
        else:
            embeds[0].description = "Currently there are no blocked users."

        session = EmbedPaginatorSession(ctx, *embeds)
        await session.run()
        if to_update:
            await self.bot.config.update()

    @blocked.command(name="whitelist")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    @trigger_typing
    async def blocked_whitelist(self, ctx: commands.Context, *, user: User = None):
        """
        Whitelist or un-whitelist a user from getting blocked.

        Useful for preventing users from getting blocked by account_age/guild_age restrictions.
        """
        if user is None:
            modmai_thread = ctx.modmail_thread
            if modmai_thread:
                user = modmai_thread.recipient
            else:
                return await ctx.send_help(ctx.command)

        mention = getattr(user, "mention", f"`{user.id}`")
        msg = ""

        if str(user.id) in self.bot.blocked_whitelisted_users:
            embed = discord.Embed(
                title="Success",
                description=f"{mention} is no longer whitelisted.",
                color=self.bot.main_color,
            )
            self.bot.blocked_whitelisted_users.remove(str(user.id))
            return await ctx.send(embed=embed)

        self.bot.blocked_whitelisted_users.append(str(user.id))

        if str(user.id) in self.bot.blocked_users:
            msg = self.bot.blocked_users.get(str(user.id)) or ""
            self.bot.blocked_users.pop(str(user.id))

        await self.bot.config.update()

        if msg.startswith("System Message: "):
            # If the user is blocked internally (for example: below minimum account age)
            # Show an extended message stating the original internal message
            reason = msg[16:].strip().rstrip(".")
            embed = discord.Embed(
                title="Success",
                description=f"{mention} was previously blocked internally for "
                f'"{reason}". {mention} is now whitelisted.',
                color=self.bot.main_color,
            )
        else:
            embed = discord.Embed(
                title="Success",
                color=self.bot.main_color,
                description=f"{mention} is now whitelisted.",
            )

        return await ctx.send(embed=embed)

    @commands.command(usage="[user] [duration] [reason]")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    @trigger_typing
    async def block(
        self,
        ctx: commands.Context,
        user: Optional[User] = None,
        *,
        after: UserFriendlyTime = None,
    ):
        """
        Block a user from using Modmail.

        You may choose to set a time as to when the user will automatically be unblocked.

        Leave `user` blank when this command is used within a thread channel to block the current recipient.
        `user` may be a user ID, mention, or name.
        `duration` may be a simple "human-readable" time text. See `{prefix}help close` for examples.
        """

        if user is None:
            modmai_thread = ctx.modmail_thread
            if modmai_thread:
                user = modmai_thread.recipient
            elif after is None:
                raise commands.MissingRequiredArgument(
                    ctx.command.clean_params.get("user")
                )
            else:
                raise commands.BadArgument(f'User "{after.arg}" not found.')

        mention = getattr(user, "mention", f"`{user.id}`")

        if str(user.id) in self.bot.blocked_whitelisted_users:
            embed = discord.Embed(
                title="Error",
                description=f"Cannot block {mention}, user is whitelisted.",
                color=self.bot.error_color,
            )
            return await ctx.send(embed=embed)

        reason = f"by {escape_markdown(ctx.author.name)}#{ctx.author.discriminator}"

        if after is not None:
            if "%" in reason:
                raise commands.BadArgument('The reason contains illegal character "%".')
            if after.arg:
                reason += f" for `{after.arg}`"
            if after.dt > after.now:
                reason += f" until {after.dt.isoformat()}"

        reason += "."

        msg = self.bot.blocked_users.get(str(user.id))
        if msg is None:
            msg = ""

        if str(user.id) in self.bot.blocked_users and msg:
            old_reason = msg.strip().rstrip(".")
            embed = discord.Embed(
                title="Success",
                description=f"{mention} was previously blocked {old_reason}.\n"
                f"{mention} is now blocked {reason}",
                color=self.bot.main_color,
            )
        else:
            embed = discord.Embed(
                title="Success",
                color=self.bot.main_color,
                description=f"{mention} is now blocked {reason}",
            )
        self.bot.blocked_users[str(user.id)] = reason
        await self.bot.config.update()

        return await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.MODERATOR)
    @trigger_typing
    async def unblock(self, ctx: commands.Context, *, user: User = None):
        """
        Unblock a user from using Modmail.

        Leave `user` blank when this command is used within a thread channel to unblock the current recipient.
        `user` may be a user ID, mention, or name.
        """

        if user is None:
            modmai_thread = ctx.modmail_thread
            if modmai_thread:
                user = modmai_thread.recipient
            else:
                raise commands.MissingRequiredArgument(
                    ctx.command.clean_params.get("user")
                )

        mention = getattr(user, "mention", f"`{user.id}`")
        name = getattr(user, "name", f"`{user.id}`")

        if str(user.id) in self.bot.blocked_users:
            msg = self.bot.blocked_users.pop(str(user.id)) or ""
            await self.bot.config.update()

            if msg.startswith("System Message: "):
                # If the user is blocked internally (for example: below minimum account age)
                # Show an extended message stating the original internal message
                reason = msg[16:].strip().rstrip(".") or "no reason"
                embed = discord.Embed(
                    title="Success",
                    description=f"{mention} was previously blocked internally {reason}.\n"
                    f"{mention} is no longer blocked.",
                    color=self.bot.main_color,
                )
                embed.set_footer(
                    text="However, if the original system block reason still applies, "
                    f"{name} will be automatically blocked again. "
                    f'Use "{self.bot.prefix}blocked whitelist {user.id}" to whitelist the user.'
                )
            else:
                embed = discord.Embed(
                    title="Success",
                    color=self.bot.main_color,
                    description=f"{mention} is no longer blocked.",
                )
        else:
            embed = discord.Embed(
                title="Error",
                description=f"{mention} is not blocked.",
                color=self.bot.error_color,
            )

        return await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.is_modmail_thread()
    async def delete(self, ctx: commands.Context, message_id: int = None):
        """
        Delete a message that was sent using the reply command or a note.

        Deletes the previous message, unless a message ID is provided, which in that case, deletes the message with that message ID.

        Notes can only be deleted when a note ID is provided.
        """
        modmai_thread = ctx.modmail_thread

        try:
            await modmai_thread.delete_message(message_id, note=True)
        except LinkMessageError as e:
            logger.warning("Failed to delete message: %s", e)
            return await ctx.send(
                embed=discord.Embed(
                    title="Failed",
                    description="Cannot find a message to delete.",
                    color=self.bot.error_color,
                )
            )

        sent_emoji, _ = await self.bot.retrieve_emoji()
        await self.bot.add_reaction(ctx.message, sent_emoji)

    @commands.command()
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def repair(self, ctx: commands.Context):
        """
        Repair a thread broken by Discord.

        Methods:
        - Finds the user ID from channel topic.
        - Search thread in cache that matches the channel.
        - Finds the genesis message in channel and retrieves the user ID.
        - Get log from database to retrieve user ID.

        __**Note:**__
        This command is meant to fix broken thread channels.
        DO NOT use this command in other than thread channels.
        """
        if not isinstance(ctx.channel, discord.TextChannel):
            return
        channel: discord.TextChannel = ctx.channel
        sent_emoji, blocked_emoji = await self.bot.retrieve_emoji()
        modmai_thread = ctx.modmail_thread

        if modmai_thread:
            user_id = match_user_id(ctx.channel.topic)
            if user_id == -1:
                logger.info("Setting current channel's topic to User ID.")
                await channel.edit(
                    reason="Fix broken Modmail thread",
                    topic=modmai_thread.topic_string(),
                )
            return await self.bot.add_reaction(ctx.message, sent_emoji)

        logger.info("Attempting to fix a broken thread %s.", channel.name)

        modmai_thread = await self.bot.thread_manager.repair(channel, check_cache=False)
        if modmai_thread:
            return await self.bot.add_reaction(ctx.message, sent_emoji)
        return await self.bot.add_reaction(ctx.message, blocked_emoji)

    @commands.command()
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def enable(self, ctx: commands.Context):
        """
        Re-enables DM functionalities of Modmail.

        Undo's the `{prefix}disable` command, all DM will be relayed after running this command.
        """
        embed = discord.Embed(
            title="Success",
            description="Modmail will now accept all DM messages.",
            color=self.bot.main_color,
        )

        if self.bot.config["dm_disabled"] != DMDisabled.NONE:
            self.bot.config["dm_disabled"] = DMDisabled.NONE
            await self.bot.config.update()

        return await ctx.send(embed=embed)

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def disable(self, ctx: commands.Context):
        """
        Disable partial or full Modmail thread functions.

        To stop all new threads from being created, do `{prefix}disable new`.
        To stop all existing threads from DMing Modmail, do `{prefix}disable all`.
        To check if the DM function for Modmail is enabled, do `{prefix}isenable`.
        """
        await ctx.send_help(ctx.command)

    @disable.command(name="new")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def disable_new(self, ctx: commands.Context):
        """
        Stop accepting new Modmail threads.

        No new threads can be created through DM.
        """
        embed = discord.Embed(
            title="Success",
            description="Modmail will not create any new threads.",
            color=self.bot.main_color,
        )
        if self.bot.config["dm_disabled"] != DMDisabled.NEW_THREADS:
            self.bot.config["dm_disabled"] = DMDisabled.NEW_THREADS
            await self.bot.config.update()

        return await ctx.send(embed=embed)

    @disable.command(name="all")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def disable_all(self, ctx: commands.Context):
        """
        Disables all DM functionalities of Modmail.

        No new threads can be created through DM nor no further DM messages will be relayed.
        """
        embed = discord.Embed(
            title="Success",
            description="Modmail will not accept any DM messages.",
            color=self.bot.main_color,
        )

        if self.bot.config["dm_disabled"] < DMDisabled.ALL_THREADS:
            self.bot.config["dm_disabled"] = DMDisabled.ALL_THREADS
            await self.bot.config.update()

        return await ctx.send(embed=embed)

    @commands.command()
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def isenable(self, ctx: commands.Context):
        """
        Check if the DM functionalities of Modmail is enabled.
        """

        if self.bot.config["dm_disabled"] == DMDisabled.NEW_THREADS:
            embed = discord.Embed(
                title="New Threads Disabled",
                description="Modmail is not creating new threads.",
                color=self.bot.error_color,
            )
        elif self.bot.config["dm_disabled"] == DMDisabled.ALL_THREADS:
            embed = discord.Embed(
                title="All DM Disabled",
                description="Modmail is not accepting any DM messages for new and existing threads.",
                color=self.bot.error_color,
            )
        else:
            embed = discord.Embed(
                title="Enabled",
                description="Modmail now is accepting all DM messages.",
                color=self.bot.main_color,
            )

        return await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Modmail(bot))
