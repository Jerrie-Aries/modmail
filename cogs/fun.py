from __future__ import annotations

import contextlib
import string
from datetime import datetime
from enum import Enum
from random import randint, choice
from typing import Literal, Union, TYPE_CHECKING

import discord
from dadjokes import Dadjoke

from core import checks
from core.enums_ext import PermissionLevel
from core.ext import commands
from core.timeutils import humanize_timedelta
from core.utils import code_block, escape, truncate
from core.views.paginator import EmbedPaginatorSession

if TYPE_CHECKING:
    from bot import ModmailBot


class RPS(Enum):
    rock = "\N{MOYAI}"
    paper = "\N{PAGE FACING UP}"
    scissors = "\N{BLACK SCISSORS}"


class RPSParser:
    def __init__(self, argument: Literal["rock", "paper", "scissors"]):
        try:
            self.choice = RPS[argument.lower()]
        except KeyError:
            self.choice = None


class Fun(commands.Cog):
    """Some Fun commands for everyone."""

    ball = [
        "As I see it, yes.",
        "It is certain.",
        "It is decidedly so.",
        "Most likely.",
        "Outlook good.",
        "Signs point to yes.",
        "Without a doubt.",
        "Yes.",
        "Yes – definitely.",
        "You may rely on it.",
        "Reply hazy, try again.",
        "Ask again later.",
        "Better not tell you now.",
        "Cannot predict now.",
        "Concentrate and ask again.",
        "Don't count on it.",
        "My reply is no.",
        "My sources say no.",
        "Outlook not so good.",
        "Very doubtful.",
    ]

    def __init__(self, bot: ModmailBot):
        """
        Parameters
        ----------
        bot : ModmailBot
            The Modmail bot.
        """
        super().__init__()
        self.bot: ModmailBot = bot

    @commands.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def choose(self, ctx: commands.Context, *choices: str):
        """
        Choose between multiple options.
        To denote options which include whitespace, you should use
        double quotes.
        """
        choices = [escape(c, mass_mentions=True) for c in choices]
        if len(choices) < 2:
            await ctx.send("Not enough options to pick from.")
        else:
            await ctx.send(choice(choices))

    @commands.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def roll(self, ctx: commands.Context, number: int = 6):
        """
        Roll a random number.
        The result will be between 1 and `<number>`.
        `<number>` if not specified, defaults to 6.
        """
        author = ctx.author
        min, max = 1, 100
        if min > number or max < number:
            return await ctx.send("Number must be or between 1 to 100.")
        if number > 1:
            n = randint(1, number)
            if n <= 6:
                dices = "".join("\N{GAME DIE}" for _ in range(n))
            else:
                dices = "".join("\N{GAME DIE}" for _ in range(3)) + ". . . . ."
            await ctx.reply("{dices} = **{n}**".format(dices=dices, n=n))
        else:
            await ctx.send(
                "{author.mention} Maybe higher than 1? ;P".format(author=author)
            )

    @commands.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def flip(self, ctx: commands.Context):
        """Flip a coin."""
        answer = choice(["HEADS!*", "TAILS!*"])
        await ctx.send(f"*Flips a coin and...{answer}")

    @commands.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def rps(self, ctx: commands.Context, your_choice: RPSParser):
        """Play Rock, Paper, Scissors."""
        author = ctx.author
        player_choice = your_choice.choice
        if not player_choice:
            return await ctx.send(
                "This isn't a valid option. Try `rock`, `paper`, or `scissors`."
            )
        bot_choice = choice((RPS.rock, RPS.paper, RPS.scissors))
        cond = {
            (RPS.rock, RPS.paper): False,
            (RPS.rock, RPS.scissors): True,
            (RPS.paper, RPS.rock): True,
            (RPS.paper, RPS.scissors): False,
            (RPS.scissors, RPS.rock): False,
            (RPS.scissors, RPS.paper): True,
        }
        if bot_choice == player_choice:
            outcome = None  # Tie
        else:
            outcome = cond[(player_choice, bot_choice)]
        if outcome is True:
            await ctx.send(
                f"I choose...{bot_choice.value}\n\nYou win {author.mention}!"
            )
        elif outcome is False:
            await ctx.send(
                f"I choose...{bot_choice.value}\n\nYou lose {author.mention}!"
            )
        else:
            await ctx.send(
                f"I choose...{bot_choice.value}\n\nWe're square {author.mention}!"
            )

    @commands.command(name="8ball", aliases=["8"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def _8ball(self, ctx: commands.Context, *, question: str):
        """
        Ask 8 ball a question.
        Question must end with a question mark, `?`.

        Disclaimer: These answers are jokes and should be taken as jokes.
        For legal advice, talk to a lawyer.
        For general advice, don't take it from a bot.
        """
        embed = discord.Embed(
            title="Question: :8ball:", description=question, color=0x2332E4
        )
        embed.add_field(name="Answer:", value=choice(self.ball), inline=False)

        if question.endswith("?") and question != "?":
            await ctx.send(embed=embed)
        else:
            await ctx.send("That doesn't look like a question.")

    @commands.command(aliases=["badjoke"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def dadjoke(self, ctx: commands.Context):
        """Gives a random Dadjoke."""
        x = Dadjoke()
        await ctx.send(x.joke)

    @commands.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def say(self, ctx: commands.Context, *, message: str):
        """Make the bot say something."""
        msg = escape(message, mass_mentions=True)
        await ctx.send(msg)

    @commands.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def emojify(self, ctx: commands.Context, *, text: str):
        """Turns your text into emojis!"""
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass
        to_send = ""
        for char in text:
            if char == " ":
                to_send += " "
            elif char.lower() in "qwertyuiopasdfghjklzxcvbnm":
                to_send += f":regional_indicator_{char.lower()}:  "
            elif char in "1234567890":
                numbers = {
                    "1": "one",
                    "2": "two",
                    "3": "three",
                    "4": "four",
                    "5": "five",
                    "6": "six",
                    "7": "seven",
                    "8": "eight",
                    "9": "nine",
                    "0": "zero",
                }
                to_send += f":{numbers[char]}: "
            else:
                return await ctx.send(
                    "Characters must be either a letter or number. Anything else is unsupported."
                )
        if len(to_send) > 2000:
            return await ctx.send("Emoji is too large to fit in a message!")
        await ctx.send(to_send)

    @commands.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    @commands.guild_only()
    async def roast(self, ctx: commands.Context, *, user: discord.Member = None):
        """Roast someone! If you suck at roasting them yourself."""

        if user is None:
            await ctx.send(
                "Please mention the user you want to roast, or provide user ID."
            )
            return
        msg = f"Hey, {user.mention}! " if user is not None else ""
        roasts = [
            "You're as useless as the 'ueue' in 'queue'.",
            "Mirrors can't talk. Lucky for you, they can't laugh either.",
            "You have something on your chin...no, the 3rd one down.",
            "You're the reason the gene pool needs a lifeguard.",
            "If I had a face like yours, I'd sue my parents.",
            "Your only chance of getting laid is to crawl up a chicken's butt and wait.",
            "Some day you'll go far...and I hope you stay there.",
            "You must have been born on a highway because that's where most accidents happen.",
            "If laughter is the best medicine, your face must be curing the world.",
            "I'm glad to see you're not letting your education get in the way of your ignorance.",
            "Is your ass jealous of the amount of shit that just came out of your mouth?",
            "If I wanted to kill myself, I'd climb your ego and jump to your IQ.",
            "I'd agree on things with you but then we'd both be wrong.",
            "When I see your face there's not a thing I would change...except the direction I was walking in.",
            "When you were born the doctor threw you out the window and the window threw you back.",
            "I love what you've done with your hair. How do you get it to come out of the nostrils like that?",
            "Your face could scare the shit out of a toilet.",
            "Seriously? You were the sperm that won?",
            "They say beauty is on the inside. You better hope that's true.",
            "You're so ugly, your potraits hang themselves.",
            "Wait, if you're here, who's scaring away the crows from our crops?",
            "I wish I had more hands so I could show you more middle fingers.",
            "Hold still. I’m trying to imagine you with personality.",
            "Did you know that your face makes onions cry?",
            "You bring everyone so much joy…when you leave the room.",
            "You are the human version of period cramps.",
            "You are like a cloud. When you disappear it’s a beautiful day.",
            "I'd give you a nasty look but you've already got one.",
            "If you're going to be two-faced, at least make one of them pretty.",
            "The only way you'll ever get laid is if you crawl up a chicken's ass and wait.",
            "It looks like your face caught fire and someone tried to put it out with a hammer.",
            "I'd like to see things from your point of view, but I can't seem to get my head that far up your ass.",
            "Scientists say the universe is made up of neutrons, protons and electrons. They forgot to mention morons.",
            "Why is it acceptable for you to be an idiot but not for me to point it out?",
            "Just because you have one doesn't mean you need to act like one.",
            "Someday you'll go far... and I hope you stay there.",
            "Which sexual position produces the ugliest children? Ask your mother.",
            "No, those pants don't make you look fatter - how could they?",
            "Save your breath - you'll need it to blow up your date.",
            "If you really want to know about mistakes, you should ask your parents.",
            "Whatever kind of look you were going for, you missed.",
            "Hey, you have something on your chin... no, the 3rd one down.",
            "I don't know what makes you so stupid, but it really works.",
            "You are proof that evolution can go in reverse.",
            "Brains aren't everything. In your case they're nothing.",
            "I thought of you today. It reminded me to take the garbage out.",
            "You're so ugly when you look in the mirror, your reflection looks away.",
            "Quick - check your face! I just found your nose in my business.",
            "It's better to let someone think you're stupid than open your mouth and prove it.",
            "You're such a beautiful, intelligent, wonderful person. Oh I'm sorry, I thought we were having a lying competition.",
            "I'd slap you but I don't want to make your face look any better.",
            "You have the right to remain silent because whatever you say will probably be stupid anyway.",
        ]
        if str(user.id) == str(ctx.bot.user.id):
            return await ctx.reply(
                f"Uh?!! Nice try! I am not going to roast myself. Instead I am going to roast you now.\n\n {ctx.author.mention} {choice(roasts)}"
            )
        await ctx.send(f"{msg} {choice(roasts)}")

    @commands.command(aliases=["sc"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    @commands.guild_only()
    async def smallcaps(self, ctx: commands.Context, *, message: str):
        """Convert you text to ꜱᴍᴀʟʟ ᴄᴀᴘꜱ!!"""
        alpha = list(string.ascii_lowercase)
        converter = [
            "ᴀ",
            "ʙ",
            "ᴄ",
            "ᴅ",
            "ᴇ",
            "ꜰ",
            "ɢ",
            "ʜ",
            "ɪ",
            "ᴊ",
            "ᴋ",
            "ʟ",
            "ᴍ",
            "ɴ",
            "ᴏ",
            "ᴘ",
            "ǫ",
            "ʀ",
            "ꜱ",
            "ᴛ",
            "ᴜ",
            "ᴠ",
            "ᴡ",
            "x",
            "ʏ",
            "ᴢ",
        ]
        new = ""
        exact = message.lower()
        for letter in exact:
            if letter in alpha:
                index = alpha.index(letter)
                new += converter[index]
            else:
                new += letter
        await ctx.send(new)

    @commands.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def urban(self, ctx: commands.Context, *, search: str):
        """
        Search on the urban dictionary!
        """

        def replace_with_link(text):
            location = 0

            previous_tracked = 0

            word = ""
            in_bracket = False
            changes = ""

            for char in text:
                if char == "[":
                    in_bracket = True
                elif char == "]":
                    changes += text[previous_tracked : location + 1]
                    changes += f"(https://www.urbandictionary.com/define.php?term={word})".replace(
                        " ", "%20"
                    )

                    in_bracket = False
                    word = ""

                    previous_tracked = location + 1
                    # tracked = 0
                elif in_bracket:
                    word += char
                location += 1
            changes += text[previous_tracked:]
            return changes

        async with ctx.typing():
            async with self.bot.session.get(
                f"https://api.urbandictionary.com/v0/define?term={search}",
                headers={"User-agent": "Super Bot 9000"},
            ) as resp:
                if resp.status != 200:
                    raise commands.BadArgument(
                        f'Bad request. Received status code: {code_block(str(resp.status), lang="py")}'
                    )

                data = await resp.json()

        entries = data.get("list", [])
        if not entries:
            embed = discord.Embed(color=self.bot.error_color)

            embed.description = "There is nothing here, try again."
            await ctx.send(embed=embed)
        else:
            pages = []
            for entry in entries:
                definition = replace_with_link(entry.get("definition"))
                example = replace_with_link(entry.get("example"))

                ups = entry["thumbs_up"]
                downs = entry["thumbs_down"]

                page = discord.Embed(title=search, color=0x5E8FBD)
                page.set_thumbnail(url="https://i.imgur.com/VFXr0ID.jpg")
                page.add_field(
                    name="Definition:",
                    value=truncate(definition, 1024),
                    inline=False,
                )
                page.add_field(
                    name="Example:", value=truncate(example, 1024), inline=False
                )
                page.add_field(name="Upvotes:", value=ups, inline=True)
                page.add_field(name="Downvotes:", value=downs, inline=True)

                pages.append(page)
            session = EmbedPaginatorSession(ctx, *pages)
            await session.run()

    # Converter

    @commands.group(aliases=["conv"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def converter(self, ctx: commands.Context):
        """Some utility converters."""
        await ctx.send_help(ctx.command)

    @converter.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def todate(self, ctx: commands.Context, timestamp: Union[int, float]):
        """Convert a unix timestamp to a readable datetime."""
        try:
            convert = datetime.utcfromtimestamp(int(timestamp)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            given = datetime.fromtimestamp(int(timestamp))
            current = datetime.fromtimestamp(int(datetime.now().timestamp()))
            secs = str((current - given).total_seconds())
            seconds = (
                secs[1:][:-2] if "-" in secs else secs[:-2] if ".0" in secs else secs
            )
            delta = humanize_timedelta(seconds=int(seconds))
            when = (
                "It will be in {}.".format(delta)
                if given > current
                else "It was {} ago.".format(delta)
            )
            await ctx.send(
                "Successfully converted `{timestamp}` to `{convert}`.\n{when}".format(
                    timestamp=int(timestamp), convert=convert, when=when
                )
            )
        except (ValueError, OverflowError, OSError):
            return await ctx.send("`{}` is not a valid timestamp.".format(timestamp))

    @converter.command()
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def tounix(self, ctx: commands.Context, *, date: str):
        """
        Convert a date to a unix timestamp.

        **Note:** Need to respect this pattern `%Y-%m-%d %H:%M:%S`.
        Year-month-day Hour:minute:second
        Minimum to work is Year.
        """
        patterns = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H",
            "%Y-%m-%d",
            "%Y-%m",
            "%Y",
            "%m",
            "%d",
        ]
        for pattern in patterns:
            with contextlib.suppress(ValueError):
                convert = int(datetime.strptime(date, pattern).timestamp())
        try:
            given = datetime.fromtimestamp(int(convert))
        except UnboundLocalError:
            return await ctx.send("`{}` is not a valid timestamp.".format(date))
        current = datetime.fromtimestamp(int(datetime.now().timestamp()))
        secs = str((current - given).total_seconds())
        seconds = secs[1:][:-2] if "-" in secs else secs[:-2] if ".0" in secs else secs
        delta = humanize_timedelta(seconds=int(seconds))
        when = (
            "It will be in {}.".format(delta)
            if given > current
            else "It was {} ago.".format(delta)
        )

        await ctx.send(
            "Successfully converted `{date}` to `{convert}`.\n{when}".format(
                date=date, convert=convert, when=when
            )
        )

    @converter.group(aliases=["c"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def celsius(self, ctx: commands.Context):
        """
        Convert degree Celsius to Fahrenheit or Kelvin.

        See correct usage below.

        **Usage:**
        To Fahrenheit: `{prefix}conv celsius fahrenheit`
        To Kelvin: `{prefix}conv celsius kelvin`
        (You can also use `{prefix}conv c f` or `{prefix}conv c k`)
        """
        await ctx.send_help(ctx.command)

    @celsius.command(name="fahrenheit", aliases=["f"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def celsius_to_fahrenheit(self, ctx: commands.Context, temperature: float):
        """Convert degree Celsius to Fahrenheit."""
        fahrenheit = round((temperature * 1.8) + 32, 1)
        msg = "{temp:,}° Celsius is equal to {f:,}° Fahrenheit.".format(
            temp=temperature, f=fahrenheit
        )
        await ctx.send(msg)

    @celsius.command(name="kelvin", aliases=["k"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def celsius_to_kelvin(self, ctx: commands.Context, temperature: float):
        """Convert degree Celsius to Kelvin."""
        kelvin = round(temperature + 273.15, 1)
        msg = "{temp:,}° Celsius is equal to {k:,}° Kelvin.".format(
            temp=temperature, k=kelvin
        )
        await ctx.send(msg)

    @converter.group(aliases=["f"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def fahrenheit(self, ctx: commands.Context):
        """
        Convert Fahrenheit degree to Celsius or Kelvin.

        See correct usage below.

        **Usage:**
        To Celsius: `{prefix}conv fahrenheit celsius`
        To Kelvin: `{prefix}conv fahrenheit kelvin`
        (You can also use `{prefix}conv f c` or `{prefix}conv f k`)
        """
        await ctx.send_help(ctx.command)

    @fahrenheit.command(name="celsius", aliases=["c"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def fahrenheit_to_celsius(self, ctx: commands.Context, temperature: float):
        """Convert Fahrenheit degree to Celsius."""
        celsius = round((temperature - 32) / 1.8, 1)
        msg = "{temp:,}° Fahrenheit is equal to {c:,}° Celsius.".format(
            temp=temperature, c=celsius
        )
        await ctx.send(msg)

    @fahrenheit.command(name="kelvin", aliases=["k"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def fahrenheit_to_kelvin(self, ctx: commands.Context, temperature: float):
        """Convert Fahrenheit degree to Kelvin."""
        kelvin = round((temperature - 32) * (5 / 9) + 273.15, 1)
        msg = "{temp:,}° Fahrenheit is equal to {k:,}° Kelvin.".format(
            temp=temperature, k=kelvin
        )
        await ctx.send(msg)

    @converter.group(aliases=["k"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def kelvin(self, ctx: commands.Context):
        """
        Convert Kelvin degree to Celsius or Fahrenheit.

        See correct usage below.

        **Usage:**
        To Celsius: `{prefix}conv kelvin celsius`
        To Fahrenheit: `{prefix}conv kelvin fahrenheit`
        (You can also use `{prefix}conv f c` or `{prefix}conv f k`)
        """
        await ctx.send_help(ctx.command)

    @kelvin.command(name="celsius", aliases=["c"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def kelvin_to_celsius(self, ctx: commands.Context, temperature: float):
        """Convert Kelvin degree to Celsius."""
        celsius = round(temperature - 273.15, 1)
        msg = "{temp:,}° Kelvin is equal to {c:,}° Celsius.".format(
            temp=temperature, c=celsius
        )
        await ctx.send(msg)

    @kelvin.command(name="fahrenheit", aliases=["f"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def kelvin_to_fahrenheit(self, ctx: commands.Context, temperature: float):
        """Convert Kelvin degree to Fahrenheit."""
        fahrenheit = round((temperature - 273.15) * (9 / 5) + 32, 1)
        msg = "{temp:,}° Kelvin is equal to {f:,}° Fahrenheit.".format(
            temp=temperature, f=fahrenheit
        )
        await ctx.send(msg)

    @converter.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def lb(self, ctx: commands.Context):
        """
        Convert pounds to kilograms.

        See correct usage below.

        **Usage:**
        `{prefix}conv lb kg`
        """
        await ctx.send_help(ctx.command)

    @lb.group(name="kg", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def lb_to_kg(self, ctx: commands.Context, mass: float):
        """Convert pounds to kilograms."""
        kg = round((mass * 0.45359237), 1)
        await ctx.send("{mass:,} lb is equal to {kg:,} kg.".format(mass=mass, kg=kg))

    @converter.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def kg(self, ctx: commands.Context):
        """
        Convert kilograms to pounds.

        See correct usage below.

        **Usage:**
        `{prefix}conv kg lb`
        """
        await ctx.send_help(ctx.command)

    @kg.command(name="lb")
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def kg_to_pounds(self, ctx: commands.Context, mass: float):
        """Convert kilograms to pounds."""
        lb = round((mass / 0.45359237), 1)
        await ctx.send("{mass:,} kg is equal to {lb:,} lb.".format(mass=mass, lb=lb))

    @converter.group(aliases=["mi"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def mile(self, ctx: commands.Context):
        """
        Convert miles to kilometers.

        See correct usage below.

        **Usage:**
        `{prefix}conv mi km`
        """
        await ctx.send_help(ctx.command)

    @mile.command(name="km")
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def mi_to_km(self, ctx: commands.Context, length: float):
        """Convert miles to kilometers."""
        km = round((length * 1.609344), 1)
        await ctx.send(
            "{length:,} mi is equal to {km:,} km.".format(length=length, km=km)
        )

    @converter.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def km(self, ctx: commands.Context):
        """
        Convert kilometers to miles.

        See correct usage below.

        **Usage:**
        `{prefix}conv km mi`
        """
        await ctx.send_help(ctx.command)

    @km.command(name="mile", aliases=["mi"])
    @checks.has_permissions(PermissionLevel.REGULAR)
    async def km_to_mile(self, ctx: commands.Context, length: float):
        """Convert kilometers to miles."""
        mi = round((length / 1.609344), 1)
        await ctx.send(
            "{length:,} km is equal to {mi:,} mi.".format(length=length, mi=mi)
        )


async def setup(bot):
    await bot.add_cog(Fun(bot))
