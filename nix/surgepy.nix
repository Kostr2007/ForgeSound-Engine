# surgepy: Python bindings of Surge XT, built from the same pinned sources
# nixpkgs uses for its surge-xt package. Only the Python module is built;
# the factory data goes to share/surge-xt (point SURGE_DATA_HOME there).
{
  lib,
  stdenv,
  surge-xt,
  cmake,
  pkg-config,
  python3,
  alsa-lib,
  fontconfig,
  freetype,
  libjack2,
  libx11,
  libxcursor,
  libxext,
  libxinerama,
  libxrandr,
}:

stdenv.mkDerivation {
  pname = "surgepy";
  inherit (surge-xt) version src patches postPatch;

  nativeBuildInputs = [
    cmake
    pkg-config
    python3
  ];

  # Surge's CMake configures JUCE (and builds its juceaide tool), which looks for these
  buildInputs = [
    python3
    alsa-lib
    fontconfig # JUCE 8 (Surge main): juceaide needs its headers
    freetype
    libjack2
    libx11
    libxcursor
    libxext
    libxinerama
    libxrandr
  ];

  cmakeFlags = [
    # the plugin targets must stay configured (src/cmake/pluginval.cmake reads
    # them); only the surgepy target is built below
    (lib.cmakeBool "SURGE_BUILD_PYTHON_BINDINGS" true)
    (lib.cmakeBool "SURGE_BUILD_TESTRUNNER" false)
    (lib.cmakeFeature "CMAKE_BUILD_TYPE" "Release")
    # the pybind11 bundled with Surge 1.3.4 asks for CMake < 3.5, which CMake 4 refuses
    (lib.cmakeFeature "CMAKE_POLICY_VERSION_MINIMUM" "3.5")
    (lib.cmakeFeature "PYTHON_EXECUTABLE" python3.interpreter)
    (lib.cmakeFeature "Python_EXECUTABLE" python3.interpreter)
  ];

  buildPhase = ''
    runHook preBuild
    cmake --build . --parallel "$NIX_BUILD_CORES" --target surgepy
    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p "$out/${python3.sitePackages}" "$out/share/surge-xt"
    cp src/surge-python/surgepy*.so "$out/${python3.sitePackages}/"
    cp -r "$src/resources/data/." "$out/share/surge-xt/"
    runHook postInstall
  '';

  meta = {
    description = "Python bindings of the Surge XT synthesizer";
    homepage = "https://github.com/surge-synthesizer/surge";
    license = lib.licenses.gpl3Only;
    platforms = [ "x86_64-linux" "aarch64-linux" ];
  };
}
