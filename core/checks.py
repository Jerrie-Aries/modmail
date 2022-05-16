from core.enums_ext import PermissionLevel
from core.ext import commands
from core.logging_ext import getLogger

logger = getLogger(__name__)


def has_permissions_predicate(
    permission_level: PermissionLevel = PermissionLevel.REGULAR,
):
    async def predicate(ctx: commands.Context):
        return await check_permissions(ctx, ctx.command.qualified_name)

    predicate.permission_level = permission_level
    return predicate


def has_permissions(permission_level: PermissionLevel = PermissionLevel.REGULAR):
    """
    A decorator that checks if the author has the required permissions.

    Parameters
    ----------
    permission_level : PermissionLevel
        The lowest level of permission needed to use this command.
        Defaults to REGULAR.

    Examples
    --------
    ::
        @has_permissions(PermissionLevel.OWNER)
        async def setup(ctx):
            print("Success")
    """

    return commands.check(has_permissions_predicate(permission_level))


async def check_permissions(ctx: commands.Context, command_name: str) -> bool:
    """Logic for checking permissions for a command for a user"""
    bot = ctx.bot
    author = ctx.author
    if await bot.is_owner(author):
        # Bot owner(s) (and creator) has absolute power over the bot
        return True

    permission_level = bot.command_perm(command_name)

    if permission_level is PermissionLevel.INVALID:
        logger.warning("Invalid permission level for command %s.", command_name)
        return True

    if (
        permission_level is not PermissionLevel.OWNER
        and ctx.channel.permissions_for(author).administrator
        and ctx.guild == bot.modmail_guild
    ):
        # Administrators have permission to all non-owner commands in the Modmail Guild
        logger.debug("Allowed due to administrator.")
        return True

    command_permissions = bot.config["command_permissions"]
    checkables = {*author.roles, author}

    if command_name in command_permissions:
        # -1 is for @everyone
        if -1 in command_permissions[command_name] or any(
            str(check.id) in command_permissions[command_name] for check in checkables
        ):
            return True

    level_permissions = bot.config["level_permissions"]

    for level in PermissionLevel:
        if level >= permission_level and level.name in level_permissions:
            # -1 is for @everyone
            if -1 in level_permissions[level.name] or any(
                str(check.id) in level_permissions[level.name] for check in checkables
            ):
                return True
    return False


async def user_has_permissions(
    ctx: commands.Context, permission_level=PermissionLevel.REGULAR
) -> bool:
    """
    Logic for checking global permissions for a user.

    This could be useful for checking user's permissions inside a command or on_message
    and any other events.

    Parameters
    ----------
    ctx : commands.Context
        The context object.
    permission_level: PermissionLevel
        Permission Level to check. Default to REGULAR.
    """
    bot = ctx.bot
    author = ctx.author
    if await bot.is_owner(author):
        return True

    if (
        ctx.channel.permissions_for(author).administrator
        and ctx.guild == bot.modmail_guild
    ):
        return True

    checkables = {*author.roles, author}

    level_permissions = bot.config["level_permissions"]

    for level in PermissionLevel:
        if level >= permission_level and level.name in level_permissions:
            # -1 is for @everyone
            if -1 in level_permissions[level.name] or any(
                str(check.id) in level_permissions[level.name] for check in checkables
            ):
                return True
    return False


def is_modmail_thread():
    """
    A decorator that checks if the command is being ran within a Modmail thread channel.
    """

    async def predicate(ctx: commands.Context) -> bool:
        """
        Parameters
        ----------
        ctx : commands.Context
            The current discord.py `Context`.

        Returns
        -------
        bool
            `True` if the current `Context` is within a Modmail thread channel.
            Otherwise, `False`.
        """
        return ctx.modmail_thread is not None

    predicate.fail_msg = "This is not a Modmail thread channel."
    return commands.check(predicate)
