# ForgeSound-Engine
A combination of Audacity and Surge XT for creating sounds and customising them either by the user or by artificial intelligence.

## Status

Research and stage 0 (build and render reproducibility spike). No application code yet.

- Research report (in Russian): [docs/research/forgesound-research.md](docs/research/forgesound-research.md)
- Stage 0 plan, commands and results: [docs/plan/stage0.md](docs/plan/stage0.md)

## Stage 0 quick start (NixOS, flakes enabled)

```sh
nix flake lock
nix flake check -L
nix develop -c python -I spike/stage0/stage0_check.py --play --out spike/stage0/results-main
```

License: GPL-3.0-or-later (Surge XT, whose Python bindings ForgeSound builds and runs, is GPL-3.0).
