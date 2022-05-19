from __future__ import annotations

import asyncio
import io
import re
import time
from datetime import datetime, timedelta, timezone
from typing import (
    Dict,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
    Union,
    TYPE_CHECKING,
)

import isodate
from discord import (
    Asset,
    CategoryChannel,
    Color,
    Embed,
    File,
    Member,
    Message,
    PermissionOverwrite,
    TextChannel,
    Forbidden as DiscordForbidden,
    HTTPException as DiscordHTTPException,
    NotFound as DiscordNotFound,
    utils as discord_utils,
)

from core.enums_ext import DMDisabled, ThreadMessageType
from core.errors import (
    DMMessageNotFound,
    IgnoredMessage,
    LinkMessageError,
    MalformedThreadMessage,
    ThreadError,
    ThreadMessageNotFound,
)
from core.ext.commands import CommandError
from core.logging_ext import getLogger
from core.models import ThreadMessage, PartialPersistentNote
from core.timeutils import human_timedelta
from core.utils import (
    create_thread_channel,
    days,
    generate_topic_string,
    get_top_role,
    is_image_url,
    parse_image_url,
    match_user_id,
    truncate,
)
from core.views.confirm import ConfirmView

if TYPE_CHECKING:
    from discord import ClientUser, DMChannel, User

    from bot import ModmailBot

    from core.types_ext.raw_data import PersistentNotePayload

    ReplyPayload = Tuple[Optional[Message], Optional[Message]]

logger = getLogger(__name__)

MISSING = discord_utils.MISSING


class ModmailThread:
    """Represents a discord Modmail thread"""

    def __init__(
        self,
        manager: ModmailThreadManager,
        recipient: Union[Member, User, int],
        channel: TextChannel = MISSING,
    ):
        self.manager: ModmailThreadManager = manager
        self.bot: ModmailBot = manager.bot
        if isinstance(recipient, int):
            self._id: int = recipient
            self._recipient: Union[Member, User] = MISSING
        else:
            if recipient.bot:
                raise CommandError("Recipient cannot be a bot.")
            self._id: int = recipient.id
            self._recipient: Union[Member, User] = recipient
        self._channel: TextChannel = channel
        self.genesis_message: Message = MISSING
        self._ready_event: asyncio.Event = asyncio.Event()
        self.wait_tasks: List[asyncio.Task] = []
        self.close_task: Optional[asyncio.TimerHandle] = None
        self.auto_close_task: Optional[asyncio.TimerHandle] = None
        self._cancelled: bool = False
        self._trash_mapping: Dict[str, Set[str]] = {
            "ignored_messages": set(),
            "deleted_messages": set(),
        }

    def __repr__(self) -> str:
        return f'ModmailThread(recipient="{self.recipient or self.id}", channel={getattr(self.channel, "id", None)})'

    async def wait_until_ready(self) -> None:
        """
        Blocks execution until the thread is fully set up.
        """
        # timeout after 30 seconds
        task = self.bot.loop.create_task(
            asyncio.wait_for(self._ready_event.wait(), timeout=25)
        )
        self.wait_tasks.append(task)
        try:
            await task
        except asyncio.TimeoutError:
            pass

        self.wait_tasks.remove(task)

    @property
    def id(self) -> int:
        """
        The ID of the recipient.
        """
        return self._id

    @property
    def channel(self) -> TextChannel:
        """
        The channel object of this thread.
        """
        return self._channel

    @property
    def recipient(self) -> Union[Member, User]:
        """
        Member or User object of the recipient if it is fetched. Depends on how this class is instantiated,
        the return value of this could be `MISSING`.
        """
        return self._recipient

    @property
    def ready(self) -> bool:
        """
        Returns True if the thread is ready.
        """
        return self._ready_event.is_set()

    @ready.setter
    def ready(self, flag: bool) -> None:
        """
        Sets the ready flag to True or False.
        If set to True, the `thread_create` event will be dispatched.
        """
        if flag:
            self._ready_event.set()
            self.bot.dispatch("thread_create", self)
        else:
            self._ready_event.clear()

    @property
    def cancelled(self) -> bool:
        """
        Returns True if the thread creation is cancelled. Otherwise, False.
        """
        return self._cancelled

    @cancelled.setter
    def cancelled(self, flag: bool) -> None:
        """
        Sets the cancel thread creation flag to True or False.
        If set to True, all the tasks for thread creation will be cancelled.
        """
        self._cancelled = flag
        if flag:
            for i in self.wait_tasks:
                i.cancel()

    @property
    def _ignored_messages(self) -> Set[str]:
        """
        The list of message IDs to be ignored when linking messages.
        """
        return self._trash_mapping["ignored_messages"]

    @property
    def _deleted_messages(self) -> Set[str]:
        """
        List of deleted message IDs to be ignored when linking deleted messages.
        """
        return self._trash_mapping["deleted_messages"]

    async def setup(
        self,
        *,
        creator: Union[Member, User] = None,
        category: CategoryChannel = None,
        initial_message: Message = None,
    ) -> None:
        """
        Create the thread channel and other io related initialisation tasks.
        """
        self.bot.dispatch("thread_initiate", self, creator, category, initial_message)

        recipient = self.recipient

        category = category or self.bot.main_category
        if isinstance(category, CategoryChannel):
            overwrites = MISSING  # synced to category
        else:
            # in case it creates a channel outside of category
            category = None
            overwrites = {
                self.bot.modmail_guild.default_role: PermissionOverwrite(
                    read_messages=False
                )
            }

        try:
            channel = await create_thread_channel(self, recipient, category, overwrites)
        except DiscordHTTPException as e:  # Failed to create due to missing perms.
            logger.critical("An error occurred while creating a thread.", exc_info=True)
            self.manager.cache.pop(self.id)

            embed = Embed(color=self.bot.error_color)
            embed.title = "Error while trying to create a thread."
            embed.description = str(e)
            embed.add_field(name="Recipient", value=recipient.mention)

            if self.bot.log_channel is not None:
                await self.bot.log_channel.send(embed=embed)
            return

        self._channel = channel

        try:
            log_url, log_data = await asyncio.gather(
                self.bot.api.create_log_entry(recipient, channel, creator or recipient),
                self.bot.api.get_user_logs(recipient.id),
            )

            log_count = sum(1 for log in log_data if not log["open"])
        except Exception:
            logger.error(
                "An error occurred while posting logs to the database.", exc_info=True
            )
            log_url = log_count = None

        self.ready = True

        if creator is not None and creator != recipient:
            mention = None
        else:
            mention = self.bot.config["mention"]

        async def send_genesis_message():
            info_embed = self._format_info_embed(
                recipient, log_url, log_count, self.bot.main_color
            )
            try:
                msg = await channel.send(mention, embed=info_embed)
                self.bot.loop.create_task(msg.pin())
                self.genesis_message = msg
            except Exception:
                logger.error("Failed unexpectedly:", exc_info=True)

        async def send_recipient_genesis_message():
            # Once thread is ready, tell the recipient.
            thread_creation_response = self.bot.config["thread_creation_response"]

            embed = Embed(
                color=self.bot.mod_color,
                description=thread_creation_response,
                timestamp=channel.created_at,
            )

            recipient_thread_close = self.bot.config.get("recipient_thread_close")

            if recipient_thread_close:
                footer = self.bot.config["thread_self_closable_creation_footer"]
            else:
                footer = self.bot.config["thread_creation_footer"]

            embed.set_footer(text=footer, icon_url=self.bot.guild.icon.url)
            embed.title = self.bot.config["thread_creation_title"]

            if creator is None or creator == recipient:
                msg = await recipient.send(embed=embed)

                if recipient_thread_close:
                    close_emoji = self.bot.config["close_emoji"]
                    close_emoji = self.bot.convert_emoji(close_emoji)
                    await self.bot.add_reaction(msg, close_emoji)

        async def send_persistent_notes():
            notes = await self.bot.api.find_notes(self.recipient)
            ids = {}

            for note in notes:
                note: PersistentNotePayload

                new_id = round(time.time() * 1000 - discord_utils.DISCORD_EPOCH) << 22
                message = PartialPersistentNote(
                    id=new_id, channel=self.channel, data=note
                )
                ids[note["_id"]] = str(
                    (await self.note(message, persistent=True, thread_creation=True)).id
                )

            await self.bot.api.update_note_ids(ids)

        await asyncio.gather(
            send_genesis_message(),
            send_recipient_genesis_message(),
            send_persistent_notes(),
        )
        self.bot.dispatch("thread_ready", self, creator, category, initial_message)

    def _format_info_embed(
        self,
        user: Union[Member, User],
        log_url: Optional[str],
        log_count: Optional[int],
        color: Union[Color, int],
    ) -> Embed:
        """
        Get information about a member of a server supports users from the guild or not.
        """
        member = self.bot.guild.get_member(user.id)
        time = discord_utils.utcnow()

        # key = log_url.split('/')[-1]

        role_names = ""
        if member is not None:
            sep_server = self.bot.using_multiple_server_setup
            separator = ", " if sep_server else " "

            roles = []

            for role in reversed(sorted(member.roles, key=lambda r: r.position)):
                if role.is_default():
                    # @everyone
                    continue

                fmt = role.name if sep_server else role.mention
                roles.append(fmt)

                if len(separator.join(roles)) > 1024:
                    roles.append("...")
                    while len(separator.join(roles)) > 1024:
                        roles.pop(-2)
                    break

            role_names = separator.join(roles)

        created = str((time - user.created_at).days)
        embed = Embed(
            color=color,
            description=f"{user.mention} was created {days(created)}",
            timestamp=time,
        )

        if user.dm_channel:
            footer = f"User ID: {user.id} • DM ID: {user.dm_channel.id}"
        else:
            footer = f"User ID: {user.id}"

        embed.set_author(name=str(user), icon_url=user.display_avatar.url, url=log_url)
        # embed.set_thumbnail(url=avi)

        if member is not None:
            joined = str((time - member.joined_at).days)
            # embed.add_field(name='Joined', value=joined + days(joined))
            embed.description += f", joined {days(joined)}"

            if member.nick:
                embed.add_field(name="Nickname", value=member.nick, inline=True)
            if role_names:
                embed.add_field(name="Roles", value=role_names, inline=True)
            embed.set_footer(text=footer)
        else:
            embed.set_footer(text=f"{footer} • (not in main server)")

        if log_count is not None:
            # embed.add_field(name="Past logs", value=f"{log_count}")
            thread = "thread" if log_count == 1 else "threads"
            embed.description += f" with **{log_count or 'no'}** past {thread}."
        else:
            embed.description += "."

        mutual_guilds = [g for g in self.bot.guilds if user in g.members]
        if member is None or len(mutual_guilds) > 1:
            embed.add_field(
                name="Mutual Server(s)", value=", ".join(g.name for g in mutual_guilds)
            )

        return embed

    async def _close_after(
        self,
        after: int,
        closer: Union[Member, User, ClientUser],
        silent: bool,
        delete_channel: bool,
        close_message: str,
    ) -> asyncio.Task:
        await asyncio.sleep(after)
        return self.bot.loop.create_task(
            self._close(closer, silent, delete_channel, close_message, True)
        )

    async def close(
        self,
        *,
        closer: Union[Member, User, ClientUser],
        after: int = 0,
        silent: bool = False,
        delete_channel: bool = True,
        close_message: str = None,
        auto_close: bool = False,
    ) -> None:
        """
        Close a thread now or after a set time in seconds.
        """

        # restarts the after timer
        await self.cancel_closure(auto_close)

        if after > 0:
            # TODO: Add somewhere to clean up broken closures
            #  (when channel is already deleted)
            now = discord_utils.utcnow()
            items = {
                # 'initiation_time': now.isoformat(),
                "time": (now + timedelta(seconds=after)).isoformat(),
                "closer_id": closer.id,
                "silent": silent,
                "delete_channel": delete_channel,
                "message": close_message,
                "auto_close": auto_close,
            }
            self.bot.config["closures"][str(self.id)] = items
            await self.bot.config.update()

            task = asyncio.create_task(
                self._close_after(after, closer, silent, delete_channel, close_message)
            )

            if auto_close:
                self.auto_close_task = task
            else:
                self.close_task = task
        else:
            await self._close(closer, silent, delete_channel, close_message)

    async def _close(
        self,
        closer: Union[Member, User, ClientUser],
        silent: bool = False,
        delete_channel: bool = True,
        close_message: str = None,
        scheduled: bool = False,
    ) -> None:
        try:
            self.manager.cache.pop(self.id)
        except KeyError as e:
            logger.error("Thread already closed: %s.", e)
            return

        await self.cancel_closure(all=True)

        # Cancel auto closing the thread if closed by any means.

        self.bot.config["subscriptions"].pop(str(self.id), None)
        self.bot.config["notification_squad"].pop(str(self.id), None)

        # Logging
        if self.channel:
            log_data = await self.bot.api.post_log(
                self.channel.id,
                {
                    "open": False,
                    "closed_at": str(discord_utils.utcnow()),
                    "nsfw": self.channel.nsfw,
                    "close_message": close_message if not silent else None,
                    "closer": {
                        "id": str(closer.id),
                        "name": closer.name,
                        "discriminator": closer.discriminator,
                        "avatar_url": str(closer.display_avatar.url),
                        "mod": True,
                    },
                },
            )
        else:
            log_data = None

        if log_data:
            prefix = self.bot.config["log_url_prefix"].strip("/")
            if prefix == "NONE":
                prefix = ""
            log_url = f"{self.bot.config['log_url'].strip('/')}{'/' + prefix if prefix else ''}/{log_data['key']}"

            if log_data["messages"]:
                content = str(log_data["messages"][0]["content"])
                sneak_peak = content.replace("\n", "")
            else:
                sneak_peak = "No content"

            desc = f"[`{log_data['key']}`]({log_url}): "
            desc += truncate(sneak_peak, max=75 - 13)
        else:
            desc = "Could not resolve log url."
            log_url = None

        embed = Embed(description=desc, color=self.bot.error_color)

        if self.recipient:
            user = f"{self.recipient} (`{self.id}`)"
        else:
            user = f"`{self.id}`"

        if self.id == closer.id:
            _closer = "the Recipient"
        else:
            _closer = f"{closer} ({closer.id})"

        embed.title = user

        event = "Thread Closed as Scheduled" if scheduled else "Thread Closed"
        # embed.set_author(name=f"Event: {event}", url=log_url)
        embed.set_footer(
            text=f"{event} by {_closer}", icon_url=closer.display_avatar.url
        )
        embed.timestamp = discord_utils.utcnow()

        tasks = [self.bot.config.update()]

        if self.bot.log_channel is not None and self.channel:
            tasks.append(self.bot.log_channel.send(embed=embed))

        # Thread closed message

        embed = Embed(
            title=self.bot.config["thread_close_title"],
            color=self.bot.error_color,
            timestamp=discord_utils.utcnow(),
        )

        if not close_message:
            if self.id == closer.id:
                close_message = self.bot.config["thread_self_close_response"]
            else:
                close_message = self.bot.config["thread_close_response"]

        close_message = self.bot.formatter.format(
            close_message,
            closer=closer,
            loglink=log_url,
            logkey=log_data["key"] if log_data else None,
        )

        embed.description = close_message
        footer = self.bot.config["thread_close_footer"]
        embed.set_footer(text=footer, icon_url=self.bot.guild.icon.url)

        if not silent and self.recipient:

            async def send_to_recipient():
                try:
                    await self.recipient.send(embed=embed)
                except DiscordForbidden:
                    logger.error(
                        "Thread close message could not be delivered since "
                        "the recipient shares no servers with the bot."
                    )

            tasks.append(send_to_recipient())

        if delete_channel:
            tasks.append(self.channel.delete())

        await asyncio.gather(*tasks)
        self.bot.dispatch(
            "thread_close",
            self,
            closer,
            silent,
            delete_channel,
            close_message,
            scheduled,
        )

    async def cancel_closure(self, auto_close: bool = False, all: bool = False) -> None:
        if self.close_task is not None and (not auto_close or all):
            self.close_task.cancel()
            self.close_task = None
        if self.auto_close_task is not None and (auto_close or all):
            self.auto_close_task.cancel()
            self.auto_close_task = None

        to_update = self.bot.config["closures"].pop(str(self.id), None)
        if to_update is not None:
            await self.bot.config.update()

    async def _restart_close_timer(self) -> None:
        """
        This will create or restart a timer to automatically close this thread.
        """
        timeout = self.bot.config.get("thread_auto_close")

        # Exit if timeout was not set
        if timeout == isodate.Duration():
            return

        # Set timeout seconds
        seconds = timeout.total_seconds()
        # seconds = 20  # Uncomment to debug with just 20 seconds
        reset_time = discord_utils.utcnow() + timedelta(seconds=seconds)
        human_time = human_timedelta(dt=reset_time)  # doesn't matter tz aware or naive

        if self.bot.config.get("thread_auto_close_silently"):
            return await self.close(
                closer=self.bot.user, silent=True, after=int(seconds), auto_close=True
            )

        # Grab message
        close_message = self.bot.formatter.format(
            self.bot.config["thread_auto_close_response"], timeout=human_time
        )

        time_marker_regex = "%t"
        if len(re.findall(time_marker_regex, close_message)) == 1:
            close_message = re.sub(time_marker_regex, str(human_time), close_message)
        elif len(re.findall(time_marker_regex, close_message)) > 1:
            logger.warning(
                "The thread_auto_close_response should only contain one '%s' to specify time.",
                time_marker_regex,
            )

        await self.close(
            closer=self.bot.user,
            after=int(seconds),
            close_message=close_message,
            auto_close=True,
        )

    async def find_message_payload(
        self,
        message: Union[Message, int] = None,
        either_direction: bool = False,
        note: bool = True,
    ) -> ThreadMessage:
        """
        Find and contsruct the message payload.

        This method is used to construct the :class:`ThreadMessagePayload` instance with the raw data
        fetched from the database, and find the linked message if any.

        Parameters
        -----------
        message : Message or int or None
            The message object or the message ID. This parameter is optional. If no value (i.e. None)
            is passed in this parameter, the message object will be retrieved from `.channel.history()`.
        either_direction : bool
            True if this method is called from `handle_reaction_events`.
        note : bool
            True if the first return value is note or persistent should be returned. Defaults to True.

        Returns
        -------
        ThreadMessage
            The message payload object.
        """

        if isinstance(message, Message):
            if str(message.id) in self._ignored_messages:
                raise IgnoredMessage("Ignored message.")
            if not (message.author == self.bot.user and message.embeds):
                self._ignored_messages.add(str(message.id))
                raise MalformedThreadMessage("Malformed thread message.")
            message1 = message

        elif isinstance(message, int):
            try:
                message1 = await self.channel.fetch_message(message)
            except DiscordNotFound:
                raise ThreadMessageNotFound("Thread message not found.")

            if str(message1.id) in self._ignored_messages:
                raise IgnoredMessage("Ignored message.")
            if not (message1.author == self.bot.user and message1.embeds):
                self._ignored_messages.add(str(message1.id))
                raise MalformedThreadMessage("Malformed thread message.")
        else:
            message1 = None  # this variable will be reassigned

        if message1 is None:
            # still None, this is usually when using `delete` or `edit` command without providing ID
            # in this case, we will fetch all the thread messages logged in the database and
            # find a match from channel history with the predicate provided.
            msg_log_payload = await self.bot.api.get_message_log_payload(self.channel)
            if not msg_log_payload:
                raise LinkMessageError("Message logs not found.")

            payload = await msg_log_payload.find_from_channel_history(
                self.channel,
                lambda payload: payload.from_mod
                and payload.type
                not in (ThreadMessageType.SYSTEM, ThreadMessageType.INTERNAL),
            )
            if not payload:
                raise ThreadMessageNotFound("Thread message not found.")

            message1 = payload.message
            if str(message1.id) in self._ignored_messages:
                # most likely not going to happen, put it here anyway
                raise IgnoredMessage("Ignored message.")
        else:
            payload = await self.bot.api.get_message_payload(message1, self.channel)
            if payload is None:
                if str(message1.id) not in self._ignored_messages:
                    self._ignored_messages.add(str(message1.id))
                    logger.error(
                        "Not a thread message. This message will be ignored from now."
                    )
                    raise IgnoredMessage("Ignored message.")
                # this also most likely not going to happen, put it here anyway
                raise ThreadMessageNotFound("Thread message not found.")

        # all of the above are just to instantiate the `ThreadMessagePayload` object and assign
        # the `message` attribue for it.
        # beyond this is for linking messages
        linked_ids = payload.linked_ids

        if linked_ids:
            if not payload.from_mod and not either_direction:
                raise ThreadMessageNotFound("Thread message not found.")

        # this will set the `linked_message` attribute
        await payload.find_linked_message(self.recipient)

        if linked_ids and not payload.linked_message:
            raise DMMessageNotFound("DM message not found.")

        if any((payload.is_note(), payload.is_persistent_note())) and not note:
            raise DMMessageNotFound("DM message not found.")

        return payload

    async def edit_message(
        self, author: Member, message_id: Optional[int], content: str
    ) -> None:
        """
        Edits the thread message and finds the linked message to edit if any.

        Parameters
        -----------
        author : Member
            The member object.
        message_id : int
            The message ID.
        content : str
            The new content for the message.
        """
        try:
            payload = await self.find_message_payload(message_id)
        except LinkMessageError:
            logger.warning("Failed to edit message.", exc_info=True)
            raise

        message1, message2 = payload.message, payload.linked_message

        embed1 = message1.embeds[0]
        embed1.description = content

        tasks = [
            self.bot.api.edit_message(message1.id, content),
            message1.edit(embed=embed1),
        ]
        if message2 is not None:
            if message2.embeds:
                embed2 = message2.embeds[0]
                embed2.description = content
                tasks += [message2.edit(embed=embed2)]
            else:
                mod_tag = self.bot.config["mod_tag"]
                if mod_tag is None:
                    role = get_top_role(author)
                    mod_tag = str(role)

                if payload.type == ThreadMessageType.ANONYMOUS:
                    anon_name = self.bot.config["anon_username"]
                    if anon_name is None:
                        anon_name = mod_tag
                    plain_message = f"**({self.bot.config['anon_tag']}) {anon_name}:** "
                else:
                    plain_message = f"**({mod_tag}) {str(author)}:** "

                # actual content
                plain_message += f"{content}"
                tasks += [message2.edit(content=plain_message)]

        if payload.is_persistent_note():
            tasks += [self.bot.api.edit_note(message1.id, content)]

        await asyncio.gather(*tasks)

    async def delete_message(
        self, message: Union[Message, int] = None, note: bool = True
    ) -> None:
        """
        Deletes the thread message and finds the linked message to delete if any.

        Parameters
        -----------
        message : Message or int or None
            The ID of the message if this is called from `delete` command, or the `Message` object itself
            if this is called from `on_delete_message` event. Defaults to None.
        note : bool
            Whether note or persistent note should be returned by `find_linked_messages`
            in this method. Defaults to True. Set to False if this method is called from
            `on_message_delete` event.
        """
        if isinstance(message, Message) and str(message.id) in self._deleted_messages:
            # special case, message was already deleted from `delete` command
            self._deleted_messages.remove(str(message.id))
            return

        try:
            payload = await self.find_message_payload(message=message, note=note)
        except LinkMessageError:
            if (
                isinstance(message, Message)
                and str(message.id) in self._ignored_messages
            ):
                self._ignored_messages.remove(str(message.id))
            raise

        message1, message2 = payload.message, payload.linked_message

        tasks = []
        if not isinstance(message, Message):
            # temporarily store the id in cache since this method will be called again
            # from `on_message_delete` in 'bot.py'
            self._deleted_messages.add(str(message1.id))

            tasks += [message1.delete()]
            if payload.is_persistent_note():
                tasks += [self.bot.api.delete_note(message1.id)]
        if message2 is not None:
            tasks += [message2.delete()]

        if tasks:
            await asyncio.gather(*tasks)

    async def find_dm_message_payload(
        self, message: Message, either_direction: bool = False, *, deleted: bool = False
    ) -> ThreadMessage:
        """
        Find and construct a message payload from a message in DM channel.

        This method is used to construct the :class:`ThreadMessagePayload` instance with the raw data
        fetched from the database, and find the linked message. If not linked message is found,
        :exc:`ThreadMessageNotFound` will be raised.

        Parameters
        -----------
        message : Message
            The message object.
        either_direction : bool
            True if this method is run from `handle_reaction_events`.
        deleted : bool
            True if the message object provided is deleted (i.e. called from `on_message_delete`).
            Defaults to `False`.

        Returns
        -------
        ThreadMessage
            The message payload object.
        """
        if not self.channel or (
            not either_direction and message.author == self.bot.user
        ):
            raise ThreadMessageNotFound("Thread channel message not found.")

        if str(message.id) in self._ignored_messages:
            if deleted:
                self._ignored_messages.remove(str(message.id))
            raise IgnoredMessage("Ignored message.")

        payload = await self.bot.api.get_message_payload(message, self.channel)
        if not payload:
            raise LinkMessageError("Message logs not found.")

        if payload.linked_ids:
            await payload.find_linked_message(self.channel)
            if payload.linked_message:
                return payload
        else:
            if str(message.id) not in self._ignored_messages:
                self._ignored_messages.add(str(message.id))
                logger.error(
                    "Not a thread message. This message will be ignored from now."
                )
                raise IgnoredMessage("Ignored message.")

        raise ThreadMessageNotFound("Thread channel message not found.")

    async def edit_dm_message(self, message: Message, content: str) -> None:
        """
        Edits DM message and finds the linked message to edit if any.

        Parameters
        -----------
        message : Message
            The message object.
        content : str
            The new content for the message.
        """

        try:
            payload = await self.find_dm_message_payload(message)
        except LinkMessageError:
            logger.warning("Failed to edit message.", exc_info=True)
            raise
        linked_msg = payload.linked_message
        embed = linked_msg.embeds[0]
        embed.add_field(name="**Edited, former message:**", value=embed.description)
        embed.description = content
        await asyncio.gather(
            self.bot.api.edit_message(message.id, content), linked_msg.edit(embed=embed)
        )

    async def note(
        self,
        message: Union[Message, PartialPersistentNote],
        persistent: bool = False,
        thread_creation: bool = False,
    ) -> Message:
        """
        Sends note or persistent note to thread channel.
        """

        if not message.content and not message.attachments:
            raise ThreadError("Missing required argument, `message`.")

        msg = await self.send(
            message,
            self.channel,
            note=True,
            persistent_note=persistent,
            thread_creation=thread_creation,
        )

        self.bot.loop.create_task(
            self.bot.api.append_log(
                message,
                message_id=str(msg.id),
                channel_id=str(self.channel.id),
                type_=ThreadMessageType.SYSTEM,
            )
        )

        return msg

    async def reply(
        self, message: Message, anonymous: bool = False, plain: bool = False
    ) -> ReplyPayload:
        """Replies a thread."""

        if not message.content and not message.attachments:
            raise ThreadError("Missing required argument, `message`.")
        if not any(g.get_member(self.id) for g in self.bot.guilds):
            await message.channel.send(
                embed=Embed(
                    color=self.bot.error_color,
                    description="Your message could not be delivered since "
                    "the recipient shares no servers with the bot.",
                )
            )
            return None, None

        tasks = []
        user_msg, msg = None, None
        from_mod = True
        try:
            user_msg = await self.send(
                message,
                destination=self.recipient,
                from_mod=from_mod,
                anonymous=anonymous,
                plain=plain,
            )
        except Exception as e:
            logger.error("Message delivery failed:", exc_info=True)
            if isinstance(e, DiscordForbidden):
                description = (
                    "Your message could not be delivered as "
                    "the recipient is only accepting direct "
                    "messages from friends, or the bot was "
                    "blocked by the recipient."
                )
            else:
                description = (
                    "Your message could not be delivered due "
                    "to an unknown error. Check `{}debug` for "
                    "more information.".format(self.bot.prefix)
                )
            tasks.append(
                message.channel.send(
                    embed=Embed(
                        color=self.bot.error_color,
                        description=description,
                    )
                )
            )
        else:
            # Send the same thing in the thread channel.
            msg = await self.send(
                message,
                destination=self.channel,
                from_mod=from_mod,
                anonymous=anonymous,
                plain=plain,
            )

            tasks.append(
                self.bot.api.append_log(
                    message,
                    message_id=str(msg.id),
                    channel_id=str(self.channel.id),
                    type_=ThreadMessageType.ANONYMOUS
                    if anonymous
                    else ThreadMessageType.NORMAL,
                    linked_ids=[msg.id, user_msg.id],
                )
            )

            # Cancel closing if a thread message is sent.
            if self.close_task is not None:
                await self.cancel_closure()
                tasks.append(
                    self.channel.send(
                        embed=Embed(
                            color=self.bot.error_color,
                            description="Scheduled close has been cancelled.",
                        )
                    )
                )

        await asyncio.gather(*tasks)
        self.bot.dispatch(
            "thread_reply",
            self,
            from_mod,  # from_mod
            message,
            anonymous,
            plain,
        )
        return user_msg, msg  # sent_to_user, sent_to_thread_channel

    async def send(
        self,
        message: [Message, PartialPersistentNote],
        destination: Union[TextChannel, DMChannel, User, Member] = None,
        from_mod: bool = False,
        note: bool = False,
        anonymous: bool = False,
        plain: bool = False,
        persistent_note: bool = False,
        thread_creation: bool = False,
    ) -> Message:
        """
        Sends the replied message to thread and DM channels.
        Note or Persistent Note also will be sent from here.
        """

        self.bot.loop.create_task(
            self._restart_close_timer()
        )  # Start or restart thread auto close

        if self.close_task is not None:
            # cancel closing if a thread message is sent.
            self.bot.loop.create_task(self.cancel_closure())
            self.bot.loop.create_task(
                self.channel.send(
                    embed=Embed(
                        color=self.bot.error_color,
                        description="Scheduled close has been cancelled.",
                    )
                )
            )

        if not self.ready:
            await self.wait_until_ready()

        destination = destination or self.channel
        author = message.author
        embed = Embed(description=message.content, timestamp=message.created_at)

        if not note:
            if anonymous and from_mod and not isinstance(destination, TextChannel):
                # Anonymously sending to the user.
                mod_tag = self.bot.config["mod_tag"]
                if mod_tag is None:
                    role = get_top_role(author)
                    mod_tag = str(role)
                name = self.bot.config["anon_username"]
                if name is None:
                    name = mod_tag
                avatar_url = self.bot.config["anon_avatar_url"]
                if avatar_url is None:
                    avatar_url = self.bot.guild.icon.url
                embed.set_author(
                    name=name,
                    icon_url=avatar_url,
                    url=f"https://discordapp.com/channels/{self.bot.guild.id}#{message.id}",
                )
            else:
                # Normal message, whether to thread or to user.
                name = str(author)
                avatar_url = author.display_avatar.url
                embed.set_author(
                    name=name,
                    icon_url=avatar_url,
                    url=f"https://discordapp.com/users/{author.id}#{message.id}",
                )
        else:
            # Special note messages
            system_avatar_url = (
                "https://discordapp.com/assets/f78426a064bc9dd24847519259bc42af.png"
            )
            embed.set_author(
                name=f'{"Persistent " if persistent_note else ""}Note ({author.name})',
                icon_url=system_avatar_url,
                url=f"https://discordapp.com/users/{author.id}#{message.id}",
            )

        ext = [(a.url, a.filename, False) for a in message.attachments]
        images = []
        attachments = []
        for attachment in ext:
            if is_image_url(attachment[0]):
                images.append(attachment)
            else:
                attachments.append(attachment)

        image_urls = re.findall(
            r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|(%[0-9a-fA-F][0-9a-fA-F]))+",
            message.content,
        )

        image_urls = [
            (parse_image_url(url, convert_size=False), None, False)
            for url in image_urls
            if is_image_url(url, convert_size=False)
        ]
        images.extend(image_urls)
        images.extend(
            (
                i.image.url if isinstance(i.image, Asset) else None,
                f"{i.name} Sticker",
                True,
            )
            for i in message.stickers
        )

        additional_images = []
        if images:
            embedded_image = False
            prioritize_uploads = any(i[1] is not None for i in images)
            additional_count = 1

            for url, filename, is_sticker in images:
                if (
                    not prioritize_uploads
                    or ((url is None or is_image_url(url)) and filename)
                ) and not embedded_image:
                    if url is not None:
                        embed.set_image(url=url)
                    if filename:
                        if is_sticker:
                            if url is None:
                                description = "Unable to retrieve sticker image."
                            else:
                                description = "\u200b"
                            embed.add_field(name=filename, value=description)
                        else:
                            embed.add_field(name="Image", value=f"[{filename}]({url})")
                    embedded_image = True
                else:
                    if note:
                        color = self.bot.main_color
                    elif from_mod:
                        color = self.bot.mod_color
                    else:
                        color = self.bot.recipient_color

                    img_embed = Embed(color=color)

                    if url is not None:
                        img_embed.set_image(url=url)
                        img_embed.url = url
                    if filename is not None:
                        img_embed.title = filename
                    img_embed.set_footer(
                        text=f"Additional Image Upload ({additional_count})"
                    )
                    img_embed.timestamp = message.created_at
                    additional_images.append(destination.send(embed=img_embed))
                    additional_count += 1

        if attachments:
            file_upload_count = 1
            for url, filename, _ in attachments:
                embed.add_field(
                    name=f"File upload ({file_upload_count})",
                    value=f"[{filename}]({url})",
                )
                file_upload_count += 1

        if from_mod:
            embed.colour = self.bot.mod_color
            # Anonymous reply sent in thread channel
            if anonymous and isinstance(destination, TextChannel):
                embed.set_footer(text="Anonymous Reply")
            # Normal messages
            elif not anonymous:
                mod_tag = self.bot.config["mod_tag"]
                if mod_tag is None:
                    role = get_top_role(author)
                    mod_tag = str(role)
                embed.set_footer(text=mod_tag)  # Normal messages
            else:
                embed.set_footer(text=self.bot.config["anon_tag"])
        elif note:
            embed.colour = self.bot.main_color
        else:
            embed.set_footer(text=f"Message ID: {message.id}")
            embed.colour = self.bot.recipient_color

        if (from_mod or note) and not thread_creation and destination == self.channel:
            try:
                await message.delete()
            except Exception as e:
                logger.warning("Cannot delete message: %s.", e)

        if (
            from_mod
            and self.bot.config["dm_disabled"] == DMDisabled.ALL_THREADS
            and destination != self.channel
        ):
            logger.info(
                "Sending a message to %s when DM disabled is set.", self.recipient
            )

        try:
            await destination.typing()
        except DiscordNotFound:
            logger.warning("Channel not found.")
            raise

        if not from_mod and not note:
            mentions = self.get_notifications()
        else:
            mentions = None

        if plain:
            if from_mod and not isinstance(destination, TextChannel):
                # Plain to user
                if embed.footer.text:
                    plain_message = f"**({embed.footer.text}) "
                else:
                    plain_message = "**"
                plain_message += f"{embed.author.name}:** {embed.description}"
                files = []
                for i in embed.fields:
                    if "Image" in i.name:
                        async with self.bot.session.get(
                            i.value[i.value.find("http") : -1]
                        ) as resp:
                            stream = io.BytesIO(await resp.read())
                            files.append(File(stream))

                msg = await destination.send(plain_message, files=files)
            else:
                # Plain to mods
                embed.set_footer(text="[PLAIN] " + embed.footer.text)
                msg = await destination.send(mentions, embed=embed)

        else:
            msg = await destination.send(mentions, embed=embed)

        if additional_images:
            self.ready = False
            await asyncio.gather(*additional_images)
            self.ready = True

        if not from_mod and not note:
            self.bot.loop.create_task(
                self.bot.api.append_log(
                    message,
                    channel_id=str(self.channel.id),
                    linked_ids=[msg.id, message.id],
                )
            )

        return msg

    def get_notifications(self) -> str:
        """
        Get user/role mentions that have subscribed for current thread.
        """
        key = str(self.id)

        mentions = []
        mentions.extend(self.bot.config["subscriptions"].get(key, []))

        if key in self.bot.config["notification_squad"]:
            mentions.extend(self.bot.config["notification_squad"][key])
            self.bot.config["notification_squad"].pop(key)
            self.bot.loop.create_task(self.bot.config.update())

        return " ".join(mentions)


class ModmailThreadManager:
    """
    Class that handles storing, finding and creating Modmail threads.
    """

    def __init__(self, bot: ModmailBot):
        """
        Parameters
        ----------
        bot : ModmailBot
            The Modmail bot.
        """
        self.bot: ModmailBot = bot
        self.cache: Dict[int, ModmailThread] = {}

    async def populate_cache(self) -> None:
        """
        Populates cache with threads. This should be executed on startup.
        """
        for channel in self.bot.modmail_guild.text_channels:
            await self._find_from_channel(channel=channel)

    def __len__(self) -> int:
        return len(self.cache)

    def __iter__(self) -> Iterator[ModmailThread]:
        return iter(self.cache.values())

    def __getitem__(self, item: int) -> ModmailThread:
        return self.cache[item]

    async def find(
        self,
        *,
        recipient: Union[Member, User] = None,
        channel: TextChannel = None,
        recipient_id: int = None,
    ) -> Optional[ModmailThread]:
        """
        Base method to find thread. Finds a thread from cache or from discord channel topics.
        """
        if recipient is None and channel is not None:
            modmail_thread = await self._find_from_channel(channel)
            if modmail_thread is None:
                # in case thread already in cache but the channel topic doesn't match for some reason,
                # or was edited in some way...
                user_id, modmail_thread = self._find_from_cache(channel)
                if modmail_thread is not None:
                    logger.debug("Found thread with tempered ID.")
                    topic = generate_topic_string(self.bot.user.id, user_id)
                    await modmail_thread.channel.edit(topic=topic)
            return modmail_thread

        if recipient:
            recipient_id = recipient.id

        modmail_thread = self.cache.get(recipient_id)
        if modmail_thread is not None:
            try:
                await modmail_thread.wait_until_ready()
            except asyncio.CancelledError:
                logger.warning("Thread for %s cancelled.", recipient)
                return modmail_thread
            else:
                if not modmail_thread.cancelled and (
                    not modmail_thread.channel
                    or not self.bot.get_channel(modmail_thread.channel.id)
                ):
                    logger.warning(
                        "Found existing thread for %s but the channel is invalid.",
                        recipient_id,
                    )
                    await modmail_thread.close(
                        closer=self.bot.user, silent=True, delete_channel=False
                    )
                    modmail_thread = None
        else:
            # generally, this is when user DM'ing the bot and no thread found in cache
            # double check, loop every channels and find the match user id from channel topic
            channel = discord_utils.find(
                lambda x: (recipient_id == match_user_id(x.topic, self.bot.user.id))
                if x.topic
                else False,
                self.bot.modmail_guild.text_channels,
            )
            if channel:
                modmail_thread = ModmailThread(self, recipient or recipient_id, channel)
                if modmail_thread.recipient:
                    # only save if data is valid
                    self.cache[recipient_id] = modmail_thread
                modmail_thread.ready = True
        return modmail_thread

    def _find_from_cache(
        self, channel: TextChannel
    ) -> Tuple[int, Optional[ModmailThread]]:
        """
        Finds Modmail thread in cache that matches with the provided channel.
        Returns a tuple of user ID and thread if found. Otherwise, -1 and None.
        """
        return next(
            (
                (user_id, modmail_thread)
                for user_id, modmail_thread in self.cache.items()
                if modmail_thread.channel == channel
            ),
            (-1, None),
        )

    async def _find_from_channel(self, channel: TextChannel) -> Optional[ModmailThread]:
        """
        Tries to find a Modmail thread specifically from channel topic.
        """
        topic = getattr(channel, "topic", None)
        if not topic:
            return None

        user_id = match_user_id(topic, self.bot.user.id)
        if user_id == -1:
            return None

        if user_id in self.cache:
            return self.cache[user_id]

        # channel topic is matched and user id is retrieved, but the thread is not cached.
        # this is originally when populating cache on startup
        try:
            recipient = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        except DiscordNotFound:
            recipient = None

        if recipient is None:
            self.cache[user_id] = modmail_thread = ModmailThread(self, user_id, channel)
        else:
            self.cache[user_id] = modmail_thread = ModmailThread(
                self, recipient, channel
            )
        modmail_thread.ready = True

        return modmail_thread

    async def create(
        self,
        recipient: Union[Member, User],
        *,
        message: Message = None,
        creator: Union[Member, User] = None,
        category: CategoryChannel = None,
        manual_trigger: bool = True,
    ) -> ModmailThread:
        """Creates a Modmail thread"""

        # checks for existing thread in cache
        modmail_thread = self.cache.get(recipient.id)
        if modmail_thread:
            try:
                await modmail_thread.wait_until_ready()
            except asyncio.CancelledError:
                logger.warning("Thread for %s cancelled, abort creating.", recipient)
                return modmail_thread
            else:
                if modmail_thread.channel and self.bot.get_channel(
                    modmail_thread.channel.id
                ):
                    logger.warning(
                        "Found an existing thread for %s, abort creating.", recipient
                    )
                    return modmail_thread
                logger.warning(
                    "Found an existing thread for %s, closing previous thread.",
                    recipient,
                )
                await modmail_thread.close(
                    closer=self.bot.user, silent=True, delete_channel=False
                )

        modmail_thread = ModmailThread(self, recipient)

        self.cache[recipient.id] = modmail_thread

        if (message or not manual_trigger) and self.bot.config[
            "confirm_thread_creation"
        ]:
            if not manual_trigger:
                destination = recipient
            else:
                destination = message.channel

            view = ConfirmView(bot=self.bot, user=recipient, timeout=20.0)
            view.message = await destination.send(
                embed=Embed(
                    title=self.bot.config["confirm_thread_creation_title"],
                    description=self.bot.config["confirm_thread_response"],
                    color=self.bot.main_color,
                ),
                view=view,
            )

            await view.wait()

            if view.value is None:
                modmail_thread.cancelled = True
                embed = None
            elif view.value:
                embed = Embed(title="Thread created", color=self.bot.mod_color)
            else:
                modmail_thread.cancelled = True
                embed = Embed(title="Cancelled", color=self.bot.error_color)

            if view.interaction and embed:
                self.bot.loop.create_task(
                    view.interaction.followup.send(embed=embed, ephemeral=True)
                )

            if modmail_thread.cancelled:
                del self.cache[recipient.id]
                return modmail_thread

        self.bot.loop.create_task(
            modmail_thread.setup(
                creator=creator, category=category, initial_message=message
            )
        )
        return modmail_thread

    async def handle_closures(self):
        """
        Handles Modmail thread closures when `on_ready` event.
        """
        closures = self.bot.config["closures"]
        logger.info("There are %d thread(s) pending to be closed.", len(closures))
        logger.line()

        for recipient_id, items in tuple(closures.items()):
            after = (
                datetime.fromisoformat(items["time"]).replace(tzinfo=timezone.utc)
                - discord_utils.utcnow()
            ).total_seconds()
            if after <= 0:
                logger.debug("Closing thread for recipient %s.", recipient_id)
                after = 0
            else:
                logger.debug(
                    "Thread for recipient %s will be closed after %s seconds.",
                    recipient_id,
                    after,
                )

            modmail_thread = await self.find(recipient_id=int(recipient_id))

            if not modmail_thread:
                # If the channel is deleted
                logger.debug("Failed to close thread for recipient %s.", recipient_id)
                self.bot.config["closures"].pop(recipient_id)
                await self.bot.config.update()
                continue

            await modmail_thread.close(
                closer=self.bot.get_user(items["closer_id"]),
                after=after,
                silent=items["silent"],
                delete_channel=items["delete_channel"],
                close_message=items["message"],
                auto_close=items.get("auto_close", False),
            )

    async def repair(
        self, channel: TextChannel, check_cache: bool = True, user_id: int = -1
    ) -> Optional[ModmailThread]:
        """
        Repairs a Modmail thread broken by Discord.

        Methods:
        - Search thread in cache that matches the channel.
        - Finds the genesis message in channel and retrieves the user ID using the `match_user_id` regex method.
        - Get log from database to retrieve user ID.

        Parameters
        ----------
        channel : TextChannel
            The channel object. This parameter is required.
        check_cache : bool
            Whether to search in cache for thread that matches the provided channel. Defaults to `True`.
        user_id : int
            The ID of the recipient. Defaults to `-1`.
        """
        reason = "Fix broken Modmail thread"
        # Search cache for channel
        # if this method is being called from command (e.g. repair), this should have already been
        # ran from method `find` when instantiating the Context, so this should be skipped by setting
        # the `check_cache` flag to False
        if check_cache:
            user_id, modmail_thread = self._find_from_cache(channel)
            if modmail_thread is not None:
                logger.debug("Found thread with tempered ID.")
                await channel.edit(
                    reason=reason,
                    topic=generate_topic_string(self.bot.user.id, user_id),
                )
                return modmail_thread

        if user_id == -1:
            # find genesis message to retrieve User ID
            async for message in channel.history(limit=10, oldest_first=True):
                if (
                    message.author == self.bot.user
                    and message.embeds
                    and message.embeds[0].color()
                    and message.embeds[0].color().value == self.bot.main_color
                    and message.embeds[0].footer.text
                ):
                    user_id = match_user_id(message.embeds[0].footer.text)
                    if user_id != -1:
                        break
            else:
                logger.warning("No genesis message found.")

                # User ID still not found, find in the database
                recipient = await self.bot.api.get_log(channel.id, field="recipient")
                if recipient:
                    user_id = int(recipient.get("id", -1))
                # TODO: Check if thread is open.
                #  - If closed, get the recipient ID and find if there's an open thread for the recipient

        # User ID successfully retrieved or provided when calling this method
        if user_id != -1:
            try:
                recipient = self.bot.get_user(user_id) or await self.bot.fetch_user(
                    user_id
                )
            except DiscordNotFound:
                recipient = None

            self.cache[user_id] = modmail_thread = ModmailThread(
                self, recipient or user_id, channel
            )
            modmail_thread.ready = True
            logger.info(
                "Setting current channel's topic to User ID, created new thread instance and stored in cache."
            )
            await channel.edit(
                reason=reason, topic=generate_topic_string(self.bot.user.id, user_id)
            )
            return modmail_thread

        return None

    async def validate_thread_channels(self, skip_repair: bool = True) -> None:
        """
        Validates Modmail thread channels from open threads.

        If channel cannot be found (None), the thread will be closed.

        Also checks whether the type of thread channel is a :class:`TextChannel`. Otherwise,
        error will be logged onto console (no fix will be attempted).

        If a thread is broken in some way (e.g. channel topic doesn't match), a warning will be logged
        onto console, and will be fixed if the `skip_repair` flag is set to `False`.

        By default, the `skip_repair` flag is set to `True`.

        Parameters
        -----------
        skip_repair : bool
            Whether repair should be attempted if a broken thread channel is found.
            Defaults to `True`.
        """
        for log in await self.bot.api.get_open_logs():
            channel = self.bot.get_channel(int(log["channel_id"]))
            if channel is None:
                logger.debug(
                    "Unable to resolve thread with channel %s.", log["channel_id"]
                )
                log_data = await self.bot.api.post_log(
                    log["channel_id"],
                    {
                        "open": False,
                        "closed_at": str(discord_utils.utcnow()),
                        "nsfw": None,
                        "close_message": "Channel has been deleted, no closer found.",
                        "closer": {
                            "id": str(self.bot.user.id),
                            "name": self.bot.user.name,
                            "discriminator": self.bot.user.discriminator,
                            "avatar_url": str(self.bot.user.display_avatar.url),
                            "mod": True,
                        },
                    },
                )
                if log_data:
                    logger.debug(
                        "Successfully closed thread with channel %s.", log["channel_id"]
                    )
                else:
                    logger.debug(
                        "Failed to close thread with channel %s, skipping.",
                        log["channel_id"],
                    )
                continue

            if not isinstance(channel, TextChannel):
                logger.error(
                    "Invalid type of thread channel %s. Expected type `TextChannel`, got `%s` instead.",
                    f"{channel}",
                    type(channel).__name__,
                )
                continue

            user_id = int(log["recipient"]["id"])
            if user_id not in self.cache:
                logger.warning("Found a broken thread channel %s.", channel.name)
                if skip_repair:
                    continue
                logger.info("Attempting to fix a broken thread %s.", channel.name)
                if not await self.repair(channel, check_cache=False, user_id=user_id):
                    logger.warning("Unable to repair thread channel %s.", channel.name)
