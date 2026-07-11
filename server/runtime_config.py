"""Runtime configuration — in-memory config that can be hot-updated."""
import threading
from typing import Any


class RuntimeConfig:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._config = {}
        return cls._instance

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._config.get(key, default)

    def set(self, key: str, value: Any):
        with self._lock:
            self._config[key] = value

    def update(self, data: dict):
        with self._lock:
            self._config.update(data)

    def all(self) -> dict:
        with self._lock:
            return dict(self._config)


runtime_config = RuntimeConfig()
