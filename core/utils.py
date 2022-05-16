from __future__ import annotations

import base64
import functools
import re
import string
from difflib import get_close_matches
from distutils.util import strtobool as _stb  # pylint: disable=import-error
from io import BytesIO
from itertools import takewhile, zip_longest
from typing import (
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
    Union,
    TYPE_CHECKING,
)
from urllib import parse

import discord
from discord.utils import escape_markdown

from core.ext import commands

if TYPE_CHECKING:
    from bot import ModmailBot
    from core.thread import ModmailThread
    from core.types_ext.raw_data import ThreadMessagePayload


__all__ = [
    "User",
    "bold",
    "cleanup_code",
    "code_block",
    "create_not_found_embed",
    "create_thread_channel",
    "days",
    "escape",
    "escape_code_block",
    "format_channel_name",
    "format_description",
    "format_preview",
    "generate_topic_string",
    "get_top_role",
    "human_join",
    "is_image_url",
    "match_user_id",
    "normalize_alias",
    "normalize_smartquotes",
    "parse_alias",
    "parse_image_url",
    "plural",
    "strtobool",
    "text_to_file",
    "trigger_typing",
    "truncate",
    "tryint",
]


def strtobool(val: Union[str, bool]) -> Union[int, bool]:
    if isinstance(val, bool):
        return val
    try:
        return _stb(str(val))
    except ValueError:
        val = val.lower()
        if val == "enable":
            return 1
        if val == "disable":
            return 0
        raise


class User(commands.MemberConverter):
    """
    A custom discord.py `Converter` that
    supports `Member`, `User`, and string ID's.
    """

    # noinspection PyCallByClass,PyTypeChecker
    async def convert(self, ctx: commands.Context, argument: str) -> discord.Member:
        try:
            return await commands.MemberConverter().convert(ctx, argument)
        except commands.BadArgument:
            pass
        try:
            return await commands.UserConverter().convert(ctx, argument)
        except commands.BadArgument:
            pass
        match = self._get_id_match(argument)
        if match is None:
            raise commands.BadArgument('User "{}" not found'.format(argument))
        return discord.Object(int(match.group(1)))


def truncate(text: str, max: int = 50) -> str:  # pylint: disable=redefined-builtin
    """
    Reduces the string to `max` length, by trimming the message into "...".

    Parameters
    ----------
    text : str
        The text to trim.
    max : int, optional
        The max length of the text.
        Defaults to 50.

    Returns
    -------
    str
        The truncated text.
    """
    text = text.strip()
    return text[: max - 3].strip() + "..." if len(text) > max else text


def format_preview(messages: List[ThreadMessagePayload]) -> str:
    """
    Used to format previews of log embeds.

    Parameters
    ----------
    messages : List[ThreadMessagePayload]
        A list of messages.

    Returns
    -------
    str
        A formatted string preview.
    """
    messages = messages[:3]
    out = ""
    for message in messages:
        if message.get("type") in {"note", "internal"}:
            continue
        author = message["author"]
        content = str(message["content"]).replace("\n", " ")
        name = author["name"] + "#" + str(author["discriminator"])
        prefix = "[M]" if author["mod"] else "[R]"
        out += truncate(f"`{prefix} {name}:` {content}", max=75) + "\n"

    return out or "No Messages"


def is_image_url(url: str, **kwargs) -> bool:
    """
    Check if the URL is pointing to an image.

    Parameters
    ----------
    url : str
        The URL to check.

    Returns
    -------
    bool
        Whether the URL is a valid image URL.
    """
    url = parse_image_url(url, **kwargs)
    if url:
        return True

    return False


def parse_image_url(url: str, *, convert_size: bool = True) -> str:
    """
    Convert the image URL into a sized Discord avatar.

    Parameters
    ----------
    url : str
        The URL to convert.
    convert_size : bool
        Convert the size of the image.

    Returns
    -------
    str
        The converted URL, or '' if the URL isn't in the proper format.
    """
    # gyazo support
    url = re.sub(
        r"(http[s]?://)(gyazo\.com(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|%[0-9a-fA-F][0-9a-fA-F])+)",
        r"\1i.\2.png",
        url,
    )

    types = [".png", ".jpg", ".gif", ".jpeg", ".webp"]
    url = parse.urlsplit(url)

    if any(url.path.lower().endswith(i) for i in types):
        if convert_size:
            return parse.urlunsplit((*url[:3], "size=128", url[-1]))
        else:
            return parse.urlunsplit(url)
    return ""


TOPIC_REGEX = re.compile(
    r"^\(Modmail thread\)\n"
    r"\bBot ID: (?P<bot_id>\d{17,21})\b\n"
    r"\bUser ID: (?P<user_id>\d{17,21})\b"
)
UID_REGEX = re.compile(r"\bUser ID:\s*(\d{17,21})\b", flags=re.IGNORECASE)


def generate_topic_string(bot_id: int, user_id: int) -> str:
    """
    Generates a formatted string for channel topic, containing Bot ID and User ID etc.

    Please note, this format will be used for regex matching when populating cache (on startup).
    If changes is made, please also check `utils.TOPIC_REGEX` to reflect the changes.

    Parameters
    -----------
    bot_id : int
        The bot ID.
    user_id : int
        The user ID.
    """
    return f"(Modmail thread)\nBot ID: {bot_id}\nUser ID: {user_id}"


def match_user_id(text: str, bot_id: int = None) -> int:
    """
    Matches a user ID in the format of "User ID: 12345".

    Parameters
    ----------
    text : str
        The text to search for user ID.
    bot_id : Optional[int]
        The bot ID. This is used to match the channel topic format using the topic regex
        matching before returning the user ID.
        Only pass this if the text string is from thread channel topic.

    Returns
    -------
    :class:`int`
        The user ID if found. Otherwise, -1.
    """
    if bot_id is None:
        match = UID_REGEX.search(text)
        if match is None:
            return -1
        user_id = int(match.group(1))
    else:
        # new: matching bot ID as well to make sure this method is unique
        match = TOPIC_REGEX.search(text)
        if match is None or int(match.group("bot_id")) != bot_id:
            return -1
        user_id = int(match.group("user_id"))
    return user_id


def create_not_found_embed(
    word: str, possibilities: Iterable[str], name: str, n: int = 2, cutoff: float = 0.6
) -> discord.Embed:
    """
    A not found Embed containing close match possibilities.
    """
    # Single reference of Color.red()
    embed = discord.Embed(
        color=discord.Color.red(),
        description=f"**{name.capitalize()} `{word}` cannot be found.**",
    )
    val = get_close_matches(word, possibilities, n=n, cutoff=cutoff)
    if val:
        embed.description += "\nHowever, perhaps you meant...\n" + "\n".join(val)
    return embed


def parse_alias(alias: str) -> List[str]:
    def encode_alias(m):
        return "\x1AU" + base64.b64encode(m.group(1).encode()).decode() + "\x1AU"

    def decode_alias(m):
        return base64.b64decode(m.group(1).encode()).decode()

    alias = re.sub(
        r"(?:(?<=^)\s*(?<!\\)\"\s*|(?<=&&)\s*(?<!\\)\"\s*)(.+?)"
        r"(?:\s*(?<!\\)\"\s*(?=&&)|\s*(?<!\\)\"\s*(?=$))",
        encode_alias,
        alias,
    ).strip()

    aliases = []
    if not alias:
        return aliases

    for a in re.split(r"\s*&&\s*", alias):
        a = re.sub("\x1AU(.+?)\x1AU", decode_alias, a)
        if a[0] == a[-1] == '"':
            a = a[1:-1]
        aliases.append(a)

    return aliases


def normalize_alias(alias: str, message: str) -> List[str]:
    aliases = parse_alias(alias)
    contents = parse_alias(message)

    final_aliases = []
    for a, content in zip_longest(aliases, contents):
        if a is None:
            break

        if content:
            final_aliases.append(f"{a} {content}")
        else:
            final_aliases.append(a)

    return final_aliases


def format_description(i: int, names: Iterable[Optional[str]]) -> str:
    return "\n".join(
        ": ".join((str(a + i * 15), b))
        for a, b in enumerate(takewhile(lambda x: x is not None, names), start=1)
    )


def get_top_role(member: discord.Member) -> discord.Role:
    """
    Returns the member's top hoisted role if any, otherwise member's top role.

    Parameters
    -----------
    member : discord.Member
        The member object.
    """
    roles = sorted(member.roles, key=lambda r: r.position, reverse=True)
    role = discord.utils.find(lambda r: r.hoist, roles)

    # if role is `None`, fallbacks to member's top role
    return role or roles[0]


if TYPE_CHECKING:
    ErrorRaised = Tuple[str, Tuple[discord.CategoryChannel, str]]
    Overwrites = Dict[Union[discord.Role, discord.Member], discord.PermissionOverwrite]


async def create_thread_channel(
    thread: ModmailThread,
    recipient: Union[discord.Member, discord.User],
    category: Optional[discord.CategoryChannel],
    overwrites: Overwrites,
    *,
    name: str = None,
    errors_raised: List[ErrorRaised] = None,
    max_retry: int = 5,
) -> discord.TextChannel:
    """
    A method to create a Modmail thread channel while handling possible errors that may occur.
    """
    bot = thread.bot
    name = name or format_channel_name(recipient, bot.modmail_guild)
    errors_raised = errors_raised or []

    try:
        channel = await bot.modmail_guild.create_text_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            reason="Creating a thread channel.",
            topic=generate_topic_string(bot.user.id, recipient.id),
        )
    except discord.HTTPException as e:
        error = (e.text, (category, name))
        if error in errors_raised or len(errors_raised) >= max_retry:
            # Just raise the error to prevent infinite recursion after retrying
            raise

        errors_raised.append(error)

        if "Maximum number of channels in category reached" in e.text:
            fallback = None
            fallback_id = bot.config["fallback_category_id"]
            if fallback_id:
                fallback = discord.utils.get(
                    category.guild.categories, id=int(fallback_id)
                )
                if fallback and (
                    len(fallback.channels) >= 50 or fallback.id == category.id
                ):
                    fallback = None

            if not fallback:
                fallback = await category.clone(name="Fallback Modmail")
                bot.config.set("fallback_category_id", str(fallback.id))
                await bot.config.update()

            return await create_thread_channel(
                thread,
                recipient,
                fallback,
                overwrites,
                errors_raised=errors_raised,
                max_retry=max_retry,
            )

        if "Contains words not allowed" in e.text:
            # try again but null-discrim (name could be banned)
            return await create_thread_channel(
                thread,
                recipient,
                category,
                overwrites,
                name=format_channel_name(recipient, bot.modmail_guild, force_null=True),
                errors_raised=errors_raised,
                max_retry=max_retry,
            )

        raise

    return channel


def format_channel_name(
    author: Union[discord.Member, discord.User],
    guild: discord.Guild,
    exclude_channel: discord.TextChannel = None,
    force_null: bool = False,
) -> str:
    """Sanitises a username for use with text channel names"""
    name = author.name.lower()
    if force_null:
        name = "null"

    name = new_name = (
        "📩┋"
        + (
            "".join(n for n in name if n not in string.punctuation and n.isprintable())
            or "null"
        )
        + f"-{author.discriminator}"
    )

    counter = 1
    existed = set(c.name for c in guild.text_channels if c != exclude_channel)
    while new_name in existed:
        new_name = f"{name}_{counter}"  # multiple channels with same name
        counter += 1

    return new_name


Coro = TypeVar("Coro")


def trigger_typing(func: Coro) -> Coro:
    """
    Triggers a *typing...* indicator to the destination.

    The indicator will go away after 10 seconds, or after a message is sent.
    """

    @functools.wraps(func)
    async def wrapper(self, ctx: commands.Context, *args, **kwargs):
        await ctx.typing()
        return await func(self, ctx, *args, **kwargs)

    return wrapper


T = TypeVar("T")


def tryint(x: T) -> Union[int, T]:
    """
    Converts the passed value to `int`. If the conversion fails, the passed value
    will be returned without changes.
    """
    try:
        return int(x)
    except (ValueError, TypeError):
        return x


# Chat formatting


def human_join(sequence: Sequence[str], delim: str = ", ", final: str = "or") -> str:
    """
    Get comma-separated list, with the last element joined with *or*.

    Parameters
    ----------
    sequence : Sequence[str]
        The items of the list to join together.
    delim : str
        The delimiter to join the sequence with. Defaults to ", ".
        This will be ignored if the length of `sequence` is or less then 2, otherwise "final" will be used instead.
    final : str
        The final delimiter to format the string with. Defaults to "or".

    Returns
    --------
    str
        The formatted string, e.g. "seq_one, seq_two and seq_three".
    """
    size = len(sequence)
    if size == 0:
        return ""

    if size == 1:
        return sequence[0]

    if size == 2:
        return f"{sequence[0]} {final} {sequence[1]}"

    return delim.join(sequence[:-1]) + f" {final} {sequence[-1]}"


def days(day: Union[int, str]) -> str:
    """
    Humanize the number of days.

    Parameters
    ----------
    day: Union[int, str]
        The number of days passed.

    Returns
    -------
    str
        A formatted string of the number of days passed.
    """
    day = int(day)
    if day == 0:
        return "**today**"
    return f"{day} day ago" if day == 1 else f"{day} days ago"


def cleanup_code(content: str) -> str:
    """
    Automatically removes code blocks from the code.

    Parameters
    ----------
    content : str
        The content to be cleaned.

    Returns
    -------
    str
        The cleaned content.
    """
    # remove ```py\n```
    if content.startswith("```") and content.endswith("```"):
        return "\n".join(content.split("\n")[1:-1])

    # remove `foo`
    return content.strip("` \n")


def escape_code_block(text: str) -> str:
    """
    Returns the text with code block (i.e ```) escaped.
    """
    return re.sub(r"```", "`\u200b``", text)


SMART_QUOTE_REPLACEMENT_DICT = {
    "\u2018": "'",  # Left single quote
    "\u2019": "'",  # Right single quote
    "\u201C": '"',  # Left double quote
    "\u201D": '"',  # Right double quote
}

SMART_QUOTE_REPLACE_RE = re.compile("|".join(SMART_QUOTE_REPLACEMENT_DICT.keys()))


def escape(text: str, *, mass_mentions: bool = False, formatting: bool = False) -> str:
    """
    Get text with all mass mentions or markdown escaped.

    Parameters
    ----------
    text : str
        The text to be escaped.
    mass_mentions : `bool`, optional
        Set to :code:`True` to escape mass mentions in the text.
    formatting : `bool`, optional
        Set to :code:`True` to escape any markdown formatting in the text.

    Returns
    -------
    str
        The escaped text.

    """
    if mass_mentions:
        text = text.replace("@everyone", "@\u200beveryone")
        text = text.replace("@here", "@\u200bhere")
    if formatting:
        text = escape_markdown(text)
    return text


def bold(text: str, escape_formatting: bool = True) -> str:
    """
    Get the given text in bold.

    Note: By default, this function will escape ``text`` prior to emboldening.

    Parameters
    ----------
    text : str
        The text to be marked up.
    escape_formatting : `bool`, optional
        Set to :code:`False` to not escape markdown formatting in the text.

    Returns
    -------
    str
        The marked up text.

    """
    text = escape(text, formatting=escape_formatting)
    return "**{}**".format(text)


def code_block(text: str, lang: str = "") -> str:
    """
    Get the given text in a code block.

    Parameters
    ----------
    text : str
        The text to be marked up.
    lang : `str`, optional
        The syntax highlighting language for the codeblock.

    Returns
    -------
    str
        The marked up text.

    """
    ret = "```{}\n{}\n```".format(lang, text)
    return ret


def normalize_smartquotes(to_normalize: str) -> str:
    """
    Get a string with smart quotes replaced with normal ones

    Parameters
    ----------
    to_normalize : str
        The string to normalize.

    Returns
    -------
    str
        The normalized string.
    """

    def replacement_for(obj):
        return SMART_QUOTE_REPLACEMENT_DICT.get(obj.group(0), "")

    return SMART_QUOTE_REPLACE_RE.sub(replacement_for, to_normalize)


def text_to_file(
    text: str,
    filename: str = "file.txt",
    *,
    spoiler: bool = False,
    encoding: str = "utf-8",
):
    """
    Prepares text to be sent as a file on Discord, without character limit.

    This writes text into a bytes object that can be used for the ``file`` or ``files`` parameters
    of :meth:`discord.abc.Messageable.send`.

    Parameters
    ----------
    text: str
        The text to put in your file.
    filename: str
        The name of the file sent. Defaults to ``file.txt``.
    spoiler: bool
        Whether the attachment is a spoiler. Defaults to ``False``.
    encoding: str
        Encoding style. Defaults to ``utf-8``.

    Returns
    -------
    discord.File
        The file containing your text.

    """
    file = BytesIO(text.encode(encoding))
    return discord.File(file, filename, spoiler=spoiler)


# noinspection PyPep8Naming
class plural:
    """
    Formats a string to singular or plural based on the length objects it refers to.

    Examples
    --------
    - 'plural(len(data)):member'
    - 'plural(len(data)):entry|entries'
    """

    def __init__(self, value):
        self.value = value

    def __format__(self, format_spec):
        v = self.value
        singular, sep, plural = format_spec.partition("|")
        plural = plural or f"{singular}s"
        if abs(v) != 1:
            return f"{v} {plural}"
        return f"{v} {singular}"
