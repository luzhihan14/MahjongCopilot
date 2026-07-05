""" Session statistics tracker for the dashboard.

Accumulates data since the app started: games played, finishing placements,
per-game points, the player's Majsoul rank/level, and a chronological event log.
All access is thread-safe (the bot thread writes, the Flask thread reads).
"""
import threading
import time

# Majsoul major rank names by index (level_id % 10000 // 100). English + 中文.
MAJOR_RANKS = {
    1: ("Novice", "初心"),
    2: ("Adept", "雀士"),
    3: ("Expert", "雀傑"),
    4: ("Master", "雀豪"),
    5: ("Saint", "雀聖"),
    6: ("Celestial", "魂天"),
    7: ("Celestial", "魂天"),
}


def rank_from_level_id(level_id: int, level_score: int = None) -> str:
    """ Convert a Majsoul level id (e.g. 10301) to a readable rank string.
    Majsoul encodes rank as: k = level_id % 10000; major = k // 100 (1-7); star = k % 100.
    Celestial (魂天) has no stars and is described by the point score instead. """
    try:
        k = int(level_id) % 10000
        major = k // 100
        star = k % 100
        name_en, name_zh = MAJOR_RANKS.get(major, ("?", "?"))
        if major >= 6:  # Celestial has no stars, show the score as the "level"
            if level_score is not None:
                return f"{name_zh} {name_en} ({level_score})"
            return f"{name_zh} {name_en}"
        return f"{name_zh}{star} {name_en} {star}"
    except (TypeError, ValueError):
        return f"level {level_id}"


class SessionStats:
    """ Thread-safe container for stats accumulated since app start. """

    def __init__(self):
        self._lock = threading.Lock()
        self.start_time = time.time()
        self.games: list[dict] = []      # per finished game: {time, placement, mode, points, seat, scores}
        self.events: list[dict] = []     # chronological log: {time, kind, text}
        self.account: dict = {}          # {nickname, rank, level_id, level_score}
        self.rank_history: list[dict] = []   # snapshots when rank/score changes: {time, rank, level_score}

    # ---- writers (called from the bot thread) ----

    def log_event(self, kind: str, text: str):
        """ append a line to the chronological event log """
        with self._lock:
            self.events.append({"time": time.time(), "kind": kind, "text": text})
            if len(self.events) > 500:
                self.events = self.events[-500:]

    def update_account(self, nickname: str = None, level_id: int = None, level_score: int = None):
        """ update the player's account info; record a rank snapshot when it changes """
        with self._lock:
            if nickname is not None:
                self.account["nickname"] = nickname
            rank_str = None
            if level_id is not None:
                rank_str = rank_from_level_id(level_id, level_score)
                self.account["level_id"] = level_id
                self.account["rank"] = rank_str
            if level_score is not None:
                self.account["level_score"] = level_score
            # snapshot on first sight or when rank/score changes
            if rank_str is not None:
                last = self.rank_history[-1] if self.rank_history else None
                if last is None or last.get("rank") != rank_str or last.get("level_score") != level_score:
                    self.rank_history.append({
                        "time": time.time(), "rank": rank_str, "level_score": level_score})
        if rank_str is not None and (not self.rank_history or len(self.rank_history) == 1):
            self.log_event("rank", f"Rank: {rank_str}" + (f" ({level_score} pts)" if level_score is not None else ""))

    def record_game_result(self, placement: int, mode: str, points=None, seat: int = None,
                           scores=None, grading=None):
        """ record one finished game's outcome (placement is 1-based; 1 == 1st place).
        grading is the Majsoul rank-point (段位ポイント) delta for this game, if known. """
        with self._lock:
            self.games.append({
                "time": time.time(), "placement": placement, "mode": mode,
                "points": points, "seat": seat, "scores": scores, "grading": grading})
        ordinal = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}.get(placement, f"{placement}th")
        pts = f", {points:+d} pts" if isinstance(points, int) else ""
        rk = f", {grading:+d} rank pts" if isinstance(grading, int) else ""
        self.log_event("game", f"Game finished: {ordinal} place ({mode}){pts}{rk}")

    # ---- reader (called from the Flask thread) ----

    def snapshot(self) -> dict:
        """ derived, JSON-serializable summary of the whole session """
        with self._lock:
            games = list(self.games)
            events = list(self.events[-50:])
            account = dict(self.account)
            rank_history = list(self.rank_history)

        n = len(games)
        counts = {1: 0, 2: 0, 3: 0, 4: 0}
        for g in games:
            p = g.get("placement")
            if p in counts:
                counts[p] += 1
        avg_place = round(sum(g["placement"] for g in games) / n, 2) if n else None
        firsts = counts[1]
        # "win" = finished in the top half (1st or 2nd); "loss" = bottom half. Also expose 1st-rate.
        wins = counts[1] + counts[2]
        losses = counts[3] + counts[4]
        total_points = sum(g["points"] for g in games if isinstance(g.get("points"), int))
        total_grading = sum(g["grading"] for g in games if isinstance(g.get("grading"), int))

        return {
            "uptime_sec": int(time.time() - self.start_time),
            "account": account,
            "games_played": n,
            "placements": [counts[1], counts[2], counts[3], counts[4]],
            "avg_placement": avg_place,
            "first_place": firsts,
            "first_rate": round(firsts / n, 3) if n else None,
            "wins": wins,          # top-half finishes
            "losses": losses,      # bottom-half finishes
            "total_points": total_points,
            "total_grading": total_grading,   # net Majsoul rank-point change this session
            "placement_history": [g["placement"] for g in games],
            "points_history": [g["points"] for g in games if isinstance(g.get("points"), int)],
            "grading_history": [g["grading"] for g in games if isinstance(g.get("grading"), int)],
            "rank_history": rank_history,
            "recent_games": games[-15:],
            "log": events,
        }
