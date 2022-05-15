from __future__ import annotations

import asyncio
import re
from pathlib import Path
from subprocess import PIPE
from typing import Dict, List, TYPE_CHECKING

from discord import Embed

from core.logging_ext import getLogger
from core.utils import truncate

if TYPE_CHECKING:
    from bot import ModmailBot


logger = getLogger(__name__)


class Version:
    """
    This class represents a single version of Modmail.

    Parameters
    ----------
    bot : ModmailBot
        The Modmail bot.
    version : str
        The version string (ie. "v2.12.0").
    lines : str
        The lines of changelog messages for this version.

    Attributes
    ----------
    bot : ModmailBot
        The Modmail bot.
    version : str
        The version string (ie. "v2.12.0").
    lines : str
        A list of lines of changelog messages for this version.
    fields : Dict[str, str]
        A dict of fields separated by "Fixed", "Changed", etc sections.
    description : str
        General description of the version.

    Class Attributes
    ----------------
    ACTION_REGEX : re.Pattern[str]
        The regex used to parse the actions.
    DESCRIPTION_REGEX: re.Pattern[str]
        The regex used to parse the description.
    """

    ACTION_REGEX: re.Pattern[str] = re.compile(
        r"###\s*(.+?)\s*\n(.*?)(?=###\s*.+?|$)", flags=re.DOTALL
    )
    DESCRIPTION_REGEX: re.Pattern[str] = re.compile(
        r"^(.*?)(?=###\s*.+?|$)", flags=re.DOTALL
    )

    def __init__(self, bot: ModmailBot, branch: str, version: str, lines: str):
        self.bot: ModmailBot = bot
        self.version: str = version.lstrip("vV")
        self.lines: str = lines.strip()
        self.fields: Dict[str, str] = {}
        self.changelog_url: str = (
            f"https://github.com/kyb3r/modmail/blob/{branch}/CHANGELOG.md"
        )
        self.description: str = ""
        self.parse()

    def __repr__(self) -> str:
        return f'Version(v{self.version}, description="{self.description}")'

    def parse(self) -> None:
        """
        Parse the lines and split them into `description` and `fields`.
        """
        self.description = self.DESCRIPTION_REGEX.match(self.lines)
        self.description = (
            self.description.group(1).strip() if self.description is not None else ""
        )

        matches = self.ACTION_REGEX.finditer(self.lines)
        for m in matches:
            try:
                self.fields[m.group(1).strip()] = m.group(2).strip()
            except AttributeError:
                logger.error(
                    "Something went wrong when parsing the changelog for version %s.",
                    self.version,
                    exc_info=True,
                )

    @property
    def url(self) -> str:
        return f"{self.changelog_url}#v{self.version[::2]}"

    @property
    def embed(self) -> Embed:
        """
        Embed: the formatted `Embed` of this `Version`.
        """
        embed = Embed(color=self.bot.main_color, description=self.description)
        embed.set_author(
            name=f"v{self.version} - Changelog",
            icon_url=self.bot.user.display_avatar.url,
            url=self.url,
        )

        for name, value in self.fields.items():
            embed.add_field(name=name, value=truncate(value, 1024))
        embed.set_footer(text=f"Current version: v{self.bot.version}")
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        return embed


class Changelog:
    """
    This class represents the complete changelog of Modmail.

    Parameters
    ----------
    bot : ModmailBot
        The Modmail bot.
    text : str
        The complete changelog text.

    Attributes
    ----------
    bot : ModmailBot
        The Modmail bot.
    text : str
        The complete changelog text.
    versions : List[Version]
        A list of `Version`'s within the changelog.

    Class Attributes
    ----------------
    VERSION_REGEX : re.Pattern[str]
        The regex used to parse the versions.
    """

    VERSION_REGEX: re.Pattern[str] = re.compile(
        r"#\s*([vV]\d+\.\d+(?:\.\d+)?(?:-\w+?)?)\s+(.*?)(?=#\s*[vV]\d+\.\d+(?:\.\d+)(?:-\w+?)?|$)",
        flags=re.DOTALL,
    )

    def __init__(self, bot: ModmailBot, branch: str, text: str):
        self.bot: ModmailBot = bot
        self.text: str = text
        self.versions: List[Version] = [
            Version(bot, branch, *m) for m in self.VERSION_REGEX.findall(text)
        ]

    @property
    def latest_version(self) -> Version:
        """
        Version: The latest `Version` of the `Changelog`.
        """
        return self.versions[0]

    @property
    def embeds(self) -> List[Embed]:
        """
        List[Embed]: A list of `Embed`'s for each of the `Version`.
        """
        return [v.embed for v in self.versions]

    @classmethod
    async def from_url(cls, bot: ModmailBot, url: str = "") -> "Changelog":
        """
        Create a `Changelog` from a URL.

        Parameters
        ----------
        bot : ModmailBot
            The Modmail bot.
        url : str
            The URL to the changelog.

        Returns
        -------
        Changelog
            The newly created `Changelog` parsed from the `url`.
        """
        # get branch via git cli if available
        proc = await asyncio.create_subprocess_shell(
            "git branch --show-current",
            stderr=PIPE,
            stdout=PIPE,
        )
        err = await proc.stderr.read()
        err = err.decode("utf-8").rstrip()
        res = await proc.stdout.read()
        branch = res.decode("utf-8").rstrip()
        if not branch or err:
            branch = "master" if not bot.version.is_prerelease else "development"

        if branch not in ("master", "development"):
            branch = "master"

        url = (
            url
            or f"https://raw.githubusercontent.com/kyb3r/modmail/{branch}/CHANGELOG.md"
        )
        logger.debug("Fetching changelog from GitHub.")

        async with await bot.session.get(url) as resp:
            return cls(bot, branch, await resp.text())

    @classmethod
    async def from_file(cls, bot: ModmailBot, file_directory: str = "") -> "Changelog":
        """
        Create a `Changelog` from a file.

        Parameters
        ----------
        bot : ModmailBot
            The Modmail bot.
        file_directory : str
            The directory to the changelog `file`.

        Returns
        -------
        Changelog
            The newly created `Changelog` parsed from the `file`.
        """
        changelog_md = Path(__file__).absolute().parent.parent / "CHANGELOG.md"
        branch = "master" if not bot.version.is_prerelease else "development"
        file_directory = file_directory or changelog_md

        with open(file_directory) as resp:
            return cls(bot, branch, resp.read())
