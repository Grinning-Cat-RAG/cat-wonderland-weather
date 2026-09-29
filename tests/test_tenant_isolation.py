"""The Cat is multi-tenant: every request of an agent asks OpenWeatherMap with the API key and the temperature unit of
that agent, whatever the requests of the other agents served before or at the same time by the same instance.

The Cat imports every ``.py`` file of the plugin, tests included: at import time this module needs only the stdlib,
the Cat and the plugin are loaded in ``setUpClass``.
"""
import asyncio
import random
import sys
import os
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

PLUGIN_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOADED = set()
CITIES = ("London", "Paris", "Tokyo")

PLUGIN = None
SETTINGS_MODULE = None
OPEN_WEATHER_MODULE = None
TOOL = None


class AsyncClient:
    """Stand-in of the asynchronous HTTP client of the plugin, answering with ``server.get``."""

    def __init__(self, server):
        self.server = server

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        return self.server.get(url, params=params)


def _load():
    """Loads the plugin once, from the test classes: the Cat imports the modules of the plugin (tests included) again
    when it loads it, so ``sys.modules`` (where unittest looks for ``setUpModule``) may hold another copy of this module:
    the globals are set here, in the module of the test classes."""
    global PLUGIN, SETTINGS_MODULE, OPEN_WEATHER_MODULE, TOOL
    if PLUGIN_PATH in LOADED:
        return
    from cat.looking_glass.mad_hatter.plugin import Plugin

    plugin = Plugin(PLUGIN_PATH)
    plugin._load_decorated_functions()
    PLUGIN = plugin
    SETTINGS_MODULE = sys.modules[plugin.overrides["load_settings"].function.__module__]
    TOOL = next(t for t in plugin.tools if t.name == "get_weather").func
    OPEN_WEATHER_MODULE = sys.modules[TOOL.__module__.rsplit(".", 1)[0] + ".open_weather"]
    LOADED.add(PLUGIN_PATH)


class FakeCrypto:
    def encrypt(self, plaintext: str) -> str:
        return f"enc:{plaintext[::-1]}"

    def decrypt(self, ciphertext: str) -> str:
        return ciphertext[4:][::-1]


class World:
    """The settings database (it yields to the event loop and can fail) and OpenWeatherMap (it echoes the key and the
    unit it is asked with, and can fail)."""

    def __init__(self, seed=0):
        self.data = {}
        self.rng = random.Random(seed)
        self.db_fault_rate = 0.0
        self.api_fault_rate = 0.0

    async def _latency(self):
        for _ in range(self.rng.choice((0, 1, 2, 5))):
            await asyncio.sleep(0)

    async def get_setting(self, agent_id, plugin_id):
        await self._latency()
        if self.db_fault_rate and self.rng.random() < self.db_fault_rate:
            raise ConnectionError("injected database fault")
        value = self.data.get((agent_id, plugin_id))
        return dict(value) if value is not None else None

    async def update_setting(self, agent_id, plugin_id, updated):
        await self._latency()
        current = {**self.data.get((agent_id, plugin_id), {}), **updated}
        self.data[(agent_id, plugin_id)] = current
        return dict(current)

    def get(self, url, params=None):
        import requests

        # the URL as requests sends it
        sent = requests.Request("GET", url, params=params).prepare().url
        query = parse_qs(urlsplit(sent).query, keep_blank_values=True)
        response = MagicMock()
        if self.api_fault_rate and self.rng.random() < self.api_fault_rate:
            response.raise_for_status.side_effect = RuntimeError("injected HTTP 500")
        response.json.return_value = {"list": [{
            "dt_txt": "2026-09-29 12:00:00",
            "main": {"temp": 20},
            "weather": [{"description": f"appid={query['appid'][0]} units={query['units'][0]}"}],
        }]}
        return response


def _cat(agent_id):
    cat = MagicMock()
    cat.agent_key = agent_id
    cat.mad_hatter.get_plugin.return_value.load_settings = lambda: PLUGIN.load_settings(agent_id)
    return cat


def _expected(city, key, unit):
    symbol = "°C" if unit == "metric" else "°F"
    return (
        f"Weather for {city}:\nDate: Tuesday 2026-09-29\nTemperature: 20{symbol}\n"
        f"Weather conditions: appid={key} units={unit}\n"
    )


class Harness:
    def __init__(self, seed=0):
        self.world = World(seed)

    def run(self, coro_factory):
        async def main():
            with patch.object(SETTINGS_MODULE, "crud_plugins", self.world), \
                    patch.object(SETTINGS_MODULE, "StringCrypto", FakeCrypto), \
                    patch.object(OPEN_WEATHER_MODULE, "_client", lambda: AsyncClient(self.world)):
                return await coro_factory()

        return asyncio.run(main())

    def save(self, agent_id, settings):
        return self.run(lambda: PLUGIN.save_settings(settings, agent_id))

    def ask(self, *requests):
        """Requests (agent, city) served at the same time by the same instance."""
        async def burst():
            return await asyncio.gather(
                *(TOOL(city, _cat(agent)) for agent, city in requests), return_exceptions=True
            )

        return self.run(burst)


class TestTenantIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _load()

    def test_every_agent_uses_its_own_key_and_unit(self):
        h = Harness()
        h.save("one", {"open_weather_api": "key-one", "temperature_unit": "metric"})
        h.save("two", {"open_weather_api": "key-two", "temperature_unit": "imperial"})
        self.assertEqual(h.ask(("one", "London")), [_expected("London", "key-one", "metric")])
        self.assertEqual(h.ask(("two", "Paris")), [_expected("Paris", "key-two", "imperial")])

    def test_concurrent_requests_of_several_agents(self):
        h = Harness()
        h.save("one", {"open_weather_api": "key-one", "temperature_unit": "metric"})
        h.save("two", {"open_weather_api": "key-two", "temperature_unit": "imperial"})
        results = h.ask(("two", "Tokyo"), ("one", "London"), ("two", "Paris"), ("one", "Tokyo"))
        self.assertEqual(results, [
            _expected("Tokyo", "key-two", "imperial"),
            _expected("London", "key-one", "metric"),
            _expected("Paris", "key-two", "imperial"),
            _expected("Tokyo", "key-one", "metric"),
        ])

    def test_a_changed_key_is_used_by_the_next_request(self):
        h = Harness()
        h.save("one", {"open_weather_api": "old", "temperature_unit": "metric"})
        h.ask(("one", "London"))
        h.save("one", {"open_weather_api": "new", "temperature_unit": "imperial"})
        self.assertEqual(h.ask(("one", "London")), [_expected("London", "new", "imperial")])

    def test_an_agent_without_settings_does_not_use_the_key_of_another_agent(self):
        h = Harness()
        h.save("one", {"open_weather_api": "key-one", "temperature_unit": "metric"})
        h.ask(("one", "London"))
        self.assertEqual(h.ask(("two", "London")), [_expected("London", "", "imperial")])

    def test_no_state_of_an_agent_in_the_modules_of_the_plugin(self):
        """Nothing survives a request: the client of OpenWeatherMap is created per request, not a singleton."""
        from cat.utils import singleton

        h = Harness()
        h.save("one", {"open_weather_api": "key-one", "temperature_unit": "metric"})
        h.ask(("one", "London"))
        self.assertNotIsInstance(OPEN_WEATHER_MODULE.WeatherAPI, singleton)
        self.assertNotIn(OPEN_WEATHER_MODULE.WeatherAPI, singleton.instances)
        self.assertIsNot(OPEN_WEATHER_MODULE.WeatherAPI("a"), OPEN_WEATHER_MODULE.WeatherAPI("a"))

    def test_the_first_forecast_of_a_day_is_the_one_of_the_day(self):
        response = MagicMock()
        response.json.return_value = {"list": [
            {"dt_txt": "2026-09-29 09:00:00", "main": {"temp": 18}, "weather": [{"description": "fog"}]},
            {"dt_txt": "2026-09-29 12:00:00", "main": {"temp": 22}, "weather": [{"description": "clear sky"}]},
            {"dt_txt": "2026-09-30 09:00:00", "main": {"temp": 15}, "weather": [{"description": "rain"}]},
        ]}
        server = MagicMock(get=lambda url, **kwargs: response)
        with patch.object(OPEN_WEATHER_MODULE, "_client", lambda: AsyncClient(server)):
            forecast = asyncio.run(OPEN_WEATHER_MODULE.WeatherAPI("key").weather("London", "metric"))
        self.assertEqual(forecast, (
            "Weather for London:\n"
            "Date: Tuesday 2026-09-29\nTemperature: 18°C\nWeather conditions: fog\n"
            "Date: Wednesday 2026-09-30\nTemperature: 15°C\nWeather conditions: rain\n"
        ))

    def test_a_request_without_city_is_not_served(self):
        self.assertEqual(Harness().ask(("one", "")), [None])

    def test_an_error_of_openweathermap_is_no_forecast(self):
        h = Harness()
        h.world.api_fault_rate = 1.0
        self.assertEqual(h.ask(("one", "London")), [None])

    def test_interleaved_requests_of_several_agents(self):
        """Stateful: agents change their settings while their requests and the ones of the other agents interleave,
        with faults of the database and of OpenWeatherMap. At every step every request is served with the key and the
        unit its agent had saved, or fails (no forecast or an error): never with the ones of another agent."""
        from hypothesis import settings, strategies as st
        from hypothesis.stateful import RuleBasedStateMachine, rule

        agents = st.sampled_from(["one", "two", "three:3", "four/4"])
        keys = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=0, max_size=8)
        units = st.sampled_from(["metric", "imperial"])
        test = self

        class Tenants(RuleBasedStateMachine):
            def __init__(self):
                super().__init__()
                self.h = Harness()
                # the settings every agent saved, as the tool must use them
                self.model = {}

            def expected(self, agent):
                saved = self.model.get(agent, {"open_weather_api": "", "temperature_unit": "imperial"})
                return saved["open_weather_api"], saved["temperature_unit"]

            @rule(agent=agents, key=keys, unit=units)
            def save(self, agent, key, unit):
                settings_ = {"open_weather_api": key, "temperature_unit": unit}
                self.h.save(agent, settings_)
                self.model[agent] = settings_

            @rule(
                requests=st.lists(st.tuples(agents, st.sampled_from(CITIES)), min_size=1, max_size=8),
                seed=st.integers(0, 2 ** 16),
                db_fault_rate=st.sampled_from([0.0, 0.0, 0.2]),
                api_fault_rate=st.sampled_from([0.0, 0.0, 0.2]),
            )
            def interleaved_requests(self, requests, seed, db_fault_rate, api_fault_rate):
                world = self.h.world
                world.rng.seed(seed)
                world.db_fault_rate, world.api_fault_rate = db_fault_rate, api_fault_rate
                try:
                    results = self.h.ask(*requests)
                finally:
                    world.db_fault_rate = world.api_fault_rate = 0.0
                for (agent, city), result in zip(requests, results):
                    if isinstance(result, BaseException):
                        test.assertIsInstance(result, ConnectionError)
                        test.assertTrue(db_fault_rate)
                    elif result is None:
                        test.assertTrue(api_fault_rate)
                    else:
                        test.assertEqual(result, _expected(city, *self.expected(agent)), (agent, city))

        Tenants.TestCase.settings = settings(max_examples=100, stateful_step_count=15, deadline=None)
        Tenants.TestCase("runTest").runTest()


if __name__ == "__main__":
    unittest.main()


class NonBlockingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _load()

    def test_a_slow_openweathermap_never_blocks_the_other_requests(self):
        # regression: the request was synchronous (requests.get) inside the async tool: while OpenWeatherMap answered,
        # the event loop was blocked, and so every other request served by the instance
        import time

        class SlowServer:
            async def get(self, url, params=None):
                await asyncio.sleep(0.3)
                response = MagicMock()
                response.json.return_value = {"list": []}
                return response

        class Client(AsyncClient):
            async def get(self, url, params=None):
                return await self.server.get(url, params=params)

        async def burst():
            api = OPEN_WEATHER_MODULE.WeatherAPI("key")
            with patch.object(OPEN_WEATHER_MODULE, "_client", lambda: Client(SlowServer())):
                begin = time.monotonic()
                await asyncio.gather(*(api.weather("London", "metric") for _ in range(5)))
                return time.monotonic() - begin

        self.assertLess(asyncio.run(burst()), 1.0, "five requests of 0.3 seconds run together")

    def test_the_client_has_a_timeout(self):
        client = OPEN_WEATHER_MODULE._client()
        self.assertEqual(client.timeout.read, OPEN_WEATHER_MODULE.TIMEOUT_SECONDS)
        asyncio.run(client.aclose())
