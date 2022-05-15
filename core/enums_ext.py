from enum import Enum, IntEnum
from typing import Iterator

from discord import ButtonStyle


class PermissionLevel(IntEnum):
    """
    Represents the enum class of permission level system.

    Replaced the legacy guild-based permissions (i.e., manage channels, manage messages).
    This system enables you to customize your desired permission level specific to a command
    or a group of commands for a role or user.

    There are five valid permission levels:
        - OWNER [5]
        - ADMINISTRATOR [4]
        - MODERATOR [3]
        - SUPPORTER [2]
        - REGULAR [1]

    By default, all newly set up Modmail will have `OWNER` set to the owner of the bot, and `REGULAR`
    set to @everyone.

    Every commands should have one of these permission levels wrapped on them. Otherwise, the level INVALID [-1]
    will automatically be set to those without the permission level wrapper.

    Example of usage in command's wrapper:
        @checks.has_permissions(PermissionLevel.OWNER)
    """

    OWNER = 5
    ADMINISTRATOR = 4
    ADMIN = 4
    MODERATOR = 3
    MOD = 3
    SUPPORTER = 2
    RESPONDER = 2
    REGULAR = 1
    INVALID = -1


class DMDisabled(IntEnum):
    """
    Level of Modmail's DM disabilities.

    There are currently three levels of DM disabilities:
        - NONE - Modmail is accepting all DM messages.
        - NEW_THREADS - Modmail is not creating new threads.
        - ALL_THREADS - Modmail is not accepting any DM messages for new and existing threads.
    """

    NONE = 0
    NEW_THREADS = 1
    ALL_THREADS = 2


class HostingMethod(IntEnum):
    """
    Type of hosting method.
    """

    HEROKU = 0
    PM2 = 1
    SYSTEMD = 2
    DOCKER = 3
    REPL = 4
    SCREEN = 5
    OTHER = 6


class ThreadMessageType(Enum):
    """
    This class represents the types of thread messages.
    Any thread messages created should be one of these types.
    """

    NORMAL = "thread_message"
    ANONYMOUS = "anonymous"
    SYSTEM = "system"
    INTERNAL = "internal"
    INVALID = "invalid"

    @classmethod
    def from_value(cls, value: str) -> "ThreadMessageType":
        """
        Instantiate this class from string that match the value of enum member.

        If the value doesn't match the value of any enum member, ThreadMessageType.INVALID
        will be returned.
        """
        try:
            return cls(value)
        except ValueError:
            return cls.INVALID

    @property
    def value(self) -> str:
        """The value of the Enum member."""
        return self._value_


class PaginatorButtonItem(Enum):
    """
    Enum class for paginator buttons. This contains five items for paginator view which
    are (in order):
        - FIRST
        - BACK
        - CLOSE
        - NEXT
        - LAST

    Note, the name of each item here must reflect their callback action in the View class
    after prefixed with `_action_` (e.g. `_action_first`, `_action_back`, ..., etc).

    The value of each enum would be a tuple of button label, emoji, and button style.
    """

    FIRST = ("≪", "⏮️", ButtonStyle.grey)
    BACK = ("<", "◀️", ButtonStyle.blurple)
    CLOSE = ("✕", "🛑", ButtonStyle.red)
    NEXT = (">", "▶️", ButtonStyle.blurple)
    LAST = ("≫", "⏭️", ButtonStyle.grey)

    def __iter__(self) -> Iterator["PaginatorButtonItem"]:
        """
        Returns members in definition order.

        Note:
            This is copied exactly from :class:`EnumMeta`.
        """
        return (self._member_map_[name] for name in self._member_names_)

    @property
    def label(self) -> str:
        """
        Return the item label name.
        """
        return self.value[0]

    @property
    def emoji(self) -> str:
        """
        Return the emoji of this item.
        """
        return self.value[1]

    @property
    def style(self) -> ButtonStyle:
        """
        Return :class:`ButtonStyle` of this item.
        """
        return self.value[2]
