"""${VAR} yer tutucularını ortam değişkenleriyle değiştirir.

Neden gerekli: config/*.yaml ve config/slots/*.json git'e commitlenir (bkz.
.gitignore). RTSP gibi kimlik doğrulamalı kaynaklar için kullanıcı adı/şifre
URL içine GÖMÜLÜP bu dosyalara KAYDEDİLEMEZ - git geçmişine sızar. Bunun
yerine URL'de rtsp://${CAM_LOCAL_USER}:${CAM_LOCAL_PASS}@... gibi bir yer
tutucu yazılır/kaydedilir; gerçek değer sadece bağlantı anında, bellekte,
.env dosyasından (gitignore'da, asla commitlenmez) okunup yerine konur.
"""

import os
import re
from pathlib import Path

from dotenv import load_dotenv

_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_env_loaded = False


def resolve_env_placeholders(value: str) -> str:
    """value içindeki ${VAR_NAME} yer tutucularını os.environ'dan doldur.

    Proje kökündeki .env dosyası (varsa) ilk çağrıda os.environ'a yüklenir.
    """
    global _env_loaded
    if not _env_loaded:
        load_dotenv()
        _env_loaded = True

    def _sub(match: re.Match) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise RuntimeError(
                f"Ortam değişkeni bulunamadı: {name} - .env dosyasına ekleyin "
                f"(şablon: .env.example, proje kökü: {Path.cwd()})"
            )
        return os.environ[name]

    return _PLACEHOLDER_RE.sub(_sub, value)
