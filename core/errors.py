from discord import Color, Embed
from discord import DiscordException
from discord.ext.commands import BadArgument


# ## Config errors ## #
class InvalidConfigError(BadArgument):
    """
    Exception raised when any related config value conversion fails.
    """

    def __init__(self, msg: str, *args):
        super().__init__(msg, *args)
        self.msg = msg

    @property
    def embed(self) -> Embed:
        """
        Returns an embed of error message.
        """
        # Single reference of Color.red()
        return Embed(title="Error", description=self.msg, color=Color.red())


# ## Any thread related errors ## #
class ThreadError(DiscordException):
    """
    Base class for any thread exceptions.
    This could be caught to handle any exceptions thrown within the Thread instance.

    This class is inherited from :exc:`DiscordException`.
    """

    pass


class ThreadNotReady(ThreadError):
    """
    Exception raised when the thread is not ready.

    This class is inherited from :exc:`ThreadError`.
    """

    pass


class ThreadCancelled(ThreadError):
    """
    Exception raised when the thread is already cancelled.

    This class is inherited from :exc:`ThreadError`.
    """

    pass


class ThreadLogsNotFound(ThreadError):
    """
    Exception raised when the thread logs cannot be found in the database.

    This class is inherited from :exc:`ThreadError`.
    """

    pass


class LinkMessageError(ThreadError):
    """
    Base exception for any linking messages exceptions.

    This class is inherited from :exc:`ThreadError`.
    """

    pass


class MalformedThreadMessage(LinkMessageError):
    """
    Exception that is thrown if the thread message is malformed. Whether the message is Note, or Persistent Note,
    or not the actual linked thread messages.
    """

    pass


class ThreadMessageNotFound(LinkMessageError):
    """Exception that is thrown when the thread message cannot be found, or is not existed."""

    pass


class DMMessageNotFound(LinkMessageError):
    """Exception that is thrown when the DM message cannot be found, or is not existed."""

    pass


class IgnoredMessage(LinkMessageError):
    """Exception that is thrown when the provided message for linking is in ignored list."""

    pass


# ## Any plugin related errors ## #
class PluginError(BadArgument):
    """
    Base class for any plugin exception.
    This could be caught to handle any exceptions thrown within the Plugin instance.

    This class is inherited from :exc:`commands.BadArgument`.
    """

    pass


class InvalidPluginError(PluginError):
    """
    Exception raised when converting or instantiating the :class:`Plugin` from string fails.
    Usually when the provided argument is invalid in some way.
    """

    pass


class PluginVersionError(PluginError):
    """Exception raised when the bot's version is lower than the required version to run the plugin."""

    pass


class PluginUpdateError(PluginError):
    """Exception raised when updating plugin fails."""

    pass


class PluginDownloadError(PluginError):
    """Exception raised when downloading the plugin fails."""

    pass


class PluginLoadError(PluginError):
    """Exception raised when loading the plugin fails."""

    pass


class PluginUnloadError(PluginError):
    """Exception raised when unloading the plugin fails."""

    pass
