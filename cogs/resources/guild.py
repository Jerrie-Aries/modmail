from __future__ import annotations

from typing import Union, TYPE_CHECKING

import discord

from core.timeutils import datetime_formatter

if TYPE_CHECKING:
    from discord import Guild

    from core.ext.commands import Context

verif = {
    "none": "0 - None",
    "low": "1 - Low",
    "medium": "2 - Medium",
    "high": "3 - High",
    "extreme": "4 - Extreme",
}

features = {
    "PARTNERED": "Partnered",
    "VERIFIED": "Verified",
    "DISCOVERABLE": "Server Discovery",
    "FEATURABLE": "Featurable",
    "COMMUNITY": "Community",
    "PUBLIC_DISABLED": "Public disabled",
    "INVITE_SPLASH": "Splash Invite",
    "VIP_REGIONS": "VIP Voice Servers",
    "VANITY_URL": "Vanity URL",
    "MORE_EMOJI": "More Emojis",
    "COMMERCE": "Commerce",
    "NEWS": "News Channels",
    "ANIMATED_ICON": "Animated Icon",
    "BANNER": "Banner Image",
    "MEMBER_LIST_DISABLED": "Member list disabled",
}


def _size(num: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"]:
        if abs(num) < 1024.0:
            return "{0:.1f}{1}".format(num, unit)
        num /= 1024.0
    return "{0:.1f}{1}".format(num, "YB")


def _bitsize(num: Union[int, float]) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB"]:
        if abs(num) < 1000.0:
            return "{0:.1f}{1}".format(num, unit)
        num /= 1000.0
    return "{0:.1f}{1}".format(num, "YB")


class GuildResource:
    def __init__(self, ctx: Context):
        self.ctx: Context = ctx
        self.guild: Guild = self.ctx.guild
        self.color: int = ctx.bot.main_color

    def guild_embed(self):
        """Create an embed containing the guild's information."""

        guild = self.guild

        bots = len([m for m in guild.members if m.bot])
        humans = len([m for m in guild.members if not m.bot])
        online = len([m for m in guild.members if m.status != discord.Status.offline])

        embed = discord.Embed(color=self.color)

        embed.set_author(name=f"{guild.name}")
        embed.description = f"Created {datetime_formatter.age(guild.created_at)} ago."

        embed.add_field(
            name=f"__Member Count:__",
            value=f"**Online** - {online}\n**Humans** - {humans}\n**Bots** - {bots}\n**All** - {guild.member_count}",
        )
        embed.add_field(
            name="__Channels:__",
            value=(
                f"**Category** - {len(guild.categories)}\n"
                f"**Text** - {len(guild.text_channels)}\n"
                f"**Voice** - {len(guild.voice_channels)}"
            ),
        )
        embed.add_field(name="__Roles:__", value=f"{len(guild.roles)}")
        embed.add_field(
            name="__Verification Level:__",
            value=f"{(verif[str(guild.verification_level)])}",
        )

        if guild.premium_tier != 0:
            nitro_boost = (
                f"**Tier {str(guild.premium_tier)} with {guild.premium_subscription_count} boosters**\n"
                f"**File size limit** - {_size(guild.filesize_limit)}\n"
                f"**Emoji limit** - {str(guild.emoji_limit)}\n"
                f"**VC's max bitrate** - {_bitsize(guild.bitrate_limit)}"
            )
            embed.add_field(name="__Nitro Boost:__", value=nitro_boost)

        embed.add_field(
            name="__Misc:__",
            value=(
                f"**AFK channel** - {str(guild.afk_channel) if guild.afk_channel else 'Not set'}\n"
                f"**AFK timeout** - {guild.afk_timeout}\n"
                f"**Custom emojis** - {len(guild.emojis)}"
            ),
            inline=False,
        )

        guild_features_list = [
            f"\N{WHITE HEAVY CHECK MARK} {name}"
            for feature, name in features.items()
            if feature in guild.features
        ]
        if guild_features_list:
            embed.add_field(
                name="__Server features:__", value="\n".join(guild_features_list)
            )

        embed.add_field(
            name="__Server Owner:__", value=guild.owner.mention, inline=False
        )

        if guild.splash:
            embed.set_image(url=str(guild.splash.replace(format="png").url))

        embed.set_thumbnail(url=str(guild.icon.url))
        embed.set_footer(text=f"Server ID: {guild.id}")

        return embed
