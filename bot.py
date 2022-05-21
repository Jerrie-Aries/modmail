from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Dict,
    Iterable,
    List,
    Optional,
    Set,
    Tuple,
    Union,
    TYPE_CHECKING,
)

import discord
import isodate
from aiohttp import ClientSession
from discord.ext.commands.view import StringView
from emoji import UNICODE_EMOJI_ENGLISH
from pkg_resources import parse_version as pkg_parse_version

from core import checks
from core.clients import MongoDBClient
from core.config import ConfigManager
from core.enums_ext import DMDisabled, HostingMethod, PermissionLevel, ThreadMessageType
from core.errors import (
    DMMessageNotFound,
    IgnoredMessage,
    LinkMessageError,
    MalformedThreadMessage,
    ThreadMessageNotFound,
)
from core.ext import commands
from core.logging_ext import configure_logging, getLogger
from core.models import SafeFormatter
from core.thread import ModmailThreadManager
from core.timeutils import human_timedelta
from core.utils import human_join, normalize_alias, tryint
from core.views.contact import ContactView

if TYPE_CHECKING:
    # noinspection PyProtectedMember
    from pkg_resources._vendor.packaging.version import Version, LegacyVersion

    from core.clients import ApiClient

    EmojiInputType = Union[discord.Emoji, discord.PartialEmoji]
    ReactionInputType = Union[discord.Reaction, EmojiInputType, str]


__version__ = "1.0.0"
__authors__ = "kyb3r, fourjr, Taaku18"
__developer__ = "Jerrie"

logger = getLogger(__name__)

MISSING = discord.utils.MISSING

current_working_dir = Path.cwd().resolve()
temp_dir = current_working_dir / "temp"
if not temp_dir.exists():
    temp_dir.mkdir(parents=True, exist_ok=True)


cog_path = current_working_dir / "cogs"
base_cogs = []
for file in cog_path.iterdir():
    if not file.is_dir() and file.suffix == ".py":
        # `.stem` returns the file without the extension
        base_cogs.append(f"cogs.{file.stem}")


class ModmailBot(commands.Bot):
    """
    An instance of Modmail bot.
    """

    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix=None, intents=intents
        )  # implemented in `get_prefix`

        # Support for Repl using keep_alive
        self.keep_alive: keep_alive = MISSING
        if self.hosting_method == HostingMethod.REPL:
            try:
                import keep_alive
            except Exception as exc:
                logger.error(f"{type(exc).__name__}: {str(exc)}")
            else:
                keep_alive.keep_alive()
                self.keep_alive = keep_alive

        self.session: ClientSession = MISSING
        self._api: ApiClient = MISSING
        self.formatter: SafeFormatter = SafeFormatter()
        self.loaded_cogs: List[str] = [*base_cogs]
        self._connected: asyncio.Event = asyncio.Event()
        self.start_time: datetime = discord.utils.utcnow()
        self._started: bool = False

        self.config: ConfigManager = ConfigManager(self)
        self.config.populate_cache()

        self.thread_manager: ModmailThreadManager = ModmailThreadManager(self)

        self.log_file_path: Path[str] = temp_dir / f"{self.token.split('.')[0]}.log"
        self._configure_logging()

        self.contact_panel_view: ContactView = MISSING

        self.startup()

    def _configure_logging(self):
        """
        Configure logging. This should only be ran once when instantiating this bot (e.g. on startup).
        """
        level_text = self.config["log_level"].upper()
        logging_levels = {
            "CRITICAL": logging.CRITICAL,
            "ERROR": logging.ERROR,
            "WARNING": logging.WARNING,
            "INFO": logging.INFO,
            "DEBUG": logging.DEBUG,
        }
        logger.line()

        log_level = logging_levels.get(level_text)
        if log_level is None:
            log_level = self.config.remove("log_level")
            logger.warning("Invalid logging level set: %s.", level_text)
            logger.warning("Using default logging level: INFO.")
        else:
            logger.info("Logging level: %s", level_text)

        logger.info("Log file: %s", self.log_file_path)
        configure_logging(self.log_file_path, log_level)
        logger.debug("Successfully configured logging.")

    def startup(self):
        logger.line()
        logger.info("┌┬┐┌─┐┌┬┐┌┬┐┌─┐┬┬")
        logger.info("││││ │ │││││├─┤││")
        logger.info("┴ ┴└─┘─┴┘┴ ┴┴ ┴┴┴─┘")
        logger.info("v%s", __version__)
        logger.info("Authors: %s", __authors__)
        logger.info("Developer: %s", __developer__)
        logger.line()
        logger.info("discord.py: v%s", discord.__version__)
        logger.info("Python: v%s", self.python_version)
        logger.line()

    async def load_extensions(self):
        for cog in self.loaded_cogs:
            logger.debug("Loading %s.", cog)
            try:
                await self.load_extension(cog)
                logger.debug("Successfully loaded %s.", cog)
            except Exception:
                logger.exception("Failed to load %s.", cog)
        logger.line("debug")

    def _set_signal_handlers(self) -> None:
        """
        An internal method to set the signal handlers to terminate the loop.
        """
        keep_alive = self.keep_alive

        def stop_callback(*_args):
            logger.line()
            logger.info("Received signal to terminate the bot and the event loop.")

            if keep_alive:
                keep_alive.shutdown()

            raise SystemExit()

        for attr in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, attr, None)
            if sig is None:
                continue
            signal.signal(sig, stop_callback)

    def run(self, *args: Any, **kwargs: Any) -> None:
        """
        A blocking call that abstracts away the event loop for you.

        If you want more control over the event loop then this function
        should not be used.
        """

        async def runner():
            async with self:
                self._set_signal_handlers()
                if self.session is MISSING:
                    self.session = ClientSession(loop=self.loop)

                try:
                    await self.start(self.token, reconnect=True)
                except discord.PrivilegedIntentsRequired:
                    logger.critical(
                        "Privileged intents are not explicitly granted in the discord developers dashboard."
                    )
                except discord.LoginFailure:
                    logger.critical("Invalid token")
                except Exception:
                    logger.critical("Fatal exception", exc_info=True)
                finally:
                    if not self.is_closed():
                        await self.close()
                    if self.session:
                        await self.session.close()
                    logger.info("Closing the event loop.")
                    await asyncio.sleep(1.0)

        try:
            asyncio.run(runner())
        except (KeyboardInterrupt, SystemExit):
            logger.line()
            logger.warning(" - Shutting down bot - ")

    async def close(self) -> None:
        """
        Closes the connection to Discord.

        And also closes underlying connector of `aiohttp.ClientSession` and releases all
        acquired resources.

        Instead of calling this directly, it is recommended to use the asynchronous context
        manager to allow resources to be cleaned up automatically:

        .. code-block:: python3
            async def main():
                async with ModmailBot() as bot:
                    await bot.start()

            asyncio.run(main())


        .. versionchanged:: 2.0
            The bot can now be closed with an asynchronous context manager.
        """
        if self.session:
            await self.session.close()
        await super().close()

    async def wait_for_connected(self) -> None:
        """
        This will wait for three things until they're all ready:
        - Client's internal cache is all ready.
        - Database (and other API connections) is successfully connected.
        - Config cache is populated with stuff from the database.
        """
        await self.wait_until_ready()
        await self._connected.wait()
        await self.config.wait_until_ready()

    async def on_connect(self):
        try:
            await self.api.validate_database_connection()
        except Exception:
            logger.debug("Logging out due to failed database connection.")
            return await self.close()

        logger.debug("Connected to gateway.")
        await self.config.refresh()
        await self.api.setup_indexes()
        await self.load_extensions()
        self._connected.set()

    async def on_ready(self):
        """Bot startup, sets uptime and configure threads."""

        # Wait until config cache is populated with stuff from db and on_connect ran
        await self.wait_for_connected()

        if self.guild is None:
            logger.error("Logging out due to invalid GUILD_ID.")
            return await self.close()

        if self._started:
            # Bot has started before
            logger.line()
            logger.warning("Bot restarted due to internal discord reloading.")
            logger.line()
            return

        logger.line()
        logger.debug("Client ready.")
        logger.info("Logged in as: %s", self.user)
        logger.info("Bot ID: %s", self.user.id)
        owners = ", ".join(
            getattr(self.get_user(owner_id), "name", str(owner_id))
            for owner_id in self.bot_owner_ids
        )
        logger.info("Owner: %s", owners)
        logger.info("Prefix: %s", self.prefix)
        logger.info("Guild Name: %s", self.guild.name)
        logger.info("Guild ID: %s", self.guild.id)
        if self.using_multiple_server_setup:
            logger.info("Receiving guild ID: %s", self.modmail_guild.id)
        logger.line()

        await self.thread_manager.populate_cache()
        await self.thread_manager.handle_closures()
        await self.thread_manager.validate_thread_channels()

        other_guilds = [
            guild for guild in self.guilds if guild not in self.registered_guilds
        ]
        if any(other_guilds):
            logger.warning(
                "The bot is in more servers other than the main and staff server. "
                "This may cause data compromise (%s).",
                ", ".join(guild.name for guild in other_guilds),
            )
            # Auto leave unrecognized guild when the bot is on ready.
            for guild in other_guilds:
                await guild.leave()
                logger.warning("Leaving the server: %s", guild.name)

        self._refresh_tree()
        if self.config.get("contact_panel_message"):
            self.add_view(ContactView(self))
        self._started = True

    def _refresh_tree(self) -> None:
        """
        Internal method to refresh the global application commands which were automatically
        stored in `Bot.tree` on startup, and copy them into every guild.

        This will not register the commands in the client. To do that,
        the `Bot.tree.sync()` needs to be called separately.
        """
        for guild in self.guilds:
            self.tree.copy_global_to(guild=guild)

        # clear global commands from `Bot.tree`
        self.tree.clear_commands(guild=None)

    async def get_prefix(self, message=None) -> List[str]:
        """
        List of the bot's prefixes including bot mentions.
        """
        return [self.prefix, f"<@{self.user.id}> ", f"<@!{self.user.id}> "]

    @property
    def prefix(self) -> str:
        """The prefix that was set for this bot, or the default prefix if not set."""
        return str(self.config["prefix"])

    @property
    def aliases(self) -> Dict[str, str]:
        """Dict of aliases for commands."""
        return self.config["aliases"]

    @property
    def snippets(self) -> Dict[str, str]:
        """Dict of snippets."""
        return self.config["snippets"]

    @property
    def uptime(self) -> str:
        """
        Bot's uptime since the startup.
        """
        now = discord.utils.utcnow()
        delta = now - self.start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        days, hours = divmod(hours, 24)

        _seconds = "{s}s" if seconds else ""
        _minutes = "{m}m" if minutes else ""
        _hours = "{h}h" if hours else ""
        _days = "{d}d" if days else ""

        fmt = f"{_seconds}"
        if minutes:
            fmt = f"{_minutes} {_seconds}"
        if hours:
            fmt = f"{_hours} {_minutes}"
        if days:
            fmt = f"{_days} {_hours} {_minutes}"

        return self.formatter.format(fmt, d=days, h=hours, m=minutes, s=seconds)

    @property
    def hosting_method(self) -> HostingMethod:
        """Bot's hosting method."""
        # use enums
        if ".heroku" in os.environ.get("PYTHONHOME", ""):
            return HostingMethod.HEROKU

        if os.environ.get("pm_id"):
            return HostingMethod.PM2

        if os.environ.get("INVOCATION_ID"):
            return HostingMethod.SYSTEMD

        if os.environ.get("USING_DOCKER"):
            return HostingMethod.DOCKER

        if os.environ.get("REPL_ID"):
            return HostingMethod.REPL

        if os.environ.get("TERM"):
            return HostingMethod.SCREEN

        return HostingMethod.OTHER

    @property
    def python_version(self) -> str:
        """The python version this instance currently runs with."""
        v = sys.version_info
        return "{0.major}.{0.minor}.{0.micro} {1}".format(v, v.releaselevel.title())

    @property
    def version(self):
        """Bot's version."""
        return self.parse_version(__version__)

    @property
    def api(self) -> Union[ApiClient, MongoDBClient]:
        """
        Returns an instance of API client that the bot is currently connected to.
        Defaults to :class:`MongoDBClient`.

        The base class of this instance also has method `request` that can be used
        to make HTTP request.

        Returns
        -------
        :class:`ApiClient` or :class:`MongoDBClient`
            The API client.
        """
        if self._api is MISSING:
            if self.config["database_type"].lower() == "mongodb":
                self._api = MongoDBClient(self)
            else:
                logger.critical("Invalid database type.")
                raise RuntimeError
        return self._api

    @property
    def bot_owner_ids(self) -> Set[int]:
        """
        List of bot owner IDs. This will fetch the IDs that were set in config `.env`
        and permission level.

        Returns
        -------
        :class:`Set[int]`
            Set of owners IDs.
        """
        owner_ids = self.config["owners"]
        if owner_ids is not None:
            owner_ids = set(map(int, str(owner_ids).split(",")))
        if self.owner_id is not None:
            owner_ids.add(self.owner_id)
        permissions = self.config["level_permissions"].get(
            PermissionLevel.OWNER.name, []
        )
        for perm in permissions:
            owner_ids.add(int(perm))
        return owner_ids

    async def is_owner(self, user: Union[discord.Member, discord.User]) -> bool:
        """
        Checks if a :class:`~discord.User` or :class:`~discord.Member` is the owner of
        this bot.

        Parameters
        -----------
        user: :class:`.abc.User`
            The user to check for.

        Returns
        --------
        :class:`bool`
            Whether the user is the owner.
        """
        if user.id in self.bot_owner_ids:
            return True
        return await super().is_owner(user)

    @property
    def token(self) -> str:
        """
        Bot's token.
        """
        token = self.config["token"]
        if token is None:
            logger.critical(
                "TOKEN must be set, set this as bot token found on the Discord Developer Portal."
            )
            sys.exit(0)
        return token

    @property
    def guild_id(self) -> Optional[int]:
        """
        The ID of the main guild (where the bot is serving).
        """
        guild_id = self.config["guild_id"]
        if guild_id is not None:
            try:
                return int(str(guild_id))
            except ValueError:
                self.config.remove("guild_id")
                logger.critical("Invalid GUILD_ID set.")
        else:
            logger.debug("No GUILD_ID set.")
        return None

    @property
    def guild(self) -> Optional[discord.Guild]:
        """
        The guild that the bot is serving (the server where users message it from).

        Returns
        -------
        :class:`discord.Guild` or None
            The guild object or None.
        """
        return discord.utils.get(self.guilds, id=self.guild_id)

    @property
    def modmail_guild(self) -> Optional[discord.Guild]:
        """
        The guild that the bot is operating in (where the bot is creating threads).

        Returns
        -------
        :class:`discord.Guild` or None
            The guild object or None.
        """
        modmail_guild_id = self.config["modmail_guild_id"]
        if modmail_guild_id is None:
            return self.guild
        try:
            guild = discord.utils.get(self.guilds, id=int(modmail_guild_id))
            if guild is not None:
                return guild
        except ValueError:
            pass
        self.config.remove("modmail_guild_id")
        logger.critical("Invalid MODMAIL_GUILD_ID set.")
        return self.guild

    @property
    def registered_guilds(self) -> List[discord.Guild]:
        """
        Registered guilds. It will return the guild objects in a list/array.
        You can loop through the list using `for loop`.

        Returns
        -------
        :class:`List[discord.Guild]`
            List of IDs of registered guilds.
        """
        guilds = [self.guild]
        if self.using_multiple_server_setup:
            guilds.append(self.modmail_guild)

        registered_guild_ids = self.config["registered_guild_ids"]
        if registered_guild_ids is not None:
            guild_ids = set(map(int, str(registered_guild_ids).split(",")))

            for guild_id in guild_ids:
                guild = discord.utils.get(self.guilds, id=int(guild_id))
                if guild is not None:
                    guilds.append(guild)

        return guilds

    @property
    def using_multiple_server_setup(self) -> bool:
        """
        Checks if the bot is using separate server setup.

        Returns
        --------
        :class:`bool`
            Whether this bot is using separate server setup.
        """
        return self.modmail_guild != self.guild

    @property
    def main_category(self) -> Optional[discord.CategoryChannel]:
        """
        The main category where the thread will be created.

        Returns
        --------
        :class:`discord.CategoryChannel` or None
            The category channel object, or None.
        """
        if self.modmail_guild is not None:
            category_id = self.config["main_category_id"]
            if category_id is not None:
                try:
                    cat = discord.utils.get(
                        self.modmail_guild.categories, id=int(category_id)
                    )
                    if cat is not None:
                        return cat
                except ValueError:
                    pass
                self.config.remove("main_category_id")
                logger.debug("MAIN_CATEGORY_ID was invalid, removed.")
            cat = discord.utils.get(self.modmail_guild.categories, name="Modmail")
            if cat is not None:
                self.config["main_category_id"] = cat.id
                logger.debug(
                    'No main category set explicitly, setting category "Modmail" as the main category.'
                )
                return cat
        return None

    @property
    def log_channel(self) -> Optional[discord.TextChannel]:
        """
        The channel where the thread logs will be posted.

        Returns
        -------
        :class:`discord.TextChannel` or None
            The text channel object, or None.
        """
        channel_id = self.config["log_channel_id"]
        if channel_id is not None:
            try:
                channel = self.get_channel(int(channel_id))
                if channel is not None:
                    if not isinstance(channel, discord.TextChannel):
                        raise TypeError(
                            f"Invalid type. Expected `TextChannel`, got `{type(channel).__name__}` instead."
                        )
                    return channel
            except ValueError:
                pass
            logger.debug("LOG_CHANNEL_ID was invalid, removed.")
            self.config.remove("log_channel_id")
        logger.warning(
            "No log channel set, set one with `%ssetup` or `%sconfig set log_channel_id <id>`.",
            self.prefix,
            self.prefix,
        )
        return None

    @property
    def blocked_users(self) -> Dict[str, str]:
        """
        Users that are currently blocked from contacting Modmail.

        Returns
        -------
        :class:`Dict[str, str]`
            Dict of 'user id': 'reason' pair of blocked users.
        """
        return self.config["blocked"]

    @property
    def blocked_whitelisted_users(self) -> List[str]:
        """
        The whitelisted users. These users cannot be blocked from contacting Modmail.

        Returns
        -------
        :class:`List`
            The IDs of whitelisted users.
        """
        return self.config["blocked_whitelist"]

    @property
    def mod_color(self) -> int:
        """
        The embed color of the messages sent by the moderators, this applies to messages within
        in the thread channel and the DM thread messages received by the recipient.
        """
        return self.config.get("mod_color")

    @property
    def recipient_color(self) -> int:
        """
        The embed color of the messages sent by the recipient, this applies to messages received
        in the thread channel.
        """
        return self.config.get("recipient_color")

    @property
    def main_color(self) -> int:
        """
        The main color for Modmail (help/about/ping embed messages, subscribe, move, etc.).
        """
        return self.config.get("main_color")

    @property
    def error_color(self) -> int:
        """
        The color for Modmail when anything goes wrong, unsuccessful commands, or a stern warning.
        """
        return self.config.get("error_color")

    @staticmethod
    def parse_version(version: str) -> Union[Version, LegacyVersion]:
        """
        A method to parse any version string using method `parse_version` from `pkg_resources` module.

        Parameters
        -----------
        version : str
            The string to be parsed.

        Returns
        -------
        :class:`Version` or :class:`LegacyVersion`
            The parsed version.
        """
        return pkg_parse_version(version)

    def command_perm(self, command_name: str) -> PermissionLevel:
        """
        Get the permission level of the specified command.

        Parameters
        -----------
        command_name : str
            The commmand name.

        Returns
        -------
        :class:`PermissionLevel`
            The permission level that was set for the command.
        """
        level = self.config["override_command_level"].get(command_name)
        if level is not None:
            try:
                return PermissionLevel[level.upper()]
            except KeyError:
                logger.warning(
                    "Invalid override_command_level for command %s.", command_name
                )
                self.config["override_command_level"].pop(command_name)

        command = self.get_command(command_name)
        if command is None:
            logger.debug("Command %s not found.", command_name)
            return PermissionLevel.INVALID

        attr_name = "permission_level"
        level: PermissionLevel = next(
            (
                getattr(check, attr_name)
                for check in command.checks
                if hasattr(check, attr_name)
            ),
            PermissionLevel.INVALID,
        )
        if level is PermissionLevel.INVALID:
            logger.debug("Command %s does not have a permission level.", command_name)
        return level

    def convert_emoji(self, name: str) -> EmojiInputType:
        """
        A method to convert the provided string to a :class:`discord.Emoji`, :class:`discord.PartialEmoji`.

        If the parsed emoji has an ID (a custom emoji) and cannot be found, or does not have an ID and
        cannot be found in :class:`UNICODE_EMOJI_ENGLISH`, :class:`commands.EmojiNotFound`
        will be raised.

        Parameters
        -----------
        name : str
            The emoji string or a unicode emoji.

        Returns
        -------
        :class:`discord.Emoji` or :class:`discord.PartialEmoji`
            The converted emoji.
        """
        name = re.sub("\ufe0f", "", name)  # remove trailing whitespace
        emoji = discord.PartialEmoji.from_str(name)
        if emoji.is_unicode_emoji():
            if emoji.name not in UNICODE_EMOJI_ENGLISH:
                emoji = None
        else:
            # custom emoji
            emoji = self.get_emoji(emoji.id)

        if emoji is None:
            logger.error("%s is not a valid emoji.", name)
            raise commands.EmojiNotFound(name)

        return emoji

    async def retrieve_emoji(self) -> Tuple[EmojiInputType, EmojiInputType]:
        """
        Retrieves sent and blocked emojis from config.

        Returns
        -------
        :class:`Tuple[str, str]`
            A tuple of sent and blocked emojis.
        """

        sent_emoji = self.config["sent_emoji"]
        blocked_emoji = self.config["blocked_emoji"]

        if sent_emoji != "disable":
            try:
                sent_emoji = self.convert_emoji(sent_emoji)
            except commands.BadArgument:
                logger.warning("Removed sent emoji (%s).", sent_emoji)
                sent_emoji = self.config.remove("sent_emoji")
                await self.config.update()

        if blocked_emoji != "disable":
            try:
                blocked_emoji = self.convert_emoji(blocked_emoji)
            except commands.BadArgument:
                logger.warning("Removed blocked emoji (%s).", blocked_emoji)
                blocked_emoji = self.config.remove("blocked_emoji")
                await self.config.update()

        return sent_emoji, blocked_emoji

    @staticmethod
    async def add_reaction(message: discord.Message, emoji: ReactionInputType) -> bool:
        """
        Add a reaction to the message.

        The emoji may be a unicode emoji or a custom guild :class:`Emoji`.

        Parameters
        ----------
        message: discord.Message
            The message to add reactions to.
        emoji : discord.Emoji or discord.Reaction or discord.PartialEmoji or str
            Emoji to add.

        Returns
        -------
        :class:`bool`
            `True` if success adding reaction, otherwise `False`.
        """
        if emoji != "disable":
            try:
                await message.add_reaction(emoji)
            except (discord.HTTPException, discord.InvalidArgument) as e:
                logger.warning("Failed to add reaction %s: %s.", emoji, e)
                return False
            return True
        return False

    @staticmethod
    def add_reactions(
        message: discord.Message, emojis: Iterable[ReactionInputType]
    ) -> asyncio.Task:
        """
        Add multiple reactions to the message.

        The emojis may be unicode emojis or a custom guild :class:`Emoji`.
        `asyncio.sleep()` is used to prevent the client from being rate limited when
        adding multiple reactions to the message.

        This is a non-blocking operation - calling this will schedule the
        reactions being added, but the calling code will continue to
        execute asynchronously. There is no need to await this function.

        This is particularly useful if you wish to start waiting for a
        reaction whilst the reactions are still being added.

        Parameters
        ----------
        message: discord.Message
            The message to add reactions to.
        emojis : Iterable[discord.Emoji or discord.Reaction or discord.PartialEmoji or str]
            Emojis to add.

        Returns
        -------
        :class:`asyncio.Task`
            The task for the coroutine adding the reactions.
        """

        async def task():
            # The task should exit silently if the message is deleted
            with contextlib.suppress(discord.NotFound):
                for emoji in emojis:
                    try:
                        await message.add_reaction(emoji)
                    except (discord.HTTPException, discord.InvalidArgument) as e:
                        logger.warning("Failed to add reaction %s: %s.", emoji, e)
                        return
                    await asyncio.sleep(0.2)

        return asyncio.create_task(task())

    def check_account_age(self, author: discord.Member) -> bool:
        """
        Checks the account age of :class:`discord.Member` and compares with the set
        account age that is required to DM the bot.

        If the member account age has not reached the required time, they will be
        temporarily blocked.

        Parameters
        ----------
        author : discord.Member
            The member object.

        Returns
        -------
        :class:`bool`
            True if the member account is older than the required time.
        """
        account_age = self.config.get("account_age")
        now = discord.utils.utcnow()

        try:
            min_account_age = author.created_at + account_age
        except ValueError:
            logger.warning("Error with 'account_age'.", exc_info=True)
            min_account_age = author.created_at + self.config.remove("account_age")

        if min_account_age > now:
            # User account has not reached the required time
            delta = human_timedelta(min_account_age)
            logger.debug("Blocked due to account age, user %s.", author.name)

            if str(author.id) not in self.blocked_users:
                new_reason = (
                    f"System Message: New Account. Required to wait for {delta}."
                )
                self.blocked_users[str(author.id)] = new_reason

            return False
        return True

    def check_guild_age(self, author: discord.Member) -> bool:
        """
        Checks the time since the :class:`discord.Member` joined the guild and compares
        with the set guild age that is required to DM the bot.

        If the member guild age has not reached the required time, they will be
        temporarily blocked.

        Parameters
        ----------
        author : discord.Member
            The member object.

        Returns
        -------
        :class:`bool`
            True if the member guild age is older than the required time.
        """
        guild_age = self.config.get("guild_age")
        now = discord.utils.utcnow()

        if not hasattr(author, "joined_at"):
            logger.warning("Not in guild, cannot verify guild_age, %s.", author.name)
            return True

        try:
            min_guild_age = author.joined_at + guild_age
        except ValueError:
            logger.warning("Error with 'guild_age'.", exc_info=True)
            min_guild_age = author.joined_at + self.config.remove("guild_age")

        if min_guild_age > now:
            # User has not stayed in the guild for long enough
            delta = human_timedelta(min_guild_age)
            logger.debug("Blocked due to guild age, user %s.", author.name)

            if str(author.id) not in self.blocked_users:
                new_reason = (
                    f"System Message: Recently Joined. Required to wait for {delta}."
                )
                self.blocked_users[str(author.id)] = new_reason

            return False
        return True

    async def check_manual_blocked(self, author: discord.Member) -> bool:
        """
        Returns True if user is not/no longer blocked. Otherwise, False.
        """
        if str(author.id) not in self.blocked_users:
            return True

        blocked_reason = self.blocked_users.get(str(author.id)) or ""
        now = discord.utils.utcnow()

        if blocked_reason.startswith("System Message:"):
            # Met the limits already, otherwise it would've been caught by the previous checks
            logger.debug("No longer internally blocked, user %s.", author.name)
            self.blocked_users.pop(str(author.id))
            await self.config.update()
            return True
        # etc "blah blah blah... until 2019-10-14T21:12:45.559948."
        end_time = re.search(r"until ([^`]+?)\.$", blocked_reason)
        if end_time is None:
            # backwards compat
            end_time = re.search(r"%([^%]+?)%", blocked_reason)
            if end_time is not None:
                logger.warning(
                    r"Deprecated time message for user %s, block and unblock again to update.",
                    author.name,
                )

        if end_time is not None:
            after = (
                datetime.fromisoformat(end_time.group(1)).replace(tzinfo=timezone.utc)
                - now
            ).total_seconds()
            if after <= 0:
                # No longer blocked
                self.blocked_users.pop(str(author.id))
                logger.debug("No longer blocked, user %s.", author.name)
                await self.config.update()
                return True
        logger.debug("User blocked, user %s.", author.name)
        return False

    async def is_blocked(
        self,
        author: Union[discord.Member, discord.User],
        *,
        channel: Union[discord.TextChannel, discord.DMChannel] = None,
        send_message: bool = False,
    ) -> bool:
        """Returns True if user is blocked. Otherwise, False."""

        member = self.guild.get_member(author.id)
        if member is None:
            # try to find in other guilds
            for g in self.guilds:
                member = g.get_member(author.id)
                if member:
                    break

            if member is None:
                logger.debug("User not in guild, %s.", author.id)

        if member is not None:
            author = member

        if str(author.id) in self.blocked_whitelisted_users:
            if str(author.id) in self.blocked_users:
                self.blocked_users.pop(str(author.id))
                await self.config.update()
            return False

        blocked_reason = self.blocked_users.get(str(author.id)) or ""

        if not self.check_account_age(author) or not self.check_guild_age(author):
            new_reason = self.blocked_users.get(str(author.id))
            if new_reason != blocked_reason:
                if channel and send_message:
                    await channel.send(
                        embed=discord.Embed(
                            title="Message not sent!",
                            description=new_reason,
                            color=self.error_color,
                        )
                    )
            return True

        if not await self.check_manual_blocked(author):
            return True

        return False

    async def _process_blocked(self, message: discord.Message):
        _, blocked_emoji = await self.retrieve_emoji()
        if await self.is_blocked(
            message.author, channel=message.channel, send_message=True
        ):
            await self.add_reaction(message, blocked_emoji)
            return True
        return False

    async def get_thread_cooldown(self, author: discord.Member) -> Optional[str]:
        """
        Get the thread cooldown from config.
        """
        thread_cooldown = self.config.get("thread_cooldown")
        now = discord.utils.utcnow()

        if thread_cooldown == isodate.Duration():
            return

        last_log = await self.api.get_latest_user_logs(author.id)

        if last_log is None:
            logger.debug("Last thread wasn't found, %s.", author.name)
            return

        last_closed_at = last_log.get("closed_at")

        if not last_closed_at:
            logger.debug("Last thread was not closed, %s.", author.name)
            return

        last_closed_at = datetime.fromisoformat(last_closed_at)
        if last_closed_at.tzinfo is None:
            # to support older log
            last_closed_at = last_closed_at.replace(tzinfo=timezone.utc)

        try:
            cooldown = last_closed_at + thread_cooldown
        except ValueError:
            logger.warning("Error with 'thread_cooldown'.", exc_info=True)
            cooldown = last_closed_at + self.config.remove("thread_cooldown")

        if cooldown > now:
            # User messaged before thread cooldown ended
            delta = human_timedelta(cooldown)
            logger.debug("Blocked due to thread cooldown, user %s.", author.name)
            return delta
        return

    async def process_dm_modmail(self, message: discord.Message) -> None:
        """Processes messages sent to the bot."""
        blocked = await self._process_blocked(message)
        if blocked:
            return
        sent_emoji, blocked_emoji = await self.retrieve_emoji()

        if message.type != discord.MessageType.default:
            return

        modmail_thread = await self.thread_manager.find(recipient=message.author)
        if modmail_thread is None:
            delta = await self.get_thread_cooldown(message.author)
            if delta:
                await message.channel.send(
                    embed=discord.Embed(
                        title=self.config["cooldown_thread_title"],
                        description=self.config["cooldown_thread_response"].format(
                            delta=delta
                        ),
                        color=self.error_color,
                    )
                )
                return

            if self.config["dm_disabled"] in (
                DMDisabled.NEW_THREADS,
                DMDisabled.ALL_THREADS,
            ):
                embed = discord.Embed(
                    title=self.config["disabled_new_thread_title"],
                    color=self.error_color,
                    description=self.config["disabled_new_thread_response"],
                )
                embed.set_footer(
                    text=self.config["disabled_new_thread_footer"],
                    icon_url=self.guild.icon.url,
                )
                logger.info(
                    "A new thread was blocked from %s due to disabled Modmail.",
                    message.author,
                )
                await self.add_reaction(message, blocked_emoji)
                await message.channel.send(embed=embed)
                return

            modmail_thread = await self.thread_manager.create(
                message.author, message=message
            )
        else:
            if self.config["dm_disabled"] == DMDisabled.ALL_THREADS:
                embed = discord.Embed(
                    title=self.config["disabled_current_thread_title"],
                    color=self.error_color,
                    description=self.config["disabled_current_thread_response"],
                )
                embed.set_footer(
                    text=self.config["disabled_current_thread_footer"],
                    icon_url=self.guild.icon.url,
                )
                logger.info(
                    "A message was blocked from %s due to disabled Modmail.",
                    message.author,
                )
                await self.add_reaction(message, blocked_emoji)
                await message.channel.send(embed=embed)
                return

        if not modmail_thread.cancelled:
            try:
                await modmail_thread.send(message)
            except Exception:
                logger.error("Failed to send message:", exc_info=True)
                await self.add_reaction(message, blocked_emoji)
            else:
                await self.add_reaction(message, sent_emoji)
                self.dispatch(
                    "thread_reply",
                    modmail_thread,
                    False,  # from_mod
                    message,
                    False,  # anonymous
                    False,  # plain
                )

    async def get_context(
        self,
        origin: Union[discord.Message, discord.Interaction],
        *,
        cls=commands.Context,
    ) -> List[commands.Context]:
        """
        Returns the invocation context from the message.
        Supports getting the prefix from database as well as command aliases.

        This method is overridden from the default "discord.py" `get_context`, so the return value here
        is a list of contexts instead of :class:`commands.Context`.

        Returns
        -------
        list : List[commands.Context]
            List of context objects.
        """

        if isinstance(origin, discord.Interaction):
            return await cls.from_interaction(origin)

        view = StringView(origin.content)
        ctx = cls(prefix=self.prefix, view=view, bot=self, message=origin)

        if origin.author.id == self.user.id:
            return [ctx]

        if isinstance(ctx.channel, discord.TextChannel):
            modmail_thread = await self.thread_manager.find(channel=ctx.channel)
        else:
            modmail_thread = None

        ctx.modmail_thread = modmail_thread
        prefixes = await self.get_prefix()
        invoked_prefix = discord.utils.find(view.skip_string, prefixes)
        if invoked_prefix is None:
            return [ctx]

        invoker = view.get_word().lower()
        # Check if there is any aliases being called.
        alias = self.aliases.get(invoker)
        if alias is not None:
            ctxs = []
            aliases = normalize_alias(
                alias, origin.content[len(f"{invoked_prefix}{invoker}") :]
            )
            if not aliases:
                logger.warning("Alias %s is invalid, removing.", invoker)
                self.aliases.pop(invoker)

            for alias in aliases:
                view = StringView(invoked_prefix + alias)
                ctx_ = cls(prefix=self.prefix, view=view, bot=self, message=origin)
                ctx_.modmail_thread = modmail_thread
                discord.utils.find(view.skip_string, prefixes)
                ctx_.invoked_with = view.get_word().lower()
                ctx_.command = self.all_commands.get(ctx_.invoked_with)
                ctxs += [ctx_]
            return ctxs

        ctx.invoked_with = invoker
        ctx.command = self.all_commands.get(invoker)

        return [ctx]

    async def create_context(
        self, message: discord.Message, *, cls=commands.Context
    ) -> commands.Context:
        """
        Similar with `get_context`, except this method doesn't check for command
        aliases.

        Returns the invocation context from the message.
        Supports getting the prefix from database.
        """

        view = StringView(message.content)
        ctx = cls(prefix=self.prefix, view=view, bot=self, message=message)

        if message.author.id == self.user.id:
            return ctx

        if isinstance(ctx.channel, discord.TextChannel):
            modmail_thread = await self.thread_manager.find(channel=ctx.channel)
        else:
            modmail_thread = None

        ctx.modmail_thread = modmail_thread
        prefixes = await self.get_prefix()
        invoked_prefix = discord.utils.find(view.skip_string, prefixes)
        if invoked_prefix is None:
            return ctx

        invoker = view.get_word().lower()
        ctx.invoked_with = invoker
        ctx.command = self.all_commands.get(invoker)

        return ctx

    async def update_perms(
        self, name: Union[PermissionLevel, str], value: int, add: bool = True
    ) -> None:
        if value != -1:
            value = str(value)
        if isinstance(name, PermissionLevel):
            level = True
            permissions = self.config["level_permissions"]
            name = name.name
        else:
            level = False
            permissions = self.config["command_permissions"]
        if name not in permissions:
            if add:
                permissions[name] = [value]
        else:
            if add:
                if value not in permissions[name]:
                    permissions[name].append(value)
            else:
                if value in permissions[name]:
                    permissions[name].remove(value)

        if level:
            self.config["level_permissions"] = permissions
        else:
            self.config["command_permissions"] = permissions
        logger.info("Updating permissions for %s, %s (add=%s).", name, value, add)
        await self.config.update()

    async def on_message(self, message: discord.Message):
        await self.wait_for_connected()
        if message.type == discord.MessageType.pins_add and message.author == self.user:
            await message.delete()
        if message.type == discord.MessageType.default:
            # only process commands if the message type is default
            await self.process_commands(message)

    async def process_commands(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if isinstance(message.channel, discord.DMChannel):
            await self.process_dm_modmail(message)
            return

        if message.content.startswith(self.prefix):
            cmd = message.content[len(self.prefix) :].strip()

            # Process snippets
            if cmd in self.snippets:
                snippet = self.snippets[cmd]
                message.content = f"{self.prefix}freply {snippet}"
        ctxs = await self.get_context(message)
        for ctx in ctxs:
            if ctx.command:
                if not any(
                    1
                    for check in ctx.command.checks
                    if hasattr(check, "permission_level")
                ):
                    logger.debug(
                        "Command %s has no permissions check, adding invalid level.",
                        ctx.command.qualified_name,
                    )
                    checks.has_permissions(PermissionLevel.INVALID)(ctx.command)

                await self.invoke(ctx)
                continue

            if ctx.modmail_thread is not None:
                internal = False
                anonymous = self.config.get("anon_reply_without_command")
                plain = self.config.get("plain_reply_without_command")
                normal_reply = self.config.get("reply_without_command")
                if any((anonymous, plain, normal_reply)):
                    if not message.content.startswith(self.prefix):
                        if anonymous and plain:
                            command_name = "pareply"
                        elif anonymous:
                            command_name = "areply"
                        elif plain:
                            command_name = "preply"
                        else:
                            command_name = "reply"

                        command = self.get_command(command_name)
                        if not await command.can_run(ctx):
                            # return silently
                            return

                        await ctx.invoke(command, msg=message.content)
                    else:
                        internal = True
                        message.content = message.content[len(self.prefix) :].strip()
                else:
                    internal = True
                if internal:
                    await self.api.append_log(message, type_=ThreadMessageType.INTERNAL)
            elif ctx.invoked_with:
                exc = commands.CommandNotFound(
                    'Command "{}" is not found'.format(ctx.invoked_with)
                )
                self.dispatch("command_error", ctx, exc)

    async def on_typing(
        self,
        channel: Union[discord.TextChannel, discord.DMChannel],
        user: Union[discord.Member, discord.User],
        _,
    ):
        await self.wait_for_connected()

        if user.bot:
            return

        if isinstance(channel, discord.DMChannel):
            if not self.config.get("user_typing"):
                return

            modmail_thread = await self.thread_manager.find(recipient=user)

            if modmail_thread:
                await modmail_thread.channel.typing()
        else:
            if not self.config.get("mod_typing"):
                return

            modmail_thread = await self.thread_manager.find(channel=channel)
            if modmail_thread is not None and modmail_thread.recipient:
                if await self.is_blocked(modmail_thread.recipient):
                    return
                await modmail_thread.recipient.typing()

    async def handle_reaction_events(self, payload: discord.RawReactionActionEvent):
        user = self.get_user(payload.user_id)
        if user is None or user.bot:
            return

        channel = self.get_channel(payload.channel_id)
        if not channel:  # dm channel not in internal cache
            _thread = await self.thread_manager.find(recipient=user)
            if not _thread:
                return
            channel = await _thread.recipient.create_dm()

        if not isinstance(channel, (discord.TextChannel, discord.DMChannel)):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden):
            return

        reaction = payload.emoji

        close_emoji = self.convert_emoji(self.config["close_emoji"])

        if isinstance(channel, discord.DMChannel):
            modmail_thread = await self.thread_manager.find(recipient=user)
            if not modmail_thread:
                return
            if (
                payload.event_type == "REACTION_ADD"
                and message.embeds
                and str(reaction) == str(close_emoji)
                and self.config.get("recipient_thread_close")
            ):
                ts = message.embeds[0].timestamp
                if modmail_thread and ts == modmail_thread.channel.created_at:
                    # the reacted message is the corresponding thread creation embed
                    # closing thread
                    return await modmail_thread.close(closer=user)
            if (
                message.author == self.user
                and message.embeds
                and self.config.get("confirm_thread_creation")
                and message.embeds[0].title
                == self.config["confirm_thread_creation_title"]
                and message.embeds[0].description
                == self.config["confirm_thread_response"]
            ):
                return
            if not modmail_thread.recipient.dm_channel:
                await modmail_thread.recipient.create_dm()
            try:
                msg_payload = await modmail_thread.find_dm_message_payload(
                    message, either_direction=True
                )
            except LinkMessageError as e:
                if not isinstance(e, IgnoredMessage):
                    logger.warning("Failed to find linked message for reactions: %s", e)
                return
        else:
            modmail_thread = await self.thread_manager.find(channel=channel)
            if not modmail_thread:
                return
            if message.embeds and message.embeds[0].author.url:
                is_log_embed = message.embeds[0].author.url.startswith(
                    self.config["log_url"]
                )
                if is_log_embed:
                    return
            try:
                msg_payload = await modmail_thread.find_message_payload(
                    message, either_direction=True
                )
            except LinkMessageError as e:
                if not isinstance(e, IgnoredMessage):
                    logger.warning("Failed to find linked message for reactions: %s", e)
                return

        linked_message = msg_payload.linked_message

        if not linked_message:
            return

        if payload.event_type == "REACTION_ADD":
            if await self.add_reaction(linked_message, reaction):
                await self.add_reaction(message, reaction)
        else:
            try:
                await linked_message.remove_reaction(reaction, self.user)
                await message.remove_reaction(reaction, self.user)
            except (discord.HTTPException, discord.InvalidArgument) as e:
                logger.warning("Failed to remove reaction: %s", e)

    async def handle_contact_panel_events(
        self,
        *,
        reaction_payload: discord.RawReactionActionEvent = MISSING,
        interaction: discord.Interaction = MISSING,
    ) -> None:
        if reaction_payload and interaction:
            raise ValueError(
                'Cannot pass both "reaction_payload" and "interaction" for "handle_contact_panel_events".'
            )
        if not reaction_payload and not interaction:
            raise ValueError(
                'One of the "reaction_payload" or "interaction" parameter is '
                'required for "handle_contact_panel_events".'
            )

        contact_panel_message = self.config.get("contact_panel_message")
        panel_emoji = self.config.get("contact_button_emoji")
        if not all((contact_panel_message, panel_emoji)):
            return

        # reaction event
        if reaction_payload:
            if (
                f"{reaction_payload.channel_id}-{reaction_payload.message_id}"
                != contact_panel_message
            ):
                return

            if reaction_payload.emoji.is_unicode_emoji():
                emoji_fmt = reaction_payload.emoji.name
            elif reaction_payload.emoji.animated:
                emoji_fmt = (
                    f"<a:{reaction_payload.emoji.name}:{reaction_payload.emoji.id}>"
                )
            else:
                emoji_fmt = (
                    f"<:{reaction_payload.emoji.name}:{reaction_payload.emoji.id}>"
                )

            if emoji_fmt != panel_emoji:
                return

            # check if the user exists in the main guild
            member = self.guild.get_member(reaction_payload.user_id)
            if not member or member.bot:
                return

            channel = self.get_channel(reaction_payload.channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                return

            message = await channel.fetch_message(reaction_payload.message_id)
            await message.remove_reaction(reaction_payload.emoji, member)  # type: ignore
            await self.add_reaction(message, emoji_fmt)  # bot adds as well
            if message.author.id == self.user.id:
                # should use button
                logger.error(
                    "Reaction emoji should not be used on the bot's contact panel."
                )
                return
        # interaction event
        else:
            member = self.guild.get_member(interaction.user.id)
            channel = self.get_channel(self.contact_panel_view.channel_id)
            if not isinstance(channel, discord.TextChannel):
                return

        if self.config["dm_disabled"] in (
            DMDisabled.NEW_THREADS,
            DMDisabled.ALL_THREADS,
        ):
            embed = discord.Embed(
                color=self.error_color,
                description=self.config["disabled_new_thread_response"],
            )
            embed.set_footer(
                text=self.config["disabled_new_thread_footer"],
                icon_url=self.guild.icon.url,
            )
            logger.info(
                "A new thread using contact panel was blocked from %s due to disabled Modmail.",
                member,
            )
            kwargs = {"embed": embed}
            if reaction_payload:
                send_func = member.send
            else:
                kwargs["ephemeral"] = True
                send_func = interaction.response.send_message
            return await send_func(**kwargs)

        if await self.is_blocked(member):
            # we just ignore reaction
            if interaction:
                embed = discord.Embed(
                    color=self.error_color,
                    description=f"You are currently blocked from contacting {self.user.name}.",
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

        modmai_thread = await self.thread_manager.find(recipient=member)
        if modmai_thread:
            desc = "A thread for this user already exists"
            if modmai_thread.channel:
                desc += f" in {modmai_thread.channel.mention}"
            desc += "."
            embed = discord.Embed(color=self.error_color, description=desc)
            kwargs = {"embed": embed}
            if reaction_payload:
                kwargs["delete_after"] = 3
                send_func = channel.send
            else:
                kwargs["ephemeral"] = True
                send_func = interaction.response.send_message
            await send_func(**kwargs)
        else:
            modmai_thread = await self.thread_manager.create(
                recipient=member, creator=member, category=None, manual_trigger=False
            )
            if modmai_thread.cancelled:
                return

            embed = discord.Embed(
                title="New thread",
                description="You have opened Modmail thread.",
                color=self.main_color,
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(icon_url=member.display_avatar.url)
            await member.send(embed=embed)

            embed = discord.Embed(
                title="Created thread",
                description=f"Thread started by {member.mention} for {member.mention}.",
                color=self.main_color,
            )
            await modmai_thread.wait_until_ready()
            await modmai_thread.channel.send(embed=embed)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await asyncio.gather(
            self.handle_reaction_events(payload),
            self.handle_contact_panel_events(reaction_payload=payload),
        )

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self.handle_reaction_events(payload)

    async def on_message_delete(self, message: discord.Message):
        """Support for deleting linked messages"""

        if message.is_system():
            return

        if isinstance(message.channel, discord.DMChannel):
            if message.author == self.user:
                return
            modmail_thread = await self.thread_manager.find(recipient=message.author)
            if not modmail_thread:
                return
            try:
                msg_payload = await modmail_thread.find_dm_message_payload(
                    message, deleted=True
                )
            except LinkMessageError as e:
                if not isinstance(e, (IgnoredMessage, ThreadMessageNotFound)):
                    logger.error("Failed to find linked message to delete: %s", e)
                return
            linked_msg = msg_payload.linked_message
            embed = linked_msg.embeds[0]
            embed.set_footer(
                text=f"{embed.footer.text} (deleted)", icon_url=embed.footer.icon_url
            )
            await linked_msg.edit(embed=embed)
            return

        if message.author != self.user:
            return

        modmail_thread = await self.thread_manager.find(channel=message.channel)
        if not modmail_thread:
            return

        try:
            await modmail_thread.delete_message(message, note=False)
            embed = discord.Embed(
                description="Successfully deleted message.", color=self.main_color
            )
        except LinkMessageError as e:
            if not isinstance(
                e, (IgnoredMessage, DMMessageNotFound, MalformedThreadMessage)
            ):
                logger.error("Failed to find linked message to delete: %s", e)
                embed = discord.Embed(
                    description="Failed to delete message.", color=self.error_color
                )
            else:
                return
        except discord.NotFound:
            return
        embed.set_footer(text=f"Message ID: {message.id} from {message.author}.")
        return await message.channel.send(embed=embed)

    async def on_bulk_message_delete(self, messages: Iterable[discord.Message]):
        await discord.utils.async_all(self.on_message_delete(msg) for msg in messages)

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.author.bot:
            return
        if before.content == after.content:
            return

        if isinstance(after.channel, discord.DMChannel):
            modmail_thread = await self.thread_manager.find(recipient=before.author)
            if not modmail_thread:
                return

            try:
                await modmail_thread.edit_dm_message(after, after.content)
            except LinkMessageError:
                _, blocked_emoji = await self.retrieve_emoji()
                await self.add_reaction(after, blocked_emoji)
            else:
                embed = discord.Embed(
                    description="Successfully edited message.", color=self.main_color
                )
                embed.set_footer(text=f"Message ID: {after.id}")
                await after.channel.send(embed=embed)

    async def on_guild_channel_delete(
        self,
        channel: Union[
            discord.CategoryChannel,
            discord.TextChannel,
            discord.abc.GuildChannel,
        ],
    ):
        if not isinstance(channel, (discord.CategoryChannel, discord.TextChannel)):
            return

        if channel.guild != self.modmail_guild:
            return

        if isinstance(channel, discord.CategoryChannel):
            if self.main_category == channel:
                logger.debug("Main category was deleted.")
                self.config.remove("main_category_id")
                await self.config.update()
            return

        if not isinstance(channel, discord.TextChannel):
            return

        if self.log_channel is None or self.log_channel == channel:
            logger.info("Log channel deleted.")
            self.config.remove("log_channel_id")
            await self.config.update()
            return

        audit_logs = self.modmail_guild.audit_logs(
            limit=10, action=discord.AuditLogAction.channel_delete
        )

        found_entry = False
        async for entry in audit_logs:
            if int(entry.target.id) == channel.id:
                found_entry = True
                break

        if not found_entry:
            logger.debug(
                "Cannot find the audit log entry for channel delete of %d.", channel.id
            )
            return

        mod = entry.user
        if mod == self.user:
            return

        modmail_thread = await self.thread_manager.find(channel=channel)
        if modmail_thread and modmail_thread.channel == channel:
            logger.debug("Manually closed channel %s.", channel.name)
            await modmail_thread.close(closer=mod, silent=True, delete_channel=False)

    async def on_member_join(self, member: discord.Member):
        if member.guild != self.guild:
            return
        modmail_thread = await self.thread_manager.find(recipient=member)
        if modmail_thread:
            embed = discord.Embed(
                description="The recipient has joined the server.", color=self.mod_color
            )
            await modmail_thread.channel.send(embed=embed)

    async def on_member_remove(self, member: discord.Member):
        if member.guild != self.guild:
            return
        modmail_thread = await self.thread_manager.find(recipient=member)
        if modmail_thread:
            if self.config["close_on_leave"]:
                await modmail_thread.close(
                    closer=member.guild.me,
                    close_message="The recipient has left the server.",
                    silent=True,
                )
            else:
                embed = discord.Embed(
                    description="The recipient has left the server.",
                    color=self.error_color,
                )
                await modmail_thread.channel.send(embed=embed)

    async def on_error(self, event_method: str, *args, **kwargs):
        logger.error("Ignoring exception in %s.", event_method)
        logger.error("Unexpected exception:", exc_info=sys.exc_info())

    async def on_command_error(
        self,
        context: commands.Context,
        exception: Exception,
        unhandled_by_cog: bool = False,
    ):
        if not unhandled_by_cog:
            if hasattr(context.command, "on_error"):
                return
            cog = context.cog
            if cog and cog.has_error_handler():
                return

        if isinstance(exception, commands.BadUnionArgument):
            msg = (
                "Could not find the specified "
                + human_join([c.__name__ for c in exception.converters])
                + "."
            )
            await context.typing()
            await context.send(
                embed=discord.Embed(color=self.error_color, description=msg)
            )

        elif isinstance(exception, commands.BadArgument):
            await context.typing()
            await context.send(
                embed=discord.Embed(color=self.error_color, description=str(exception))
            )
        elif isinstance(exception, commands.CommandNotFound):
            logger.warning("CommandNotFound: %s", exception)
        elif isinstance(exception, commands.MissingRequiredArgument):
            await context.send_help(context.command)
        elif isinstance(exception, commands.CommandOnCooldown):
            await context.send(
                embed=discord.Embed(
                    title="Command on cooldown",
                    description=f"Try again in {exception.retry_after:.2f} seconds",
                    color=self.error_color,
                )
            )
        elif isinstance(exception, commands.CheckFailure):
            for check in context.command.checks:
                if not await check(context):
                    if hasattr(check, "fail_msg"):
                        await context.send(
                            embed=discord.Embed(
                                color=self.error_color, description=check.fail_msg
                            )
                        )
                    if hasattr(check, "permission_level"):
                        corrected_permission_level = self.command_perm(
                            context.command.qualified_name
                        )
                        logger.warning(
                            "User %s does not have permission to use this command: `%s` (%s).",
                            context.author.name,
                            context.command.qualified_name,
                            corrected_permission_level.name,
                        )
            logger.warning("CheckFailure: %s", exception)
        elif isinstance(exception, commands.DisabledCommand):
            logger.info(
                "DisabledCommand: %s is trying to run eval but it's disabled",
                context.author.name,
            )
        else:
            logger.error("Unexpected exception:", exc_info=exception)

    # Auto leave unrecognized guild when the bot is on event: on_guild_join.
    async def on_guild_join(self, guild: discord.Guild):
        if guild not in self.registered_guilds:
            logger.warning(
                "The bot just joined unrecognized server. This may cause data compromise: %s.",
                guild.name,
            )
            await asyncio.sleep(15)
            logger.warning(
                "Leaving the server: %s.",
                guild.name,
            )
            await guild.leave()


def main():
    bot = ModmailBot()
    bot.run()


if __name__ == "__main__":
    main()
