"""Executable entry point: serve the Terraform plugin protocol over gRPC.

Terraform/OpenTofu launches this as ``terraform-provider-qubes``. ``run_provider``
performs the go-plugin handshake (magic cookie check, opens a socket, prints the
handshake line on stdout) and serves the plugin's gRPC services.
"""

from tf.runner import run_provider

from .provider import QubesProvider


def main():
    run_provider(QubesProvider())


if __name__ == "__main__":
    main()
