"""Arena Hero「均衡扩张 + 防守」战术包。

决策入口：``strategy.decide(turn)``
运行入口：``python -m bot.main``
"""

from bot.config import TacticConfig, DEFAULT_CONFIG
from bot.strategy import decide

__all__ = [
    "TacticConfig",
    "DEFAULT_CONFIG",
    "decide",
]

__version__ = "0.1.0"
