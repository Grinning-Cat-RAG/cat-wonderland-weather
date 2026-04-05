from pydantic import BaseModel
from enum import Enum
from cat import plugin


class UnitSelect(Enum):
    METRIC = "metric"
    IMPERIAL = "imperial"


class WonderlandWeatherSettings(BaseModel):
    open_weather_api: str = ""
    temperature_unit: UnitSelect = UnitSelect.IMPERIAL


@plugin
def settings_schema():
    return WonderlandWeatherSettings.model_json_schema()
