from dataclasses import dataclass
from random import Random

from market_intelligence.collection.errors import ClassifiedCollectionError


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_retries: int
    base_delay: float
    max_delay: float
    max_retry_after: float

    def should_retry(self, error: ClassifiedCollectionError, retry_count: int) -> bool:
        return error.retryable and retry_count < self.max_retries

    def delay(
        self,
        error: ClassifiedCollectionError,
        retry_count: int,
        random_source: Random,
    ) -> float:
        if error.retry_after is not None:
            return min(max(error.retry_after, 0), self.max_retry_after)
        ceiling = min(self.max_delay, self.base_delay * (2**retry_count))
        return random_source.uniform(0, ceiling)
