from __future__ import annotations

import base64
import ctypes
import getpass
import json
import os
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SecretStoreError(RuntimeError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def dpapi_protect(text: str) -> str:
    if os.name != 'nt':
        raise SecretStoreError('Windows DPAPI is only available on Windows.')
    data, keep = _blob(text.encode('utf-8'))
    out = DATA_BLOB()
    # CRYPTPROTECT_UI_FORBIDDEN = 1
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(data), 'translationCore AI Bridge', None, None, None, 1, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        raw = ctypes.string_at(out.pbData, out.cbData)
        return base64.b64encode(raw).decode('ascii')
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def dpapi_unprotect(encoded: str) -> str:
    if os.name != 'nt':
        raise SecretStoreError('Windows DPAPI is only available on Windows.')
    raw = base64.b64decode(encoded)
    data, keep = _blob(raw)
    out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(data), None, None, None, None, 1, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData).decode('utf-8')
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _default_app_root() -> Path:
    """Return (and, on first use, migrate into) Bridge's per-user data folder.

    This used to be ``Path(os.getenv('LOCALAPPDATA') or Path.home() / '.translationcore-ai-bridge')``
    — `or` binds looser than `/`, so on any machine with LOCALAPPDATA set (i.e.
    every real Windows install) `root` was the *entire* LOCALAPPDATA directory,
    not a namespaced subfolder. That put settings.json loose at
    ``%LOCALAPPDATA%\\settings.json`` and forced a workaround in
    bridge_service.py to redirect the project registry into a differently
    named ``.translationcore-ai-bridge`` folder. Three inconsistent names for
    one app's data (install dir "Bridge", WebView2's identifier-keyed folder,
    and this legacy pair) made it easy for an unrelated/older install sharing
    the legacy name to have its projects auto-discovered as this app's own.
    ``data`` is a subfolder of the install-named "Bridge" directory (not the
    same path) so an NSIS uninstall — which wipes its install directory
    (``$INSTDIR``) — cannot delete user settings/projects as a side effect.

    Both legacy artifacts (the loose settings.json and the differently-named
    project folder) could exist independently, so each is migrated on its
    own rather than as an either/or.
    """
    local_app_data = os.getenv('LOCALAPPDATA')
    base = Path(local_app_data) if local_app_data else Path.home()
    root = base / 'Bridge' / 'data'
    legacy_root = base / '.translationcore-ai-bridge'
    if not root.exists() and legacy_root.is_dir():
        try:
            legacy_root.rename(root)
        except OSError:
            pass
    legacy_loose_settings = base / 'settings.json'
    if legacy_loose_settings.is_file() and not (root / 'settings.json').exists():
        try:
            root.mkdir(parents=True, exist_ok=True)
            legacy_loose_settings.rename(root / 'settings.json')
        except OSError:
            pass
    return root


def _is_secret_key(key: str) -> bool:
    """DPAPI-wrapped values stay in settings.json; everything else is a
    `settings_kv` row in the workspace database (#77)."""
    return str(key).endswith('_dpapi')


class AppSettings:
    def __init__(self, path: Path | None = None, workspace: 'WorkspaceRepository | None' = None):
        from .workspace_repository import WorkspaceRepository

        if path is None:
            path = _default_app_root() / 'settings.json'
        self.path = Path(path)
        # Non-secret settings live in the workspace database beside this file;
        # `self.data` is the merged in-memory view of both, so every property
        # below reads and writes exactly as it did when the file held it all.
        self.workspace = workspace or WorkspaceRepository(self.path.parent / 'workspace.sqlite3')
        self.data: dict = {}
        on_disk: dict = {}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text('utf-8'))
                on_disk = loaded if isinstance(loaded, dict) else {}
            except Exception:
                on_disk = {}
        stored = self.workspace.load_settings()
        # A settings.json written before #77 carries non-secret keys too. They
        # are adopted into the database once (the database wins where both
        # have a value) and the file is rewritten to secrets only below.
        legacy_non_secret = {
            k: v for k, v in on_disk.items()
            if not _is_secret_key(k) and not str(k).startswith('_') and k not in stored
        }
        self.data = {**{k: v for k, v in on_disk.items() if _is_secret_key(k) or str(k).startswith('_')},
                     **stored, **legacy_non_secret}
        if legacy_non_secret or any(not _is_secret_key(k) and not str(k).startswith('_') for k in on_disk):
            self.save_sanitized()

        # Older Bridge builds could accidentally persist session-only values by
        # calling save() after set_api_key(). Keep the value available for this
        # process, migrate it to DPAPI where possible, and immediately remove
        # every private (underscore-prefixed) value from disk.
        if any(str(key).startswith('_') for key in self.data):
            legacy_key = str(self.data.get('_session_api_key', '')).strip()
            if legacy_key and os.name == 'nt' and not self.data.get('api_key_dpapi'):
                try:
                    self.data['api_key_dpapi'] = dpapi_protect(legacy_key)
                except Exception:
                    # Sanitizing the file is mandatory even if the platform
                    # credential store is temporarily unavailable.
                    pass
            self.save_sanitized()

    def save(self) -> None:
        """Persist non-secret settings without serializing session values.

        Retained for compatibility with older callers; all settings writes now
        use the same safe path.
        """
        self.save_sanitized()

    def set_api_key(self, key: str, persist: bool = True) -> None:
        self.data.pop('api_key_dpapi', None)
        self.data['_session_api_key'] = key.strip()
        if persist and key.strip() and os.name == 'nt':
            self.data['api_key_dpapi'] = dpapi_protect(key.strip())
        self.save_sanitized()

    def save_sanitized(self) -> None:
        persistent = {k: v for k, v in self.data.items() if not k.startswith('_')}
        secrets = {k: v for k, v in persistent.items() if _is_secret_key(k)}
        self.workspace.replace_settings({k: v for k, v in persistent.items() if not _is_secret_key(k)})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(secrets, indent=2), encoding='utf-8')
        # The persisted file never contains a plaintext API key. Restrict other reviewer/settings
        # metadata as well on platforms that support POSIX permissions; Windows DPAPI protects
        # the credential material itself.
        try:
            os.chmod(self.path, 0o600)
        except (OSError, NotImplementedError):
            pass

    def get_api_key(self) -> str:
        env = os.getenv('OPENAI_API_KEY', '').strip()
        if env:
            return env
        session = str(self.data.get('_session_api_key', '')).strip()
        if session:
            return session
        enc = str(self.data.get('api_key_dpapi', '')).strip()
        if enc and os.name == 'nt':
            try: return dpapi_unprotect(enc)
            except Exception: return ''
        return ''

    @property
    def model(self) -> str:
        return str(self.data.get('model') or 'gpt-5.6')

    @model.setter
    def model(self, value: str) -> None:
        self.data['model'] = value.strip() or 'gpt-5.6'
        self.save_sanitized()

    @property
    def provider(self) -> str:
        """Free-text provider label (e.g. 'openai', 'anthropic', 'azure',
        'local', 'custom'). Added for Bridge so Settings can point at any
        OpenAI-compatible endpoint, not just OpenAI specifically — see
        api_base_url below."""
        return str(self.data.get('provider') or 'openai')

    @provider.setter
    def provider(self, value: str) -> None:
        self.data['provider'] = (value or 'openai').strip() or 'openai'
        self.save_sanitized()

    @property
    def api_base_url(self) -> str:
        """Empty string means 'use the provider's default endpoint'.
        Set explicitly to point at Azure OpenAI, a self-hosted
        OpenAI-compatible server (vLLM, LM Studio, Ollama's OpenAI-compat
        mode, OpenRouter, etc), or any other compatible endpoint."""
        return str(self.data.get('api_base_url') or '')

    @api_base_url.setter
    def api_base_url(self, value: str) -> None:
        self.data['api_base_url'] = str(value or '').strip()
        self.save_sanitized()

    @property
    def triage_hide_threshold(self) -> int:
        """Report screen: hide findings AI triage rated this likely (0-100) to
        be a false positive. 0 means the slider is off and nothing is hidden.

        Default 90, deliberately conservative — hiding a real translation
        error is a much worse failure than leaving a false positive on
        screen. Kept in settings rather than per project because it is a
        reviewer's tolerance, not a property of the text.
        """
        try:
            value = int(self.data.get('triage_hide_threshold', 90))
        except (TypeError, ValueError):
            return 90
        if value <= 0:
            return 0
        return max(50, min(100, value))

    @triage_hide_threshold.setter
    def triage_hide_threshold(self, value: Any) -> None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = 90
        self.data['triage_hide_threshold'] = 0 if number <= 0 else max(50, min(100, number))
        self.save_sanitized()

    @property
    def language_qa_inline_precision(self) -> int:
        """Settings > Language QA: draw an indic-qa finding in the verse text only
        when its rule's measured reviewer precision is at least this (0-100).
        A rule nobody has labelled always passes. 0, the default, draws every
        one. A reviewer's tolerance, like triage_hide_threshold, so app-level
        rather than per project (language_qa_drawn)."""
        try:
            value = int(self.data.get('language_qa_inline_precision', 0))
        except (TypeError, ValueError):
            return 0
        return max(0, min(100, value))

    @language_qa_inline_precision.setter
    def language_qa_inline_precision(self, value: Any) -> None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = 0
        self.data['language_qa_inline_precision'] = max(0, min(100, number))
        self.save_sanitized()

    @property
    def language_qa_inline_confidence(self) -> str:
        """The lowest rule confidence drawn in the text: "low" (the default:
        every one), "medium" or "high"."""
        value = str(self.data.get('language_qa_inline_confidence') or 'low').lower()
        return value if value in ('low', 'medium', 'high') else 'low'

    @language_qa_inline_confidence.setter
    def language_qa_inline_confidence(self, value: Any) -> None:
        text = str(value or 'low').lower()
        self.data['language_qa_inline_confidence'] = text if text in ('low', 'medium', 'high') else 'low'
        self.save_sanitized()

    @property
    def language_qa_learned_fixes(self) -> bool:
        """Settings > Language QA: learn a word the reviewer replaced and offer
        the same change where it recurs. On by default."""
        return bool(self.data.get('language_qa_learned_fixes', True))

    @language_qa_learned_fixes.setter
    def language_qa_learned_fixes(self, value: Any) -> None:
        self.data['language_qa_learned_fixes'] = bool(value)
        self.save_sanitized()

    @staticmethod
    def _seed_reviewer_name() -> str:
        """A brand-new profile has never had a reviewer name typed in.

        V11-005: seeding from a product-sounding literal ('AI Bridge
        Reviewer') let every unattributed action look like Bridge itself
        did it. Seed from the OS account instead -- still just a starting
        point, fully editable via Settings -- and fall back to a name that
        obviously means "not yet set" if the OS account can't be read.
        """
        try:
            name = str(getpass.getuser() or '').strip()
            if name:
                return name
        except Exception:
            pass
        return 'Unnamed Reviewer'

    @property
    def reviewer_name(self) -> str:
        value = str(self.data.get('reviewer_name') or '').strip()
        updated_at = str(self.data.get('reviewer_name_updated_at') or '').strip()
        # A profile written before V11-005 landed has 'AI Bridge Reviewer'
        # persisted as data, so the "reseed if empty" check below never
        # fires for it -- it looks identical to someone who deliberately
        # renamed themselves to that string. The tell is
        # reviewer_name_updated_at: a real Settings-initiated rename always
        # stamps it (see the setter), while the old literal default never
        # did. Treat that combination as still unset and reseed.
        if value and not (value == 'AI Bridge Reviewer' and not updated_at):
            return value
        seeded = self._seed_reviewer_name()
        self.data['reviewer_name'] = seeded
        self.save_sanitized()
        return seeded

    @reviewer_name.setter
    def reviewer_name(self, value: str) -> None:
        self.data['reviewer_name'] = str(value or '').strip() or self._seed_reviewer_name()
        self.data['reviewer_name_updated_at'] = datetime.now(timezone.utc).isoformat()
        self.save_sanitized()

    @property
    def reviewer_name_updated_at(self) -> str:
        """UTC ISO-8601 timestamp of the last Settings-initiated rename, or
        '' if the reviewer name has never been explicitly changed (the
        OS-seeded default does not count as a change)."""
        return str(self.data.get('reviewer_name_updated_at') or '')

    @property
    def reviewer_mode(self) -> str:
        value = str(self.data.get('reviewer_mode') or 'basic').strip().lower()
        return value if value in ('basic', 'advanced') else 'basic'

    @reviewer_mode.setter
    def reviewer_mode(self, value: str) -> None:
        normalized = str(value or '').strip().lower()
        if normalized not in ('basic', 'advanced'):
            raise ValueError('reviewer_mode must be basic or advanced')
        self.data['reviewer_mode'] = normalized
        self.save_sanitized()

    @property
    def paratext_navigation(self) -> bool:
        return bool(self.data.get('paratext_navigation', False))

    @paratext_navigation.setter
    def paratext_navigation(self, value: bool) -> None:
        self.data['paratext_navigation'] = bool(value)
        self.save_sanitized()

    @property
    def logos_navigation(self) -> bool:
        return bool(self.data.get('logos_navigation', False))

    @logos_navigation.setter
    def logos_navigation(self, value: bool) -> None:
        self.data['logos_navigation'] = bool(value)
        self.save_sanitized()

    @property
    def paratext_username(self) -> str:
        return str(self.data.get('paratext_username') or '')

    @paratext_username.setter
    def paratext_username(self, value: str) -> None:
        self.data['paratext_username'] = str(value or '').strip()
        self.save_sanitized()

    @property
    def paratext_project_guid(self) -> str:
        """Legacy/global Paratext GUID fallback.

        v0.7.4 stores GUIDs per translationCore project so switching projects cannot silently
        send notes to the previously selected Paratext project. This property is retained for
        migration and callers that do not yet supply a project key.
        """
        return str(self.data.get('paratext_project_guid') or '')

    @paratext_project_guid.setter
    def paratext_project_guid(self, value: str) -> None:
        self.data['paratext_project_guid'] = str(value or '').strip()
        self.save_sanitized()

    def get_paratext_project_guid(self, project_key: str = '') -> str:
        key = str(project_key or '').strip()
        mapping = self.data.get('paratext_project_guids')
        if key and isinstance(mapping, dict):
            value = str(mapping.get(key) or '').strip()
            if value:
                return value
        return self.paratext_project_guid if not key else ''

    def set_paratext_project_guid(self, project_key: str, value: str) -> None:
        key = str(project_key or '').strip()
        if not key:
            self.paratext_project_guid = value
            return
        mapping = self.data.get('paratext_project_guids')
        if not isinstance(mapping, dict):
            mapping = {}
        else:
            mapping = dict(mapping)
        clean = str(value or '').strip()
        if clean:
            mapping[key] = clean
        else:
            mapping.pop(key, None)
        self.data['paratext_project_guids'] = mapping
        self.save_sanitized()

    def set_paratext_registration_code(self, code: str, persist: bool = True) -> None:
        self.data.pop('paratext_registration_code_dpapi', None)
        self.data['_session_paratext_registration_code'] = str(code or '').strip()
        if persist and str(code or '').strip() and os.name == 'nt':
            self.data['paratext_registration_code_dpapi'] = dpapi_protect(str(code).strip())
        self.save_sanitized()

    def get_paratext_registration_code(self) -> str:
        session = str(self.data.get('_session_paratext_registration_code', '')).strip()
        if session:
            return session
        enc = str(self.data.get('paratext_registration_code_dpapi', '')).strip()
        if enc and os.name == 'nt':
            try:
                return dpapi_unprotect(enc)
            except Exception:
                return ''
        return ''

    def record_ai_usage(self, total_tokens: int = 0, estimated_cost_usd: float = 0.0) -> None:
        """Persist Bridge-observed lifetime API usage for this Windows user/settings file."""
        usage = self.data.get('ai_usage_totals')
        if not isinstance(usage, dict):
            usage = {}
        usage = dict(usage)
        usage['tokens'] = int(usage.get('tokens', 0) or 0) + max(0, int(total_tokens or 0))
        usage['estimatedCostUSD'] = float(usage.get('estimatedCostUSD', 0.0) or 0.0) + max(0.0, float(estimated_cost_usd or 0.0))
        self.data['ai_usage_totals'] = usage
        self.save_sanitized()

    def get_ai_usage_totals(self) -> dict:
        usage = self.data.get('ai_usage_totals')
        if not isinstance(usage, dict):
            return {'tokens': 0, 'estimatedCostUSD': 0.0}
        return {
            'tokens': max(0, int(usage.get('tokens', 0) or 0)),
            'estimatedCostUSD': max(0.0, float(usage.get('estimatedCostUSD', 0.0) or 0.0)),
        }

# Production settings are deliberately simple JSON values; secrets remain DPAPI-protected above.
def _get_setting(self, key, default=None):
    return self.data.get(key, default)
def _set_setting(self, key, value):
    self.data[key]=value; self.save_sanitized()
AppSettings.get_setting=_get_setting
AppSettings.set_setting=_set_setting
