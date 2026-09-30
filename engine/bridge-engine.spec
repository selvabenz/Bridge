# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files


# Wildebeest is optional in development but its package data must accompany
# it whenever the real engine is installed in the release environment.
wildebeest_datas = collect_data_files('wildebeest')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    # Unlike the USFM checker (a separate helper exe), tc_ai_bridge.versification
    # imports the vendored versification.py directly into this process at
    # runtime via sys.path + import, after bridge-engine.spec extracts it
    # here under sys._MEIPASS (see tc_ai_bridge/versification.py's
    # _vendor_root()). PyInstaller's static analysis cannot see that dynamic
    # import, so 'regex' (versification.py's one third-party dependency,
    # via the vendored ualign_utilities/general_util helpers) must be listed
    # explicitly below or a frozen build crashes with ModuleNotFoundError
    # the first time versification detection actually runs.
    #
    # names_adapter.py (Phase 5) is the same shape: the vendored Smart Edit
    # Distance module is imported dynamically via sys.path + import (see
    # its own _vendor_root()), and Uroman ships its own ~4.2MB data/
    # directory resolved relative to its installed package location
    # (Path(__file__).parent / "data") — invisible to PyInstaller's static
    # analysis either way, so both must be listed explicitly. Verified
    # against a real PyInstaller build (see docs/BUILD_LOG.md's
    # Phase 5 section): the frozen bridge-engine.exe correctly resolves
    # both under sys._MEIPASS and produces real names.* findings.
    # 'resources' (the bundled tN/tW/tA/UHB/UGNT snapshot, ~45MB) used to
    # be listed here too. Every PyInstaller onefile launch re-extracts its
    # ENTIRE datas payload to a fresh temp directory before any Python code
    # runs, regardless of whether that code ever touches a given file —
    # measured directly against the frozen exe: ping took 26-60s on every
    # single launch, cold or warm, dominated by this one folder. It's now
    # shipped separately via Tauri's bundle.resources (installed once, not
    # re-extracted per launch) and located at runtime through
    # BRIDGE_BUNDLED_RESOURCES_DIR (see main.py's _apply_cli_overrides and
    # resource_materializer.bundled_resources_source()'s docstring) rather
    # than sys._MEIPASS. The much smaller vendor trees below stay bundled
    # here — they're needed for basic engine startup, not resource-heavy.
    datas=[
        ('vendor/greekroom-versification', 'vendor/greekroom-versification'),
        ('vendor/greekroom-smart-edit-distance', 'vendor/greekroom-smart-edit-distance'),
        # tc_ai_bridge/logos_connector.py's LogosConnectorClient resolves this script
        # relative to sys._MEIPASS in a frozen build (_default_script_path()) — like
        # the vendor trees above, it's reached only via a runtime path join, invisible
        # to PyInstaller's static import analysis, so it must be listed explicitly or
        # a frozen build ships with no Logos bridge helper at all (Phase 7).
        ('logos_connector', 'logos_connector'),
        # The Language QA packs (engine/language_packs) are deliberately NOT
        # here any more: like 'resources' above, a onefile archive re-extracts
        # every data file on every launch, and a pack carries a lexicon. They
        # ship via Tauri's bundle.resources and are found through
        # --language-packs-dir / BRIDGE_LANGUAGE_PACKS_DIR
        # (tc_ai_bridge/language_packs/registry.py; docs/LANGUAGE_QA_PACKS.md).
        *collect_data_files('uroman'),
        # usfmtc (the one USFM parser, #91) ships its USX schema and .vrs
        # versification files beside its modules. The parse path Bridge uses
        # does not read them today, but a grammar or reference call that did
        # would fail only in the frozen build, so they are collected anyway.
        *collect_data_files('usfmtc'),
        *wildebeest_datas,
    ],
    # usfmtc is imported on first parse inside tc_ai_bridge/usfm_parser.py so
    # sidecar start does not pay its ~100 ms import; listed so the freeze does
    # not depend on analysis finding a function-level import.
    hiddenimports=['regex', 'usfmtc'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='bridge-engine',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX isn't installed in this project's build environment (upx=True was
    # a no-op here), but leaving it True would add real per-launch
    # decompression cost on any machine where UPX IS present, with no
    # benefit now that the archive doesn't carry the 45MB resources folder
    # anymore -- explicitly off rather than accidentally-off.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
