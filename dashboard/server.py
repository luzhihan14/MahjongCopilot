""" Dashboard web server.

Runs a small Flask app on the LAN in a background thread, and advertises the host
name (default 'mahjongsoul.local') over mDNS via zeroconf so it can be reached from
any device on the same Wi-Fi at http://mahjongsoul.local:<port>.

The page polls /api/state (JSON) once a second and renders:
  - session stats since app start (games, placements/win-loss, points, rank)
  - the current game view (round, scores, hand, AI guidance)
  - history panels (placement history, rank history, event log)
"""
import logging
import socket
import threading

from werkzeug.serving import make_server
from flask import Flask, jsonify, Response

from common.log_helper import LOGGER
from common.mj_helper import MJAI_TILE_2_UNICODE
from .page import DASHBOARD_HTML


def lan_ip() -> str:
    """ best-effort primary LAN IPv4 of this machine """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))   # no packets sent; just picks the default-route interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def tiles_to_unicode(tiles) -> str:
    """ convert a list of mjai tiles to a unicode mahjong-tile string """
    if not tiles:
        return ""
    return "".join(MJAI_TILE_2_UNICODE.get(t, t) for t in tiles)


class DashboardServer:
    """ Flask + mDNS dashboard server, run in a background thread. """

    def __init__(self, bot_manager, settings, port: int = 80, host_name: str = "mahjongsoul.local",
                 fallback_port: int = 8080, bind_ip: str = ""):
        self.bot_manager = bot_manager
        self.st = settings
        self.req_port = port                # requested port (80 -> clean URL, needs privileges)
        self.fallback_port = fallback_port  # used if the requested port can't be bound unprivileged
        self.port = None                    # the port actually bound, set in start()
        self.host_name = host_name
        self.bind_ip = bind_ip              # specific local IP to bind (""/None -> all interfaces)
        self.bind_host = bind_ip or "0.0.0.0"

        self._server = None
        self._thread: threading.Thread = None
        self._zeroconf = None
        self._service = None
        self._ip = None

    # ---------------- lifecycle ----------------

    def start(self):
        """ start the web server thread and register mDNS """
        if self._thread and self._thread.is_alive():
            return
        # advertise (and report) the specific bind IP when one is set, else the default-route IP
        self._ip = self.bind_ip or lan_ip()
        app = self._build_app()
        self.port = self._bind_server(app)
        if self.port is None:
            return
        self._thread = threading.Thread(target=self._server.serve_forever, name="DashboardThread", daemon=True)
        self._thread.start()
        self._register_mdns()
        LOGGER.info("Dashboard running at %s  (also %s)", self.url, self._ip_url())

    def _bind_server(self, app) -> int | None:
        """ bind the requested port, automatically falling back to another port if it can't be
        bound (already in use, or a privileged port <1024 we lack rights for). Tries, in order:
        the requested port, the configured fallback_port, then port 0 (an OS-assigned free port)
        as a last resort — so the dashboard comes up on *some* port instead of failing. """
        candidates = [self.req_port]
        if self.fallback_port and self.fallback_port != self.req_port:
            candidates.append(self.fallback_port)
        candidates.append(0)   # last resort: let the OS pick any free port
        for p in candidates:
            server = self._make_server(app, p)
            if server is None:
                continue
            self._server = server
            bound = server.server_port   # actual bound port (differs from p when p == 0)
            if bound != self.req_port:
                hint = (f" Run the app with sudo to serve on port {self.req_port} "
                        f"(clean http://{self.host_name}).") if self.req_port < 1024 else ""
                LOGGER.warning(
                    "Dashboard: port %d unavailable (in use or needs privileges); "
                    "using port %d instead - reach it at http://%s:%d.%s",
                    self.req_port, bound, self.host_name, bound, hint)
            return bound
        LOGGER.error("Dashboard: could not bind any port; dashboard disabled.")
        return None

    def _make_server(self, app, port: int):
        """ create the werkzeug server on `port`, or return None if the port is unavailable.

        We probe the port with our own socket first: werkzeug's make_server does NOT raise on a
        bind failure - it prints a message and calls sys.exit(1) (raising SystemExit), which would
        kill the app instead of letting us fall back. Probing lets us detect the failure and move
        on. SO_REUSEADDR mirrors werkzeug's own bind so a TIME_WAIT socket isn't a false negative. """
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # SO_REUSEADDR mirrors werkzeug's own bind: it avoids a TIME_WAIT false negative, and lets a
        # specific-IP bind coexist with another app's 0.0.0.0:<port> (how we share port 80 via a 2nd IP).
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((self.bind_host, port))
        except PermissionError:
            LOGGER.warning("Dashboard: binding %s:%d needs elevated privileges (run with sudo).",
                           self.bind_host, port)
            return None
        except OSError as e:
            LOGGER.warning("Dashboard: could not bind %s:%d: %s", self.bind_host, port, e)
            return None
        finally:
            probe.close()
        try:
            return make_server(self.bind_host, port, app, threaded=True)
        except (SystemExit, OSError) as e:   # lost a race for the port between probe and real bind
            LOGGER.warning("Dashboard: %s:%d became unavailable: %s", self.bind_host, port, e)
            return None

    def stop(self):
        """ stop the server and unregister mDNS """
        try:
            if self._zeroconf and self._service:
                self._zeroconf.unregister_service(self._service)
        except Exception as e:   # pylint: disable=broad-except
            LOGGER.debug("Dashboard: mDNS unregister error: %s", e)
        finally:
            if self._zeroconf:
                self._zeroconf.close()
                self._zeroconf = None
        if self._server:
            self._server.shutdown()
            self._server = None
        LOGGER.debug("Dashboard stopped")

    @property
    def url(self) -> str:
        """ the URL to reach the dashboard (port omitted when it's the default 80) """
        if self.port in (80, None):
            return f"http://{self.host_name}"
        return f"http://{self.host_name}:{self.port}"

    def _ip_url(self) -> str:
        """ same URL but via raw LAN IP (fallback if mDNS name doesn't resolve on a device) """
        if self.port == 80:
            return f"http://{self._ip}"
        return f"http://{self._ip}:{self.port}"

    # ---------------- mDNS ----------------

    def _register_mdns(self):
        try:
            from zeroconf import Zeroconf, ServiceInfo
        except ImportError:
            LOGGER.warning("Dashboard: zeroconf not installed; '%s' name unavailable. Use http://%s:%s instead.",
                           self.host_name, self._ip, self.port)
            return
        try:
            server = self.host_name if self.host_name.endswith(".") else self.host_name + "."
            self._zeroconf = Zeroconf()
            self._service = ServiceInfo(
                "_http._tcp.local.",
                "Mahjong Copilot Dashboard._http._tcp.local.",
                addresses=[socket.inet_aton(self._ip)],
                port=self.port,
                properties={"path": "/"},
                server=server,
            )
            self._zeroconf.register_service(self._service)
            LOGGER.debug("Dashboard: mDNS registered %s -> %s", server, self._ip)
        except Exception as e:   # pylint: disable=broad-except
            LOGGER.warning("Dashboard: mDNS registration failed (%s). Use http://%s:%s instead.",
                           e, self._ip, self.port)

    # ---------------- flask app ----------------

    def _build_app(self) -> Flask:
        # quiet the per-request werkzeug access log so it doesn't spam the app log
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        app = Flask(__name__)
        app.logger.disabled = True

        @app.route("/")
        def index():   # pylint: disable=unused-variable
            return Response(DASHBOARD_HTML, mimetype="text/html")

        @app.route("/api/state")
        def api_state():   # pylint: disable=unused-variable
            return jsonify(self._state())

        return app

    # ---------------- state serialization ----------------

    def _state(self) -> dict:
        bm = self.bot_manager
        st = self.st
        return {
            "status": self._status(),
            "session": bm.session_stats.snapshot(),
            "game": self._current_game(),
            "server": {"host": self.host_name, "ip": self._ip, "port": self.port},
            "lang": {
                "self": st.lan().SEAT if hasattr(st.lan(), "SEAT") else "You",
            },
        }

    def _status(self) -> dict:
        bm = self.bot_manager
        st = self.st
        error = None
        if bm.main_thread_exception:
            error = f"Main thread error: {bm.main_thread_exception}"
        elif bm.game_exception:
            error = f"Game error: {bm.game_exception}"
        client = bm.get_game_client_type()
        return {
            "model_type": st.model_type,
            "model_loaded": bm.is_bot_created(),
            "in_game": bm.is_in_game(),
            "calculating": bm.is_bot_calculating(),
            "syncing": bool(bm.is_game_syncing()),
            "client": client.name if client is not None else None,
            "autoplay": st.enable_automation,
            "overlay": st.enable_overlay,
            "autojoin": st.auto_join_game,
            "recording": getattr(st, "enable_game_record", False),
            "error": error,
        }

    def _current_game(self) -> dict:
        bm = self.bot_manager
        try:
            gs = bm.game_state
            if gs is None or not bm.is_in_game():
                return {"in_game": False}
            gi = bm.get_game_info()
            if gi is None:
                return {"in_game": True, "started": False}

            guide = None
            reaction = bm.get_pending_reaction()
            if reaction:
                from bot_manager import mjai_reaction_2_guide   # lazy import: avoids circular import
                action_str, options = mjai_reaction_2_guide(reaction, 3, self.st.lan())
                guide = {
                    "action": action_str,
                    "options": [{"tile": t, "weight": round(float(q), 4)} for t, q in options],
                }

            mode = gs.game_mode.value if gs.game_mode is not None else None
            scores = list(gs.player_scores) if gs.player_scores else None
            return {
                "in_game": True,
                "started": True,
                "mode": mode,
                "bakaze": gi.bakaze,
                "kyoku": gi.kyoku,
                "honba": gi.honba,
                "my_seat": gi.self_seat,
                "my_tehai": gi.my_tehai,
                "my_tehai_unicode": tiles_to_unicode(gi.my_tehai),
                "my_tsumohai": gi.my_tsumohai,
                "my_tsumohai_unicode": tiles_to_unicode([gi.my_tsumohai]) if gi.my_tsumohai else "",
                "self_reached": gi.self_reached,
                "player_reached": gi.player_reached,
                "scores": scores,
                "calculating": bm.is_bot_calculating(),
                "syncing": bool(bm.is_game_syncing()),
                "guide": guide,
            }
        except Exception as e:   # pylint: disable=broad-except
            LOGGER.debug("Dashboard: error building current game view: %s", e)
            return {"in_game": False, "error": str(e)}
