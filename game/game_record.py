""" Game record: save the mjai message stream of a game to disk for later AI training.

The recorded log is in mjai protocol format (https://mjai.app), one JSON event per
line (JSONL). This is the same event format Mortal-style models train on, so recorded
games can be gzipped and fed into a Mortal training dataset (which globs '**/*.json.gz').
"""
import json

from common.utils import Folder, GameMode, sub_file
from common.log_helper import LOGGER, dt_string

# a record shorter than this (start_game + a few events) is an aborted/empty game, not worth saving
MIN_RECORD_EVENTS = 5


def save_mjai_record(msgs: list[dict], seat: int = None, mode: GameMode = None) -> str | None:
    """ Save recorded mjai messages to a JSONL file under the records folder.
    params:
        msgs(list): the game's mjai event stream (list of mjai message dicts)
        seat(int): player seat index, used in the file name
        mode(GameMode): game mode, used in the file name
    returns:
        str: the saved file path, or None if nothing was saved
    """
    if not msgs or len(msgs) < MIN_RECORD_EVENTS:
        LOGGER.debug("Game record has %d events (< %d), skip saving", len(msgs) if msgs else 0, MIN_RECORD_EVENTS)
        return None

    mode_str = mode.value if isinstance(mode, GameMode) else "NA"
    seat_str = str(seat) if seat is not None else "NA"
    file_name = f"game_{dt_string()}_{mode_str}_seat{seat_str}.mjson"
    file_path = sub_file(Folder.RECORD, file_name)
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            for msg in msgs:
                f.write(json.dumps(msg, ensure_ascii=False))
                f.write('\n')
        LOGGER.info("Saved game record (%d events) to %s", len(msgs), file_path)
        return file_path
    except Exception as e:  # pylint: disable=broad-except
        LOGGER.error("Failed to save game record: %s", e, exc_info=True)
        return None
