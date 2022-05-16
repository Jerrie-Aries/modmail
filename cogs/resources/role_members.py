from __future__ import annotations

from typing import List, TYPE_CHECKING

import discord

from core.utils import plural

if TYPE_CHECKING:
    from discord import Role

    from core.ext.commands import Context


class RoleMembersResource:
    def __init__(self, ctx: Context, role: Role, member_mention: bool = False):
        self.ctx: Context = ctx
        self.role: Role = role
        self.member_mention: bool = member_mention

    def role_members_embed(self) -> List[discord.Embed]:
        """Create an embed containing the role members."""

        r = self.role

        member_list = r.members.copy()

        def base_embed(continued=False, description=None):
            embed = discord.Embed(
                description=description if description is not None else "",
                color=r.color,
            )

            embed.title = f"Members in {discord.utils.escape_markdown(r.name).title()}"
            if continued:
                embed.title += " (Continued)"

            embed.set_thumbnail(
                url=f"https://placehold.it/100/{str(r.color)[1:]}?text=+"
            )

            footer_text = f"Found {plural(len(member_list)):member}"
            embed.set_footer(text=footer_text)
            return embed

        embeds = [base_embed()]
        entries = 0

        if member_list:
            embed = embeds[0]

            for member in sorted(member_list, key=lambda m: m.name.lower()):
                line = (
                    (f"<@{member.id}>\n" if not member.nick else f"<@!{member.id}>\n")
                    if self.member_mention is True
                    else f"{member.name}#{member.discriminator}\n"
                )
                if entries == 25:
                    embed = base_embed(continued=True, description=line)
                    embeds.append(embed)
                    entries = 1
                else:
                    embed.description += line
                    entries += 1
        else:
            embeds[0].description = "Currently there are no members in that role."

        return embeds
