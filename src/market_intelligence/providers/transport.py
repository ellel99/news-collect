"""Network-free transport implementation for provider adapter tests."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from market_intelligence.providers.contracts import (
    ProviderTransportRequest,
    ProviderTransportResponse,
)


class MockProviderTransport:
    def __init__(
        self,
        responses: Iterable[ProviderTransportResponse | Exception],
    ) -> None:
        self._responses = deque(responses)
        self.calls: list[ProviderTransportRequest] = []

    async def send(self, request: ProviderTransportRequest) -> ProviderTransportResponse:
        self.calls.append(request)
        if not self._responses:
            raise RuntimeError("mock_transport_response_missing")
        response = self._responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response
