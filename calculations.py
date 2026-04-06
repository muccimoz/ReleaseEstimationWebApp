"""
Release Estimation — Core calculation engine.
Validated against spreadsheet test cases — do not modify RSM values.
"""

import math
from datetime import date, timedelta
from scipy.stats import norm


# ── RSM (Ratio Scale Multiplier) lookup table ─────────────────────────────────
# Fixed constants derived from the statistical estimation methodology.
# These are baked in — do not surface or edit in the UI.
RSM = {
    "Near certainty":           0.0707,
    "Very high confidence":     0.1000,
    "High confidence":          0.1414,
    "Medium-high confidence":   0.1732,
    "Medium confidence":        0.2000,
    "Medium-low confidence":    0.2345,
    "Low confidence":           0.2739,
    "Very low confidence":      0.3162,
    "Extremely low confidence": 0.3536,
    "Guesstimate":              0.4062,
}

CONFIDENCE_LABELS = list(RSM.keys())


def compute_estimate(
    most_likely: float,
    worst_case: float,
    best_case: float,
    confidence_label: str,
    desired_confidence: float,
    backlog: float,
    sprint_weeks: int,
    start_date: date,
    std_dev_override: float = None,
    extra_days: int = 0,
) -> dict:
    """
    Compute a single release date estimate.

    Parameters
    ----------
    most_likely         : most likely velocity per sprint
    worst_case          : worst-case velocity per sprint
    best_case           : best-case velocity per sprint
    confidence_label    : how confident the team is in the most likely estimate
    desired_confidence  : confidence level for the output date (0.0001–0.9999)
    backlog             : total story points / issues in the release
    sprint_weeks        : sprint length in weeks
    start_date          : release start date
    std_dev_override    : optional — use this std dev instead of the derived one
    extra_days          : optional extra calendar days to add (e.g. holidays)

    Returns
    -------
    dict with all intermediate and final values
    """
    # Bell-shape health check
    lower_tail  = most_likely - worst_case
    upper_tail  = best_case - most_likely
    farther     = max(lower_tail, upper_tail)
    bell_ratio  = (min(lower_tail, upper_tail) / farther) if farther > 0 else 0.0
    bell_ok     = bell_ratio >= 0.5

    # PERT weighted average velocity
    pert_mean = (best_case + 4 * most_likely + worst_case) / 6

    # Standard deviation
    if std_dev_override is not None:
        std_dev = float(std_dev_override)
    else:
        rsm     = RSM[confidence_label]
        std_dev = (best_case - worst_case) * rsm

    # Guaranteed minimum velocity at the desired confidence level
    # Mirrors Excel's NORM.INV(1 - desired_confidence, mean, std_dev)
    guaranteed_min = norm.ppf(1 - desired_confidence, loc=pert_mean, scale=std_dev)

    # Sprints needed (raw; ceil used for display only — tells user which sprint they finish in)
    sprints_raw     = backlog / guaranteed_min
    sprints_rounded = math.ceil(sprints_raw)

    # Weeks and calendar days — rounded at weeks level to match spreadsheet
    business_weeks = round(sprint_weeks * sprints_raw)
    total_days     = business_weeks * 7 + extra_days

    # Projected completion date
    projected_date = start_date + timedelta(days=total_days)

    return {
        "bell_ratio":      round(bell_ratio, 4),
        "bell_ok":         bell_ok,
        "pert_mean":       round(pert_mean, 4),
        "std_dev":         round(std_dev, 4),
        "guaranteed_min":  round(guaranteed_min, 4),
        "sprints_raw":     round(sprints_raw, 4),
        "sprints_rounded": sprints_rounded,
        "business_weeks":  business_weeks,
        "total_days":      total_days,
        "projected_date":  projected_date,
    }
