import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.mixture import GaussianMixture
from sklearn.model_selection import cross_val_score
import warnings

warnings.filterwarnings("ignore", message="KMeans is known")
sys.stdout.reconfigure(encoding="utf-8")

# Config
pa_num = input("Enter PA number (3,4,5,7,8): ").strip()
CSV_PATH = Path(__file__).parent / "filtered-data" / f"pa{pa_num}_out_filtered.csv"
MAX_COMPONENTS = None  # set dynamically from submission date range
TIMEOUT_MS = 837290  # max observed; treat -1 rows as censored here
RANDOM_STATE = 42

# Load & partition
df = pd.read_csv(CSV_PATH)

# Blank: pre-execution failure - excluded (no compute load)
# -1: grader timeout - censored at TIMEOUT_MS
# >0: valid measured runtimes

df_timeout = df[df["runtime_ms"] == -1].copy()
df_valid = df[df["runtime_ms"] > 0].copy()

times = pd.to_datetime(df["submission_time"], utc=True)
MAX_COMPONENTS = max(1, (times.max() - times.min()).days)

print(f"Total submissions   : {len(df)}")
print(f"Pre-exec failures   : {df['runtime_ms'].isna().sum()}  (excluded)")
print(f"Timeouts (-1)       : {len(df_timeout)}  (censored at {TIMEOUT_MS:,} ms)")
print(f"Valid runtimes      : {len(df_valid)}")
print()

# log-transform valid runtimes
log_rt = np.log(df_valid["runtime_ms"].values).reshape(-1, 1)

# BIC model selection
bic_scores = []
models = []

for k in range(1, MAX_COMPONENTS + 1):
    gmm = GaussianMixture(
        n_components=k,
        covariance_type="full",
        random_state=RANDOM_STATE,
        n_init=10,          # multiple restarts to avoid local optima
        max_iter=500,
    )
    gmm.fit(log_rt)
    bic = gmm.bic(log_rt)
    bic_scores.append(bic)
    models.append(gmm)
    print(f"  k={k}  BIC={bic:.1f}  converged={gmm.converged_}")

best_k = np.argmin(bic_scores) + 1
best_gmm = models[best_k - 1]
print(f"\nBest k by BIC: {best_k}")

# Component summary
labels = best_gmm.predict(log_rt)
weights = best_gmm.weights_
means = best_gmm.means_.flatten()
stds = np.sqrt(best_gmm.covariances_.flatten())

# sort components by mean (ascending runtime)
order = np.argsort(means)

print("\nGMM Components (sorted by median runtime):")
print(f"{'Component':<12} {'Weight':>8} {'Median (ms)':>12} {'P5 (ms)':>10} {'P95 (ms)':>10}")
print("-" * 56)

component_stats = []
for rank, i in enumerate(order):
    median_ms = np.exp(means[i])
    p5_ms = np.exp(means[i] - 1.645 * stds[i])
    p95_ms = np.exp(means[i] + 1.645 * stds[i])
    component_stats.append((rank + 1, weights[i], median_ms, p5_ms, p95_ms))
    print(f"  C{rank+1:<9} {weights[i]:>8.3f} {median_ms:>12.1f} {p5_ms:>10.1f} {p95_ms:>10.1f}")

# timeout contribution on top of GMM
timeout_frac = len(df_timeout) / (len(df_valid) + len(df_timeout))
print(f"\n  Timeouts       {timeout_frac:>8.3f}  (>{TIMEOUT_MS:,} ms, censored)")

# plotting
fig, ax = plt.subplots(figsize=(8, 5))
fig.suptitle(f"Submission Runtime for PA{pa_num}", fontsize=14, fontweight="bold")

log_range = np.linspace(log_rt.min() - 0.5, log_rt.max() + 0.5, 500).reshape(-1, 1)
log_pdf = np.exp(best_gmm.score_samples(log_range))

ax.hist(log_rt, bins=40, density=True, color="#b0c4de", edgecolor="white",
        linewidth=0.5, label="Observed (log ms)")
ax.plot(log_range, log_pdf, color="#1f3a5f", linewidth=2, label="GMM mixture")

colors = plt.cm.tab10.colors
for rank, i in enumerate(order):
    comp_pdf = (
        weights[i]
        / (stds[i] * np.sqrt(2 * np.pi))
        * np.exp(-0.5 * ((log_range.flatten() - means[i]) / stds[i]) ** 2)
    )
    ax.plot(log_range, comp_pdf, "--", color=colors[rank % len(colors)],
            linewidth=1.4, label=f"C{rank+1} (w={weights[i]:.2f})")

# mark timeout threshold
ax.axvline(np.log(TIMEOUT_MS), color="crimson", linewidth=1.2,
           linestyle=":", label=f"Timeout ({TIMEOUT_MS/1000:.0f}s)")

ax.set_xlabel("log(runtime_ms)")
ax.set_ylabel("Density")
# ax.set_title(f"Log-space density  (k={best_k})")
ax.legend(fontsize=8)
ax.grid(True, alpha=0.3)

# Equation output
eq_lines = ["f(log_ms) ="]
for rank, i in enumerate(order):
    eq_lines.append(f"  + {weights[i]:.3f} · N(μ={means[i]:.3f}, σ={stds[i]:.3f})")
eq_str = "\n".join(eq_lines)
print("\nMixture equation:")
print(eq_str)

ax.text(0.02, 0.95, eq_str, transform=ax.transAxes, fontsize=7,
        verticalalignment="top", fontfamily="monospace",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.7))

# BIC curve
# ax2 = axes[1]
# ks = list(range(1, len(bic_scores) + 1))
# ax2.plot(ks, bic_scores, marker="o", color="#1f3a5f", linewidth=2)
# ax2.axvline(best_k, color="crimson", linestyle="--", linewidth=1.2,
#             label=f"Best k={best_k}")
# ax2.set_xlabel("Number of components (k)")
# ax2.set_ylabel("BIC")
# ax2.set_title("BIC model selection")
# ax2.legend()
# ax2.grid(True, alpha=0.3)

plt.tight_layout()
plot_path = f"gmm-figures/gmm_runtime_PA{pa_num}.png"
plt.savefig(plot_path, dpi=150, bbox_inches="tight")
print(f"\nPlot saved to {plot_path}")

# Load characterization
print("\nLoad characterization:")
print("  Median job (P50 of mixture) :", f"{np.exp(np.average(means, weights=weights)):.1f} ms")
print("  Timeout fraction            :", f"{timeout_frac:.1%} of all executed submissions")
print("  Timeout load contribution   : disproportionate - each holds a worker thread")
print("                                for the full grader kill window")