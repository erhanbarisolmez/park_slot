"""Gerçek JSON log formatter.

config/logging.yaml'daki eski "json" formatter aslında düz string
interpolasyonuydu (`'{"time":"%(asctime)s",...}'`) - mesaj içinde tırnak
veya newline olursa geçersiz JSON üretiyordu. Bu formatter `json.dumps`
kullanarak her zaman geçerli JSON satırı üretir.
"""

import json
import logging


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)
