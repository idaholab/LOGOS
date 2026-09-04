import math

def normalized(arr,norm=0):
    if(norm==0):
        newarr=[i/max(arr) for i in arr]
    else:
        newarr=[i/norm for i in arr]
    return newarr

def normalize_tuples(data):
    """
    Normalize the second value in each tuple by dividing by the max value.

    Args:
        data (list of tuples): [(key, value), ...]

    Returns:
        list of tuples: [(key, normalized_value), ...]
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
    return a / b if b != 0 else 1

def sigmoid_bipolar(x):
    """Maps x to (-1, 1): 2/(1+exp(-x)) - 1"""
    return 2 / (1 + math.exp(-x)) - 1

def sigmoid_inv(x):
    """Maps x to (0, 2): 2/(1+exp(x))"""
    return 2 / (1 + math.exp(0.001*x))

def custom_priority_mehh_8000_b(ES, EF, LS, LF, TPC, TSC, RR, AvgRReq, MaxRReq, MinRReq):
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
    return (
        -2 * AvgRReq
        + EF
        - LF
        - max(EF, TSC)
        + max(LS, safe_div(RR, TPC) - max(MaxRReq + MinRReq, MaxRReq + TSC))
    )

def custom_priority_gphh_b(ES, EF, LS, LF, TPC, TSC, RR, AvgRReq, MaxRReq, MinRReq):
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
