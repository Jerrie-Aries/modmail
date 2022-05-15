from __future__ import annotations

from typing import List, TYPE_CHECKING

import discord

from core.utils import plural

if TYPE_CHECKING:
    from discord import Guild, Role

    from core.ext.commands import Context


class AllRolesResource:
    def __init__(self, ctx: Context):
        self.ctx: Context = ctx
        self.guild: Guild = self.ctx.guild
        self.all_roles: List[Role] = self.ctx.guild.roles  # @everyone included

    def all_roles_embed(self) -> List[discord.Embed]:
        """Create an embed containing the list of roles."""

        roles_list = [
            role
            for role in self.all_roles
            if role is not self.guild.default_role  # @everyone not included
        ]

        def base_embed(continued=False, description=None):
            embed = discord.Embed(color=discord.Color.dark_theme())
            embed.title = f"All roles"
            if continued:
                embed.title += " (Continued)"
            embed.description = description if description is not None else ""
            embed.set_footer(text=f"Found {plural(len(roles_list)):role}")
            return embed

        embeds = [base_embed()]
        entries = 0

        if roles_list:
            embed = embeds[0]

            for role in reversed(sorted(roles_list, key=lambda role: role.position)):
                line = f"{role.mention} : {plural(len(role.members)):member}\n"
                if entries == 25:
                    embed = base_embed(True, line)
                    embeds.append(embed)
                    entries = 1
                else:
                    embed.description += line
                    entries += 1
        else:
            embeds[0].description = "There are no roles available."

        return embeds
