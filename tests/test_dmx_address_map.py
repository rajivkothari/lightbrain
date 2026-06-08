"""
Tests for DMX address collision checker (fixtures/fixture.py).

Validates that check_dmx_address_map() correctly detects:
  1. Valid non-overlapping layouts (no exception)
  2. Direct address collision (two fixtures share channels)
  3. Out-of-bounds address (> 512 or < 1)
  4. Last-channel boundary (exactly ch 512 — valid)
  5. One-past-end collision (ch 513 — invalid)
  6. Partial overlap (second fixture starts mid-first fixture)
  7. Correct layout table in error message
  8. All real fixture types report correct channel counts
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fixtures.fixture import FixtureBase, check_dmx_address_map
from fixtures.rockwedge import RockWedge
from fixtures.chauvet_wash_fx2 import ChauvetWashFX2
from fixtures.chauvet_gigbar_move_ils import ChauvetGigBarMoveILS
from fixtures.djflx_beam import DJFLXBeam


# ---------------------------------------------------------------------------
# Minimal stub fixture for parametric testing
# ---------------------------------------------------------------------------

class _Stub(FixtureBase):
    def __init__(self, name: str, address: int, channels: int):
        super().__init__(fixture_id=name, name=name, dmx_address=address)
        self._channels = channels

    @property
    def channel_count(self) -> int:
        return self._channels

    def render_to_universe(self, universe, **kwargs) -> None:
        pass


# ---------------------------------------------------------------------------
# 1. Valid layouts
# ---------------------------------------------------------------------------

class TestValidLayouts:
    def test_single_fixture(self):
        check_dmx_address_map([_Stub("A", 1, 8)])

    def test_two_adjacent_fixtures(self):
        check_dmx_address_map([
            _Stub("A", 1, 8),
            _Stub("B", 9, 8),
        ])

    def test_three_fixtures_with_gap(self):
        check_dmx_address_map([
            _Stub("A", 1,  8),
            _Stub("B", 20, 8),
            _Stub("C", 50, 29),
        ])

    def test_last_valid_address(self):
        # Single 1-channel fixture at ch 512
        check_dmx_address_map([_Stub("A", 512, 1)])

    def test_fills_universe_exactly(self):
        # 64 × 8-channel fixtures = 512 channels
        fixtures = [_Stub(f"F{i}", i * 8 + 1, 8) for i in range(64)]
        check_dmx_address_map(fixtures)

    def test_real_rig_layout(self):
        """The planned hardware rig: WashFX2 L, WashFX2 R, GigBAR."""
        check_dmx_address_map([
            ChauvetWashFX2(fixture_id="l", name="Wash L", dmx_address=1),
            ChauvetWashFX2(fixture_id="r", name="Wash R", dmx_address=9),
            ChauvetGigBarMoveILS(fixture_id="g", name="GigBAR", dmx_address=17),
        ])

    def test_empty_list(self):
        check_dmx_address_map([])


# ---------------------------------------------------------------------------
# 2. Collision detection
# ---------------------------------------------------------------------------

class TestCollisions:
    def test_exact_same_address(self):
        with pytest.raises(ValueError, match="collision"):
            check_dmx_address_map([
                _Stub("A", 1, 8),
                _Stub("B", 1, 8),
            ])

    def test_partial_overlap_second_starts_inside_first(self):
        with pytest.raises(ValueError, match="collision"):
            check_dmx_address_map([
                _Stub("A", 1, 10),
                _Stub("B", 5, 8),   # starts at ch 5, inside A's ch 1–10
            ])

    def test_partial_overlap_first_ends_inside_second(self):
        with pytest.raises(ValueError, match="collision"):
            check_dmx_address_map([
                _Stub("A", 1,  10),  # ch 1–10
                _Stub("B", 8,  10),  # ch 8–17: overlaps A on ch 8–10
            ])

    def test_collision_in_third_fixture(self):
        with pytest.raises(ValueError, match="collision"):
            check_dmx_address_map([
                _Stub("A", 1,  8),
                _Stub("B", 9,  8),
                _Stub("C", 14, 8),  # overlaps B (9–16) on ch 14–16
            ])

    def test_error_includes_both_fixture_names(self):
        with pytest.raises(ValueError) as exc_info:
            check_dmx_address_map([
                _Stub("AlphaFixture", 1, 8),
                _Stub("BetaFixture",  5, 8),
            ])
        msg = str(exc_info.value)
        assert "AlphaFixture" in msg
        assert "BetaFixture" in msg

    def test_error_includes_layout_table(self):
        with pytest.raises(ValueError) as exc_info:
            check_dmx_address_map([
                _Stub("X", 1, 8),
                _Stub("Y", 4, 8),
            ])
        assert "Start" in str(exc_info.value) or "ch" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 3. Out-of-bounds detection
# ---------------------------------------------------------------------------

class TestOutOfBounds:
    def test_address_zero(self):
        with pytest.raises(ValueError, match="1-indexed"):
            check_dmx_address_map([_Stub("A", 0, 8)])

    def test_negative_address(self):
        with pytest.raises(ValueError, match="1-indexed"):
            check_dmx_address_map([_Stub("A", -5, 8)])

    def test_one_channel_past_512(self):
        with pytest.raises(ValueError, match="512"):
            check_dmx_address_map([_Stub("A", 513, 1)])

    def test_multi_channel_overflow(self):
        # Starts at 510, 8 channels → ends at 517
        with pytest.raises(ValueError, match="512"):
            check_dmx_address_map([_Stub("A", 510, 8)])

    def test_exactly_512_is_valid(self):
        # Single channel fixture at 512 should not raise
        check_dmx_address_map([_Stub("A", 512, 1)])

    def test_last_byte_of_universe(self):
        # 8-channel fixture ending exactly at 512
        check_dmx_address_map([_Stub("A", 505, 8)])

    def test_one_past_last_byte(self):
        with pytest.raises(ValueError, match="512"):
            check_dmx_address_map([_Stub("A", 506, 8)])


# ---------------------------------------------------------------------------
# 4. Real fixture channel counts
# ---------------------------------------------------------------------------

class TestFixtureChannelCounts:
    def test_rockwedge_is_8_channels(self):
        fx = RockWedge(fixture_id="rw", name="RW", dmx_address=1)
        assert fx.channel_count == 8

    def test_wash_fx2_is_8_channels(self):
        fx = ChauvetWashFX2(fixture_id="wfx", name="WFX2", dmx_address=1)
        assert fx.channel_count == 8

    def test_gigbar_is_29_channels(self):
        fx = ChauvetGigBarMoveILS(fixture_id="gb", name="GigBAR", dmx_address=1)
        assert fx.channel_count == 29

    def test_djflx_beam_is_10_channels(self):
        fx = DJFLXBeam(fixture_id="bm", name="Beam", dmx_address=1)
        assert fx.channel_count == 10

    def test_planned_rig_footprint(self):
        """Wash L (1–8) + Wash R (9–16) + GigBAR (17–45) = 45 channels total."""
        fixtures = [
            ChauvetWashFX2(fixture_id="l", name="Wash L", dmx_address=1),
            ChauvetWashFX2(fixture_id="r", name="Wash R", dmx_address=9),
            ChauvetGigBarMoveILS(fixture_id="g", name="GigBAR", dmx_address=17),
        ]
        total = sum(fx.channel_count for fx in fixtures)
        assert total == 45
        last_ch = max(fx.dmx_address + fx.channel_count - 1 for fx in fixtures)
        assert last_ch == 45
