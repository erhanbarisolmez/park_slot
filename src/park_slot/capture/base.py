"""FrameSource Protocol - tüm kamera kaynaklarının uyması gereken sözleşme.

Neden Protocol kullanıyoruz?
- ABC (Abstract Base Class) yerine Protocol tercih ettik
- Protocol yapısal tipleme sağlar: sınıfın Protocol'dan miras alması gerekmez
- Doğru metodları tanımlayan her sınıf otomatik olarak FrameSource sayılır
- Test yazarken mock oluşturmak çok kolay
"""

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class FrameSource(Protocol):
    """Kare kaynağı protokolü.

    Her kamera/stream kaynağı bu metodları tanımlamalı:
    - open(): Bağlantı aç
    - read(): Bir kare oku
    - is_open(): Bağlantı açık mı?
    - close(): Bağlantıyı kapat
    """

    def open(self) -> None:
        """Kaynağa bağlan."""
        ...

    def read(self) -> np.ndarray | None:
        """Bir kare oku. Kare yoksa None döner."""
        ...

    def is_open(self) -> bool:
        """Kaynak açık mı?"""
        ...

    def close(self) -> None:
        """Kaynağı kapat ve kaynakları serbest bırak."""
        ...
