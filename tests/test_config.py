import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from aide.config import (
    Config, ConfigError, load_config, missing_fields, read_file_values,
    run_wizard, write_config_values,
)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "sub" / "config.toml"

    def test_defaults_when_nothing_is_set(self):
        cfg = load_config(self.path, env={})
        self.assertEqual(cfg, Config())
        self.assertEqual(missing_fields(cfg), ["base_url", "api_key", "model"])

    def test_precedence_cli_over_env_over_file(self):
        write_config_values({"model": "from-file", "max_tokens": 111}, self.path)
        self.assertEqual(load_config(self.path, env={}).model, "from-file")
        env = {"OMNIROUTE_MODEL": "from-env"}
        self.assertEqual(load_config(self.path, env=env).model, "from-env")
        cfg = load_config(self.path, env=env, overrides={"model": "from-cli", "max_tokens": None})
        self.assertEqual((cfg.model, cfg.max_tokens), ("from-cli", 111))

    def test_env_values_are_coerced(self):
        cfg = load_config(self.path, env={"OMNIROUTE_TEMPERATURE": "0.7",
                                          "OMNIROUTE_MAX_TOKENS": "2048",
                                          "OMNIROUTE_STREAM_USAGE": "false"})
        self.assertEqual((cfg.temperature, cfg.max_tokens, cfg.stream_usage), (0.7, 2048, False))

    def test_bad_values_raise_friendly_errors(self):
        with self.assertRaises(ConfigError):
            load_config(self.path, env={"OMNIROUTE_TEMPERATURE": "hot"})
        with self.assertRaises(ConfigError):
            load_config(self.path, env={"OMNIROUTE_TEMPERATURE": "5"})
        with self.assertRaises(ConfigError):
            load_config(self.path, env={"OMNIROUTE_BASE_URL": "ftp://x"})
        with self.assertRaises(ConfigError):
            load_config(self.path, env={"OMNIROUTE_MAX_TOKENS": "12.5"})

    def test_base_url_is_normalized(self):
        for raw in ("https://h/v1/", "https://h/v1/chat/completions", " https://h/v1 "):
            cfg = load_config(self.path, env={"OMNIROUTE_BASE_URL": raw})
            self.assertEqual(cfg.base_url, "https://h/v1")

    def test_saved_file_has_mode_600_and_roundtrips_odd_strings(self):
        model = 'weird "model" \\ name é 😀'
        write_config_values({"api_key": "sk-secret", "model": model, "timeout": 30.0}, self.path)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(read_file_values(self.path)["model"], model)
        cfg = load_config(self.path, env={})
        self.assertEqual((cfg.api_key, cfg.timeout), ("sk-secret", 30.0))

    def test_loose_permissions_are_tightened_on_load(self):
        write_config_values({"model": "m"}, self.path)
        os.chmod(self.path, 0o644)
        load_config(self.path, env={})
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_invalid_toml_is_reported(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("this is = = not toml", encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(self.path, env={})


class WizardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "config.toml"

    @staticmethod
    def feeder(*answers):
        it = iter(answers)
        return lambda prompt: next(it)

    def test_asks_only_for_missing_values_and_never_copies_env_key_to_disk(self):
        current = load_config(self.path, env={"OMNIROUTE_API_KEY": "env-key-12345"})
        def no_secret(prompt):
            raise AssertionError("must not ask for the key")
        run_wizard(self.path, current, input_fn=self.feeder("https://gw.example/v1/", "my-model"),
                   secret_fn=no_secret, out=lambda s: None)
        self.assertEqual(read_file_values(self.path),
                         {"base_url": "https://gw.example/v1", "model": "my-model"})

    def test_required_values_are_asked_again_when_empty(self):
        current = load_config(self.path, env={})
        run_wizard(self.path, current,
                   input_fn=self.feeder("", "not-a-url", "https://h/v1", "m"),
                   secret_fn=self.feeder("", "key-123456"), out=lambda s: None)
        self.assertEqual(read_file_values(self.path)["api_key"], "key-123456")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_force_with_enter_keeps_existing_values(self):
        write_config_values({"base_url": "https://h/v1", "api_key": "k-123456", "model": "m"},
                            self.path)
        current = load_config(self.path, env={})
        run_wizard(self.path, current, force=True, input_fn=self.feeder("", ""),
                   secret_fn=self.feeder(""), out=lambda s: None)
        self.assertEqual(read_file_values(self.path)["model"], "m")

    def test_eof_cancels_without_saving(self):
        current = load_config(self.path, env={})
        def eof(prompt):
            raise EOFError
        with self.assertRaises(ConfigError):
            run_wizard(self.path, current, input_fn=eof, secret_fn=eof, out=lambda s: None)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
