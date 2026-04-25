import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.mixture import GaussianMixture

# Config
CSV_PATH = "pa7_out_filtered.csv"
MAX_COMPONENTS = 10
RANDOM_STATE = 42

# Load & compute hours
df = pd.read_csv(CSV_PATH)
df["submission_time"] = pd.to_datetime(df["submission_time"], utc=True)

t0 = df["submission_time"].min()
df["hours"] = (df["submission_time"] - t0).dt.total_seconds() / 3600

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
fig, ax = plt.subplots(figsize=(10, 5))
fig.suptitle("Submission Timing for PA7", fontsize=13, fontweight="bold")

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
    ax.plot(x, comp_pdf, "--", color=colors[rank], linewidth=1.4,
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

plt.tight_layout()
plt.savefig("gmm_hours_PA7.png", dpi=150, bbox_inches="tight")
print("\nPlot saved to gmm_hours_PA7.png")