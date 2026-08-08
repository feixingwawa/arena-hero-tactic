"""轻量 Turn/Unit stub，不依赖 pytest 与联网。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID, uuid4


Position = tuple[int, int]


@dataclass
class StubUnit:
    """通用单位 stub。"""

    id: UUID = field(default_factory=uuid4)
    position: Position = (0, 0)
    hp: int = 2
    cargo: int = 0
    unit_type: str = "WORKER"
    action: Optional[str] = None
    action_args: Any = None

    def move(self, direction: Any) -> None:
        self.action = "move"
        self.action_args = direction

    def harvest(self) -> None:
        self.action = "harvest"
        self.action_args = None

    def deposit(self) -> None:
        self.action = "deposit"
        self.action_args = None

    def heal(self) -> None:
        self.action = "heal"
        self.action_args = None

    def wait(self) -> None:
        self.action = "wait"
        self.action_args = None

    def sweep(self, direction: Any) -> None:
        self.action = "sweep"
        self.action_args = direction

    def shoot(self, target: Any, expected_cell: Any = None) -> None:
        self.action = "shoot"
        self.action_args = (target, expected_cell)

    def shoot_cell(self, cell: Position) -> None:
        self.action = "shoot_cell"
        self.action_args = cell

    def self_destruct(self) -> None:
        self.action = "self_destruct"
        self.action_args = None

    def pickup_beacon(self) -> None:
        self.action = "pickup_beacon"
        self.action_args = None

    def clear_action(self) -> None:
        self.action = None
        self.action_args = None


@dataclass
class StubCore:
    id: UUID = field(default_factory=uuid4)
    position: Position = (10, 10)
    hp: int = 5
    shield: int = 5
    resources: int = 0
    action: Optional[str] = None
    action_args: Any = None

    def spawn(self, unit_type: Any) -> None:
        self.action = "spawn"
        self.action_args = unit_type

    def heal(self) -> None:
        self.action = "heal"
        self.action_args = None

    def repair_shield(self) -> None:
        self.action = "repair_shield"
        self.action_args = None

    def wait(self) -> None:
        self.action = "wait"
        self.action_args = None

    def start_move(self, direction: Any) -> None:
        self.action = "start_move"
        self.action_args = direction

    def clear_action(self) -> None:
        self.action = None
        self.action_args = None


@dataclass
class StubState:
    """PlayerState 轻量 stub。

    注意：v0.14 已移除 `upkeep_next_tick` 字段，本类不再声明；
    旧测试若仍对其 setattr，仅作为普通实例属性存在，经济模块不再读取。
    """

    resources: int = 5
    population: int = 1
    population_tier: int = 0
    status: str = "ACTIVE"
    respawn_at_tick: Optional[int] = None


@dataclass
class StubEnemy:
    id: UUID = field(default_factory=uuid4)
    position: Position = (0, 0)
    hp: int = 2
    unit_type: str = "VANGUARD"
    controlled: bool = False


@dataclass
class StubBeacon:
    """ChampionBeacon 轻量 stub（status 用字符串 "GROUND"/"CARRIED"）。"""

    position: Position = (0, 0)
    status: Optional[str] = "GROUND"
    carrier_id: Optional[UUID] = None


@dataclass
class StubEvent:
    """ResolutionEvent 轻量 stub。"""

    event_type: str = ""
    position: Optional[Position] = None
    values: Optional[dict[str, Any]] = None
    tick: int = 0
    actor_id: Optional[UUID] = None
    reason_code: Optional[str] = None


@dataclass
class StubTurn:
    tick: int = 1
    resources: int = 5
    resource_capacity: int = 10
    core: Optional[StubCore] = None
    workers: list[StubUnit] = field(default_factory=list)
    vanguards: list[StubUnit] = field(default_factory=list)
    rangers: list[StubUnit] = field(default_factory=list)
    visible_enemies: list[StubEnemy] = field(default_factory=list)
    resource_cells: set[Position] = field(default_factory=set)
    obstacle_cells: set[Position] = field(default_factory=set)
    events: list[StubEvent] = field(default_factory=list)
    beacon: Optional[StubBeacon] = None
    state: Optional[StubState] = None
    submitted: bool = False

    def __post_init__(self) -> None:
        if self.core is None:
            self.core = StubCore()
        # 同步 core.resources 与 turn.resources（Task 3 choose_spawn 使用 turn.core.resources）
        if self.core.resources == 0 and self.resources != 0:
            self.core.resources = self.resources
        elif self.core.resources != 0 and self.resources == 5:  # default value
            self.resources = self.core.resources
        if self.state is None:
            pop = len(self.workers) + len(self.vanguards) + len(self.rangers)
            self.state = StubState(
                resources=max(self.resources, self.core.resources),
                population=pop,
            )

    @property
    def units(self) -> list[StubUnit]:
        return list(self.workers) + list(self.vanguards) + list(self.rangers)

    def submit(self) -> None:
        self.submitted = True

    def clear(self) -> None:
        for u in self.units:
            u.clear_action()
        if self.core:
            self.core.clear_action()
