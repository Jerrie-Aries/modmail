from __future__ import annotations

import asyncio
import json
import os
import re
from copy import deepcopy
from typing import Any, Dict, ItemsView, Protocol, Optional, TypeVar, TYPE_CHECKING

import discord
import isodate
from discord.ext.commands import BadArgument
from dotenv import load_dotenv
from yarl import URL

from core._color_data import ALL_COLORS
from core.enums_ext import DMDisabled
from core.errors import InvalidConfigError
from core.logging_ext import getLogger
from core.models import Default
from core.timeutils import UserFriendlyTime
from core.utils import strtobool

if TYPE_CHECKING:
    from bot import ModmailBot

    from core.ext.commands import Context

    VT = TypeVar("VT")

    class _ItemsProtocol(Protocol):
        def items(self) -> ItemsView:
            ...


logger = getLogger(__name__)
load_dotenv()


class ConfigManager:

    public_keys = {
        # activity
        "twitch_url": "https://www.twitch.tv/discordmodmail/",
        # bot settings
        "main_category_id": None,
        "fallback_category_id": None,
        "prefix": "?",
        "mention": "@here",
        "main_color": str(discord.Color.gold()),
        "error_color": str(discord.Color.red()),
        "user_typing": False,
        "mod_typing": False,
        "account_age": isodate.Duration(),
        "guild_age": isodate.Duration(),
        "thread_cooldown": isodate.Duration(),
        "reply_without_command": False,
        "anon_reply_without_command": False,
        "plain_reply_without_command": False,
        # logging
        "log_channel_id": None,
        # threads
        "sent_emoji": "✅",
        "blocked_emoji": "🚫",
        "close_emoji": "🔒",
        "recipient_thread_close": False,
        "thread_auto_close_silently": False,
        "thread_auto_close": isodate.Duration(),
        "thread_auto_close_response": "This thread has been closed automatically due to inactivity after {timeout}.",
        "thread_creation_response": "The staff team will get back to you as soon as possible.",
        "thread_creation_footer": "Your message has been sent",
        "thread_contact_anonymously": False,
        "thread_self_closable_creation_footer": "Click the lock to close the thread",
        "thread_creation_title": "Thread Created",
        "thread_close_footer": "Replying will create a new thread",
        "thread_close_title": "Thread Closed",
        "thread_close_response": "{closer.mention} has closed this Modmail thread.",
        "thread_self_close_response": "You have closed this Modmail thread.",
        "thread_move_title": "Thread Moved",
        "thread_move_notify": False,
        "thread_move_notify_mods": False,
        "thread_move_response": "This thread has been moved.",
        "cooldown_thread_title": "Message not sent!",
        "cooldown_thread_response": "You must wait for {delta} before you can contact me again.",
        "disabled_new_thread_title": "Not Delivered",
        "disabled_new_thread_response": "We are not accepting new threads.",
        "disabled_new_thread_footer": "Please try again later...",
        "disabled_current_thread_title": "Not Delivered",
        "disabled_current_thread_response": "We are not accepting any messages.",
        "disabled_current_thread_footer": "Please try again later...",
        "close_on_leave": False,
        # moderation
        "recipient_color": str(discord.Color.gold()),
        "mod_color": str(discord.Color.green()),
        "mod_tag": None,
        # anonymous message
        "anon_username": None,
        "anon_avatar_url": None,
        "anon_tag": "Response",
        # react to contact
        "react_to_contact_message": None,
        "react_to_contact_emoji": "📩",
        # confirm thread creation
        "confirm_thread_creation": False,
        "confirm_thread_creation_title": "Confirm thread creation",
        "confirm_thread_response": "React to confirm thread creation which will directly contact the moderators.",
    }

    private_keys = {
        # bot presence
        "activity_message": "",
        "activity_type": None,
        "status": None,
        "dm_disabled": DMDisabled.NONE,
        "oauth_whitelist": [],
        # moderation
        "blocked": {},
        "blocked_whitelist": [],
        "command_permissions": {},
        "level_permissions": {},
        "override_command_level": {},
        # threads
        "snippets": {},
        "notification_squad": {},
        "subscriptions": {},
        "closures": {},
        # misc
        "plugins": [],
        "extensions": [],
        "aliases": {},
    }

    protected_keys = {
        # Modmail
        "registered_guild_ids": None,
        "modmail_guild_id": None,
        "guild_id": None,
        "log_url": "https://example.com/",
        "log_url_prefix": "/logs",
        "mongo_uri": None,
        "database_type": "mongodb",
        "connection_uri": None,  # replace mongo uri in the future
        "owners": None,
        # bot
        "token": None,
        "enable_plugins": True,
        "enable_eval": False,
        # github access token for private repositories
        "github_token": None,
        # Google Client
        "credentials_url": None,
        # Logging
        "log_level": "INFO",
    }

    colors = {"mod_color", "recipient_color", "main_color", "error_color"}

    time_deltas = {"account_age", "guild_age", "thread_auto_close", "thread_cooldown"}

    booleans = {
        "user_typing",
        "mod_typing",
        "reply_without_command",
        "anon_reply_without_command",
        "plain_reply_without_command",
        "recipient_thread_close",
        "thread_auto_close_silently",
        "thread_move_notify",
        "thread_move_notify_mods",
        "close_on_leave",
        "confirm_thread_creation",
        "enable_plugins",
        "enable_eval",
        "thread_contact_anonymously",
    }

    enums = {
        "dm_disabled": DMDisabled,
        "status": discord.Status,
        "activity_type": discord.ActivityType,
    }

    urls = {
        "twitch_url",
        "anon_avatar_url",
        "log_url",
    }

    defaults = {**public_keys, **private_keys, **protected_keys}
    all_keys = set(defaults.keys())

    def __init__(self, bot: ModmailBot):
        self.bot: ModmailBot = bot
        self._cache: Dict[str, Any] = {}
        self.ready_event: asyncio.Event = asyncio.Event()
        self.config_help: Dict[str, str] = {}

    def __repr__(self) -> str:
        return repr(self._cache)

    def populate_cache(self) -> Dict[str, Any]:
        data = deepcopy(self.defaults)

        # populate from env var and .env file
        data.update(
            {k.lower(): v for k, v in os.environ.items() if k.lower() in self.all_keys}
        )
        config_json = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"
        )
        if os.path.exists(config_json):
            logger.debug("Loading envs from config.json.")
            with open(config_json, "r", encoding="utf-8") as f:
                # Config json should override env vars
                try:
                    data.update(
                        {
                            k.lower(): v
                            for k, v in json.load(f).items()
                            if k.lower() in self.all_keys
                        }
                    )
                except json.JSONDecodeError:
                    logger.critical(
                        "Failed to load config.json env values.", exc_info=True
                    )
        self._cache = data

        config_help_json = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config_help.json"
        )
        with open(config_help_json, "r", encoding="utf-8") as f:
            self.config_help = dict(sorted(json.load(f).items()))

        return self._cache

    async def update(self) -> None:
        """Updates the config with data from the cache"""
        await self.bot.api.update_config(self.filter_default(self._cache))

    async def refresh(self) -> Dict[str, Any]:
        """Refreshes internal cache with data from database"""
        for k, v in (await self.bot.api.get_config()).items():
            k = k.lower()
            if k in self.all_keys:
                self._cache[k] = v
        if not self.ready_event.is_set():
            self.ready_event.set()
            logger.debug("Successfully fetched configurations from database.")
        return self._cache

    async def wait_until_ready(self) -> None:
        await self.ready_event.wait()

    def __setitem__(self, key: str, item: Any) -> None:
        key = key.lower()
        logger.info("Setting %s.", key)
        if key not in self.all_keys:
            raise InvalidConfigError(f'Configuration "{key}" is invalid.')
        self._cache[key] = item

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __delitem__(self, key: str) -> None:
        return self.remove(key)

    def get(self, key: str, convert=True) -> Any:
        key = key.lower()
        if key not in self.all_keys:
            raise InvalidConfigError(f'Configuration "{key}" is invalid.')
        if key not in self._cache:
            self._cache[key] = deepcopy(self.defaults[key])
        value = self._cache[key]

        if not convert:
            return value

        if key in self.colors:
            try:
                return int(value.lstrip("#"), base=16)
            except ValueError:
                logger.error("Invalid %s provided.", key)
            value = int(self.remove(key).lstrip("#"), base=16)

        elif key in self.time_deltas:
            if not isinstance(value, isodate.Duration):
                try:
                    value = isodate.parse_duration(value)
                except isodate.ISO8601Error:
                    logger.warning(
                        "The {account} age limit needs to be a "
                        'ISO-8601 duration formatted duration, not "%s".',
                        value,
                    )
                    value = self.remove(key)

        elif key in self.booleans:
            try:
                value = strtobool(value)
            except ValueError:
                value = self.remove(key)

        elif key in self.enums:
            if value is None:
                return None
            try:
                value = self.enums[key](value)
            except ValueError:
                logger.warning("Invalid %s %s.", key, value)
                value = self.remove(key)

        return value

    async def before_set(self, ctx: Context, key: str, value: VT) -> Optional[str, VT]:
        """
        A method for any additional coro task/check that must be done before setting up the value for
        `config set` command.
        """
        if key == "react_to_contact_message":
            try:
                message = await ctx.fetch_message(int(value))
            except ValueError:
                raise InvalidConfigError(f"Unable to convert `{value}` to int.")
            except discord.NotFound:
                raise InvalidConfigError(
                    f"Message ID `{value}` can't be found in this channel."
                )
            react_message_emoji = self.bot.config.get("react_to_contact_emoji")
            await self.bot.add_reaction(message, react_message_emoji)

        if key == "mention":
            ids = list(v for v in value.split(" "))
            if len(ids) == 1 and isinstance(ids[0], str) and ids[0] == "disable":
                value = None
            else:
                user_or_role = []
                for id in ids:
                    try:
                        id = int(id)
                    except ValueError:
                        raise InvalidConfigError(f'Unable to convert "{id}" to int.')
                    member = ctx.guild.get_member(id)
                    if member is not None:
                        user_or_role.append(member)
                        continue

                    role = ctx.guild.get_role(id)
                    if role is not None:
                        user_or_role.append(role)
                        continue

                    raise InvalidConfigError(f'Member or Role with ID "{id}" not found')
                value = " ".join(v.mention for v in user_or_role)

        return value

    def set(self, key: str, item: Any, convert=True) -> None:
        if not convert:
            return self.__setitem__(key, item)

        if key in self.colors:
            try:
                hex_ = str(item)
                if hex_.startswith("#"):
                    hex_ = hex_[1:]
                if len(hex_) == 3:
                    hex_ = "".join(s for s in hex_ for _ in range(2))
                if len(hex_) != 6:
                    raise InvalidConfigError("Invalid color name or hex.")
                try:
                    int(hex_, 16)
                except ValueError:
                    raise InvalidConfigError("Invalid color name or hex.")

            except InvalidConfigError:
                name = str(item).lower()
                name = re.sub(r"[\-+|. ]+", " ", name)
                hex_ = ALL_COLORS.get(name)
                if hex_ is None:
                    name = re.sub(r"[\-+|. ]+", "", name)
                    hex_ = ALL_COLORS.get(name)
                    if hex_ is None:
                        raise
            return self.__setitem__(key, "#" + hex_)

        if key in self.time_deltas:
            try:
                isodate.parse_duration(item)
            except isodate.ISO8601Error:
                try:
                    time = UserFriendlyTime().do_conversion(item)
                    if time.arg:
                        raise ValueError
                except BadArgument as exc:
                    raise InvalidConfigError(*exc.args)
                except Exception as e:
                    logger.debug(e)
                    raise InvalidConfigError(
                        "Unrecognized time, please use ISO-8601 duration format "
                        'string or a simpler "human readable" time.'
                    )
                item = isodate.duration_isoformat(time.difference())
            return self.__setitem__(key, item)

        if key in self.booleans:
            try:
                return self.__setitem__(key, strtobool(item))
            except ValueError:
                raise InvalidConfigError("Must be a yes/no value.")

        elif key in self.enums:
            if isinstance(item, (self.enums[key])):
                # value is an enum type
                item = item.value

        elif key in self.urls:
            url = URL(item)
            if url.scheme not in ("http", "https"):
                raise InvalidConfigError(
                    "Invalid url schema. URLs must start with either `http` or `https`."
                )

        return self.__setitem__(key, item)

    def remove(self, key: str) -> Any:
        key = key.lower()
        logger.info("Removing %s.", key)
        if key not in self.all_keys:
            raise InvalidConfigError(f'Configuration "{key}" is invalid.')
        if key in self._cache:
            del self._cache[key]
        self._cache[key] = deepcopy(self.defaults[key])
        return self._cache[key]

    def items(self) -> ItemsView[str, Any]:
        return self._cache.items()

    @classmethod
    def filter_valid(cls, data: _ItemsProtocol) -> Dict[str, Any]:
        return {
            k.lower(): v
            for k, v in data.items()
            if k.lower() in cls.public_keys or k.lower() in cls.private_keys
        }

    @classmethod
    def filter_default(cls, data: _ItemsProtocol) -> Dict[str, Any]:
        # TODO: use .get to prevent errors
        filtered = {}
        for k, v in data.items():
            default = cls.defaults.get(k.lower(), Default)
            if default is Default:
                logger.error("Unexpected configuration detected: %s.", k)
                continue
            if v != default:
                filtered[k.lower()] = v
        return filtered
