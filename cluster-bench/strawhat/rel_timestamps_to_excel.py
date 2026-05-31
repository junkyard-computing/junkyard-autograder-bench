import os
import re
import glob
import pandas as pd
import argparse

def process_benchmark_timestamps(directory_path, output_excel):
    data = []

    # Regex patterns
    re_gs_start = re.compile(r'Timestamp \(gradescope start\):\s*(\d+)')
    re_gy_start = re.compile(r'Timestamp \(gradescope -> junkyard, start\):\s*(\d+)')
    re_gy_end = re.compile(r'Timestamp \(gradescope -> junkyard, end\):\s*(\d+)')
    re_pod_created = re.compile(r'Timestamp \(job .*, node ([\w-]+): pod created\):\s*(\d+)')
    re_out_ret = re.compile(r'Timestamp \(job .*, node [\w-]+: output returned, (\w+)\):\s*(\d+)')

    file_paths = glob.glob(os.path.join(directory_path, '*.txt'))

    if not file_paths:
        print(f"Error: No .txt files found in directory '{directory_path}'")
        return

    for file_path in file_paths:
        filename = os.path.basename(file_path)

        m_file = re.match(r'(\d+)_(\d+)\.txt', filename)
        if not m_file:
            print(f"Warning: Skipping '{filename}' because the filename doesn't match the expected format (e.g., 1000000_1.txt)")
            continue

        duration_us = int(m_file.group(1))
        iteration = int(m_file.group(2))
        job_name = f"{duration_us // 1000000}s_{iteration}"

        with open(file_path, 'r') as f:
            content = f.read()

        # Extract
        gs_start_match = re_gs_start.search(content)
        gy_start_match = re_gy_start.search(content)
        gy_end_match = re_gy_end.search(content)
        pod_created_match = re_pod_created.search(content)
        out_ret_match = re_out_ret.search(content)

        # DEBUGGING CHECK: Find exactly what is missing
        if not all([gs_start_match, gy_start_match, gy_end_match, pod_created_match, out_ret_match]):
            missing = []
            if not gs_start_match: missing.append("gradescope start")
            if not gy_start_match: missing.append("junkyard start")
            if not gy_end_match: missing.append("junkyard end")
            if not pod_created_match: missing.append("pod created")
            if not out_ret_match: missing.append("output returned")

            print(f"Warning: Skipping '{filename}'. Missing timestamps for: {', '.join(missing)}")
            continue

        # Parse
        gs_start = int(gs_start_match.group(1))
        gy_start = int(gy_start_match.group(1))
        gy_end = int(gy_end_match.group(1))
        node = pod_created_match.group(1)
        pod_created = int(pod_created_match.group(2))
        status = out_ret_match.group(1).capitalize()
        out_ret = int(out_ret_match.group(2))

        # Calculations
        gradescope_duration = gy_start - gs_start
        junkyard_duration = gy_end - gy_start
        waiting_tb_scheduled = pod_created - gy_end
        pod_duration = out_ret - pod_created

        data.append({
            'Node': node,
            'Job': job_name,
            'duration_sec': duration_us // 1000000,
            'iteration': iteration,
            'gradescope start': gs_start,
            'gradescope duration': gradescope_duration,
            'junkyard duration': junkyard_duration,
            'waiting t.b. scheduled duration': waiting_tb_scheduled,
            'pod duration': pod_duration,
            'Status': status,
            'pod created': pod_created,
            'out_ret': out_ret,
            'gy_end': gy_end  # Added to calculate baseline
        })

    if not data:
        print("No valid data found.")
        return

    df = pd.DataFrame(data)

    # Sort primarily by node, then by pod creation time to establish chronological order
    df = df.sort_values(['Node', 'pod created']).reset_index(drop=True)

    # Calculate average spinup latency using the FIRST JOB on each node
    first_jobs_idx = df.groupby('Node')['pod created'].idxmin()
    first_jobs = df.loc[first_jobs_idx]

    # Baseline spinup = (Pod Created) - (Junkyard End) for the very first job on a node
    avg_spinup_latency = (first_jobs['pod created'] - first_jobs['gy_end']).mean()
    print(f"\nCalculated Average Spinup Latency (First-Job Baseline): {avg_spinup_latency:.2f} ns")

    # Calculate "node waiting for availability"
    df['node waiting for availability'] = 0.0

    for node in df['Node'].unique():
        node_indices = df[df['Node'] == node].index

        for i in range(len(node_indices) - 1):
            curr_idx = node_indices[i]
            next_idx = node_indices[i + 1]

            # Gap: Next job's start minus current job's end
            gap = df.loc[next_idx, 'pod created'] - df.loc[curr_idx, 'out_ret']

            # Subtract baseline spinup latency, cap at 0 to prevent negative chart rendering
            avail_time = max(0, gap - avg_spinup_latency)
            df.loc[curr_idx, 'node waiting for availability'] = avail_time

        # Handle the last job on the node (use the node's mean to prevent an empty bar)
        if len(node_indices) > 1:
            node_mean = df.loc[node_indices[:-1], 'node waiting for availability'].mean()
            df.loc[node_indices[-1], 'node waiting for availability'] = node_mean
        else:
            df.loc[node_indices[-1], 'node waiting for availability'] = 0

    final_df = pd.DataFrame()
    # Mask repeating Nodes so the chart labels don't get cluttered
    final_df['Node'] = df['Node'].mask(df['Node'] == df['Node'].shift(), "")
    final_df['Job'] = df['Job']
    final_df['gradescope start'] = df['gradescope start']
    final_df['gradescope duration'] = df['gradescope duration']
    final_df['junkyard duration'] = df['junkyard duration']
    final_df['waiting t.b. scheduled duration'] = df['waiting t.b. scheduled duration']
    final_df['pod duration'] = df['pod duration']

    row_indices = range(2, 2 + len(df))
    final_df['pod success duration'] = [f'=IF(J{i}="Success", G{i}, NA())' for i in row_indices]
    final_df['pod failure duration'] = [f'=IF(J{i}="Failure", G{i}, NA())' for i in row_indices]
    final_df['Status'] = df['Status']
    final_df['node waiting for availability'] = df['node waiting for availability']

    # --- Excel and Chart Generation using XlsxWriter ---
    with pd.ExcelWriter(output_excel, engine='xlsxwriter') as writer:
        final_df.to_excel(writer, index=False, sheet_name='Timestamps')

        workbook = writer.book
        worksheet = writer.sheets['Timestamps']

        chart = workbook.add_chart({'type': 'bar', 'subtype': 'stacked'})
        max_row = len(final_df)

        # Series 0: Invisible Offset
        chart.add_series({
            'name':       ['Timestamps', 0, 2],
            'categories': ['Timestamps', 1, 0, max_row, 1], 
            'values':     ['Timestamps', 1, 2, max_row, 2],
            'fill':       {'none': True},
            'border':     {'none': True},
        })

        template_colors = {
            3: '#C0504D',  # gradescope duration
            4: '#9BBB59',  # junkyard duration
            5: '#8064A2',  # waiting t.b. scheduled
            7: '#4BACC6',  # pod success duration
            8: '#F79646',  # pod failure duration
            10: '#1F497D'  # node waiting for availability
        }

        visible_columns = [3, 4, 5, 7, 8, 10]

        for col in visible_columns:
            chart.add_series({
                'name':       ['Timestamps', 0, col],
                'categories': ['Timestamps', 1, 0, max_row, 1],
                'values':     ['Timestamps', 1, col, max_row, col],
                'fill':       {'color': template_colors[col]}, 
                'gap':        50,
            })

        unique_nodes = len(df['Node'].unique())

        chart.set_title({
            'name': f'{len(final_df)} jobs running across {unique_nodes} nodes',
            'name_font': {'size': 20}
        })

        chart.set_x_axis({
            'name': 'Time (nanoseconds)', 
            'major_gridlines': {'visible': True},
            'name_font': {'size': 20},
            'num_font':  {'size': 18}
        })

        chart.set_y_axis({
            'name': 'Node',
            'reverse': True,
            'name_font': {'size': 20},
            'num_font':  {'size': 14},
            'interval_unit': 1
        })

        dynamic_height = max(900, len(final_df) * 25)

        chart.set_size({'width': 1600, 'height': dynamic_height})

        chart.set_legend({
            'position': 'bottom',
            'delete_series': [0],
            'font': {'size': 16}
        })

        worksheet.insert_chart('M2', chart)

    print(f"\nSuccessfully processed {len(final_df)} files.")
    print(f"Data and Chart exported to {output_excel}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process benchmark timestamps and generate a Gantt chart.")
    parser.add_argument("directory", help="Path to the directory containing the benchmark .txt files")
    parser.add_argument("-o", "--output", default="benchmark_results.xlsx", help="Output Excel file name")
    args = parser.parse_args()

    process_benchmark_timestamps(args.directory, args.output)
