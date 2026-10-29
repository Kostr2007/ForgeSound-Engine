{
  description = "ForgeSound: a sound design studio for short game sounds";

  inputs = {
    # nixos-26.05 at the revision stage 0 was tested on (2026-10-28)
    nixpkgs.url = "github:NixOS/nixpkgs/08d3a97586f6b6343b3b5e799980773629432610";
    # Surge main at a pinned commit, with submodules: its Python bindings have
    # the MSEG / formula / parameter-feature API that release 1.3.4 lacks
    surge-main = {
      url = "git+https://github.com/surge-synthesizer/surge?rev=dab78c6170732b3883216e569a84bf084ae0ea97&submodules=1";
      flake = false;
    };
  };

  outputs =
    { self, nixpkgs, surge-main }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      site = pkgs.python3.sitePackages;

      # release 1.3.4: the sources and patches of nixpkgs' surge-xt
      surgepy-release = pkgs.callPackage ./nix/surgepy.nix { };
      surgepy-main = surgepy-release.overrideAttrs (old: {
        version = "1.3.4-unstable-2026-10-25";
        src = surge-main;
        # main no longer needs the CMake-4 patch nixpkgs applies to 1.3.4
        patches = [ ];
        postPatch = ''
          export XDG_DOCUMENTS_DIR=$(mktemp -d)
          # CMake runs this script to build LuaJIT (formula modulators);
          # its #!/bin/bash does not exist in the build sandbox
          patchShebangs libs/luajitlib
        '';
      });

      # the main interpreter: no surgepy in it (see forgesound/surge_worker.py)
      python = pkgs.python3.withPackages (ps: [
        ps.numpy
        ps.scipy
        ps.soundfile
        ps.sounddevice
        ps.fastapi
        ps.uvicorn
        ps.pytest
      ]);

      # only the application sources, so a change in docs/ does not rebuild it
      appSrc = pkgs.lib.fileset.toSource {
        root = ./.;
        fileset = ./forgesound;
      };

      # `nix run`: the local studio on http://127.0.0.1:8737/
      forgesound = pkgs.writeShellApplication {
        name = "forgesound";
        runtimeInputs = [
          python
          pkgs.ffmpeg
        ];
        text = ''
          unset LD_LIBRARY_PATH
          export SURGEPY_SITE="${surgepy-main}/${site}"
          export SURGE_DATA_HOME="${surgepy-main}/share/surge-xt"
          export PYTHONPATH="${appSrc}"
          # -P: the packaged sources, not a forgesound/ in the current directory
          exec python -s -P -m forgesound "$@"
        '';
      };

      shellFor = sp: pkgs.mkShell {
        packages = [
          python
          pkgs.ffmpeg
        ];
        # only the Surge worker process gets these
        SURGEPY_SITE = "${sp}/${site}";
        SURGE_DATA_HOME = "${sp}/share/surge-xt";
        FORGE_SURGE_VERSION = sp.version;
        # NixOS with services.pipewire.jack.enable exports LD_LIBRARY_PATH with
        # the host's alsa-lib / pipewire-jack, built against the host's glibc;
        # PortAudio from the pinned nixpkgs then fails to load (GLIBC_2.43 not found)
        shellHook = ''
          unset LD_LIBRARY_PATH
        '';
      };

      # nix flake check: the worker imports surgepy and renders a note in the
      # build sandbox (no audio device, no network, no home directory)
      smoke = name: sp: pkgs.runCommand "${name}-smoke" { nativeBuildInputs = [ python ]; } ''
        export HOME=$TMPDIR PYTHONPATH=${sp}/${site} SURGE_DATA_HOME=${sp}/share/surge-xt
        echo '{"id": 1, "op": "render", "sr": 48000, "out": "'$TMPDIR'/o.npy", "params": {"scene/0/osc/0/retrigger": 1}}' \
          | python -s ${./spike/stage0/surge_worker.py} > $TMPDIR/reply.json
        python -I -c "
        import json, numpy as np
        r = json.load(open('$TMPDIR/reply.json'))
        assert r['ok'], r
        x = np.load('$TMPDIR/o.npy')
        assert np.isfinite(x).all() and abs(x).max() > 1e-3, 'silent or broken render'
        print('${name}:', r)
        "
        touch $out
      '';
    in
    {
      packages.${system} = {
        surgepy = surgepy-main;
        inherit forgesound surgepy-main surgepy-release;
        default = forgesound;
      };

      apps.${system}.default = {
        type = "app";
        program = "${forgesound}/bin/forgesound";
      };

      devShells.${system} = {
        default = shellFor surgepy-main;
        release = shellFor surgepy-release;
      };

      checks.${system} = {
        surgepy-main = smoke "surgepy-main" surgepy-main;
        surgepy-release = smoke "surgepy-release" surgepy-release;
        # the engine renders a default project through the Surge worker
        engine = pkgs.runCommand "forgesound-engine-tests" { nativeBuildInputs = [ python ]; } ''
          export HOME=$TMPDIR SURGEPY_SITE=${surgepy-main}/${site} SURGE_DATA_HOME=${surgepy-main}/share/surge-xt
          export FORGESOUND_HOME=$TMPDIR/home PYTHONDONTWRITEBYTECODE=1
          cp -r ${appSrc}/forgesound $TMPDIR/forgesound
          cp -r ${./tests} $TMPDIR/tests
          cd $TMPDIR && python -s -m pytest -q -p no:cacheprovider tests
          touch $out
        '';
      };
    };
}
