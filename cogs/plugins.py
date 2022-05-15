from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import sys
import zipfile
from difflib import get_close_matches
from importlib import invalidate_caches
from pathlib import Path, PurePath
from re import match
from site import USER_SITE
from subprocess import PIPE
from typing import Any, Dict, Optional, Set, Union, TYPE_CHECKING

import discord
from pkg_resources import parse_version

from core import checks
from core.ext import commands
from core.errors import (
    InvalidPluginError,
    PluginError,
    PluginVersionError,
    PluginUpdateError,
    PluginDownloadError,
    PluginLoadError,
    PluginUnloadError,
)
from core.enums_ext import PermissionLevel
from core.logging_ext import getLogger
from core.utils import truncate, trigger_typing
from core.views.paginator import EmbedPaginatorSession

if TYPE_CHECKING:
    from bot import ModmailBot

logger = getLogger(__name__)

MISSING = discord.utils.MISSING


class Plugin:
    """
    A class to manage Modmail plugin.

    Although all the keyword arguments are optional (default to `MISSING`), some of them are required
    to instantiate this class (e.g. `repo` and `name`), and :class:`InvalidPluginError` will be raised if
    they are not passed in.
    As for `user` parameter, it can be left out only if the plugin is "local" or "extension". Otherwise,
    it is also required.

    Parameters
    -----------
    bot : ModmailBot
        The Modmail bot. This parameter is required to access Modmail attributes within this class.
    user : str
        Optional. The name of the GitHub user. This parameter should be None if the plugin
        is "local" or "extension".
    repo : str
        Required. The name of the GitHub repository. Or "local" if local plugin, "extension" if extension plugin.
    name : str
        Required. Name of the plugin.
    branch : str
        Branch of the GitHub repository. Defaults to "master". This parameter is optional only if
        the `user` parameter is not None. Otherwise (plugin is "extension" or "local"), this should
        always be `None`.
    """

    local: bool
    extension: bool
    user: str
    repo: str
    branch: str
    url: str
    link: str

    def __init__(
        self,
        bot: ModmailBot,
        user: str = MISSING,
        repo: str = MISSING,
        name: str = MISSING,
        branch: str = MISSING,
    ):
        if not repo or not name:
            raise InvalidPluginError("`repo` and `name` parameters are required.")

        self.bot: ModmailBot = bot
        self.name: str = name
        self.local, self.extension = (repo == r for r in ("local", "extension"))

        if not user:
            if not self.local and not self.extension:
                raise InvalidPluginError(
                    "Plugin should be a local or extension if the `user` parameter is not passed in."
                )
            self.user = self.repo = self.branch = f"@{repo}"
            self.url = self.link = f"{self.repo}/{self.name}"
        else:
            self.user = user
            self.repo = repo
            self.branch = branch if branch else "master"
            self.url = (
                f"https://github.com/{self.user}/{self.repo}/archive/{self.branch}.zip"
            )
            self.link = f"https://github.com/{self.user}/{self.repo}/tree/{self.branch}/{self.name}"

        self.required_version: Optional[str] = None  # implemented in `convert`

    def __str__(self) -> str:
        if self.local or self.extension:
            return f"{self.repo}/{self.name}"
        return f"{self.user}/{self.repo}/{self.name}@{self.branch}"

    def __lt__(self, other: Plugin) -> bool:
        return self.name.lower() < other.name.lower()

    def __hash__(self):
        return hash((self.user, self.repo, self.name, self.branch))

    def __repr__(self) -> str:
        return f"<Plugins: {self.__str__()}>"

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Plugin) and self.__str__() == other.__str__()

    @property
    def path(self) -> PurePath:
        """
        Returns a :class:`PurePath` object of this plugin constructed from strings.
        """
        if self.local or self.extension:
            return PurePath("plugins") / self.repo / self.name
        return (
            PurePath("plugins") / self.user / self.repo / f"{self.name}_{self.branch}"
        )

    @property
    def abs_path(self) -> Path:
        """
        Returns an absolute path of this plugin.
        """
        return Path(__file__).absolute().parent.parent / self.path

    @property
    def cache_path(self) -> Path:
        """
        Returns the cache path of this plugin.
        """
        if self.local or self.extension:
            raise ValueError("No cache path for local or extension plugins!")
        return (
            Path(__file__).absolute().parent.parent
            / "temp"
            / "plugins-cache"
            / f"{self.user}-{self.repo}-{self.branch}.zip"
        )

    @property
    def ext_string(self) -> str:
        """
        Returns the extension string of this plugin. This string will be used to load the plugin (cog)
        using :meth:`bot.load_extension()`.
        """
        if self.local or self.extension:
            return f"plugins.{self.repo}.{self.name}.{self.name}"
        return f"plugins.{self.user}.{self.repo}.{self.name}_{self.branch}.{self.name}"

    @classmethod
    def from_string(
        cls, bot: ModmailBot, string: str, strict: bool = False
    ) -> "Plugin":
        """
        Instantiate this class from string. This method will do regex matching on the string
        to get the necessary keyword arguments to instantiate this class.

        Parameters
        -----------
        bot: ModmailBot
            The Modmail bot.
        string : str
            The plugin string. Usually this should be passed when executing the command, or retrieved
            from the database.
        strict : bool
            Whether to use strict method on regex matching for backward compatibility.
        """
        m = match(r"^@?(?P<repo>local|extension)/(?P<name>.+)$", string)
        if m is None:
            if not strict:
                m = match(
                    r"^(?P<user>.+?)/(?P<repo>.+?)/(?P<name>.+?)(?:@(?P<branch>.+?))?$",
                    string,
                )
            else:
                m = match(
                    r"^(?P<user>.+?)/(?P<repo>.+?)/(?P<name>.+?)@(?P<branch>.+?)$",
                    string,
                )

        if m is not None:
            return cls(bot, **m.groupdict())
        raise InvalidPluginError("Cannot decipher %s.", string)

    @classmethod
    async def convert(cls, ctx: commands.Context, plugin_name: str) -> "Plugin":
        """
        Converts a string into a :class:`Plugin` instance.

        Returns
        -------
        Plugin
            An instance of this class.
        """
        # using `bot.get_cog` instead of `ctx.cog`, so this converter can be used in other cogs as well
        cog: Plugins = ctx.bot.get_cog("Plugins")

        if cog and plugin_name in cog.registry:
            details = cog.registry[plugin_name]
            user, repo = details["repository"].split("/", maxsplit=1)
            branch = details.get("branch")

            plugin = cls(ctx.bot, user, repo, plugin_name, branch)
            plugin.required_version = details.get("bot_version")

        else:
            try:
                plugin = cls.from_string(ctx.bot, plugin_name)
            except InvalidPluginError:
                raise InvalidPluginError(
                    "Invalid plugin name, double check the plugin name "
                    "or use one of the following formats: "
                    "`username/repo/plugin-name`, `username/repo/plugin-name@branch`, `local/plugin-name`."
                )
        return plugin

    def is_compatible(self) -> bool:
        """
        Returns `True` if the bot version is compatible to use this plugin.
        Otherwise, `False`.
        """
        if self.required_version and self.bot.version < parse_version(
            self.required_version
        ):
            return False
        return True

    async def download(self, force: bool = False) -> None:
        """
        Downloads plugin from GitHub repository.
        If this method is used on "local" or "extension" plugins, :class:`InvalidPluginError` will be raised.

        Raises
        -------
        InvalidPluginError
            Plugin cannot be download or not found.
        PluginDownloadError
            Downloading the plugin fails.
        """
        if self.local or self.extension:
            raise InvalidPluginError(
                f"{self.repo.strip('@').title()} plugin cannot be dowloaded."
            )

        if self.abs_path.exists() and not force:
            return

        self.abs_path.mkdir(parents=True, exist_ok=True)

        if self.cache_path.exists() and not force:
            plugin_io = self.cache_path.open("rb")
            logger.debug("Loading cached %s.", self.cache_path)
        else:
            headers = {}
            github_token = self.bot.config["github_token"]
            if github_token is not None:
                headers["Authorization"] = f"token {github_token}"

            async with self.bot.session.get(self.url, headers=headers) as resp:
                logger.debug("Downloading %s.", self.url)
                raw = await resp.read()

                try:
                    raw = await resp.text()
                except UnicodeDecodeError:
                    pass
                else:
                    if raw == "Not Found":
                        raise InvalidPluginError("Plugin not found")
                    else:
                        raise PluginDownloadError(
                            "Invalid download received, non-bytes object"
                        )

            plugin_io = io.BytesIO(raw)
            if not self.cache_path.parent.exists():
                self.cache_path.parent.mkdir(parents=True)

            with self.cache_path.open("wb") as f:
                f.write(raw)

        with zipfile.ZipFile(plugin_io) as zipf:
            for info in zipf.infolist():
                path = PurePath(info.filename)
                if len(path.parts) >= 3 and path.parts[1] == self.name:
                    plugin_path = self.abs_path / Path(*path.parts[2:])
                    if info.is_dir():
                        plugin_path.mkdir(parents=True, exist_ok=True)
                    else:
                        plugin_path.parent.mkdir(parents=True, exist_ok=True)
                        with zipf.open(info) as src, plugin_path.open("wb") as dst:
                            shutil.copyfileobj(src, dst)

        plugin_io.close()

    async def load(self) -> None:
        """
        Loads the plugin extension.

        Raises
        ------
        InvalidPluginError
            The plugin file not found.
        PluginDownloadError
            Downloading the plugin requirements fails.
        PluginLoadError
            Loading the plugin fails.
        """
        if not (self.abs_path / f"{self.name}.py").exists():
            raise InvalidPluginError(f"Plugin file `{self.name}.py` not found.")

        req_txt = self.abs_path / "requirements.txt"

        if req_txt.exists():
            # Install PIP requirements

            venv = hasattr(sys, "real_prefix") or hasattr(
                sys, "base_prefix"
            )  # in a virtual env
            user_install = " --user" if not venv else ""
            proc = await asyncio.create_subprocess_shell(
                f'"{sys.executable}" -m pip install --upgrade{user_install} -r {req_txt} -q -q',
                stderr=PIPE,
                stdout=PIPE,
            )

            logger.debug("Downloading requirements for %s.", self.ext_string)

            stdout, stderr = await proc.communicate()

            if stdout:
                logger.debug("[stdout]\n%s.", stdout.decode())

            if stderr:
                logger.debug("[stderr]\n%s.", stderr.decode())
                logger.error(
                    "Failed to download requirements for %s.",
                    self.ext_string,
                    exc_info=True,
                )
                raise PluginDownloadError(
                    f"Unable to download requirements: ```\n{stderr.decode()}\n```"
                )

            if os.path.exists(USER_SITE):
                sys.path.insert(0, USER_SITE)

        try:
            await self.bot.load_extension(self.ext_string)
            logger.info("Loaded plugin: %s", self.ext_string.split(".")[-1])
        except commands.ExtensionError as exc:
            logger.error("Plugin load failure: %s", self.ext_string, exc_info=True)
            raise PluginLoadError(str(exc))

    async def unload(self) -> None:
        """
        Unloads the plugin extension from bot.

        Raises
        -------
        PluginUnloadError
            Unloading the plugin fails.
        """
        try:
            await self.bot.unload_extension(self.ext_string)
        except (commands.ExtensionNotLoaded, Exception) as exc:
            logger.error(f"{type(exc).__name__}: {str(exc)}")
            raise PluginUnloadError(str(exc))

    async def update(self) -> None:
        """
        Updates plugin. If the plugin is from GitHub repository, the plugin files will be redownloaded.
        This will also unload and reload the plugin extension.

        Raises
        ------
        InvalidPluginError
            Plugin is not installed (e.g. not in config).
        PluginVersionError
            The bot version is not compatible (i.e. too low) to use this plugin.
        PluginUpdateError
            Updating the plugin fails.
        """
        if str(self) not in (
            self.bot.config["plugins"] + self.bot.config["extensions"]
        ):
            raise InvalidPluginError("Plugin is not installed.")

        logger.debug("Updating plugin `%s`...", self.name)

        if not self.is_compatible():
            raise PluginVersionError(
                "Bot's version is too low. "
                f"This plugin requires version `{self.required_version}`."
            )

        if not any((self.local, self.extension)):
            await self.download(force=True)
        if self.bot.config.get("enable_plugins"):
            try:
                await self.unload()
            except PluginUnloadError:
                logger.warning("Plugin unload fail.", exc_info=True)
            try:
                await self.load()
            except Exception as exc:
                logger.error("Failed to update plugin: `%s`.", self.name)
                logger.error(f"{type(exc).__name__}: {str(exc)}")
                raise PluginUpdateError(str(exc))

        logger.debug("Successfully updated plugin `%s`.", self.name)


class Plugins(commands.Cog):
    """
    Plugins expand Modmail functionality by allowing third-party addons.
    """

    def __init__(self, bot: ModmailBot):
        """
        Parameters
        ----------
        bot : ModmailBot
            The Modmail bot.
        """
        self.bot: ModmailBot = bot
        self.registry: Dict[str, Dict[str, str]] = {}
        self.loaded_plugins: Set[Plugin] = set()
        self._ready_event: asyncio.Event = asyncio.Event()

    async def cog_load(self) -> None:
        await self.populate_registry()
        if self.bot.config.get("enable_plugins"):
            await self.initial_load_plugins()
        else:
            logger.info("Plugins not loaded since ENABLE_PLUGINS=false.")

    @property
    def ready_event(self) -> asyncio.Event:
        """
        An event manages a flag that can be set to `True` with the `.set()` method, and reset
        to `False` with the `.clear()` method.
        The `.wait()` method blocks until the flag is `True`. The flag is initially `False`.
        """
        return self._ready_event

    async def populate_registry(self) -> None:
        """
        Populates plugins registry. This will only run once when initializing the cog.
        """
        registry_json = (
            Path(__file__).absolute().parent.parent / "plugins" / "registry.json"
        )
        with open(registry_json, encoding="utf-8") as resp:
            self.registry = json.loads(resp.read())
            resp.close()

    async def initial_load_plugins(self) -> None:
        """
        Loads all plugins when initializing the cog.

        If exception occurs when loading the plugins, they will be removed from config.
        """
        update_db = False
        for plugin_name in list(
            self.bot.config["extensions"] + self.bot.config["plugins"]
        ):
            try:
                plugin = Plugin.from_string(self.bot, plugin_name, strict=True)
            except InvalidPluginError:
                extension = plugin_name in self.bot.config["extensions"]
                self.remove_from_config(plugin_name, extension)
                update_db = True
                try:
                    # For backwards compat
                    plugin = Plugin.from_string(self.bot, plugin_name)
                except InvalidPluginError:
                    logger.error(
                        "Failed to parse plugin name: %s.", plugin_name, exc_info=True
                    )
                    logger.error("Plugin removed from config.")
                    continue

                logger.info(
                    "Migrated legacy plugin name: %s, now %s.", plugin_name, str(plugin)
                )
                self.save_to_config(str(plugin), extension)

            # now we have the Plugin instance,
            # and its attributes/properties/methods now can be accessed
            try:
                if not any((plugin.local, plugin.extension)):
                    await plugin.download()
                await plugin.load()
            except (PluginError, Exception) as exc:
                logger.error("Error when loading plugin: %s.", plugin)
                logger.error(f"{type(exc).__name__}: {str(exc)}")
                self.remove_from_config(
                    str(plugin), True if plugin.extension else False
                )
                update_db = True
                logger.error("Plugin removed from config.")
                continue
            self.loaded_plugins.add(plugin)

        logger.debug("Finished loading all plugins.")

        self.bot.dispatch("plugins_ready")

        self._ready_event.set()
        if update_db:
            await self.bot.config.update()

    def save_to_config(self, plugin_name: str, extension: bool = False) -> None:
        """
        Saves the plugin to the database.

        Parameters
        -----------
        plugin_name : str
            The formatted string of the plugin. See `Plugin.__str__()`.
        extension : bool
            True if extension plugin.
        """
        if extension:
            self.bot.config["extensions"].append(plugin_name)
        else:
            self.bot.config["plugins"].append(plugin_name)

    def remove_from_config(self, plugin_name: str, extension: bool = False) -> None:
        """
        Removes the plugin from the database.

        Parameters
        -----------
        plugin_name : str
            The formatted string of the plugin. See `Plugin.__str__()`.
        extension : bool
            True if extension plugin.
        """
        if extension:
            self.bot.config["extensions"].remove(plugin_name)
        else:
            self.bot.config["plugins"].remove(plugin_name)

    @commands.group(aliases=["plugin"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def plugins(self, ctx: commands.Context):
        """
        Manage plugins for Modmail.
        """
        await ctx.send_help(ctx.command)

    @plugins.command(name="add", aliases=["install", "load"])
    @checks.has_permissions(PermissionLevel.OWNER)
    @trigger_typing
    async def plugins_add(self, ctx: commands.Context, *, plugin: Plugin):
        """
        Install a new plugin for the bot.

        `plugin_name` can be:
        - The name of the plugin found in `{prefix}plugin registry`.
        - A direct reference to a GitHub hosted plugin (in the format `user/repo/name[@branch]`).
        - `local/name` for local plugins.
        """

        if not self.bot.config["enable_plugins"]:
            raise commands.BadArgument(
                "Plugins are disabled, enable them by setting `ENABLE_PLUGINS=true`"
            )

        if not self.ready_event.is_set():
            raise commands.BadArgument(
                "Plugins are still loading, please try again later."
            )

        if str(plugin) in (self.bot.config["plugins"] + self.bot.config["extensions"]):
            raise commands.BadArgument("This plugin is already installed.")

        if plugin.name in self.bot.cogs:
            # another class with the same name
            raise commands.BadArgument("Cannot install this plugin (dupe cog name).")

        if not plugin.is_compatible():
            raise PluginVersionError(
                "Bot's version is too low. "
                f"This plugin requires version `{plugin.required_version}`."
            )

        embed = discord.Embed(color=self.bot.main_color)
        if plugin.local or plugin.extension:
            embed.description = f"Starting to load {plugin.repo.strip('@')} plugin from {plugin.link}..."
        else:
            embed.description = f"Starting to download plugin from {plugin.link}..."
        msg = await ctx.send(embed=embed)

        if not any((plugin.local, plugin.extension)):
            try:
                await plugin.download(True)
            except (PluginError, Exception) as e:
                logger.warning("Unable to download plugin %s.", plugin)

                embed = discord.Embed(
                    description=(
                        f"Failed to download plugin `{plugin.name}`, check logs for error.\n"
                        f"\n{type(e).__name__}:\n```py\n{str(e)}\n```"
                    ),
                    color=self.bot.error_color,
                )

                return await msg.edit(embed=embed)

        if self.bot.config.get("enable_plugins"):

            invalidate_caches()

            try:
                await plugin.load()
            except (PluginError, Exception) as e:
                logger.warning("Unable to load plugin %s.", plugin)

                embed = discord.Embed(
                    description=(
                        f"Failed to load plugin `{plugin.name}`, check logs for error.\n"
                        f"\n{type(e).__name__}:\n```py\n{str(e)}\n```"
                    ),
                    color=self.bot.error_color,
                )
                return await msg.edit(embed=embed)

            else:
                self.loaded_plugins.add(plugin)
                embed = discord.Embed(
                    description="Successfully installed plugin.\n"
                    "*Friendly reminder, plugins have absolute control over your bot. "
                    "Please only install plugins from developers you trust.*",
                    color=self.bot.main_color,
                )
        else:
            embed = discord.Embed(
                description="Successfully installed plugin.\n"
                "*Friendly reminder, plugins have absolute control over your bot. "
                "Please only install plugins from developers you trust.*\n\n"
                "This plugin is currently not enabled due to `ENABLE_PLUGINS=false`, "
                "to re-enable plugins, remove or change `ENABLE_PLUGINS=true` and restart your bot.",
                color=self.bot.main_color,
            )

        self.save_to_config(str(plugin), True if plugin.extension else False)
        await self.bot.config.update()
        return await msg.edit(embed=embed)

    @plugins.command(name="remove", aliases=["del", "delete"])
    @checks.has_permissions(PermissionLevel.OWNER)
    async def plugins_remove(self, ctx: commands.Context, *, plugin: Plugin):
        """
        Remove an installed plugin of the bot.

        `plugin_name` can be:
        - The name of the plugin found in `{prefix}plugin registry`.
        - A direct reference to a GitHub hosted plugin (in the format `user/repo/name[@branch]`).
        - `local/name` for local plugins.
        """

        if not self.ready_event.is_set():
            raise commands.BadArgument(
                "Plugins are still loading, please try again later."
            )

        if str(plugin) not in (
            self.bot.config["plugins"] + self.bot.config["extensions"]
        ):
            raise commands.BadArgument("Plugin is not installed.")

        if self.bot.config.get("enable_plugins"):
            try:
                plugin.unload()
                self.loaded_plugins.remove(plugin)
            except (PluginUnloadError, KeyError):
                logger.warning("Plugin was never loaded.")

        self.remove_from_config(str(plugin), True if plugin.extension else False)
        await self.bot.config.update()

        if not any((plugin.local, plugin.extension)):
            shutil.rmtree(
                plugin.abs_path,
                onerror=lambda *args: logger.warning(
                    "Failed to remove plugin files %s: %s", plugin, str(args[2])
                ),
            )
            try:
                plugin.abs_path.parent.rmdir()
                plugin.abs_path.parent.parent.rmdir()
            except OSError:
                pass  # dir not empty

        embed = discord.Embed(
            description="The plugin is successfully uninstalled.",
            color=self.bot.main_color,
        )
        await ctx.send(embed=embed)

    @plugins.group(name="update", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def plugins_update(self, ctx: commands.Context, *, plugin: Plugin):
        """
        Update a plugin for the bot.

        `plugin_name` can be:
        - The name of the plugin found in `{prefix}plugin registry`.
        - A direct reference to a GitHub hosted plugin (in the format `user/repo/name[@branch]`).
        - `local/name` for local plugins.

        To update all plugins, do `{prefix}plugins update all`.

        __**Note:**__
        - If exception occurs when updating or loading the plugin, it will be removed from config.
        """

        if not self.ready_event.is_set():
            raise commands.BadArgument(
                "Plugins are still loading, please try again later."
            )

        async with ctx.typing():
            try:
                await plugin.update()
            except (InvalidPluginError, PluginVersionError) as exc:
                raise commands.BadArgument(str(exc))
            except Exception as exc:
                self.remove_from_config(
                    str(plugin), True if plugin.extension else False
                )
                await self.bot.config.update()
                if plugin in self.loaded_plugins:
                    self.loaded_plugins.remove(plugin)
                raise commands.BadArgument(
                    f"Failed to update plugin `{plugin.name}`.\n"
                    f"\n{type(exc).__name__}:\n```py\n{str(exc)}\n```"
                    f"This plugin will now be removed from your bot."
                )

        if self.bot.config.get("enable_plugins"):
            self.loaded_plugins.add(
                plugin
            )  # has no effect if the plugin is already present

        embed = discord.Embed(
            description=f"Successfully updated `{plugin.name}`.",
            color=self.bot.main_color,
        )
        await ctx.send(embed=embed)

    @plugins_update.command(name="all")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def plugins_update_all(self, ctx: commands.Context):
        """
        Update all plugins for the bot.
        """

        if not self.ready_event.is_set():
            raise commands.BadArgument(
                "Plugins are still loading, please try again later."
            )

        success = []
        fails = []
        for plugin_name in list(
            self.bot.config["plugins"] + self.bot.config["extensions"]
        ):
            try:
                plugin = Plugin.from_string(self.bot, plugin_name)
                await plugin.update()
            except Exception:
                fails.append(plugin_name)
                continue
            else:
                success.append(plugin.name)

        embed = discord.Embed(title="Plugin updates", color=self.bot.main_color)
        if success:
            success = "\n".join(
                f"{i}. `{name}`" for i, name in enumerate(success, start=1)
            )
            embed.add_field(name="Success", value=success)
        if fails:
            fails = "\n".join(f"{i}. `{name}`" for i, name in enumerate(fails, start=1))
            embed.add_field(name="Failed", value=fails)

        await ctx.send(embed=embed)

    @plugins.command(name="reset")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def plugins_reset(self, ctx: commands.Context):
        """
        Reset all plugins for the bot.

        Deletes all cache and plugins from config and unloads from the bot.

        __**Note:**__
        - This will also unload all extensions and local plugins from the bot.
        """

        if not self.ready_event.is_set():
            raise commands.BadArgument(
                "Plugins are still loading, please try again later."
            )

        logger.warning("Purging plugins...")
        for ext in list(self.bot.extensions):
            if not ext.startswith("plugins."):
                continue
            try:
                logger.error("Unloading plugin: %s.", ext)
                await self.bot.unload_extension(ext)
            except Exception:
                logger.error("Failed to unload plugin: %s.", ext)
            else:
                if not self.loaded_plugins:
                    continue
                plugin = next(
                    (p for p in self.loaded_plugins if p.ext_string == ext), None
                )
                if plugin:
                    self.loaded_plugins.remove(plugin)

        self.bot.config["plugins"].clear()
        self.bot.config["extensions"].clear()

        await self.bot.config.update()

        root_path = Path(__file__).absolute().parent.parent
        cache_path = root_path / "temp" / "plugins-cache"
        if cache_path.exists():
            logger.warning("Removing cache path.")
            shutil.rmtree(cache_path)

        plugins_path = root_path / "plugins"
        for entry in plugins_path.iterdir():
            if entry.is_dir() and entry.name not in ("@local", "@extension"):
                shutil.rmtree(entry)
                logger.warning("Removing folder `%s`.", entry.name)

        embed = discord.Embed(
            description="Successfully purged all plugins from the bot.",
            color=self.bot.main_color,
        )
        return await ctx.send(embed=embed)

    @plugins.command(name="loaded", aliases=["enabled", "installed"])
    @checks.has_permissions(PermissionLevel.OWNER)
    async def plugins_loaded(self, ctx: commands.Context):
        """
        Show a list of currently loaded plugins.
        """

        if not self.bot.config.get("enable_plugins"):
            raise commands.BadArgument(
                "No plugins are loaded due to `ENABLE_PLUGINS=false`, "
                "to re-enable plugins, remove or set `ENABLE_PLUGINS=true` and restart your bot."
            )

        if not self._ready_event.is_set():
            raise commands.BadArgument(
                "Plugins are still loading, please try again later."
            )

        if not self.loaded_plugins:
            raise commands.BadArgument("There are no plugins currently loaded.")

        loaded_plugins = map(str, sorted(self.loaded_plugins))
        pages = ["```\n"]
        for plugin in loaded_plugins:
            msg = str(plugin) + "\n"
            if len(msg) + len(pages[-1]) + 3 <= 2048:
                pages[-1] += msg
            else:
                pages[-1] += "```"
                pages.append(f"```\n{msg}")

        if pages[-1][-3:] != "```":
            pages[-1] += "```"

        embeds = []
        for page in pages:
            embed = discord.Embed(
                title="Loaded plugins:", description=page, color=self.bot.main_color
            )
            embeds.append(embed)
        paginator = EmbedPaginatorSession(ctx, *embeds)
        await paginator.run()

    @plugins.group(
        name="registry", aliases=["list", "info"], invoke_without_command=True
    )
    @checks.has_permissions(PermissionLevel.OWNER)
    async def plugins_registry(
        self, ctx: commands.Context, *, plugin_name: Union[int, str] = None
    ):
        """
        Shows a list of all approved plugins.

        __**Usage:**__
        `{prefix}plugin registry` - Details about all plugins.
        `{prefix}plugin registry plugin-name` - Details about the indicated plugin.
        `{prefix}plugin registry page-number` - Jump to a page in the registry.
        """
        embeds = []

        registry = sorted(self.registry.items(), key=lambda elem: elem[0])

        if isinstance(plugin_name, int):
            index = plugin_name - 1
            if index < 0:
                index = 0
            if index >= len(registry):
                index = len(registry) - 1
        else:
            index = next(
                (i for i, (n, _) in enumerate(registry) if plugin_name == n), 0
            )

        if not index and plugin_name is not None:
            embed = discord.Embed(
                color=self.bot.error_color,
                description=f'Could not find a plugin with name "{plugin_name}" within the registry.',
            )

            matches = get_close_matches(plugin_name, self.registry.keys())

            if matches:
                embed.add_field(
                    name="Perhaps you meant:",
                    value="\n".join(f"`{m}`" for m in matches),
                )

            return await ctx.send(embed=embed)

        for name, details in registry:
            details = self.registry[name]
            user, repo = details["repository"].split("/", maxsplit=1)
            branch = details.get("branch")

            plugin = Plugin(user, repo, name, branch)

            embed = discord.Embed(
                color=self.bot.main_color,
                description=details["description"],
                url=plugin.link,
                title=details["repository"],
            )

            embed.add_field(
                name="Installation", value=f"```{self.bot.prefix}plugins add {name}```"
            )

            embed.set_author(
                name=details["title"], icon_url=details.get("icon_url"), url=plugin.link
            )

            if details.get("thumbnail_url"):
                embed.set_thumbnail(url=details.get("thumbnail_url"))

            if details.get("image_url"):
                embed.set_image(url=details.get("image_url"))

            if plugin in self.loaded_plugins:
                embed.set_footer(text="This plugin is currently loaded.")
            else:
                required_version = details.get("bot_version", False)
                if required_version and self.bot.version < parse_version(
                    required_version
                ):
                    embed.set_footer(
                        text="Your bot is unable to install this plugin, "
                        f"minimum required version is v{required_version}."
                    )
                else:
                    embed.set_footer(text="Your bot is able to install this plugin.")

            embeds.append(embed)

        paginator = EmbedPaginatorSession(ctx, *embeds)
        paginator.current = index
        await paginator.run()

    @plugins_registry.command(name="compact", aliases=["slim"])
    @checks.has_permissions(PermissionLevel.OWNER)
    async def plugins_registry_compact(self, ctx: commands.Context):
        """
        Shows a compact view of all plugins within the registry.
        """
        registry = sorted(self.registry.items(), key=lambda elem: elem[0])

        pages = [""]

        for plugin_name, details in registry:
            details = self.registry[plugin_name]
            user, repo = details["repository"].split("/", maxsplit=1)
            branch = details.get("branch")

            plugin = Plugin(user, repo, plugin_name, branch)

            desc = discord.utils.escape_markdown(
                details["description"].replace("\n", "")
            )

            name = f"[`{plugin.name}`]({plugin.link})"
            fmt = f"{name} - {desc}"

            if plugin_name in self.loaded_plugins:
                limit = 75 - len(plugin_name) - 4 - 8 + len(name)
                if limit < 0:
                    fmt = plugin.name
                    limit = 75
                fmt = truncate(fmt, limit) + "[loaded]\n"
            else:
                limit = 75 - len(plugin_name) - 4 + len(name)
                if limit < 0:
                    fmt = plugin.name
                    limit = 75
                fmt = truncate(fmt, limit) + "\n"

            if len(fmt) + len(pages[-1]) <= 2048:
                pages[-1] += fmt
            else:
                pages.append(fmt)

        embeds = []

        for page in pages:
            embed = discord.Embed(color=self.bot.main_color, description=page)
            embed.set_author(
                name="Plugin Registry", icon_url=self.bot.user.display_avatar.url
            )
            embeds.append(embed)

        paginator = EmbedPaginatorSession(ctx, *embeds)
        await paginator.run()


async def setup(bot):
    await bot.add_cog(Plugins(bot))
