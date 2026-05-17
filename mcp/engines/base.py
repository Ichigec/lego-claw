"""Базовый контракт поискового движка.

Определяет:
  - `SearchResult` — унифицированная структура одного результата;
  - `SearchEngine` — абстрактный класс движка (имя, доступность, `search`);
  - `get_client()` — общий `httpx.AsyncClient` с timeout/User-Agent/retry;
  - `EngineError` — обёртка для исключений, чтобы `multi_search` мог
    разложить ошибки по движкам без общего gather-падения.

Все движки в `engines/*.py` импортируют только это.
"""
from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import httpx

logger = logging.getLogger("searchbox.engines")

DEFAULT_TIMEOUT = float(os.environ.get("SEARCHBOX_HTTP_TIMEOUT", "10"))
DEFAULT_UA = os.environ.get(
    "SEARCHBOX_USER_AGENT",
    "searchbox-mcp/0.2 (+https://github.com/open-webui)",
)

_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()


async def get_client() -> httpx.AsyncClient:
    """Возвращает процесс-глобальный `httpx.AsyncClient`.

    Ленивая инициализация; один connection pool на все движки.
    Закрывается из `close_client()` при завершении сервера.
    """
    global _client
    if _client is not None and not _client.is_closed:
        return _client
    async with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.AsyncClient(
                timeout=httpx.Timeout(DEFAULT_TIMEOUT, connect=5.0),
                headers={"User-Agent": DEFAULT_UA, "Accept": "application/json"},
                follow_redirects=True,
                limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
            )
    return _client


async def close_client() -> None:
    """Закрывает общий `httpx.AsyncClient` (если был создан)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


class EngineError(RuntimeError):
    """Ошибка конкретного движка, пригодная для вывода пользователю."""


@dataclass(slots=True)
class SearchResult:
    """Один результат поиска в унифицированной форме.

    Поля:
      - `title`       — заголовок страницы/документа/репозитория.
      - `url`         — URL; должен нормализоваться и использоваться как
        дедупликационный ключ в `multi_search`.
      - `description` — короткий сниппет/аннотация.
      - `source`      — имя движка, из которого пришёл результат.
      - `score`       — опциональный relevance score (если движок даёт).
      - `raw`         — сырой ответ движка (для отладки / сложных случаев).
    """

    title: str
    url: str
    description: str = ""
    source: str = ""
    score: Optional[float] = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-сериализуемая форма (raw не отдаём наружу по умолчанию)."""
        d = asdict(self)
        d.pop("raw", None)
        return d


class SearchEngine(ABC):
    """Абстрактный движок.

    Конкретные движки переопределяют `name`, `available()` и `search()`.
    `available()` отвечает на вопрос: «есть ли нужные env-ключи / хост?».
    `search()` обязан отдавать `list[SearchResult]` и бросать
    `EngineError` на фатальных проблемах (`multi_search` разложит ошибки
    по движкам сам).
    """

    #: Короткое имя (DNS-friendly), используется как tool-suffix
    #: (`search_<name>`) и в реестре `ENGINES`.
    name: str = ""

    #: Человекочитаемое описание для `list_engines` (нужно LLM для выбора).
    description: str = ""

    #: Требуется ли сетевой токен/ключ/хост (для `list_engines`).
    requires: tuple[str, ...] = ()

    @abstractmethod
    def available(self) -> bool:
        """Готов ли движок работать прямо сейчас (env задан, URL пингуется).

        Проверка должна быть синхронной и быстрой — её дёргает
        `search_status` без сетевого I/O.
        """

    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        """Выполнить поиск. Обязан возвращать `list[SearchResult]`."""

    async def safe_search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[SearchResult]:
        """Обёртка с try/except для `multi_search`.

        Логирует исключение и конвертирует любые ошибки в `EngineError`,
        чтобы `asyncio.gather(..., return_exceptions=True)` вернул
        типизированный объект, а не голый `httpx.RequestError`.
        """
        if not self.available():
            raise EngineError(f"{self.name}: engine is not available")
        try:
            return await self.search(query, max_results=max_results, **kwargs)
        except EngineError:
            raise
        except httpx.HTTPStatusError as exc:
            msg = f"{self.name}: HTTP {exc.response.status_code} on {exc.request.url}"
            logger.warning(msg)
            raise EngineError(msg) from exc
        except httpx.RequestError as exc:
            msg = f"{self.name}: network error ({exc.__class__.__name__}: {exc})"
            logger.warning(msg)
            raise EngineError(msg) from exc
        except Exception as exc:  # noqa: BLE001
            msg = f"{self.name}: {exc.__class__.__name__}: {exc}"
            logger.exception("%s failed", self.name)
            raise EngineError(msg) from exc


async def http_get_json(
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: Optional[float] = None,
) -> dict[str, Any]:
    """Общий helper: GET → JSON с `raise_for_status` и retry на 5xx."""
    client = await get_client()
    last_exc: Optional[Exception] = None
    for attempt in range(2):
        try:
            resp = await client.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            if 500 <= exc.response.status_code < 600 and attempt == 0:
                last_exc = exc
                await asyncio.sleep(0.5)
                continue
            raise
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt == 0:
                await asyncio.sleep(0.5)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    return {}


async def http_post_json(
    url: str,
    *,
    data: Optional[dict[str, Any]] = None,
    json_body: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: Optional[float] = None,
) -> dict[str, Any]:
    """Общий helper: POST → JSON с `raise_for_status`."""
    client = await get_client()
    resp = await client.post(
        url,
        data=data,
        json=json_body,
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()
