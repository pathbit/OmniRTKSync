"""Testes unitários da CLI do OmniRTKSync."""

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from omni_rtksync.cli import OmniSyncEngine, main, print_status
from omni_rtksync.config import Settings


def setUpModule():
    """Nenhum teste deste modulo pode sair para a internet.

    main() e print_status() montam Settings a partir do ambiente, onde a
    validacao viva de credenciais vem ligada por padrao.
    """
    os.environ["CREDENTIAL_CHECK_ENABLED"] = "0"


def tearDownModule():
    os.environ.pop("CREDENTIAL_CHECK_ENABLED", None)




class TestOmniCLI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "storage.sqlite")
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE provider_connections (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                name TEXT,
                access_token TEXT,
                refresh_token TEXT,
                api_key TEXT,
                expires_at TEXT,
                test_status TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        c.execute("""
            INSERT INTO provider_connections
            (id, provider, name, access_token, refresh_token, api_key, expires_at, test_status, created_at, updated_at)
            VALUES
            ('test-1', 'groq', 'Groq Account', NULL, NULL, 'test-key', '1789000000000', 'ok', '2026-09-12T00:00:00Z', '2026-09-12T00:00:00Z')
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sync_engine_once(self):
        # validate_credentials desligado de proposito: sem isso o ciclo chama a
        # API do provedor de verdade e o teste passa a depender da internet --
        # foi assim que a CI quebrou antes.
        settings = Settings(
            db_path=self.db_path,
            sync_interval=300,
            enable_web=False,
            validate_credentials=False,
        )
        engine = OmniSyncEngine(settings)
        res = engine.sync_all()
        self.assertTrue(res["success"])
        self.assertEqual(res["total"], 1)

    def test_print_status(self):
        settings = Settings(db_path=self.db_path, enable_web=False, validate_credentials=False)
        # Deve executar sem levantar exceção
        print_status(settings)

    def test_cli_main_status(self):
        with patch("sys.argv", ["omnirtksync", "--db-path", self.db_path, "--status"]):
            main()

    def test_cli_main_once(self):
        with patch("sys.argv", ["OmniRTKSync", "--db-path", self.db_path, "--once"]):
            main()

    def test_one_shot_reports_an_invalid_database(self):
        database = Path(self.temp_dir.name) / "invalid.sqlite"
        database.write_text("invalid SQLite file", encoding="utf-8")
        args = ["omnirtksync", "--db-path", str(database), "--once", "--no-web"]
        with patch("sys.argv", args), redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as exit_signal:
                main()
        self.assertEqual(exit_signal.exception.code, 1)
        self.assertIn("SQLite database", stderr.getvalue())

    def test_one_shot_reports_that_the_gateway_has_not_created_the_database(self):
        database = Path(self.temp_dir.name) / "absent.sqlite"
        args = ["omnirtksync", "--db-path", str(database), "--once", "--no-web"]
        with patch("sys.argv", args), redirect_stdout(io.StringIO()) as stdout:
            main()
        self.assertIn("Waiting for the gateway", stdout.getvalue())
        self.assertFalse(database.exists())

    def test_one_shot_returns_failure_if_a_connection_cannot_be_synchronized(self):
        args = ["omnirtksync", "--db-path", self.db_path, "--once", "--no-web"]
        with patch("sys.argv", args), patch.object(OmniSyncEngine, "sync_all", return_value={"success": False}):
            with redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit) as exit_signal:
                main()
        self.assertEqual(exit_signal.exception.code, 1)
        self.assertIn("could not be synchronized", stderr.getvalue())

    def test_database_argument_selects_logging_and_recovery_directory(self):
        requested_directory = Path(self.db_path).parent
        environment_directory = requested_directory / "environment"
        environment_directory.mkdir()
        env = {
            "DB_PATH": str(environment_directory / "storage.sqlite"),
            "DATA_DIR": "",
            "DASHBOARD_RECOVERY_HASH": "",
        }
        args = ["omnirtksync", "--db-path", self.db_path, "--status"]
        with patch.dict(os.environ, env), patch("sys.argv", args), patch(
            "omni_rtksync.cli.setup_logging"
        ) as setup_logging:
            main()

        setup_logging.assert_called_once_with(self.db_path)
        recovery_file = requested_directory / ".dashboard_recovery"
        self.assertTrue(recovery_file.is_file())
        self.assertFalse((environment_directory / recovery_file.name).exists())
        logged_arguments = setup_logging.return_value.warning.call_args.args
        self.assertEqual(logged_arguments[-1], str(recovery_file))
        self.assertNotIn(recovery_file.read_text(encoding="utf-8").strip(), str(logged_arguments))


if __name__ == "__main__":
    unittest.main()
