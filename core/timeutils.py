"""
UserFriendlyTime by Rapptz
Source:
https://github.com/Rapptz/RoboDanny/blob/rewrite/cogs/utils/time.py
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional, SupportsInt, TYPE_CHECKING

import parsedatetime as pdt
from dateutil.relativedelta import relativedelta
from discord.ext.commands import BadArgument, Converter

from core.logging_ext import getLogger
from core.utils import human_join

if TYPE_CHECKING:
    from core.ext import commands

logger = getLogger(__name__)


class ShortTime:
    compiled = re.compile(
        r"""
                   (?:(?P<years>[0-9])(?:years?|y))?             # e.g. 2y
                   (?:(?P<months>[0-9]{1,2})(?:months?|mo))?     # e.g. 9mo
                   (?:(?P<weeks>[0-9]{1,4})(?:weeks?|w))?        # e.g. 10w
                   (?:(?P<days>[0-9]{1,5})(?:days?|d))?          # e.g. 14d
                   (?:(?P<hours>[0-9]{1,5})(?:hours?|h))?        # e.g. 12h
                   (?:(?P<minutes>[0-9]{1,5})(?:min(?:ute)?s?|m))?  # e.g. 10m
                   (?:(?P<seconds>[0-9]{1,5})(?:sec(?:ond)?s?|s))?  # e.g. 15s
                          """,
        re.VERBOSE,
    )

    def __init__(self, argument: str):
        match = self.compiled.fullmatch(argument)
        if match is None or not match.group(0):
            raise BadArgument("Invalid time provided.")

        data = {k: int(v) for k, v in match.groupdict(default="0").items()}
        now = datetime.utcnow()
        self.dt = now + relativedelta(**data)


# Monkey patch mins and secs into the units
units = pdt.pdtLocales["en_US"].units
units["minutes"].append("mins")
units["seconds"].append("secs")


class HumanTime:
    calendar = pdt.Calendar(version=pdt.VERSION_CONTEXT_STYLE)

    def __init__(self, argument: str):
        now = datetime.utcnow()
        dt, status = self.calendar.parseDT(argument, sourceTime=now)
        if not status.hasDateOrTime:
            raise BadArgument('Invalid time provided, try e.g. "tomorrow" or "3 days".')

        if not status.hasTime:
            # replace it with the current time
            dt = dt.replace(
                hour=now.hour,
                minute=now.minute,
                second=now.second,
                microsecond=now.microsecond,
            )

        self.dt = dt
        self._past = dt < now


class Time(HumanTime):
    def __init__(self, argument: str):
        try:
            short_time = ShortTime(argument)
        except Exception:
            super().__init__(argument)
        else:
            self.dt = short_time.dt
            self._past = False


class FutureTime(Time):
    def __init__(self, argument: str):
        super().__init__(argument)

        if self._past:
            raise BadArgument("The time is in the past.")


class UserFriendlyTime(Converter):
    """
    That way quotes aren't absolutely necessary.

    Note that all the operations in this class only work with naive datetime string.
    """

    def __init__(self):
        self.raw: Optional[str] = None
        self.dt: Optional[datetime] = None
        self.arg: Optional[str] = None
        self.now: Optional[datetime] = None

    async def convert(
        self, ctx: Optional[commands.Context], argument: str
    ) -> "UserFriendlyTime":
        return self.do_conversion(argument)

    def check_constraints(self, now: datetime, remaining: str) -> "UserFriendlyTime":
        if self.dt < now:
            raise BadArgument("This time is in the past.")

        self.arg = remaining
        return self

    def do_conversion(self, argument: str) -> "UserFriendlyTime":
        """
        This way we can do the conversion directly without passing the value for `ctx` parameter.
        """
        self.raw = argument
        remaining = ""
        try:
            calendar = HumanTime.calendar
            regex = ShortTime.compiled
            self.dt = self.now = datetime.utcnow()

            match = regex.match(argument)
            if match is not None and match.group(0):
                data = {k: int(v) for k, v in match.groupdict(default="0").items()}
                remaining = argument[match.end() :].strip()
                self.dt = self.now + relativedelta(**data)
                return self.check_constraints(self.now, remaining)

            # apparently nlp does not like "from now"
            # it likes "from x" in other cases though
            # so let me handle the 'now' case
            if argument.endswith(" from now"):
                argument = argument[:-9].strip()
            # handles "in xxx hours"
            if argument.startswith("in "):
                argument = argument[3:].strip()

            elements = calendar.nlp(argument, sourceTime=self.now)
            if elements is None or not elements:
                return self.check_constraints(self.now, argument)

            # handle the following cases:
            # "date time" foo
            # date time foo
            # foo date time

            # first the first two cases:
            dt, status, begin, end, _ = elements[0]

            if not status.hasDateOrTime:
                return self.check_constraints(self.now, argument)

            if begin not in (0, 1) and end != len(argument):
                raise BadArgument(
                    "Time is either in an inappropriate location, which must "
                    "be either at the end or beginning of your input, or I "
                    "just flat out did not understand what you meant. Sorry."
                )

            if not status.hasTime:
                # replace it with the current time
                dt = dt.replace(
                    hour=self.now.hour,
                    minute=self.now.minute,
                    second=self.now.second,
                    microsecond=self.now.microsecond,
                )

            # if midnight is provided, just default to next day
            if status.accuracy == pdt.pdtContext.ACU_HALFDAY:
                dt = dt.replace(day=self.now.day + 1)

            self.dt = dt

            if begin in (0, 1):
                if begin == 1:
                    # check if it's quoted:
                    if argument[0] != '"':
                        raise BadArgument("Expected quote before time input...")

                    if not (end < len(argument) and argument[end] == '"'):
                        raise BadArgument("If the time is quoted, you must unquote it.")

                    remaining = argument[end + 1 :].lstrip(" ,.!")
                else:
                    remaining = argument[end:].lstrip(" ,.!")
            elif len(argument) == end:
                remaining = argument[:begin].strip()

            return self.check_constraints(self.now, remaining)
        except Exception:
            logger.exception("Something went wrong while parsing the time.")
            raise

    def difference(self) -> timedelta:
        """
        Returns the difference between the converted datetime and the current time.
        """
        if not self.dt or not self.now:
            return timedelta(seconds=0)
        return self.dt - self.now


def human_timedelta(dt: datetime, *, source: datetime = None) -> str:
    """
    Convert datetime object to human readable string.

    All the provided parameters could be datetime objects whether timezone naive or aware,
    conversion will be done inside this function.
    """
    if source is not None:
        if source.tzinfo is not None:
            source = source.replace(tzinfo=None)
        now = source
    else:
        now = datetime.utcnow()

    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)

    if dt > now:
        delta = relativedelta(dt, now)
        suffix = ""
    else:
        delta = relativedelta(now, dt)
        suffix = " ago"

    if delta.microseconds and delta.seconds:
        delta = delta + relativedelta(seconds=+1)

    attrs = ["years", "months", "days", "hours", "minutes", "seconds"]

    output = []
    for attr in attrs:
        elem = getattr(delta, attr)
        if not elem:
            continue

        if elem > 1:
            output.append(f"{elem} {attr}")
        else:
            output.append(f"{elem} {attr[:-1]}")

    if not output:
        return "now"
    if len(output) == 1:
        return output[0] + suffix
    if len(output) == 2:
        return f"{output[0]} and {output[1]}{suffix}"
    return f"{output[0]}, {output[1]} and {output[2]}{suffix}"


def humanize_timedelta(
    *, timedelta: Optional[timedelta] = None, seconds: Optional[SupportsInt] = None
) -> str:
    """
    Get an aware human timedelta representation.

    This works with either a timedelta object or a number of seconds.

    Fractional values will be omitted, and values less than 1 second
    an empty string.

    Parameters
    ----------
    timedelta: Optional[timedelta]
        A timedelta object.
    seconds: Optional[SupportsInt]
        A number of seconds.

    Returns
    -------
    str
        A locale aware representation of the timedelta or seconds.

    Raises
    ------
    ValueError
        The function was called with neither a number of seconds nor a timedelta object.
    """

    try:
        obj = seconds if seconds is not None else timedelta.total_seconds()
    except AttributeError:
        raise ValueError("You must provide either a timedelta or a number of seconds")

    seconds = int(obj)
    periods = [
        ("year", "years", 60 * 60 * 24 * 365),
        ("month", "months", 60 * 60 * 24 * 30),
        ("day", "days", 60 * 60 * 24),
        ("hour", "hours", 60 * 60),
        ("minute", "minutes", 60),
        ("second", "seconds", 1),
    ]

    strings = []
    for period_name, plural_period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            if period_value == 0:
                continue
            unit = plural_period_name if period_value > 1 else period_name
            strings.append(f"{period_value} {unit}")

    return human_join(strings, final="and")


# Datetime formatter

MONTHNAMES = {
    "01": "January",
    "02": "February",
    "03": "March",
    "04": "April",
    "05": "May",
    "06": "Jun",
    "07": "July",
    "08": "August",
    "09": "September",
    "10": "October",
    "11": "November",
    "12": "December",
}

DAYNAMES = {
    "0": "Sunday",
    "1": "Monday",
    "2": "Tuesday",
    "3": "Wednesday",
    "4": "Thursday",
    "5": "Friday",
    "6": "Saturday",
}

# Abbreviated, takes only 3 initial letters
MONTHS_ABBRV = {k: v[:3] for k, v in MONTHNAMES.items()}
DAYS_ABBRV = {k: v[:3] for k, v in DAYNAMES.items()}


TimestampStyle = Literal["f", "F", "d", "D", "t", "T", "R"]


# noinspection PyPep8Naming
class datetime_formatter:
    """
    Datetime formatter. A class to convert and format datetime object.
    """

    @staticmethod
    def time_string(date_time: datetime, tzinfo: timezone = timezone.utc) -> str:
        """
        Converts the datetime object to formatted string with UTC timezone.

        Parameters
        ----------
        date_time : datetime
            A datetime object. Doesn't have to be from the past. This parameter is required.
        tzinfo : timezone
            Timezone info. If not provided, defaults to UTC.

        Returns
        -------
        str : str
            A string of formatted value, e.g. `Sun, 02 Sep 2020 12:56 PM UTC`.
        """
        convert = date_time.replace(tzinfo=tzinfo)
        year = convert.strftime("%Y")
        month = MONTHS_ABBRV.get(convert.strftime("%m"))
        day = convert.strftime("%d")  # use "%-d" to get without zero-padded number
        day_abbrv = DAYS_ABBRV.get(convert.strftime("%w"))
        hour = convert.strftime("%I")
        minute = convert.strftime("%M")
        am_pm = convert.strftime("%p")
        tz_name = convert.strftime("%Z")

        fmt = f"{day_abbrv}, {day} {month} {year}\n{hour}:{minute} {am_pm} {tz_name}"
        return fmt

    @staticmethod
    def age(date_time: datetime) -> str:
        """
        Converts the datetime to an age (difference between the `date_time` passed in and now).

        Parameters
        ----------
        date_time : datetime
            A datetime object. This parameter is required and could be either timezone aware or naive.
            Note, the `date_time` passed here will be compared with `datetime.now()` UTC timezone aware.

        Returns
        -------
        str : str
            A string of formatted age or an empty string if there's no output,
            e.g. `1 year and 6 months`.
        """
        if date_time.tzinfo is None:
            date_time = date_time.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        # use `abs` in case the seconds is negative if the
        # `date_time` passed in is a future datetime
        delta = int(abs(now - date_time).total_seconds())

        months, remainder = divmod(delta, 2628000)
        hours, seconds = divmod(remainder, 3600)
        minutes, seconds = divmod(seconds, 60)
        days, hours = divmod(hours, 24)
        years, months = divmod(months, 12)

        attrs = ["years", "months", "days", "hours", "minutes", "seconds"]
        parsed = {
            "years": years,
            "months": months,
            "days": days,
            "hours": hours,
            "minutes": minutes,
            "seconds": seconds,
        }

        for attr in attrs:
            value = parsed.get(attr)
            if value:
                value = f"{value} {attr if value != 1 else attr[:-1]}"
                parsed[attr] = value

        if years:
            output = [parsed.get(attr) for attr in attrs[0:3]]
        elif months:
            output = [parsed.get(attr) for attr in attrs[1:3]]
        elif days:
            output = [parsed.get(attr) for attr in attrs[2:4]]
        elif hours:
            output = [parsed.get(attr) for attr in attrs[3:5]]
        elif minutes:
            output = [parsed.get(attr) for attr in attrs[4:]]
        else:
            output = [parsed.get(attrs[-1])]
        output = [v for v in output if v]
        return human_join(output, " ", "and")  # this could return an empty string

    @staticmethod
    def time_age(date_time: datetime) -> str:
        """
        Formats the datetime to time and age combined together from `format_time` and `format_age`.

        Parameters
        ----------
        date_time : datetime
            A datetime object. Doesn't have to be from the past. This parameter is required
            to intantiate the class.

        Returns
        -------
        str : str
            The formatted string.
        """
        fmt = datetime_formatter.format_dt(date_time)
        fmt_age = datetime_formatter.age(date_time)
        fmt += f"\n{fmt_age if fmt_age else '.....'} ago"
        return fmt

    @staticmethod
    def format_dt(dt: datetime, style: Optional[TimestampStyle] = "F") -> str:
        """
        A helper function to format a :class:`datetime` for presentation within Discord.

        This allows for a locale-independent way of presenting data using Discord specific Markdown.

        +-------------+----------------------------+-----------------+
        |    Style    |       Example Output       |   Description   |
        +=============+============================+=================+
        | t           | 22:57                      | Short Time      |
        +-------------+----------------------------+-----------------+
        | T           | 22:57:58                   | Long Time       |
        +-------------+----------------------------+-----------------+
        | d           | 17/05/2016                 | Short Date      |
        +-------------+----------------------------+-----------------+
        | D           | 17 May 2016                | Long Date       |
        +-------------+----------------------------+-----------------+
        | f (default) | 17 May 2016 22:57          | Short Date Time |
        +-------------+----------------------------+-----------------+
        | F           | Tuesday, 17 May 2016 22:57 | Long Date Time  |
        +-------------+----------------------------+-----------------+
        | R           | 5 years ago                | Relative Time   |
        +-------------+----------------------------+-----------------+

        Note that the exact output depends on the user's locale setting in the client. The example output
        presented is using the ``en-GB`` locale.

        Parameters
        ----------
        dt : datetime
            The datetime object.
        style : Optional[str]
            The style to be converted to. The value for this should be one of the
            "f", "F", "d", "D", "t", "T", and "R". Defaults to "F".

        Returns
        --------
        :class:`str`
            The formatted string.
        """
        if style is None:
            return f"<t:{int(dt.timestamp())}>"
        return f"<t:{int(dt.timestamp())}:{style}>"

    @classmethod
    def format_relative(cls, dt: datetime) -> str:
        """
        Converts datetime object to Unix Timestamp string (relative) for presentation within Discord.

        Parameters
        ----------
        dt : datetime
            The datetime object.

        Returns
        --------
        :class:`str`
            The formatted string.
        """
        return cls.format_dt(dt, "R")
