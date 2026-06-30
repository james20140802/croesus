"""Thin adapter isolating all version-specific TimesFM API.

Exposes forecaster(context: np.ndarray, horizon: int) -> np.ndarray of length
`horizon` (predicted PRICE LEVELS), matching metrics.rolling_origin_eval.

Verified against timesfm==1.2.0 (pytorch backend).
API surface: timesfm.TimesFm, timesfm.TimesFmHparams, timesfm.TimesFmCheckpoint
Constructor: TimesFm(hparams, checkpoint)
Forecast:    TimesFm.forecast(inputs: list[np.ndarray], freq: list[int]) -> (point, quantiles)
"""
import numpy as np


class TimesFMForecaster:
    def __init__(self, context_len: int = 512, horizon_len: int = 128):
        import timesfm
        # timesfm==1.2.0 exports TimesFm (not TimesFM)
        self._tfm = timesfm.TimesFm(
            hparams=timesfm.TimesFmHparams(
                backend="cpu",
                per_core_batch_size=1,
                context_len=context_len,
                horizon_len=horizon_len,
            ),
            checkpoint=timesfm.TimesFmCheckpoint(
                huggingface_repo_id="google/timesfm-1.0-200m-pytorch"),
        )

    def __call__(self, context: np.ndarray, horizon: int) -> np.ndarray:
        point, _ = self._tfm.forecast([np.asarray(context, dtype=float)], freq=[0])
        return np.asarray(point[0])[:horizon]
