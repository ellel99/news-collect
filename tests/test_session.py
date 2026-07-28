from unittest.mock import AsyncMock, MagicMock

import pytest

from market_intelligence.db.session import session_scope


@pytest.mark.asyncio
async def test_session_scope_enters_and_exits_session() -> None:
    session = AsyncMock()
    context_manager = AsyncMock()
    context_manager.__aenter__.return_value = session
    factory = MagicMock(return_value=context_manager)

    async with session_scope(factory) as yielded:
        assert yielded is session

    factory.assert_called_once_with()
    context_manager.__aexit__.assert_awaited_once()
