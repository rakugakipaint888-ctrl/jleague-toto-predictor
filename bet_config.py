"""Version7-Cの買い目最適化で使用する固定設定。

画面で変更できるダブル数、トリプル数、予算、引分候補閾値は
Streamlit Session Stateだけで扱い、このファイルや採用済みモデル設定へは保存しない。
"""

from __future__ import annotations


TOTO_OUTCOMES = ("1", "0", "2")
TOTO_TICKET_PRICE_YEN = 100

BET_TARGETS = {
    "toto": {
        "label": "toto（13試合）",
        "source_match_numbers": tuple(range(1, 14)),
    },
    "mini_a": {
        "label": "mini toto A組（toto第1～5試合）",
        "source_match_numbers": tuple(range(1, 6)),
    },
    "mini_b": {
        "label": "mini toto B組（toto第6～10試合）",
        "source_match_numbers": tuple(range(6, 11)),
    },
}

DOUBLE_COUNT_PRESETS = (0, 1, 2, 3, 4, 5)
TRIPLE_COUNT_PRESETS = (0, 1, 2, 3)
BUDGET_PRESETS_YEN = (500, 1_000, 2_000, 3_000, 5_000, 10_000)

# 全組み合わせは画面200口、CSV 100,000口までとする。買い目の試合別CSVは
# 口数にかかわらず常に出力できるため、大規模時も提案自体は失われない。
MAX_COMBINATION_DISPLAY = 200
MAX_COMBINATION_EXPORT = 100_000

PROBABILITY_SUM_TOLERANCE = 1e-6
SCORE_TIE_TOLERANCE = 1e-12

# 各0～1特徴量を100点へ合成する。すべての重みは合計1.0。
UNCERTAINTY_SCORE_WEIGHTS = {
    "entropy": 0.35,
    "top_two_closeness": 0.25,
    "maximum_uncertainty": 0.20,
    "draw_signal": 0.10,
    "top_three_closeness": 0.10,
}
DOUBLE_SCORE_WEIGHTS = {
    "top_two_closeness": 0.40,
    "entropy": 0.25,
    "maximum_uncertainty": 0.15,
    "draw_signal": 0.10,
    "second_probability": 0.10,
}
TRIPLE_SCORE_WEIGHTS = {
    "entropy": 0.30,
    "top_three_closeness": 0.20,
    "maximum_uncertainty": 0.20,
    "top_two_closeness": 0.15,
    "draw_signal": 0.10,
    "third_probability": 0.05,
}
SINGLE_CONFIDENCE_WEIGHTS = {
    "maximum_certainty": 0.40,
    "margin_certainty": 0.30,
    "distribution_certainty": 0.20,
    "draw_safety": 0.10,
}

# 確率差を0～1特徴量へ変換する尺度。結果に合わせた自動調整は行わない。
TOP_TWO_MARGIN_SCALE = 0.50
TOP_THREE_MARGIN_SCALE = 2.0 / 3.0
SINGLE_MARGIN_SCALE = 0.35
DRAW_CLOSENESS_SCALE = 0.25
SECOND_PROBABILITY_SCALE = 0.50
THIRD_PROBABILITY_SCALE = 1.0 / 3.0

SINGLE_CONFIDENCE_HIGH = 60.0
SINGLE_CONFIDENCE_MEDIUM = 35.0
DEFAULT_DRAW_CANDIDATE_THRESHOLD = 0.25
DEFAULT_DRAW_CANDIDATE_MARGIN = 0.05
MIN_DRAW_CANDIDATE_PROBABILITY = 0.20
DRAW_SIGNAL_THRESHOLD_WEIGHT = 0.60
DRAW_SIGNAL_CLOSENESS_WEIGHT = 0.40
MODEL_DRAW_SIGNAL_FLOOR = 0.75

# 画面入力の安全な範囲。現在の設定をファイルへ永続化する値ではない。
DEFAULT_BUDGET_YEN = 3_000
MAX_CUSTOM_BUDGET_YEN = 100_000_000
