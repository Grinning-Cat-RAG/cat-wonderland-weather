from cat import tool, log

from .open_weather import WeatherAPI


@tool(examples=["weather today", "weather tomorrow", "weather Sunday"])
async def get_weather(city: str, cat):
    """
    Replies to "weather today", "what the weather will be like on Tuesday", "What will the weather be like this weekend?"
    and similar questions. The city can be specified as well, e.g. "what the weather will be like in New York on Tuesday".
    If the city is not specified, then do not use this tool at all.
    """
    if not city:
        return None

    # the settings of the agent of this request: the client is created per request, with its API key
    settings = await cat.mad_hatter.get_plugin().load_settings()
    weather_api = WeatherAPI(settings["open_weather_api"])
    try:
        weather_data = await weather_api.weather(city, settings["temperature_unit"])
        return weather_data
    except Exception as e:
        log.warning(f"Error getting weather data: {e}")
        return None
