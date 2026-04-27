import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.mixture import GaussianMixture
import warnings

warnings.filterwarnings("ignore", message="KMeans is known")
sys.stdout.reconfigure(encoding="utf-8")

# Config
pa_num = input("Enter PA number: ").strip()
CSV_PATH = Path(__file__).parent / "filtered-data" / f"pa{pa_num}_out_filtered.csv"
RANDOM_STATE = 42

# Load & compute hours
df = pd.read_csv(CSV_PATH)
df["submission_time"] = pd.to_datetime(df["submission_time"], utc=True)

t0 = df["submission_time"].min()
df["hours"] = (df["submission_time"] - t0).dt.total_seconds() / 3600

MAX_COMPONENTS = max(1, int(df["hours"].max() / 24))

print(f"First submission : {t0}")
print(f"Last submission  : {df['submission_time'].max()}")
print(f"Span             : {df['hours'].max():.1f} hours ({df['hours'].max()/24:.1f} days)")
print(f"Total submissions: {len(df)}")
print()

hours = df["hours"].values.reshape(-1, 1)

# BIC model selection
print("BIC search:")
bic_scores, models = [], []
for k in range(1, MAX_COMPONENTS + 1):
    gmm = GaussianMixture(n_components=k, covariance_type="full",
                          random_state=RANDOM_STATE, n_init=10, max_iter=500)
    gmm.fit(hours)
    bic = gmm.bic(hours)
    bic_scores.append(bic)
    models.append(gmm)
    print(f"  k={k}  BIC={bic:.1f}  converged={gmm.converged_}")

best_k = int(np.argmin(bic_scores)) + 1
gmm = models[best_k - 1]
print(f"\nBest k by BIC: {best_k}")

# sort components by mean (chronological order)
order = np.argsort(gmm.means_.flatten())
weights = gmm.weights_[order]
means = gmm.means_.flatten()[order]
stds = np.sqrt(gmm.covariances_.flatten())[order]

# Component summary
print(f"\nGMM Components (k={best_k}, sorted chronologically):")
print(f"{'Component':<12} {'Weight':>8} {'Center (hrs)':>14} {'Center (day)':>14} {'±1σ (hrs)':>10}")
print("-" * 62)

for rank, (w, mu, sigma) in enumerate(zip(weights, means, stds)):
    print(f"  C{rank+1:<9} {w:>8.3f} {mu:>14.1f} {mu/24:>14.1f} {sigma:>10.1f}")

# plotting
fig, ax = plt.subplots(figsize=(8, 5))
fig.suptitle(f"Submission Timing for PA{pa_num}", fontsize=13, fontweight="bold")

x = np.linspace(hours.min() - 5, hours.max() + 5, 1000).reshape(-1, 1)
mixture_pdf = np.exp(gmm.score_samples(x))

ax.hist(hours, bins=60, density=True, color="#b0c4de", edgecolor="white",
        linewidth=0.5, label="Observed submissions")
ax.plot(x, mixture_pdf, color="#1f3a5f", linewidth=2, label="GMM mixture")

colors = plt.cm.tab10.colors
for rank, (w, mu, sigma) in enumerate(zip(weights, means, stds)):
    comp_pdf = (
        w / (sigma * np.sqrt(2 * np.pi))
        * np.exp(-0.5 * ((x.flatten() - mu) / sigma) ** 2)
    )
    ax.plot(x, comp_pdf, "--", color=colors[rank % len(colors)], linewidth=1.4,
            label=f"C{rank+1}  {mu:.0f}h / day {mu/24:.1f}  (w={w:.2f})")

# day markers
for day in range(1, int(df["hours"].max() / 24) + 1):
    ax.axvline(day * 24, color="gray", linewidth=0.7, linestyle=":", alpha=0.6)
    ax.text(day * 24 + 0.5, ax.get_ylim()[1] * 0.98, f"Day {day}",
            fontsize=7, color="gray", va="top")

ax.set_xticks(np.arange(0, df["hours"].max() + 24, 24))
ax.set_xlabel("Hours since first submission")
ax.set_ylabel("Density")
ax.legend(fontsize=8, loc="upper right")
ax.grid(True, alpha=0.3)

# Equation output
eq_lines = ["f(hours) ="]
for w, mu, sigma in zip(weights, means, stds):
    eq_lines.append(f"  + {w:.3f} · N(μ={mu:.2f}h, σ={sigma:.2f}h)")
eq_str = "\n".join(eq_lines)
print("\nMixture equation:")
print(eq_str)

ax.text(0.02, 0.95, eq_str, transform=ax.transAxes, fontsize=7,
        verticalalignment="top", horizontalalignment="left", fontfamily="monospace",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.7))

plt.tight_layout()
plot_path = f"gmm-figures/gmm_hours_PA{pa_num}.png"
plt.savefig(plot_path, dpi=150, bbox_inches="tight")
print(f"\nPlot saved to {plot_path}")