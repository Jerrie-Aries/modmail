from __future__ import annotations

import _string
from copy import deepcopy
from string import Formatter
from types import SimpleNamespace
from typing import (
    Any,
    Callable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Union,
    TYPE_CHECKING,
)

import discord

from core.enums_ext import ThreadMessageType
from core.ext import commands

if TYPE_CHECKING:
    from core.types_ext.raw_data import (
        AttachmentPayload,
        UserPayload,
        ThreadMessagePayload,
        PersistentNoteAuthorPayload,
        PersistentNotePayload,
    )

MISSING = discord.utils.MISSING


class _Default:
    pass


Default = _Default()


class SafeFormatter(Formatter):
    def get_field(
        self, field_name: str, args: Sequence[Any], kwargs: Mapping[str, Any]
    ) -> Any:
        first, rest = _string.formatter_field_name_split(field_name)

        try:
            obj = self.get_value(first, args, kwargs)
        except (IndexError, KeyError):
            return "<Invalid>", first

        # loop through the rest of the field_name, doing
        #  getattr or getitem as needed
        # stops when reaches the depth of 2 or starts with _.
        try:
            for n, (is_attr, i) in enumerate(rest):
                if n >= 2:
                    break
                if is_attr:
                    if str(i).startswith("_"):
                        break
                    obj = getattr(obj, i)
                else:
                    obj = obj[i]
            else:
                return obj, first
        except (IndexError, KeyError):
            pass
        return "<Invalid>", first


class UnseenFormatter(Formatter):
    def get_value(
        self, key: Union[int, str], args: Sequence[Any], kwargs: Mapping[str, Any]
    ) -> Any:
        if isinstance(key, str):
            try:
                return kwargs[key]
            except KeyError:
                return "{" + key + "}"
        else:
            return super().get_value(key, args, kwargs)


class SimilarCategoryConverter(commands.CategoryChannelConverter):
    async def convert(self, ctx: commands.Context, argument: str):
        bot = ctx.bot
        guild = ctx.guild

        try:
            return await super().convert(ctx, argument)
        except commands.ChannelNotFound:

            def check(c):
                return isinstance(
                    c, discord.CategoryChannel
                ) and c.name.lower().startswith(argument.lower())

            if guild:
                result = discord.utils.find(check, guild.categories)
            else:
                result = discord.utils.find(check, bot.get_all_channels())

            if not isinstance(result, discord.CategoryChannel):
                raise commands.ChannelNotFound(argument)

        return result


class DummyMessage:
    """
    A class mimicking the original :class:discord.Message
    where all functions that require an actual message to exist
    is replaced with a dummy function.
    """

    def __init__(self, message):
        message.attachments = []
        self._message = message

    def __getattr__(self, name: str):
        return getattr(self._message, name)

    def __bool__(self):
        return bool(self._message)

    async def delete(self, *, delay=None):
        return

    async def edit(self, **fields):
        return

    async def add_reaction(self, emoji):
        return

    async def remove_reaction(self, emoji):
        return

    async def clear_reaction(self, emoji):
        return

    async def clear_reactions(self):
        return

    async def pin(self, *, reason=None):
        return

    async def unpin(self, *, reason=None):
        return

    async def publish(self):
        return

    async def ack(self):
        return


class ThreadMessage:
    """
    Message payload constructed from raw message log data fetched from database.

    Parameters
    -----------
    key : Optional[str]
        The uniuque log key.
    raw_data : ThreadMessagePayload
        The raw data to construct the payload.
    message : discord.Message
        The message object. This parameter is optional. If not provided, the value of `message` attribute
        for this class instance will be `MISSING` and should be reassigned.

    Attributes
    -----------
    key : str
        The unique log key.
    timestamp : str
        The timestamp when the message was sent.
    message_id : int
        The ID of the source message.
    linked_ids : List[int]
        The list of linked IDs if the message is a thread message. Otherwise, empty list.
    author : UserPayload
        The dict of author's data.
    from_mod : bool
        Whether the message was sent from mod (i.e. from thread channel).
    content : str
        The message content.
    type : str
        The type of the message.
    attachments : List[AttachmentPayload]
        The list of message attachments if any. Otherwise, empty list.
    """

    def __init__(
        self,
        key: Optional[str],
        raw_data: ThreadMessagePayload,
        message: discord.Message = MISSING,
    ):
        self.key: Optional[str] = key
        self.timestamp: str = raw_data["timestamp"]  # datestring
        self.message_id: int = int(raw_data["message_id"])
        self.linked_ids: List[int] = list(map(int, raw_data["linked_ids"]))
        self.author: UserPayload = raw_data["author"]
        self.from_mod: bool = self.author.get("mod", False)
        self.content: str = raw_data["content"]
        self.type: ThreadMessageType = ThreadMessageType.from_value(raw_data["type"])
        self.attachments: List[AttachmentPayload] = raw_data["attachments"]

        # placeholder
        self._message: discord.Message = message
        self._linked_msg: Optional[discord.Message] = None

    def __repr__(self) -> str:
        return f"<ThreadMessage message_id={self.message_id} linked_ids={self.linked_ids} from_mod={self.from_mod}>"

    @property
    def message(self) -> discord.Message:
        """
        Returns the message object of the payload. Depends on how this class is instantiated,
        the return value could be `MISSING`.
        """
        return self._message

    @message.setter
    def message(self, item: discord.Message) -> None:
        """
        Sets the message attribute for this class instance.

        Parameters
        -----------
        item : discord.Message
            The message object.
        """
        self._message = item

    @property
    def linked_message(self) -> Optional[discord.Message]:
        """
        Returns the linked message object of the payload. Depends on how this class is instantiated,
        the return value could be None.
        """
        return self._linked_msg

    @linked_message.setter
    def linked_message(self, item: discord.Message) -> None:
        """
        Sets the linked message attribute for this class instance.

        Parameters
        -----------
        item : discord.Message
            The message object.
        """
        self._linked_msg = item

    async def find_linked_message(self, channel: discord.abc.Messageable) -> None:
        """
        Finds the linked message. If the linked message is found, the `linked_message` attribute
        for this class instance will be set.

        This method doesn't return anything. Any operation (i.e. setting the `linked_message` attribute)
        is all done within inside this method.

        Parameters
        -----------
        channel : discord.abc.Messageable
            The messageable channel to search for linked message in its history. This could be
            the `discord.TextChannel`, `discord.User`, or `discord.Member` object.
        """
        if self.is_system() or not self.linked_ids:
            return
        async for msg in channel.history():
            if msg.id in self.linked_ids:
                self.linked_message = msg
                break
        else:
            # probably raise ValueError or something
            pass

    def is_system(self) -> bool:
        """Returns whether this payload message is `ThreadMessageType.SYSTEM`."""
        return self.type == ThreadMessageType.SYSTEM

    def is_internal(self) -> bool:
        """Returns whether this payload message is `ThreadMessageType.INTERNAL`."""
        return self.type == ThreadMessageType.INTERNAL

    def is_note(self) -> bool:
        """Returns whether this payload message is `Note`."""
        if self.message is MISSING:
            raise ValueError(
                "The `message` attribute for this class instance has not been set."
            )
        embeds = self.message.embeds
        return (
            self.is_system()
            and embeds
            and getattr(embeds[0].author, "name", "").startswith("Note")
        )

    def is_persistent_note(self) -> bool:
        """Returns whether this payload message is `Persistent Note`."""
        if self.message is MISSING:
            raise ValueError(
                "The `message` attribute for this class instance has not been set."
            )
        embeds = self.message.embeds
        return (
            self.is_system()
            and embeds
            and getattr(embeds[0].author, "name", "").startswith("Persistent Note")
        )


class ThreadMessageLog:
    """
    A class that populates a map of :class:`ThreadMessagePayload` from raw data fetched from database.

    This class has the `__iter__` dunder method which iterates over a copy of mapped object created
    from the raw data. This means you can iterate this class using `for` loop or using any other functions
    that iterate items.

    Please note, the `ThreadMessagePayload` mapped from this class wouldn't have a `message` attribute
    set (the value of it would be `None`), and should be manually set whenever you match the payload
    with :class:`discord.Message` object.

    Parameters
    -----------
    key : Optional[str]
        The uniuque log key.
    raw_data : List[ThreadMessagePayload]
        The list of raw data to construct the payloads.

    Attributes
    -----------
    key : str
        The unique log key.
    mapping : Iterator[ThreadMessage]
        An iterator of ThreadMessagePayload mapped from the raw data.
    """

    def __init__(self, key: Optional[str], raw_data: List[ThreadMessagePayload]):
        self.key: str = key
        self.mapping: Iterator[ThreadMessage] = map(self._create_payload, raw_data)

        # no longer need the raw_data list, so we just delete it to consume
        # less memory usage
        del raw_data

    def __repr__(self) -> str:
        return f'<ThreadMessageLog key="{self.key}" mapping={self.mapping}>'

    def __iter__(self) -> Iterator[ThreadMessage]:
        return iter(deepcopy(self.mapping))

    def _create_payload(self, raw_data: ThreadMessagePayload) -> ThreadMessage:
        """
        Creates a :class:`ThreadMessagePayload` instance from a single raw data.

        Please note, this is an internal method for mapping and shouldn't be used outside of
        this class. And also the `message` attribue for the instance created from this would be `None`
        and should be reassigned.

        Parameters
        ----------
        raw_data : ThreadMessagePayload
            The raw data (i.e. a dictionary).

        Returns
        -------
        ThreadMessage
            The ThreadMessagePayload object.
        """
        return ThreadMessage(self.key, raw_data)

    async def find_from_channel_history(
        self,
        channel: Union[discord.TextChannel, discord.DMChannel],
        predicate: Optional[Callable[[ThreadMessage], bool]] = None,
    ) -> Optional[ThreadMessage]:
        """
        Finds a thread message payload from channel history.

        Please note, the return value depends on the predicate provided. If no predicate
        provided, this will return any payload that matches the message in channel history
        (Note, Persistent Note, System will also be included).

        Parameters
        -----------
        channel : Union[discord.TextChannel, discord.DMChannel]
            The channel where the messages will be fetched from its history.
            Most of the times, this should be the thread channel.
        predicate : Optional[Callable[[ThreadMessagePayload], bool]]
            The checks that should be done before returning the value.
            The predicate should take one parameter which is the `payload`.

        Returns
        -------
        Optional[ThreadMessage]
            The MessagePayload instance or None.
        """
        async for message in channel.history():
            payload = self.get_payload(message)
            if not payload:
                continue
            if predicate:
                if predicate(payload):
                    payload.message = message
                    break
            else:
                # returns any message that matches the payload
                payload.message = message
                break
        else:
            payload = None
        return payload

    def get_payload(self, message: discord.Message) -> Optional[ThreadMessage]:
        """
        Get payload instance from mapping that matches with provided message object.

        Parameters
        -----------
        message: discord.Message
            The message object.

        Returns
        -------
        Optional[ThreadMessage]
            The ThreadMessagePayload instance if found. Otherwise, None.
        """
        return discord.utils.find(
            lambda payload: message.id in [payload.message_id] + payload.linked_ids,
            self,
        )


class NoteAuthor:
    """
    Payload object for persistent note's author.

    All the attributes here are the required ones for methods `Thread.send` and `APIClient.append_log`.
    """

    def __init__(self, data: PersistentNoteAuthorPayload):
        self.name = data["name"]
        self.id = int(data["id"])
        self.discriminator = data["discriminator"]
        self.display_avatar = SimpleNamespace(url=data["avatar_url"])


class PartialPersistentNote(discord.PartialMessage):
    """
    Represents a partial persistent note to aid with working messages on `Thread` creation when only
    a message and channel ID are present.
    This class is constructed from persistent note raw data fetched from the database.

    All the attributes here are the required ones for methods `Thread.send` and `APIClient.append_log`.

    Note that this class is trimmed down and has no rich attributes.
    """

    def __init__(
        self, id: int, channel: discord.TextChannel, data: PersistentNotePayload
    ):

        super().__init__(channel=channel, id=id)
        self.content: str = data["message"]

        self.attachments: List[discord.Attachment] = []
        self.stickers: List[discord.Sticker] = []
        self.embeds: List[discord.Embed] = []
        self.edited_timestamp: Optional[int] = None
        self.type: Optional[discord.MessageType] = None
        self.pinned: bool = False
        self.mention_everyone: bool = False
        self.tts: bool = False
        self.author: NoteAuthor = NoteAuthor(data["author"])
