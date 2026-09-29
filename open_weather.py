import datetime

import httpx

#: seconds to wait for OpenWeatherMap: a slow service never holds a request (or the instance) for long
TIMEOUT_SECONDS = 10.0


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT_SECONDS)


class WeatherAPI:
    """A client of OpenWeatherMap with the API key of one agent.

    The Cat is multi-tenant: a client is created per request with the settings of the agent of the request, and it is
    never shared (no singleton, no module state), so an agent never uses the API key of another one.
    """

    def __init__(self, api_key: str):
        self._base_url = "https://api.openweathermap.org/data/2.5/forecast"
        self.api_key = api_key

    async def weather(self, city: str, units: str):
        """The daily forecast of ``city``, asked without blocking the event loop (the other requests served by the
        instance go on meanwhile). The city is sent as a parameter of the query, encoded by the HTTP client: a name
        with ``&``, ``=`` or ``#`` never changes the unit or the API key of the request."""
        temperature_symbol = "°C" if units == "metric" else "°F"
        params = {"q": city, "units": units, "appid": self.api_key}

        async with _client() as client:
            response = await client.get(self._base_url, params=params)
        response.raise_for_status()

        data = response.json()
        daily_forecasts = {}

        for forecast in data["list"]:
            date = forecast["dt_txt"].split()[0]
            temperature = forecast["main"]["temp"]
            description = forecast["weather"][0]["description"]

            if date not in daily_forecasts:
                daily_forecasts[date] = {"temperature": temperature, "description": description}

        result = f"Weather for {city}:\n"

        for date, forecast in daily_forecasts.items():
            day = datetime.datetime.strptime(date, "%Y-%m-%d")
            result += f"Date: {day.strftime('%A %Y-%m-%d')}\n"
            result += f"Temperature: {forecast['temperature']}{temperature_symbol}\n"
            result += f"Weather conditions: {forecast['description']}\n"

        return result
