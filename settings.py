from typing import Any, Dict
from pydantic import BaseModel
from enum import Enum

from cat import plugin
from cat.db.cruds import plugins as crud_plugins
from cat.services.string_crypto import StringCrypto

from .crypt import decrypt_secrets, encrypt_secrets


class UnitSelect(Enum):
    METRIC = "metric"
    IMPERIAL = "imperial"


class WonderlandWeatherSettings(BaseModel):
    open_weather_api: str = ""
    temperature_unit: UnitSelect = UnitSelect.IMPERIAL


def _decrypted(stored: Dict[str, Any], agent_id: str) -> Dict[str, Any]:
    settings, failed = decrypt_secrets(stored, StringCrypto())
    for key in failed:
        log.error(
            f"[connectors] agent {agent_id}: cannot decrypt '{key}' (was CAT_CRYPTO_KEY changed?): "
            "it is ignored until saved again"
        )
    return settings


@plugin
def settings_schema():
    return WonderlandWeatherSettings.model_json_schema()


@plugin
async def load_settings(plugin_id: str, agent_id: str) -> Dict[str, Any]:
    stored = await crud_plugins.get_setting(agent_id, plugin_id)
    if stored is None:
        return WonderlandWeatherSettings().model_dump()
    return _decrypted(stored, agent_id)


@plugin
async def save_settings(plugin_id: str, settings: Dict[str, Any], agent_id: str) -> Dict[str, Any]:
    stored = await crud_plugins.update_setting(agent_id, plugin_id, encrypt_secrets(settings, StringCrypto()))
    return _decrypted(stored, agent_id)
