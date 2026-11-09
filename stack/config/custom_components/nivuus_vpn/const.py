"""Constants for the Nivuus guest VPN integration.

No Home Assistant import here, on purpose: tests/test_vpn_payloads.py reads the
error keys below to prove every one of them is translated.
"""

DOMAIN = "nivuus_vpn"
NAME = "Nivuus guest VPN"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_TOKEN = "token"

# The agent listens on loopback only, and Home Assistant runs with
# network_mode: host, so 127.0.0.1 IS the agent.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766

# Options-flow fields. None of them is ever stored: the options flow forwards
# them to the agent and saves an empty options dict.
FIELD_OVPN = "ovpn_file"
FIELD_CA = "ca_file"
FIELD_USERNAME = "username"
FIELD_PASSWORD = "password"

# Bounds on one exchange with the agent. A status read may run one supervision
# pass (nmcli plus an exit-IP probe bounded at 15 s). An import waits for the
# agent's queue, then for `nmcli connection up` (bounded at 120 s by the agent)
# and a status read: the form must outlast that, or it would report a failure
# while the agent is still bringing the tunnel up.
STATUS_TIMEOUT = 45
IMPORT_TIMEOUT = 240

ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_INVALID_AUTH = "invalid_auth"
ERROR_TIMEOUT = "timeout"
ERROR_AGENT = "agent_error"
ERROR_ACTIVATION = "activation_failed"
ERROR_NOT_TEXT = "not_utf8"
ERROR_UPLOAD_MISSING = "upload_missing"

CONFIG_ERRORS = (ERROR_CANNOT_CONNECT, ERROR_INVALID_AUTH, ERROR_TIMEOUT, ERROR_AGENT)
OPTIONS_ERRORS = CONFIG_ERRORS + (
    ERROR_ACTIVATION,
    ERROR_NOT_TEXT,
    ERROR_UPLOAD_MISSING,
)
