"""Nivuus guest VPN: upload the guest network's VPN profile from Home Assistant.

The guest VPN itself is run by the mqtt system agent (nivuus/mqtt), whose
switch and state entities reach Home Assistant through MQTT discovery. MQTT
cannot carry a file, nor a secret that must stay out of entity states, so this
integration exists for one thing: its options flow sends the `.ovpn`, its
optional CA and the provider credentials to the agent over the agent's
WebSocket API. There is nothing to set up at run time.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True
