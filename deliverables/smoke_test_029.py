"""Smoke test: verify 0.2.9 parses the NEW server protocol and bot decide() works.

Simulates the post-upgrade 'state' WebSocket message (no population_tier /
upkeep_next_tick) and runs the full bot decision pipeline on a real SDK Turn.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arena_hero import ArenaHeroClient, Direction, Turn, UnitType
from arena_hero._protocol import parse_stream_message
from arena_hero.errors import (
    APIError,
    ArenaHeroError,
    AuthenticationError,
    ConfigurationError,
    PolicyViolationError,
    ProtocolError,
    TransportError,
)
from bot.config import TacticConfig
from bot.strategy import decide


def build_new_state_message() -> str:
    """Build a 'state' envelope matching the upgraded server protocol.

    NOTE: deliberately omits ``population_tier`` and ``upkeep_next_tick``,
    which 0.2.8 required and 0.2.9 no longer expects.
    """
    return json.dumps(
        {
            "type": "state",
            "data": {
                "status": "ACTIVE",
                "resources": 20,
                "population": 1,
                "champion_beacon": {"position": [0, 0], "status": "GROUND"},
                "objects": [
                    {
                        "kind": "CORE",
                        "id": "11111111-1111-1111-1111-111111111111",
                        "controlled": True,
                        "owner_username": "kou",
                        "position": [10, 10],
                        "hp": 5,
                        "shield": 5,
                        "state": "NORMAL",
                    },
                    {
                        "kind": "UNIT",
                        "id": "22222222-2222-2222-2222-222222222222",
                        "controlled": True,
                        "position": [11, 10],
                        "hp": 2,
                        "unit_type": "WORKER",
                        "cargo": 0,
                    },
                    {"kind": "OBSTACLE", "positions": [[12, 10]]},
                ],
                "events": [],
            },
        },
        separators=(",", ":"),
    )


def main() -> None:
    print("--- 0.2.9 parse new-format state message ---")
    msg = build_new_state_message()
    state = parse_stream_message(msg)
    assert type(state).__name__ == "PlayerState", type(state)
    print("PARSED OK:", type(state).__name__, "resources=", state.resources,
          "population=", state.population)
    print("has population_tier attr:", hasattr(state, "population_tier"))
    print("has upkeep_next_tick attr:", hasattr(state, "upkeep_next_tick"))
    assert not hasattr(state, "population_tier")
    assert not hasattr(state, "upkeep_next_tick")

    class DummySubmitter:
        def __call__(self, plan, key=None):  # type: ignore[no-untyped-def]
            return None

    turn = Turn(tick=42, state=state, submitter=DummySubmitter())
    print("turn.tick=", turn.tick, "turn.resources=", turn.resources)
    print("workers=", len(turn.workers), "core=", turn.core is not None)
    print("resource_cells=", turn.resource_cells, "obstacle_cells=", turn.obstacle_cells)

    cfg = TacticConfig(
        max_population=18,
        target_workers=4,
        target_vanguards=2,
        target_rangers=1,
        reserve_resources=2,
        early_game_pop=4,
    )
    result = decide(turn, config=cfg)
    print("decide OK:", result.summary())

    symbols_ok = all(
        [
            hasattr(ArenaHeroClient, "turns"),
            hasattr(turn, "submit"),
            hasattr(turn.workers[0], "move"),
            hasattr(turn.workers[0], "harvest"),
            hasattr(turn.workers[0], "deposit"),
            hasattr(turn.workers[0], "heal"),
            hasattr(turn.workers[0], "wait"),
            hasattr(turn.core, "spawn"),
            hasattr(turn.core, "heal"),
            hasattr(turn.core, "repair_shield"),
            Direction.UP is not None,
            UnitType.WORKER is not None,
            AuthenticationError is not None,
            PolicyViolationError is not None,
            TransportError is not None,
            ProtocolError is not None,
            ConfigurationError is not None,
            APIError is not None,
            ArenaHeroError is not None,
        ]
    )
    print("ALL API symbols present:", symbols_ok)
    assert symbols_ok
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
