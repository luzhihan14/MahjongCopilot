""" Logging helper functions """
import datetime
import logging
import queue
from .utils import Folder, sub_file

DEFAULT_LOGGER_NAME = 'majsoul_copilot'
LOGGER = logging.getLogger(DEFAULT_LOGGER_NAME)

# Single-line, fixed-width layout so columns line up and every record is one grep-able line:
#   2026-07-05 00:13:12.588 WARNING  BotThread     bot_manager.py:398     | Failed to parse ...
#   <----- asctime ----->  <lvl 8>  <-thread 13->  <---- loc 22 ---->     | message
_LOG_FORMAT = '%(asctime)s %(levelname)-8s %(threadName)-13.13s %(loc)-22s | %(message)s'
_DATE_FMT = '%Y-%m-%d %H:%M:%S'     # milliseconds appended as '.NNN' by SingleLineFormatter

# Console-only ANSI color for attention-grabbing levels (never written to the file).
_LEVEL_COLORS = {
    'WARNING':  '\033[33m',     # yellow
    'ERROR':    '\033[31m',     # red
    'CRITICAL': '\033[1;41m',   # bold on red background
}
_RESET = '\033[0m'


class SingleLineFormatter(logging.Formatter):
    """ Formatter that guarantees exactly one physical line per record.

    - Builds a combined 'loc' = 'filename:lineno' column so the '|' separator aligns.
    - Uses '.' (not ',') before milliseconds.
    - Flattens any newline in the final rendered record (message body and/or exception
      traceback) to a literal '\\n', so every logical record stays on one grep-able line.
    - color=True wraps warning/error lines in ANSI; used only for a TTY console handler
      (the whole rendered string is wrapped, so it never mutates the record or leaks into
      the plain file formatter).
    """

    def __init__(self, color: bool = False):
        super().__init__(fmt=_LOG_FORMAT, datefmt=_DATE_FMT)
        self.color = color

    def formatTime(self, record, datefmt=None):
        base = super().formatTime(record, datefmt or _DATE_FMT)
        return '%s.%03d' % (base, record.msecs)

    def format(self, record):
        loc = '%s:%d' % (record.filename, record.lineno)
        record.loc = loc[-22:]      # keep the meaningful tail (line number) if very long
        s = super().format(record)
        s = s.replace('\r\n', '\\n').replace('\r', '\\n').replace('\n', '\\n')
        if self.color:
            color = _LEVEL_COLORS.get(record.levelname)
            if color:
                s = color + s + _RESET
        return s


def log_formatter(color: bool = False) -> logging.Formatter:
    """ return the standard single-line log formatter (color only for a TTY console) """
    return SingleLineFormatter(color=color)


class LogHelper:
    """ Log helper"""
    log_file_name:str = None
    initialized:bool = False
    debug:bool = False              # whether DEBUG output is enabled (-debug flag)
    level:int = logging.INFO        # effective minimum level; set by config_logging
    @staticmethod
    def config_logging(debug:bool=False, file_prefix:str=DEFAULT_LOGGER_NAME, console=True, file=True):
        """ Initialize logging format/output. Run once.
        params:
            debug (bool): if True, minimum level is DEBUG; otherwise INFO (DEBUG suppressed)
            file_prefix(str): prefix of the log file name
            console (bool): if output to console
            file (bool): if output to file
        """
        if LogHelper.initialized:
            LOGGER.warning("Logger %s already initialized", LOGGER.name)
            return

        level = logging.DEBUG if debug else logging.INFO
        LogHelper.debug = debug
        LogHelper.level = level

        logger = LOGGER
        logger.setLevel(level)      # logger level is the first gate: drops DEBUG before any handler

        if console:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(level)
            # color only when the console is an interactive terminal
            stream = getattr(console_handler, 'stream', None)
            use_color = bool(stream is not None and getattr(stream, 'isatty', None) and stream.isatty())
            console_handler.setFormatter(log_formatter(color=use_color))
            logger.addHandler(console_handler)

        if file:
            file_name = file_prefix + '_' + dt_string() + '.log'
            LogHelper.log_file_name = sub_file(Folder.LOG, file_name)
            file_handler = logging.FileHandler(LogHelper.log_file_name, encoding='utf-8')
            file_handler.setLevel(level)
            file_handler.setFormatter(log_formatter(color=False))   # never color the file
            logger.addHandler(file_handler)

        LogHelper.initialized = True
        logger.info("Logging initialized at %s level", logging.getLevelName(level))

def dt_string() -> str:
    """ return datetime string"""
    return datetime.datetime.now().strftime(r'%Y-%m-%d_%H-%M-%S')

class QueueHandler(logging.Handler):
    """ Log handler to send logging records to a thread-safe queue """
    def __init__(self, log_queue:queue.Queue):
        super().__init__()
        self.log_queue = log_queue
        self.setLevel(LogHelper.level)      # inherit the app-wide effective level (-debug aware)
        self.setFormatter(log_formatter())

    def emit(self, record):
        self.log_queue.put(record)
