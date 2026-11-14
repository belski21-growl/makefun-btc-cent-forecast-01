# btc-cent-forecast-01: forecast Bitcoin's price 240 minutes after a checkpoint.
#
# Model: a dead-zone mean-reversion fade of the trailing 30-minute move
# around a persistence centre.
#
#   guess = x * exp(clamp(-K * excess(r30), -CAP, CAP))
#   x     = last close in the case (the close at the checkpoint)
#   r30   = log(x / price 30 minutes before the checkpoint)
#   excess(r30) = sign(r30) * max(0, |r30| - TAU), TAU = 0.003 (0.3%)
#   K     = 0.50, CAP = 0.01 (1% of price)
#
# Why this shape (calibrated on the kit's practice history, not on the 5
# scored cases alone):
#   - The score is mean absolute error, so the right point forecast is the
#     conditional MEDIAN of the price 240 minutes out.
#   - Over the practice history, the next 4-hour log-return regresses on the
#     trailing 30-minute log-return with slope about -0.64 (corr -0.47) on
#     22 non-overlapping 4-hour blocks: a sharp 30-minute move in this one
#     Orca pool is usually a displacement (a large trade moving a thin
#     pool), and it partly reverts within 4 hours.
#   - REVISI-1 (15 Nov 2026, post-open): the plain fade (K = 0.40, no dead
#     zone) was re-weighed on the same practice data, rebuilt on a
#     corrected 22-block grid (the build's 19-block grid missed the p01..p04
#     checkpoints). Three slices, all from the kit's six practice cases:
#       * 22 mixed-history blocks: plain fade 33,201 vs persistence 35,390
#         (-6.2%); dead-zone fade 32,631 (-7.8%).
#       * 5 scored cases p00..p04 (one 20-hour downtrend slice): plain fade
#         53,685 vs persistence 47,055 (+14.1%); dead-zone fade 50,727
#         (+7.8%).
#       * 21 blocks excluding the single crash block (r30 = -2.19%, the
#         displacement that carries the whole fade edge): plain fade +2.5%
#         vs persistence; dead-zone fade +1.4%.
#     The dead-zone fade dominates the plain fade on every slice, so the
#     plain fade is retired. The fade edge still rests on displacement
#     episodes: with no displacement in the final 48 hours every fade loses
#     ~1-2% to persistence, but one crash-scale case is worth ~6,000 cents
#     of mean error over 12 cases, so the bet stays on.
#   - TAU = 0.3% is about 3x the median |r30| (~0.1%) over the 22 blocks:
#     a move under that is mostly pool micro-noise, and fading it cost more
#     than it saved on 18 of 22 blocks. Only 4 of 22 blocks are faded at
#     all. The tau curve is smooth (no spike at one value), so 0.3% is a
#     noise-floor choice, not a fitted digit.
#   - K = 0.50 sits between the original shrunk slope (0.40) and the
#     22-block regression slope (-0.64). Larger K (0.64-0.79) wins a little
#     more on the crash block but loses more on momentum blocks (p02's
#     -0.78% 30-minute drop kept falling); K = 0.50 is the best balance
#     across the three slices.
#   - The 1% cap is unchanged: it binds only on crash-scale moves and keeps
#     the guess sane if the input ever carries a wild print. Raising it was
#     considered and rejected: it would chase a single block.
#   - Rejected again: plain momentum (wins the 5 downtrend cases, loses
#     +4.6% on the 22 blocks), 3-minute median smoothing of r30 (worse on
#     the 22 blocks), fading the 60-minute move, and raising K to the raw
#     regression slope.
#   - No drift, no trend extrapolation, no long-window mean reversion: every
#     one of those tested worse on the 22-block history.
#
# Measured on the practice history (actuals taken from each case's successor
# case; p05 has no successor in the kit, so it is unscored):
#   - 22 non-overlapping 4h blocks: MAE 32,631 cents vs persistence 35,390
#     (-7.8%).
#   - 5 scored practice cases p00..p04: MAE 50,727 cents vs persistence
#     47,055 (+7.8%). Those 5 are consecutive blocks of one downtrend day,
#     the regime where fading hurts most; the 22-block mixture is the better
#     guide to a 12-case final over 48 fresh hours. The practice score is,
#     as the rules say, no guide to the final.
#
# Deterministic: pure function of the input prices. No randomness, no clock,
# no network, stdlib only. Two runs on the same case write byte-identical
# output.
#
# Usage (as the grader runs it, one case per run):
#   MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py

import json
import math
import os

INPUT_DIR = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
OUTPUT_DIR = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")

# Fade strength: applied to the part of the trailing-30m return beyond the
# dead zone (see TAU_DEADZONE). 0.50 sits between the original shrunk slope
# (0.40) and the 22-block regression slope (-0.64): enough to capture most
# of a displacement's reversion, small enough to limit the damage when a
# 30-minute move is momentum rather than displacement.
K_FADE = 0.50
# Dead zone: trailing-30m moves smaller than this are treated as pool noise
# and not faded at all (the guess stays at persistence). 0.3% is about 3x
# the median |r30| (~0.1%) over the 22 practice-history blocks, so only
# 4 of 22 blocks move enough to be faded.
TAU_DEADZONE = 0.003
# Largest allowed log-shift of the guess, either way (1% of price).
CAP_SHIFT = 0.01

# Scorer bounds: price must be a finite number in (0, 10,000,000).
MIN_PRICE = 0.00001
MAX_PRICE = 9999999.99999


def price5(value):
    """Keep a price on the grid's 5 decimals and inside the scorer's bounds."""
    clamped = min(MAX_PRICE, max(MIN_PRICE, value))
    return round(clamped, 5)


def forecast(prices):
    """One price guess for one case's 1-minute closes (last = checkpoint)."""
    # Index of the checkpoint close: the last finite positive price. Using
    # its index (rather than assuming it is prices[-1]) keeps the 30-minute
    # lookback aligned with the checkpoint even if a trailing print were
    # ever non-finite.
    last_idx = None
    for i in range(len(prices) - 1, -1, -1):
        value = prices[i]
        if (isinstance(value, (int, float)) and math.isfinite(value)
                and value > 0):
            last_idx = i
            break
    if last_idx is None:
        return price5(1.0)
    x = float(prices[last_idx])
    if last_idx < 30:
        return price5(x)
    earlier = prices[last_idx - 30]
    if (not isinstance(earlier, (int, float)) or not math.isfinite(earlier)
            or earlier <= 0):
        return price5(x)
    r30 = math.log(x / float(earlier))
    # Fade only the displacement beyond the dead zone: a small r30 is
    # mostly pool micro-noise, and fading it adds error on most blocks
    # (on the 22 practice-history blocks, fading moves under 0.3% cost
    # more than it saved). The guess stays at persistence when |r30|
    # is inside the dead zone.
    excess = math.copysign(max(0.0, abs(r30) - TAU_DEADZONE), r30)
    shift = -K_FADE * excess
    shift = max(-CAP_SHIFT, min(CAP_SHIFT, shift))
    return price5(x * math.exp(shift))


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    names = sorted(
        name for name in os.listdir(INPUT_DIR) if name.endswith(".in")
    )
    for name in names:
        case_id = name[: -len(".in")]
        path = os.path.join(INPUT_DIR, name)
        with open(path, "r", encoding="utf-8") as handle:
            case = json.load(handle)
        prices = []
        try:
            markets = case.get("markets", [])
            if markets:
                prices = markets[0].get("prices", []) or []
        except AttributeError:
            prices = []
        guess = forecast(prices)
        payload = {"forecasts": [{"case": case_id, "price": guess}]}
        out_path = os.path.join(OUTPUT_DIR, case_id + ".out")
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")


if __name__ == "__main__":
    main()
