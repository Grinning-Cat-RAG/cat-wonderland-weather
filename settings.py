from typing import Any, Dict
from pydantic import BaseModel
from enum import Enum

from cat import log, plugin
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
    """The stored settings with the API key decrypted, completed with the defaults of the model (as JSON values): the
    core saves only the fields sent, merged with the stored ones, so a field may never have been saved. The keys that
    the model does not declare are kept as they are."""
    settings, failed = decrypt_secrets(stored, StringCrypto())
    for key in failed:
        log.error(
            f"[connectors] agent {agent_id}: cannot decrypt '{key}' (was CAT_CRYPTO_KEY changed?): "
            "it is ignored until saved again"
        )
    return {**WonderlandWeatherSettings().model_dump(mode="json"), **settings}


@plugin
def settings_schema():
    return WonderlandWeatherSettings.model_json_schema()


@plugin
async def load_settings(plugin_id: str, agent_id: str) -> Dict[str, Any]:
    """The settings of the agent: every field of the model, the ones never saved with their default."""
    return _decrypted(await crud_plugins.get_setting(agent_id, plugin_id) or {}, agent_id)


@plugin
async def save_settings(plugin_id: str, settings: Dict[str, Any], agent_id: str) -> Dict[str, Any]:
    """Saves the fields sent (merged with the stored ones, the API key encrypted), and returns the settings as
    ``load_settings`` reads them."""
    stored = await crud_plugins.update_setting(agent_id, plugin_id, encrypt_secrets(settings, StringCrypto()))
    return _decrypted(stored, agent_id)
