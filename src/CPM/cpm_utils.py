import math

def normalized(arr,norm=0):
    """
    Scale a sequence of values by a common divisor.

    Parameters
    ----------
    arr : list of float
        Values to scale.
    norm : float, optional
        Divisor to apply.  When ``0`` (default) each element is divided by
        ``max(arr)``; otherwise every element is divided by ``norm``.

    Returns
    -------
    list of float
        The scaled values.
    """
    if(norm==0):
        newarr=[i/max(arr) for i in arr]
    else:
        newarr=[i/norm for i in arr]
    return newarr

def normalize_tuples(data):
    """
    Normalize the second value in each tuple by dividing by the max value.

    Parameters
    ----------
    data : list of tuple
        Pairs in the form ``[(key, value), ...]``.

    Returns
    -------
    list of tuple
        Pairs in the form ``[(key, normalized_value), ...]``.  Returns an empty
        list when ``data`` is empty, and zeroes every value when the maximum
        value is ``0``.
    """
    if not data:
        return []

    # Extract the second values
    values = [v for _, v in data]

    max_val = max(values)

    # Avoid division by zero
    if max_val == 0:
        return [(k, 0) for k, _ in data]

    # Normalize
    normalized = [(k, v / max_val) for k, v in data]

    return normalized

def safe_div(a, b):
    """Return ``a / b``, or ``1`` when ``b`` is zero."""
    return a / b if b != 0 else 1

def sigmoid_bipolar(x):
    """Maps x to (-1, 1): 2/(1+exp(-x)) - 1"""
    return 2 / (1 + math.exp(-x)) - 1

def sigmoid_inv(x):
    """Maps x to (0, 2): 2/(1+exp(x))"""
    return 2 / (1 + math.exp(0.001*x))

def custom_priority_mehh_8000_b(ES, EF, LS, LF, TPC, TSC, RR, AvgRReq, MaxRReq, MinRReq):
    """
    Priority score for the ``mehh_8000_b`` evolved (MEHH) heuristic rule.

    Combines an activity's normalized CPM timing metrics and resource-requirement
    metrics into a single scalar.  Registered in ``CUSTOM_PRIORITY_FUNCS`` and
    evaluated by ``Pert.calculate_gp_rules`` to rank candidate activities.

    Parameters
    ----------
    ES, EF : float
        Normalized earliest-start and earliest-finish times of the activity.
    LS, LF : float
        Normalized latest-start and latest-finish times of the activity.
    TPC : float
        Normalized ``mtp`` (most-total-predecessors) count.
    TSC : float
        Normalized ``mts`` (most-total-successors) count.
    RR : float
        Resource-requirement ratio: fraction of resource types the activity
        consumes.
    AvgRReq, MaxRReq, MinRReq : float
        Average, maximum and minimum normalized resource requirement across all
        resource types.

    Returns
    -------
    float
        Priority score for the activity under this heuristic rule.
    """
    return (
        LF * LS
        - max(-LF, min(AvgRReq, MaxRReq))
        - min(-AvgRReq, AvgRReq) * min(LF, -MaxRReq) * min(-LS, ES + MaxRReq)
        + min(
            -TSC,
            2 * LF * (-RR - TPC + min(MinRReq, RR)) * min(AvgRReq, EF),
        )
    )

def custom_priority_mehh_3375_b(ES, EF, LS, LF, TPC, TSC, RR, AvgRReq, MaxRReq, MinRReq):
    """
    Priority score for the ``mehh_3375_b`` evolved (MEHH) heuristic rule.

    Combines an activity's normalized CPM timing metrics and resource-requirement
    metrics into a single scalar.  Registered in ``CUSTOM_PRIORITY_FUNCS`` and
    evaluated by ``Pert.calculate_gp_rules`` to rank candidate activities.

    Parameters
    ----------
    ES, EF : float
        Normalized earliest-start and earliest-finish times of the activity.
    LS, LF : float
        Normalized latest-start and latest-finish times of the activity.
    TPC : float
        Normalized ``mtp`` (most-total-predecessors) count.
    TSC : float
        Normalized ``mts`` (most-total-successors) count.
    RR : float
        Resource-requirement ratio: fraction of resource types the activity
        consumes.
    AvgRReq, MaxRReq, MinRReq : float
        Average, maximum and minimum normalized resource requirement across all
        resource types.

    Returns
    -------
    float
        Priority score for the activity under this heuristic rule.
    """
    return (
        max(LS, MinRReq)
        + min(
            -TSC * (AvgRReq * TSC + AvgRReq + ES + 1 + safe_div(1, (-EF - LS))),
            ES**2 * LS * MinRReq * max(AvgRReq, EF, AvgRReq * ES)
            + min(
                -MinRReq,
                MaxRReq * RR * (AvgRReq + safe_div(1, MaxRReq)),
                LS - RR,
            )
            - safe_div(1, EF * ES * (EF + MaxRReq)),
        )
    )

def custom_priority_mehh_1000_b(ES, EF, LS, LF, TPC, TSC, RR, AvgRReq, MaxRReq, MinRReq):
    """
    Priority score for the ``mehh_1000_b`` evolved (MEHH) heuristic rule.

    Combines an activity's normalized CPM timing metrics and resource-requirement
    metrics into a single scalar.  Registered in ``CUSTOM_PRIORITY_FUNCS`` and
    evaluated by ``Pert.calculate_gp_rules`` to rank candidate activities.

    Parameters
    ----------
    ES, EF : float
        Normalized earliest-start and earliest-finish times of the activity.
    LS, LF : float
        Normalized latest-start and latest-finish times of the activity.
    TPC : float
        Normalized ``mtp`` (most-total-predecessors) count.
    TSC : float
        Normalized ``mts`` (most-total-successors) count.
    RR : float
        Resource-requirement ratio: fraction of resource types the activity
        consumes.
    AvgRReq, MaxRReq, MinRReq : float
        Average, maximum and minimum normalized resource requirement across all
        resource types.

    Returns
    -------
    float
        Priority score for the activity under this heuristic rule.
    """
    return (
        -AvgRReq
        + LF * LS
        - TSC
        + (EF * MaxRReq - MaxRReq * TPC)
        * (LS * MaxRReq + safe_div(MaxRReq, TSC))
        * (-MaxRReq * RR - safe_div(1, LS * MaxRReq))
        * (
            -MaxRReq * min(AvgRReq, EF)
            + min(ES, RR)
            + min(
                safe_div(min(1, AvgRReq * TSC), (EF * TPC + 2 * MinRReq)),
                safe_div(1, (-AvgRReq - min(AvgRReq, TPC))),
            )
            + min(safe_div(1, (MinRReq + RR)), max(TPC, TSC))
        )
        * max(ES * LF, min(LS, RR))
        * min(ES * MinRReq, max(EF, MinRReq))
        * min(MinRReq + TSC, max(MinRReq, RR))
        * min(AvgRReq, TSC, max(EF, LS))
    )

def custom_priority_mehh_125_b(ES, EF, LS, LF, TPC, TSC, RR, AvgRReq, MaxRReq, MinRReq):
    """
    Priority score for the ``mehh_125_b`` evolved (MEHH) heuristic rule.

    Combines an activity's normalized CPM timing metrics and resource-requirement
    metrics into a single scalar.  Registered in ``CUSTOM_PRIORITY_FUNCS`` and
    evaluated by ``Pert.calculate_gp_rules`` to rank candidate activities.

    Parameters
    ----------
    ES, EF : float
        Normalized earliest-start and earliest-finish times of the activity.
    LS, LF : float
        Normalized latest-start and latest-finish times of the activity.
    TPC : float
        Normalized ``mtp`` (most-total-predecessors) count.
    TSC : float
        Normalized ``mts`` (most-total-successors) count.
    RR : float
        Resource-requirement ratio: fraction of resource types the activity
        consumes.
    AvgRReq, MaxRReq, MinRReq : float
        Average, maximum and minimum normalized resource requirement across all
        resource types.

    Returns
    -------
    float
        Priority score for the activity under this heuristic rule.
    """
    return (
        -2 * AvgRReq
        + EF
        - LF
        - max(EF, TSC)
        + max(LS, safe_div(RR, TPC) - max(MaxRReq + MinRReq, MaxRReq + TSC))
    )

def custom_priority_gphh_b(ES, EF, LS, LF, TPC, TSC, RR, AvgRReq, MaxRReq, MinRReq):
    """
    Priority score for the ``gphh_b`` evolved (GPHH) heuristic rule.

    Combines an activity's normalized CPM timing metrics and resource-requirement
    metrics into a single scalar.  Registered in ``CUSTOM_PRIORITY_FUNCS`` and
    evaluated by ``Pert.calculate_gp_rules`` to rank candidate activities.

    Parameters
    ----------
    ES, EF : float
        Normalized earliest-start and earliest-finish times of the activity.
    LS, LF : float
        Normalized latest-start and latest-finish times of the activity.
    TPC : float
        Normalized ``mtp`` (most-total-predecessors) count.
    TSC : float
        Normalized ``mts`` (most-total-successors) count.
    RR : float
        Resource-requirement ratio: fraction of resource types the activity
        consumes.
    AvgRReq, MaxRReq, MinRReq : float
        Average, maximum and minimum normalized resource requirement across all
        resource types.

    Returns
    -------
    float
        Priority score for the activity under this heuristic rule.
    """
    return (
        -AvgRReq
        - EF
        - ES
        - LF
        - 2 * LS
        - max(AvgRReq, TSC)
        - min(AvgRReq, -TSC)
        + min(EF, LS)
    )


CUSTOM_PRIORITY_FUNCS = {
    "mehh_8000_b": custom_priority_mehh_8000_b,
    "mehh_3375_b": custom_priority_mehh_3375_b,
    "mehh_1000_b": custom_priority_mehh_1000_b,
    "mehh_125_b": custom_priority_mehh_125_b,
    "gphh_b": custom_priority_gphh_b,
}
