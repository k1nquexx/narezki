from __future__ import annotations

from typing import Any


class EMASmoother:
    """Exponential moving average smoother with deadzone to eliminate micro-jitter."""

    def __init__(self, alpha: float = 0.25, deadzone: float = 0.012) -> None:
        self.alpha = max(0.05, min(0.95, float(alpha)))
        self.deadzone = max(0.0, float(deadzone))
        self.state: float | None = None

    def reset(self) -> None:
        self.state = None

    def update(self, measurement: float) -> float:
        if self.state is None:
            self.state = measurement
            return self.state
        if abs(measurement - self.state) < self.deadzone:
            return self.state
        self.state = self.alpha * measurement + (1.0 - self.alpha) * self.state
        return self.state


class KalmanSmoother1D:
    """Constant-velocity 1D Kalman filter with deadzone for smooth virtual camera panning."""

    def __init__(
        self,
        process_noise: float = 0.008,
        measurement_noise: float = 0.06,
        deadzone: float = 0.010,
    ) -> None:
        self.q = process_noise
        self.r = measurement_noise
        self.deadzone = deadzone
        self.x: float | None = None
        self.v: float = 0.0
        self.p00: float = 1.0
        self.p01: float = 0.0
        self.p10: float = 0.0
        self.p11: float = 1.0
        self.last_t: float | None = None

    def reset(self) -> None:
        self.x = None
        self.v = 0.0
        self.p00 = 1.0
        self.p01 = 0.0
        self.p10 = 0.0
        self.p11 = 1.0
        self.last_t = None

    def update(self, measurement: float, t: float) -> float:
        if self.x is None or self.last_t is None:
            self.x = measurement
            self.v = 0.0
            self.last_t = t
            return self.x

        dt = max(1e-3, min(1.0, t - self.last_t))
        self.last_t = t

        if abs(measurement - self.x) < self.deadzone:
            measurement = self.x

        # Predict step: x = x + v*dt
        x_pred = self.x + self.v * dt
        v_pred = self.v * 0.92  # slight velocity damping prevents overshoot

        p00_pred = self.p00 + dt * (self.p10 + self.p01) + (dt * dt) * self.p11 + self.q * dt
        p01_pred = self.p01 + dt * self.p11
        p10_pred = self.p10 + dt * self.p11
        p11_pred = self.p11 + self.q

        # Update step
        s = p00_pred + self.r
        k0 = p00_pred / s
        k1 = p10_pred / s
        innov = measurement - x_pred

        self.x = max(0.0, min(1.0, x_pred + k0 * innov))
        self.v = v_pred + k1 * innov

        self.p00 = (1.0 - k0) * p00_pred
        self.p01 = (1.0 - k0) * p01_pred
        self.p10 = p10_pred - k1 * p00_pred
        self.p11 = p11_pred - k1 * p01_pred
        return self.x


def smooth_trajectory(
    raw_points: list[dict[str, Any]],
    scene_cuts: list[float] | None = None,
    clip_start: float = 0.0,
    method: str = "kalman",
    strength: float = 0.65,
) -> list[dict[str, Any]]:
    """Smooth normalized (cx, cy) trajectory while hard-resetting state across scene cuts."""
    if not raw_points:
        return []

    rel_cuts = sorted(
        {
            round(float(c) - clip_start, 3)
            for c in (scene_cuts or [])
            if float(c) > clip_start + 0.05
        }
    )

    # Higher strength -> heavier smoothing (higher measurement noise / lower alpha)
    s = max(0.1, min(0.95, float(strength)))
    if method == "ema":
        alpha = max(0.08, min(0.65, 0.72 - s * 0.6))
        sx = EMASmoother(alpha=alpha, deadzone=0.012)
        sy = EMASmoother(alpha=alpha, deadzone=0.012)
    else:
        r_noise = 0.01 + (s ** 2) * 0.16
        q_noise = 0.015 * (1.0 - s * 0.5)
        sx_k = KalmanSmoother1D(process_noise=q_noise, measurement_noise=r_noise, deadzone=0.010)
        sy_k = KalmanSmoother1D(process_noise=q_noise, measurement_noise=r_noise, deadzone=0.010)

    cut_idx = 0
    prev_t = -1.0
    smoothed: list[dict[str, Any]] = []

    for pt in raw_points:
        t = float(pt["t"])
        cx = max(0.0, min(1.0, float(pt["cx"])))
        cy = max(0.0, min(1.0, float(pt["cy"])))
        conf = float(pt.get("conf", 1.0))

        # Reset smoother if we crossed a scene cut between prev_t and t
        while cut_idx < len(rel_cuts) and rel_cuts[cut_idx] <= t:
            if prev_t >= 0.0 and rel_cuts[cut_idx] > prev_t:
                if method == "ema":
                    sx.reset()
                    sy.reset()
                else:
                    sx_k.reset()
                    sy_k.reset()
            cut_idx += 1

        if method == "ema":
            out_x = sx.update(cx)
            out_y = sy.update(cy)
        else:
            out_x = sx_k.update(cx, t)
            out_y = sy_k.update(cy, t)

        smoothed.append(
            {
                "t": round(t, 3),
                "cx": round(out_x, 4),
                "cy": round(out_y, 4),
                "conf": round(conf, 3),
            }
        )
        prev_t = t

    return smoothed
