"""The settings as the plugin loads them: plain JSON values (the unit is ``"imperial"``, not the enum), whether they
are stored or not, with the API key encrypted at rest.

Run from the core root: ``python -m pytest cat/plugins/cat-wonderland-weather/tests``.

The Cat imports every ``.py`` file of the plugin, tests included: at import time this module needs only the stdlib,
the Cat and the plugin are loaded in ``setUpClass``.
"""
import asyncio
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

PLUGIN_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOADED = set()
AGENT_ID = "agent"

PLUGIN = None
SETTINGS_MODULE = None
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
    the globals are set here, in the module of the test classes, after the loading."""
    global PLUGIN, SETTINGS_MODULE, TOOL
    if PLUGIN_PATH in LOADED:
        return
    from cat.looking_glass.mad_hatter.plugin import Plugin

    plugin = Plugin(PLUGIN_PATH)
    plugin._load_decorated_functions()
    PLUGIN = plugin
    SETTINGS_MODULE = sys.modules[plugin.overrides["load_settings"].function.__module__]
    TOOL = next(t for t in plugin.tools if t.name == "get_weather").func
    LOADED.add(PLUGIN_PATH)


class FakeCrypto:
    def encrypt(self, plaintext: str) -> str:
        return f"enc:{plaintext[::-1]}"

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith("enc:"):
            raise ValueError("invalid token")
        return ciphertext[4:][::-1]


class FakeStore:
    """``cat.db.cruds.plugins``, in memory, with the merge of the core."""

    def __init__(self):
        self.data = {}

    async def get_setting(self, agent_id, plugin_id):
        value = self.data.get((agent_id, plugin_id))
        return dict(value) if value is not None else None

    async def update_setting(self, agent_id, plugin_id, updated):
        current = {**self.data.get((agent_id, plugin_id), {}), **updated}
        self.data[(agent_id, plugin_id)] = current
        return dict(current)


def _sent_url(url, params=None):
    """The URL as ``requests`` sends it, with the parameters of the query encoded."""
    import requests

    return requests.Request("GET", url, params=params).prepare().url


def _query(url):
    from urllib.parse import parse_qs, urlsplit

    return parse_qs(urlsplit(url).query, keep_blank_values=True)


class FakeOpenWeather:
    def __init__(self):
        self.urls = []

    def get(self, url, params=None):
        self.urls.append(_sent_url(url, params))
        response = MagicMock()
        response.json.return_value = {"list": [
            {"dt_txt": "2026-09-29 12:00:00", "main": {"temp": 70}, "weather": [{"description": "clear sky"}]},
        ]}
        return response


class TestSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _load()

    def setUp(self):
        self.store = FakeStore()
        self.errors = []
        log = MagicMock()
        log.error.side_effect = self.errors.append
        for patcher in (
            patch.object(SETTINGS_MODULE, "crud_plugins", self.store),
            patch.object(SETTINGS_MODULE, "StringCrypto", FakeCrypto),
            patch.object(SETTINGS_MODULE, "log", log),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def load(self):
        return asyncio.run(PLUGIN.load_settings(AGENT_ID))

    def save(self, settings):
        return asyncio.run(PLUGIN.save_settings(settings, AGENT_ID))

    def test_the_schema_offers_the_units(self):
        schema = PLUGIN.settings_schema()
        self.assertEqual(schema["$defs"]["UnitSelect"]["enum"], ["metric", "imperial"])

    def test_the_defaults_are_json_values(self):
        loaded = self.load()
        self.assertEqual(loaded, {"open_weather_api": "", "temperature_unit": "imperial"})
        self.assertEqual(json.loads(json.dumps(loaded)), loaded)

    def test_the_stored_settings_are_returned_with_the_key_in_plaintext(self):
        returned = self.save({"open_weather_api": "key", "temperature_unit": "metric"})
        self.assertEqual(returned, {"open_weather_api": "key", "temperature_unit": "metric"})
        self.assertEqual(self.store.data[(AGENT_ID, PLUGIN.id)]["open_weather_api"], "enc:yek")
        self.assertEqual(self.load(), {"open_weather_api": "key", "temperature_unit": "metric"})

    def test_an_undecryptable_key_is_logged_and_ignored(self):
        self.store.data[(AGENT_ID, PLUGIN.id)] = {"open_weather_api": "garbage", "temperature_unit": "metric"}
        self.assertEqual(self.load(), {"open_weather_api": "", "temperature_unit": "metric"})
        self.assertEqual(len(self.errors), 1)
        self.assertNotIn("garbage", self.errors[0])

    def ask(self, city, open_weather):
        cat = MagicMock()
        cat.mad_hatter.get_plugin.return_value.load_settings = lambda: PLUGIN.load_settings(AGENT_ID)
        open_weather_module = sys.modules[TOOL.__module__.rsplit(".", 1)[0] + ".open_weather"]
        with patch.object(open_weather_module, "_client", lambda: AsyncClient(open_weather)):
            return asyncio.run(TOOL(city, cat))

    def test_the_forecast_is_asked_in_the_default_unit(self):
        open_weather = FakeOpenWeather()
        forecast = self.ask("London", open_weather)
        self.assertEqual(_query(open_weather.urls[0])["units"], ["imperial"])
        self.assertIn("Temperature: 70°F", forecast)

    def test_a_partial_save_is_completed_with_the_defaults(self):
        """The core saves only the fields sent, merged with the stored ones: the missing ones are the defaults."""
        self.assertEqual(self.save({"open_weather_api": "key"}), {"open_weather_api": "key", "temperature_unit": "imperial"})
        self.assertEqual(self.store.data[(AGENT_ID, PLUGIN.id)], {"open_weather_api": "enc:yek"})
        self.assertEqual(self.load(), {"open_weather_api": "key", "temperature_unit": "imperial"})
        open_weather = FakeOpenWeather()
        self.assertIn("Temperature: 70°F", self.ask("London", open_weather))
        self.assertEqual(_query(open_weather.urls[0])["appid"], ["key"])

    def test_stored_settings_without_the_key(self):
        self.store.data[(AGENT_ID, PLUGIN.id)] = {"temperature_unit": "metric"}
        self.assertEqual(self.load(), {"open_weather_api": "", "temperature_unit": "metric"})
        self.assertEqual(self.errors, [])

    def test_the_keys_that_the_model_does_not_declare_are_kept(self):
        self.store.data[(AGENT_ID, PLUGIN.id)] = {"legacy": 1}
        self.assertEqual(self.load(), {"open_weather_api": "", "temperature_unit": "imperial", "legacy": 1})

    def test_the_city_is_one_parameter_of_the_query(self):
        """A city with ``&``, ``=``, ``#`` or spaces never changes the unit or the key of the request."""
        self.save({"open_weather_api": "key", "temperature_unit": "metric"})
        for city in ("Paris&units=imperial&appid=stolen", "a=b", "São Paulo", "x#y", "50% off?"):
            with self.subTest(city=city):
                open_weather = FakeOpenWeather()
                self.assertIsNotNone(self.ask(city, open_weather))
                self.assertEqual(_query(open_weather.urls[0]), {"q": [city], "units": ["metric"], "appid": ["key"]})

    def test_partial_saves_and_loads(self):
        """Stateful: sequences of partial saves (any subset of the fields, as the core merges them) and of loads, with
        faults of the database. At every step the loaded settings (and the ones a save returns) are the defaults
        completed with every field saved so far, the API key is never stored in plaintext, and the tool asks
        OpenWeatherMap with the loaded key and unit."""
        from hypothesis import settings, strategies as st
        from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

        defaults = {"open_weather_api": "", "temperature_unit": "imperial"}
        fields = st.fixed_dictionaries({}, optional={
            "open_weather_api": st.text(alphabet="abcxyz0123-", max_size=6),
            "temperature_unit": st.sampled_from(["metric", "imperial"]),
        })
        test = self

        class FaultyStore(FakeStore):
            fail = False

            async def get_setting(self, agent_id, plugin_id):
                if self.fail:
                    raise ConnectionError("injected database fault")
                return await super().get_setting(agent_id, plugin_id)

            async def update_setting(self, agent_id, plugin_id, updated):
                if self.fail:
                    raise ConnectionError("injected database fault")
                return await super().update_setting(agent_id, plugin_id, updated)

        class PartialSettings(RuleBasedStateMachine):
            def __init__(self):
                super().__init__()
                self.store = FaultyStore()
                self.model = dict(defaults)
                self.patchers = [
                    patch.object(SETTINGS_MODULE, "crud_plugins", self.store),
                    patch.object(SETTINGS_MODULE, "StringCrypto", FakeCrypto),
                ]
                for patcher in self.patchers:
                    patcher.start()

            def teardown(self):
                for patcher in self.patchers:
                    patcher.stop()

            @rule(partial=fields, fault=st.booleans())
            def save(self, partial, fault):
                self.store.fail = fault
                try:
                    returned = asyncio.run(PLUGIN.save_settings(partial, AGENT_ID))
                except ConnectionError:
                    test.assertTrue(fault)
                    return
                finally:
                    self.store.fail = False
                test.assertFalse(fault)
                self.model.update(partial)
                test.assertEqual(returned, self.model)

            @rule(fault=st.booleans())
            def load(self, fault):
                self.store.fail = fault
                try:
                    loaded = asyncio.run(PLUGIN.load_settings(AGENT_ID))
                except ConnectionError:
                    test.assertTrue(fault)
                    return
                finally:
                    self.store.fail = False
                test.assertEqual(loaded, self.model)

            @rule()
            def ask(self):
                open_weather = FakeOpenWeather()
                with patch.object(SETTINGS_MODULE, "log", MagicMock()):
                    forecast = test.ask("London", open_weather)
                symbol = "°C" if self.model["temperature_unit"] == "metric" else "°F"
                test.assertIn(f"Temperature: 70{symbol}", forecast)
                test.assertEqual(_query(open_weather.urls[0]), {
                    "q": ["London"], "units": [self.model["temperature_unit"]],
                    "appid": [self.model["open_weather_api"]],
                })

            @invariant()
            def the_key_is_never_stored_in_plaintext(self):
                stored = self.store.data.get((AGENT_ID, PLUGIN.id), {})
                key = stored.get("open_weather_api")
                if key:
                    test.assertEqual(key, FakeCrypto().encrypt(self.model["open_weather_api"]))

        PartialSettings.TestCase.settings = settings(max_examples=100, stateful_step_count=15, deadline=None)
        PartialSettings.TestCase("runTest").runTest()


if __name__ == "__main__":
    unittest.main()
