# Vendored Python wheels

The provider bundles a handful of version-sensitive PyPI dependencies rather than relying on the
distro's, because the target distros ship versions too old for `tf`'s generated gRPC/protobuf code.
`qubesadmin` is **not** bundled — it is shipped by Qubes (`qubes-core-admin-client` /
`python3-qubesadmin`) and resolved from the system site-packages at runtime.

## What's here

Only the `*.whl.sha256` files are committed — each contains the expected SHA-256 (bare hex, one
line) of the wheel of the same name. The wheels themselves are **not** in git; they are fetched at
build time (see below) and land in this directory.

Bundled packages: `tf`, `grpcio`, `protobuf`, `cryptography`, `cffi`, `pycparser`, `msgpack`,
`typing_extensions`. Native packages (`grpcio`, `cryptography`, `cffi`, `msgpack`) ship one wheel
per supported CPython ABI so a single wheelhouse covers multiple distro Python versions — e.g.
`cp313` (Debian trixie) and `cp314` (Fedora 43); `cryptography` uses one `cp311-abi3` wheel that
covers 3.11+.

## How they're consumed

- **Fetch (both distros):** `.qubesbuilder` lists each wheel by `url:` + `sha256:` (pointing at the
  matching `vendor/<name>.whl.sha256`); qubes-builder downloads and verifies them. The DEB build's
  `source: commands` then copies `*.whl` from the distfiles into `@SOURCE_DIR@/vendor/`.
- **Debian:** `debian/rules` installs the venv fully offline with
  `--extra-pip-arg=--no-index --extra-pip-arg=--find-links=$(CURDIR)/vendor`.
- **Fedora:** `rpm_spec/qubes-terraform.spec.in` lists each wheel as `SourceN`, copies them into a
  `_wheelhouse`, and `pip install --no-index --find-links _wheelhouse`.

## Refreshing a wheel

When bumping a bundled dependency, keep all four references in sync:

1. Update the `url:` and the `vendor/<name>.whl.sha256` in **`.qubesbuilder`** (add a per-ABI wheel
   for each supported CPython if the package is native).
2. Update the `SourceN` list **and** the `cp %{SOURCEn} ...` line in
   **`rpm_spec/qubes-terraform.spec.in`**.
3. The Debian side needs no per-wheel edit — it globs `vendor/*.whl` via `--find-links`.
4. Rename/replace the corresponding `vendor/*.whl.sha256` (the filename must match the wheel).
