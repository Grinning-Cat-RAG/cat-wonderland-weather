import requests
import datetime

from cat.utils import singleton


@singleton
class WeatherAPI:
    def __init__(self, api_key: str):
        self._base_url = "https://api.openweathermap.org/data/2.5/forecast"
        self.api_key = api_key

    def weather(self, city: str, units: str):
        temperature_symbol = "°C" if units == "metric" else "°F"
        url = f"{self._base_url}?q={city}&units={units}&appid={self.api_key}"

        response = requests.get(url)
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
