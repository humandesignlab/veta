"""Layer 2 signal: distributor-relative positioning.

Layer 1 (veta.history + veta.intelligence) grades how contestable a buyer +
partida market is - the same for every distributor. Layer 2 answers the
commercial director's real question: how well positioned is MY company to win
THIS tender? It reads the client's own RFC against the historical contracts and
produces a position grade plus a win-probability band.

Three empirically measurable effects, each shrinkage-damped so a single lucky
win cannot dominate:

  1. Incumbency  - prior wins at the SAME buyer + partida (with recency decay).
  2. Category    - wins in the SAME partida at OTHER buyers (expertise transfer).
  3. Relationship - wins in OTHER partidas at the SAME buyer (procedural access).

The win probability is an ESTIMATE, shown as a band, never a precise number.
All constants are judgment defaults pending the backtest calibration noted in
AGENTS.md. All supplier names/RFCs are Spanish source data, left as-is.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

import pandas as pd

from veta.intelligence import EnrichedTender

CURRENCY_MXN = "MXN"

# Effect weights: direct incumbency dominates because a prior win at the exact
# buyer+partida is the strongest predictor of a repeat win.
W_INCUMBENCY = 0.55
W_CATEGORY = 0.25
W_RELATIONSHIP = 0.20

# Shrinkage denominators: a single win yields only a moderate strength; the
# score approaches 1 only with sustained history. Larger alpha = more damping.
ALPHA_INCUMBENCY = 5.0
ALPHA_CATEGORY = 3.0
ALPHA_RELATIONSHIP = 4.0

# Incumbency advantage decays with staleness: a win last year counts fully, a
# win four years ago counts for nothing.
RECENCY_DECAY = 0.25  # per year

# Probability estimates are capped; nothing in public tendering is a sure thing.
P_WIN_CAP = 0.95

# Probability band half-width, tightened when Layer 1 confidence is high.
BAND_HIGH_CONFIDENCE = 0.05
BAND_LOW_CONFIDENCE = 0.10


@dataclass
class ClientPosition:
    position_grade: str  # INCUMBENT / EXPERIENCED / ADJACENT / OUTSIDER
    position_score: float
    p_win_low: float
    p_win_high: float
    n_prior_wins: int
    last_win_year: int | None
    share_of_buyer: float
    n_other_buyers_same_partida: int
    n_other_partidas_same_buyer: int
    incumbency_strength: float
    category_strength: float
    relationship_strength: float
    total_wins_other_buyers: int = 0
    total_wins_same_buyer: int = 0


def compute_position(
    client_rfc: str,
    siglas: str,
    partida: str,
    contracts: pd.DataFrame,
    repeat_win_rate: float,
    openness_shrunk: float,
    current_year: int | None = None,
    confidence: str = "low",
) -> ClientPosition:
    """Compute the client's position for one buyer + partida."""
    current_year = current_year or datetime.date.today().year
    base_rate = openness_shrunk if openness_shrunk is not None else repeat_win_rate

    client = contracts[contracts["rfc"] == client_rfc]

    # Effect 1: direct incumbency (same buyer + same partida).
    same_cell = client[(client["siglas"] == siglas) & (client["partida"] == partida)]
    n_prior_wins = int(len(same_cell))
    last_win_year = (
        int(same_cell["source_year"].max()) if n_prior_wins else None
    )
    if n_prior_wins:
        years_since = max(0, current_year - last_win_year)
        recency_factor = max(0.0, 1.0 - RECENCY_DECAY * years_since)
        incumbency_strength = recency_factor * (
            n_prior_wins / (n_prior_wins + ALPHA_INCUMBENCY)
        )
    else:
        incumbency_strength = 0.0

    share_of_buyer = _share_of_buyer(client_rfc, siglas, partida, contracts)

    # Effect 2: category transferability (same partida, other buyers).
    other_buyers = client[(client["partida"] == partida) & (client["siglas"] != siglas)]
    n_other_buyers = int(other_buyers["siglas"].nunique())
    total_wins_other_buyers = int(len(other_buyers))
    category_strength = n_other_buyers / (n_other_buyers + ALPHA_CATEGORY)

    # Effect 3: relationship breadth (same buyer, other partidas).
    same_buyer = client[client["siglas"] == siglas]
    other_partidas = same_buyer[same_buyer["partida"] != partida]
    n_other_partidas = int(other_partidas["partida"].nunique())
    total_wins_same_buyer = int(len(same_buyer))
    relationship_strength = n_other_partidas / (n_other_partidas + ALPHA_RELATIONSHIP)

    position_score = (
        W_INCUMBENCY * incumbency_strength
        + W_CATEGORY * category_strength
        + W_RELATIONSHIP * relationship_strength
    )

    if n_prior_wins > 0:
        p_win = repeat_win_rate * incumbency_strength + base_rate * (
            1 - incumbency_strength
        )
    else:
        p_win = base_rate * (1 + position_score)
    p_win = min(p_win, P_WIN_CAP)

    half = BAND_HIGH_CONFIDENCE if confidence == "high" else BAND_LOW_CONFIDENCE
    p_low = max(0.0, p_win - half)
    p_high = min(P_WIN_CAP, p_win + half)

    if n_prior_wins > 0:
        grade = "INCUMBENT"
    elif n_other_buyers > 0:
        grade = "EXPERIENCED"
    elif n_other_partidas > 0:
        grade = "ADJACENT"
    else:
        grade = "OUTSIDER"

    return ClientPosition(
        position_grade=grade,
        position_score=round(position_score, 4),
        p_win_low=round(p_low, 4),
        p_win_high=round(p_high, 4),
        n_prior_wins=n_prior_wins,
        last_win_year=last_win_year,
        share_of_buyer=round(share_of_buyer, 4),
        n_other_buyers_same_partida=n_other_buyers,
        n_other_partidas_same_buyer=n_other_partidas,
        incumbency_strength=round(incumbency_strength, 4),
        category_strength=round(category_strength, 4),
        relationship_strength=round(relationship_strength, 4),
        total_wins_other_buyers=total_wins_other_buyers,
        total_wins_same_buyer=total_wins_same_buyer,
    )


def _share_of_buyer(
    client_rfc: str, siglas: str, partida: str, contracts: pd.DataFrame
) -> float:
    """Client's share of total MXN award value at this buyer + partida."""
    cell = contracts[
        (contracts["siglas"] == siglas)
        & (contracts["partida"] == partida)
        & (contracts["moneda"] == CURRENCY_MXN)
        & (contracts["importe"] > 0)
    ]
    total = float(cell["importe"].sum())
    if total <= 0:
        return 0.0
    mine = float(cell[cell["rfc"] == client_rfc]["importe"].sum())
    return mine / total


def _pct_band(pos: ClientPosition) -> str:
    return f"{pos.p_win_low * 100:.0f}-{pos.p_win_high * 100:.0f}%"


def format_position(pos: ClientPosition) -> str:
    """Spanish one-liner describing the client's position and its evidence."""
    band = _pct_band(pos)
    if pos.position_grade == "INCUMBENT":
        evidence = f"ya ganaste {pos.n_prior_wins} contratos aqui"
        if pos.share_of_buyer > 0:
            evidence += f" ({pos.share_of_buyer:.0%} del valor)"
    elif pos.position_grade == "EXPERIENCED":
        evidence = (
            f"nunca vendiste a este comprador en esta categoria, pero ganaste "
            f"{pos.total_wins_other_buyers} contratos de la misma partida en "
            f"{pos.n_other_buyers_same_partida} otros compradores"
        )
    elif pos.position_grade == "ADJACENT":
        evidence = (
            f"vendes otras {pos.n_other_partidas_same_buyer} categorias a este "
            f"comprador ({pos.total_wins_same_buyer} contratos), pero nunca esta "
            "partida"
        )
    else:
        evidence = "sin historial con este comprador ni esta categoria"
    return f"{pos.position_grade} (P~{band}): {evidence}"


def enrich_with_position(
    shortlist: list[EnrichedTender],
    client_rfc: str,
    contracts: pd.DataFrame,
    repeat_win_rate_by_partida: dict[str, float],
    current_year: int | None = None,
) -> list[EnrichedTender]:
    """Attach a ClientPosition to each tender's primary intel and refresh signal.

    Position is single-valued per tender: it is computed for primary_intel (the
    dominant line-item category), so the report shows one answer per tender row.
    """
    from veta import intelligence

    global_rate = repeat_win_rate_by_partida.get("__global__", 0.0)
    for tender in shortlist:
        intel = tender.primary_intel
        if intel is None or not intel.has_history:
            continue
        repeat_rate = repeat_win_rate_by_partida.get(intel.partida, global_rate)
        intel.position = compute_position(
            client_rfc=client_rfc,
            siglas=intel.siglas,
            partida=intel.partida,
            contracts=contracts,
            repeat_win_rate=repeat_rate,
            openness_shrunk=intel.openness_shrunk if intel.openness_shrunk is not None else global_rate,
            current_year=current_year,
            confidence=intel.confidence,
        )
        tender.signal = intelligence._signal(intel)
    return shortlist
