""" Web dashboard: a LAN-accessible live view of the app, its session stats and history.

Served over Flask and discoverable at http://mahjongsoul.local:<port> via mDNS (zeroconf).
See dashboard.server.DashboardServer.
"""
from .server import DashboardServer
from .stats import SessionStats
