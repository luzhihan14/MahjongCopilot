""" Parse Majsoul liqi messages into the small facts the dashboard needs.

The liqi parser produces camelCase keys (MessageToDict without preserving_proto_field_name),
so these helpers accept both camelCase and snake_case to be robust across versions.
"""


def _first(d: dict, *keys, default=None):
    """ return the first present key from a dict (tolerates camelCase/snake_case) """
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d:
            return d[k]
    return default


def parse_account(oauth_data: dict, three_player: bool = False) -> dict:
    """ From an oauth2Login response `data`, extract nickname + level (rank) id/score.
    Majsoul carries 4-player rank in `account.level` and 3-player rank in `account.level3`,
    each an object with `id` (rank id, e.g. 10301) and `score` (points within the rank). """
    account = _first(oauth_data, "account")
    if not isinstance(account, dict):
        return {}
    level = _first(account, "level3" if three_player else "level", "level") or {}
    return {
        "nickname": _first(account, "nickname"),
        "level_id": _first(level, "id"),
        "level_score": _first(level, "score"),
    }


def _as_int(v, default=None):
    """ coerce a value (protobuf int64 can arrive as a string) to int """
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def parse_end_result(end_data: dict, my_seat: int) -> dict | None:
    """ From a NotifyGameEndResult `data`, return the finishing outcome for my_seat.
    `data.result.players` has one entry per player: {seat, totalPoint, partPoint1, gradingScore}.
    There is no explicit rank field, so placement is derived by sorting on totalPoint
    (descending), tie-broken by seat (lower seat wins). """
    result = _first(end_data, "result")
    players = _first(result, "players") if isinstance(result, dict) else None
    if not isinstance(players, list) or not players:
        return None
    players = [p for p in players if isinstance(p, dict)]
    if not players:
        return None

    def total_of(p):
        return _as_int(_first(p, "totalPoint", "total_point"), 0)

    ordered = sorted(players, key=lambda p: (-total_of(p), _first(p, "seat", default=99)))

    placement = points = grading = None
    for idx, p in enumerate(ordered):
        if _first(p, "seat", default=-1) == my_seat:
            placement = idx + 1
            points = total_of(p)
            grading = _as_int(_first(p, "gradingScore", "grading_score"))
            break
    if placement is None:
        return None

    scores = {_first(p, "seat"): _as_int(_first(p, "partPoint1", "part_point_1")) for p in players}
    return {"placement": placement, "points": points, "grading": grading, "seat": my_seat, "scores": scores}
