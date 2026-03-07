# Playing Locally

On macOS, the fastest local flow is:

```bash
./play
```

That launches `pokeblue.gbc` in SameBoy. Other variants:

```bash
./play red
./play blue-debug
./play blue --rebuild
```

Prerequisites:

```bash
brew upgrade rgbds
brew install --cask sameboy
```

The launcher rebuilds only when the selected ROM is out of date, unless you pass
`--rebuild`.

For the programmable runtime and telemetry service, see
[tools/runtime/README.md](tools/runtime/README.md).

Shortcut:

```bash
./runtime --rom blue --port 8765 --auto-run --boot-frames 600
```
