import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from typing import Optional, TYPE_CHECKING


try:
    from colorama import Fore, Style, init as color_init
except ImportError:
    color_init = None
    Fore = Style = type("Dummy", (object,), {"__getattr__": lambda self, item: ""})()
else:
    color_init()


class ModmailLogger(logging.Logger):
    """
    Represent ModmailLogger. This class is a custom logger, inherits from logging.Logger.

    Every logger should be an instance of this class. And every instance of this class
    represents a single logging channel.

    This class will be set as a logger class globally for logging module using `logging.setLoggerClass()`.

    To instantiate this class from project file, simply import the function `getLogger` and define a variable
    (e.g. `logger`), then call the function `getLogger` with `__name__` as the argument.
    E.g:
        logger = getLogger(__name__)
    """

    @staticmethod
    def _debug_(*msgs: str) -> str:
        return f'{Fore.CYAN}{" ".join(msgs)}{Style.RESET_ALL}'

    @staticmethod
    def _info_(*msgs: str) -> str:
        return f'{Fore.LIGHTMAGENTA_EX}{" ".join(msgs)}{Style.RESET_ALL}'

    @staticmethod
    def _error_(*msgs: str) -> str:
        return f'{Fore.RED}{" ".join(msgs)}{Style.RESET_ALL}'

    def debug(self, msg, *args, **kwargs) -> None:
        if self.isEnabledFor(logging.DEBUG):
            self._log(logging.DEBUG, self._debug_(msg), args, **kwargs)

    def info(self, msg, *args, **kwargs) -> None:
        if self.isEnabledFor(logging.INFO):
            self._log(logging.INFO, self._info_(msg), args, **kwargs)

    def warning(self, msg, *args, **kwargs) -> None:
        if self.isEnabledFor(logging.WARNING):
            self._log(logging.WARNING, self._error_(msg), args, **kwargs)

    def error(self, msg, *args, **kwargs) -> None:
        if self.isEnabledFor(logging.ERROR):
            self._log(logging.ERROR, self._error_(msg), args, **kwargs)

    def critical(self, msg, *args, **kwargs) -> None:
        if self.isEnabledFor(logging.CRITICAL):
            self._log(logging.CRITICAL, self._error_(msg), args, **kwargs)

    def line(self, level="info") -> None:
        if level == "info":
            level = logging.INFO
        elif level == "debug":
            level = logging.DEBUG
        else:
            level = logging.INFO
        if self.isEnabledFor(level):
            self._log(
                level,
                Fore.BLACK
                + Style.BRIGHT
                + "-------------------------"
                + Style.RESET_ALL,
                (),
            )


logging.setLoggerClass(ModmailLogger)
log_level = logging.INFO
loggers = set()

ch: logging.StreamHandler = logging.StreamHandler(stream=sys.stdout)
ch.setLevel(log_level)
formatter = logging.Formatter(
    "%(asctime)s %(name)s[%(lineno)d] - %(levelname)s: %(message)s",
    datefmt="%m/%d/%y %H:%M:%S",
)
ch.setFormatter(formatter)

ch_debug: Optional[logging.Handler] = None


def getLogger(name: str) -> ModmailLogger:
    """
    Return a logger with the specified name, creating it if necessary.

    If no name is specified, return the root logger.
    """
    if TYPE_CHECKING:
        logger = ModmailLogger(name)
    else:
        logger = logging.getLogger(name)

    logger.setLevel(log_level)
    logger.addHandler(ch)
    if ch_debug is not None:
        logger.addHandler(ch_debug)
    loggers.add(logger)
    return logger


class FileFormatter(logging.Formatter):
    """
    Formatter instances are used to convert a LogRecord to text.
    """

    ansi_escape = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

    def format(self, record: logging.LogRecord) -> str:
        """
        Format the specified record as text.
        """
        record.msg = self.ansi_escape.sub("", record.msg)
        return super().format(record)


def configure_logging(filename: Path, level: int = None):
    """
    Configures logging feature for the client.

    Basically this will gloablly configure the log level, file formatter, handler, etc.
    All other logger instantiated from `.getLogger()` will use the settings configured by this
    function.
    """
    global ch_debug, log_level
    ch_debug = RotatingFileHandler(
        filename, mode="a+", maxBytes=48000, backupCount=1, encoding="utf-8"
    )

    formatter_debug = FileFormatter(
        "%(asctime)s %(name)s[%(lineno)d] - %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ch_debug.setFormatter(formatter_debug)
    ch_debug.setLevel(logging.DEBUG)

    if level is not None:
        log_level = level

    ch.setLevel(log_level)

    for logger in loggers:
        logger.setLevel(log_level)
        logger.addHandler(ch_debug)
