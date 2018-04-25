import sys
import os
import json
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.abspath("../optimizer/"))
import utils
from plotting_utils import get_latencies_inferline_e2e


def load_run(path):
    """
    Loads a single result file and returns utilization and slo miss rate
    """
    with open(path, "r") as f:
        results = json.load(f)
    slo = results["loaded_config"]["slo"]
    lam = results["loaded_config"]["lam"]
    utilization = results["loaded_config"]["utilization"]
    lats = get_latencies_inferline_e2e(results) 
    slo_miss_rate = np.sum(lats > slo) / len(lats)
    return {"utilization": utilization, "slo_miss_rate": slo_miss_rate, "lam": lam}

def load_exp_slo_500(dir_path):
    """
    Assumes the weird directory structure of
        .../high_load/dir_path/exp_path.json
    where the directory at dir_path only has one file in it
    """

    results = []
    for d in os.listdir(dir_path):
        full_d_path = os.path.join(dir_path, d)
        for f in os.listdir(full_d_path):
            if f[-4:] == "json":
                results.append(load_run(os.path.join(full_d_path, f)))
    df = pd.DataFrame(results)
    df = df.sort_values("utilization")
    return df

def load_exp_slo_1000(dir_path):

    results = []
    for f in os.listdir(dir_path):
        if f[-4:] == "json":
            results.append(load_run(os.path.join(dir_path, f)))
    df = pd.DataFrame(results)
    df = df.sort_values("utilization")
    return df
    

def load_all(slo, pipeline):
    if pipeline == "pipeline_one":
        if slo == 0.5:
            high_load_df = load_exp_slo_500(os.path.abspath(
                "../results_cpp_benchmarker/e2e_results/image_driver_1/util_sweep/slo_0.5/high_load"))
            low_load_df = load_exp_slo_500(os.path.abspath(
                "../results_cpp_benchmarker/e2e_results/image_driver_1/util_sweep/slo_0.5/low_load"))
            return high_load_df, low_load_df
        if slo == 1.0:
            return load_exp_slo_1000(os.path.abspath(
                "../results_cpp_benchmarker/e2e_results/image_driver_1/util_sweep/"
                "slo_1.0/util-sweep-image_driver_one_slo_1.0_cost_10.6"))
    if pipeline == "pipeline_three":
        if slo == 1.0:
            return load_exp_slo_1000(os.path.abspath(
                "../results_cpp_benchmarker/e2e_results/resnet_cascade/util_sweep/slo_1.0/"
                # "resnet-cascade_utilization_sweep_slo_1.0_cv_1.0"))
                "resnet-cascade_utilization_sweep_with_prune_slo_1.0_cv_1.0"))

