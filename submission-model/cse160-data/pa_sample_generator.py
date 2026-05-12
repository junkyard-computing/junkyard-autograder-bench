import sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture
import warnings

warnings.filterwarnings("ignore", message="KMeans is known")
sys.stdout.reconfigure(encoding="utf-8")

TIMEOUT_MS = 837290
RANDOM_STATE = 42
rng = np.random.default_rng(RANDOM_STATE)

pa_hours = input("Enter PA number for submission timing (1-8): ").strip()
pa_runtime = input("Enter PA number for runtimes (1-8): ").strip()
n_samples = int(input("Number of samples to generate: ").strip())

FILTERED = Path(__file__).parent / "filtered-data"
OUT_PATH = Path(__file__).parent / f"pa{pa_hours}_hours_pa{pa_runtime}_runtime_synthetic.csv"

df_hours = pd.read_csv(FILTERED / f"pa{pa_hours}_out_filtered.csv")
df_hours["submission_time"] = pd.to_datetime(df_hours["submission_time"], utc=True)

df_runtime = pd.read_csv(FILTERED / f"pa{pa_runtime}_out_filtered.csv")

# alias df to df_hours for score/active sampling
df = df_hours

# --- Fit timing GMM (hours since first submission) ---
t0 = df_hours["submission_time"].min()
df_hours["hours"] = (df_hours["submission_time"] - t0).dt.total_seconds() / 3600
hours = df_hours["hours"].values.reshape(-1, 1)
max_k_timing = max(1, int(df_hours["hours"].max() / 24))

bic_scores, models = [], []
for k in range(1, max_k_timing + 1):
    gmm = GaussianMixture(n_components=k, covariance_type="full",
                          random_state=RANDOM_STATE, n_init=10, max_iter=500)
    gmm.fit(hours)
    bic_scores.append(gmm.bic(hours))
    models.append(gmm)
timing_gmm = models[int(np.argmin(bic_scores))]
print(f"Timing GMM: k={int(np.argmin(bic_scores))+1} components")

# --- Fit runtime GMM (log-space, valid runtimes only) ---
df_timeout = df_runtime[df_runtime["runtime_ms"] == -1]
df_valid = df_runtime[df_runtime["runtime_ms"] > 0]
timeout_frac = len(df_timeout) / (len(df_valid) + len(df_timeout)) if (len(df_valid) + len(df_timeout)) > 0 else 0
preexec_frac = df_runtime["runtime_ms"].isna().sum() / len(df_runtime)

log_rt = np.log(df_valid["runtime_ms"].values).reshape(-1, 1)
df_runtime["submission_time"] = pd.to_datetime(df_runtime["submission_time"], utc=True)
max_k_runtime = max(1, (df_runtime["submission_time"].max() - df_runtime["submission_time"].min()).days)

bic_scores, models = [], []
for k in range(1, max_k_runtime + 1):
    gmm = GaussianMixture(n_components=k, covariance_type="full",
                          random_state=RANDOM_STATE, n_init=10, max_iter=500)
    gmm.fit(log_rt)
    bic_scores.append(gmm.bic(log_rt))
    models.append(gmm)
runtime_gmm = models[int(np.argmin(bic_scores))]
print(f"Runtime GMM: k={int(np.argmin(bic_scores))+1} components")
print(f"Timeout fraction: {timeout_frac:.1%}  Pre-exec failure fraction: {preexec_frac:.1%}")

# --- Empirical pools for score and active ---
score_pool = df["score"].dropna().values
active_pool = df["active"].values

# --- Sample ---
sampled_hours = timing_gmm.sample(n_samples)[0].flatten()
sampled_hours = np.clip(sampled_hours, 0, None)

# sample all valid runtimes at once to avoid GMM random_state reset bug
log_vals = runtime_gmm.sample(n_samples)[0].flatten()
valid_iter = iter(np.exp(log_vals).round(2))

rolls = rng.random(n_samples)
runtime_samples = []
for roll in rolls:
    if roll < preexec_frac:
        runtime_samples.append(None)
    elif roll < preexec_frac + timeout_frac:
        runtime_samples.append(-1)
    else:
        runtime_samples.append(float(next(valid_iter)))

sampled_scores = rng.choice(score_pool, size=n_samples)
sampled_active = rng.choice(active_pool, size=n_samples)

# Sort by submission time so the CSV is chronological
order = np.argsort(sampled_hours)
sampled_hours = sampled_hours[order]
runtime_samples = [runtime_samples[i] for i in order]
sampled_scores = sampled_scores[order]
sampled_active = sampled_active[order]

out_df = pd.DataFrame({
    "attempt_number": range(1, n_samples + 1),
    "hours_since_first": np.round(sampled_hours, 4),
    "runtime_ms": runtime_samples,
    "score": sampled_scores,
    "active": sampled_active,
})

out_df.to_csv(OUT_PATH, index=False)
print(f"\nWrote {n_samples} synthetic submissions to {OUT_PATH}")
print(f"  Timing from PA{pa_hours}, runtimes from PA{pa_runtime}")
