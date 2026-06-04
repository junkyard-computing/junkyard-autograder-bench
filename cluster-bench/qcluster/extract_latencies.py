import os
import re
import glob
import pandas as pd
import argparse

def calculate_average_latencies(directory_path):
    data = []

    # Regex patterns
    re_gs_start = re.compile(r'Timestamp \(gradescope start\):\s*(\d+)')
    re_gq_start = re.compile(r'Timestamp \(gradescope -> qcluster, start\):\s*(\d+)')
    re_gq_end = re.compile(r'Timestamp \(gradescope -> qcluster, end\):\s*(\d+)')
    re_pod_created = re.compile(r'Timestamp \(job .*, node ([\w-]+): pod created\):\s*(\d+)')
    re_out_ret = re.compile(r'Timestamp \(job .*, node [\w-]+: output returned, (\w+)\):\s*(\d+)')

    file_paths = glob.glob(os.path.join(directory_path, '*.txt'))

    if not file_paths:
        print(f"Error: No .txt files found in directory '{directory_path}'")
        return

    for file_path in file_paths:
        with open(file_path, 'r') as f:
            content = f.read()

        gs_start_match = re_gs_start.search(content)
        gq_start_match = re_gq_start.search(content)
        gq_end_match = re_gq_end.search(content)
        pod_created_match = re_pod_created.search(content)
        out_ret_match = re_out_ret.search(content)

        if not all([gs_start_match, gq_start_match, gq_end_match, pod_created_match, out_ret_match]):
            continue

        qs_start = int(gs_start_match.group(1))
        gq_start = int(gq_start_match.group(1))
        gq_end = int(gq_end_match.group(1))
        node = pod_created_match.group(1)
        pod_created = int(pod_created_match.group(2))
        out_ret = int(out_ret_match.group(2))

        data.append({
            'Node': node,
            'gradescope duration': gq_start - qs_start,
            'qcluster duration': gq_end - gq_start,
            'waiting t.b. scheduled': pod_created - gq_end,
            'pod duration': out_ret - pod_created,
            'pod created': pod_created,
            'out_ret': out_ret,
            'gq_end': gq_end
        })

    if not data:
        print("No valid data found to calculate averages.")
        return

    df = pd.DataFrame(data)
    df = df.sort_values(['Node', 'pod created']).reset_index(drop=True)

    # Calculate average spinup latency using FIRST JOB on each node
    first_jobs_idx = df.groupby('Node')['pod created'].idxmin()
    first_jobs = df.loc[first_jobs_idx]
    avg_spinup_latency = (first_jobs['pod created'] - first_jobs['gq_end']).mean()

    # Calculate node available latencies dynamically
    node_avail_latencies = []

    for node in df['Node'].unique():
        node_indices = df[df['Node'] == node].index
        for i in range(len(node_indices) - 1):
            curr_idx = node_indices[i]
            next_idx = node_indices[i + 1]

            # Gap: Next job's start minus current job's end
            gap = df.loc[next_idx, 'pod created'] - df.loc[curr_idx, 'out_ret']

            # Subtract baseline spinup latency, cap at 0
            avail_time = max(0, gap - avg_spinup_latency)
            node_avail_latencies.append(avail_time)

    # Generate Report
    print("========================================")
    print(f"BENCHMARK AVERAGES (Processed {len(df)} jobs)")
    print("========================================")
    print(f"Gradescope Duration:      {df['gradescope duration'].mean():,.2f} ns")
    print(f"QCluster Duration:        {df['qcluster duration'].mean():,.2f} ns")
    print(f"Waiting for Scheduler:    {df['waiting t.b. scheduled'].mean():,.2f} ns")
    print(f"Pod Execution Duration:   {df['pod duration'].mean():,.2f} ns")

    if node_avail_latencies:
        avg_node_avail = sum(node_avail_latencies) / len(node_avail_latencies)
        print(f"Node Available Latency:   {avg_node_avail:,.2f} ns")
        print(f" *(Derived using first-job spinup baseline of {avg_spinup_latency:,.2f} ns)*")
    else:
        print("Node Available Latency:   N/A (Not enough sequential jobs on the same node to calculate gaps)")
    print("========================================")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calculate average latencies from benchmark logs.")
    parser.add_argument("directory", help="Path to the directory containing the benchmark .txt files")
    args = parser.parse_args()

    calculate_average_latencies(args.directory)
