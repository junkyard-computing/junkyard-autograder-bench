import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from matplotlib import cm
from sklearn import mixture
import os
import pathlib
import sys
import re

SECS_IN_HR = 3600
NUM_COMPONENTS = 2
PA_PATTERN = r'.+pa([1-9]*)'
DEFAULT_PA_NUM = 42
pa = DEFAULT_PA_NUM
os.environ["OMP_NUM_THREADS"] = "2"

def extract_features(df: pd.DataFrame):
    clean_df = df.dropna()
    clean_df = clean_df[clean_df["runtime_ms"] > 0]

    sub_times = np.array(clean_df["submission_time"])
    sub_times = np.array(list(map(datetime.fromisoformat, sub_times)))
    min_sub_time = np.min(sub_times)

    secs_since_first_sub = np.array(list(map(timedelta.total_seconds, (sub_times - min_sub_time))))

    return (
        divmod(secs_since_first_sub, SECS_IN_HR)[0],
        np.log(np.array(clean_df["runtime_ms"]) + 1)
    )
    
def create_gmm(train_data, n_components):
    gmm = mixture.GaussianMixture(n_components=n_components, covariance_type="full")
    gmm.fit(train_data)
    return gmm

def create_fig(gmm, x, y):
    X, Y = np.meshgrid(x, y)
    XX = np.array([X.ravel(), Y.ravel()]).T
    Z = np.exp(gmm.score_samples(XX))
    Z = Z.reshape(X.shape)

    fig = plt.figure(figsize=(8, 8))
    ax = fig.add_subplot(111, projection='3d')
    ax.set_box_aspect(None, zoom=0.80)
    #ax.tick_params(axis="z", which="major", pad=10)
    ax.tick_params(axis="z", which="major", pad=12)
    ax.plot_surface(X,Y,Z, cmap=cm.jet)

    ax.view_init(elev=30, azim=-67)

    ax.set_xlabel("Time since first submission (Hours)", labelpad=10)
    ax.set_ylabel("Runtime (ms)", labelpad=10)
    ax.set_zlabel("Density", labelpad=20)
    ax.set_title(f"GMM for Submission Time and Runtime (PA{pa})", y=0.98)

    return fig

def create_model(df: pd.DataFrame, visualize=True, save_img=False,
                 out_file: pathlib.Path | None = None):
    hrs_since_first_sub, runtime_ms = extract_features(df)
    train_data = np.array((hrs_since_first_sub, runtime_ms)).T

    gmm = create_gmm(train_data, NUM_COMPONENTS)
    x = np.linspace(hrs_since_first_sub.min(), hrs_since_first_sub.max(), 100)
    y = np.linspace(runtime_ms.min(), runtime_ms.max(), 100)

    fig = create_fig(gmm, x, y)
    if visualize:
        plt.show()
    if save_img and out_file is not None:
        try:
            fig.savefig(out_file, format=out_file.suffix[1:], dpi=1200)
        except Exception as e:
            print(f"Failed to save gmm figure, {e}")
            sys.exit(1)

def main():
    global pa
    try:
        df = pd.read_csv(sys.argv[1])
        matches = re.match(PA_PATTERN, sys.argv[1])
        if matches:
            pa = matches.group(1)

        if len(sys.argv) == 3:
            out_file = pathlib.Path(sys.argv[2])
            create_model(
                df,
                visualize=False,
                save_img=True,
                out_file=out_file
            )
        else:
            create_model(df)
    except Exception as e:
        print(f"Failed to create model, {e}") 


if __name__ == '__main__':
    if len(sys.argv) not in (2, 3):
        print("Usage:")
        print("  python pa_gmm.py <pa_csv> <out_file>")
        print("    (out_file can be any image format)")
        print("  python pa_gmm.py <pa_csv>")
        print("    (use to view model w/out saving)")
        sys.exit(1)

    main()
