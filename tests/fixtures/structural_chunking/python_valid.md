# Python

```python
"""Synthetic module."""

import math

CONSTANT = 3

class Calculator:
    def square(self, value: int) -> int:
        return value * value


def helper(value: int) -> float:
    return math.sqrt(value)


async def async_helper(value: int) -> int:
    return value + CONSTANT


RESULT = helper(CONSTANT)
```
