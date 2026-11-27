"""Validação da imagem Docker do OmniRTKSync usando Testcontainers.

Garante que a imagem Docker gerada:
1. Inicializa sem falhas com as configurações padrão.
2. Expõe o dashboard web na porta 9090.
3. Responde requisições HTTP em /login com código 200 e página HTML válida.
4. Responde no endpoint /healthz com o cabeçalho e corpo documentados.
5. Permite executar a CLI (omnirtksync --help) com sucesso dentro do container.
"""

from contextlib import closing
import os
import sys
import time
import unittest
from unittest.mock import Mock, patch
import urllib.request
import urllib.error

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKER_IMAGE_PADRAO = "omnirtksync:test"


def obter_ou_construir_imagem(cliente, nome_imagem: str) -> str:
    imagens = cliente.images.list(name=nome_imagem)
    if imagens:
        return nome_imagem

    # Constrói localmente se não existir pré-construída
    dockerfile = os.path.join(RAIZ, "Dockerfile")
    if os.path.exists(dockerfile):
        imagem, _ = cliente.images.build(path=RAIZ, tag=nome_imagem, rm=True)
        return nome_imagem

    raise RuntimeError(f"Imagem {nome_imagem} não encontrada e Dockerfile não localizado em {RAIZ}")


class TestOmniRTKSyncContainer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        requested_image = os.environ.get("TEST_DOCKER_IMAGE")
        try:
            import testcontainers
            import docker
            with closing(docker.from_env()) as client:
                client.ping()
                if requested_image is None:
                    cls.imagem = obter_ou_construir_imagem(client, DOCKER_IMAGE_PADRAO)
                else:
                    if not requested_image.strip():
                        raise ValueError("TEST_DOCKER_IMAGE must name a locally built image")
                    client.images.get(requested_image)
                    cls.imagem = requested_image
        except Exception as error:
            if requested_image is not None:
                raise RuntimeError(f"Could not validate requested Docker image {requested_image!r}: {error}") from error
            raise unittest.SkipTest(f"Optional Docker validation is unavailable: {error}") from error

    def test_container_web_dashboard_e_cli(self):
        from testcontainers.core.container import DockerContainer

        with DockerContainer(self.imagem).with_exposed_ports(9090) as container:
            host = container.get_container_host_ip()
            porta = container.get_exposed_port(9090)
            url_login = f"http://{host}:{porta}/login"
            url_healthz = f"http://{host}:{porta}/healthz"

            # 1. Aguarda o servidor web aceitar conexões (até 15s)
            conectou = False
            ultimo_erro = None
            for _ in range(15):
                time.sleep(1)
                try:
                    with urllib.request.urlopen(url_login, timeout=2) as resp:
                        if resp.status == 200:
                            conectou = True
                            break
                except Exception as e:
                    ultimo_erro = e

            self.assertTrue(conectou, f"Não foi possível conectar ao dashboard web na porta {porta}: {ultimo_erro}")

            # 2. Valida resposta do endpoint /login
            with urllib.request.urlopen(url_login, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                cabecalhos = dict(resp.getheaders())
                self.assertIn("text/html", cabecalhos.get("Content-Type", ""))
                corpo = resp.read().decode("utf-8")
                self.assertIn("OmniRTKSync", corpo)
                self.assertIn("<!DOCTYPE html>", corpo)

            # 3. Valida resposta do endpoint /healthz
            try:
                with urllib.request.urlopen(url_healthz, timeout=5) as resp:
                    status_healthz = resp.status
                    corpo_healthz = resp.read().decode("utf-8")
                    cabecalho_server = resp.headers.get("Server", "")
            except urllib.error.HTTPError as e:
                with e:
                    status_healthz = e.code
                    corpo_healthz = e.read().decode("utf-8")
                    cabecalho_server = e.headers.get("Server", "")

            # Sem banco montado e sem gateway externo, o healthz esperado é 503 DATABASE_NOT_READY
            self.assertIn(status_healthz, (200, 503))
            self.assertEqual(cabecalho_server, "OmniRTKSync")
            self.assertTrue(
                "OK" in corpo_healthz or "DATABASE_NOT_READY" in corpo_healthz or "GATEWAY_SERVICE_UNREACHABLE" in corpo_healthz,
                f"Corpo inesperado do healthz: {corpo_healthz}",
            )

            # 4. Valida execução da CLI dentro do container
            codigo_saida, saida = container.exec(["omnirtksync", "--help"])
            self.assertEqual(codigo_saida, 0, f"Comando omnirtksync --help falhou com código {codigo_saida}: {saida}")
            texto_saida = saida.decode("utf-8")
            self.assertIn("omnirtksync", texto_saida.lower())
            self.assertIn("--daemon", texto_saida)


class TestContainerPrerequisites(unittest.TestCase):
    def test_optional_run_can_skip_missing_dependencies(self):
        with patch.dict(os.environ, clear=True), patch.dict(sys.modules, {"testcontainers": None}):
            with self.assertRaises(unittest.SkipTest):
                TestOmniRTKSyncContainer.setUpClass()

    def test_requested_image_cannot_skip_missing_dependencies(self):
        with patch.dict(os.environ, {"TEST_DOCKER_IMAGE": "requested-image:test"}), patch.dict(
            sys.modules, {"testcontainers": None}
        ):
            with self.assertRaisesRegex(RuntimeError, "requested-image:test"):
                TestOmniRTKSyncContainer.setUpClass()

    def test_requested_image_must_already_exist_locally(self):
        docker = Mock()
        client = docker.from_env.return_value
        client.images.get.side_effect = LookupError("missing local image")
        with patch.dict(os.environ, {"TEST_DOCKER_IMAGE": "requested-image:test"}), patch.dict(
            sys.modules, {"docker": docker, "testcontainers": Mock()}
        ):
            with self.assertRaisesRegex(RuntimeError, "missing local image"):
                TestOmniRTKSyncContainer.setUpClass()
        client.images.get.assert_called_once_with("requested-image:test")
        client.images.build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
