import sys
import os
import json
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.abspath("../optimizer/"))
import utils


##########################################################
################## GENERAL UTILITIES #####################

def get_lam_and_cv_from_fname(arrival_process_fname):

    lam_and_cv = arrival_process_fname.rstrip(".deltas")
    if "_" in lam_and_cv:
        lam = int(lam_and_cv.split("_")[0])
        cv = float(lam_and_cv.split("_")[1])
    else:
        cv = 1.0
        lam = int(lam_and_cv)
    return (lam, cv)





##########################################################
################# TENSORFLOW SERVING #####################

def compute_tfs_cost(results):
    node_configs = results["node_configs"]
    cost = 0
    for n in node_configs:
        # Divide by 2 to convert from virtual cpus to physical cpus
        num_cpus = n["cpus_per_replica"] / 2 * n["num_replicas"]
        num_gpus = n["gpus_per_replica"] * n["num_replicas"]
        if num_gpus > 0:
            if "p3" in n["instance_type"]:
                gpu_type = "v100"
            elif "p2" in n["instance_type"]:
                gpu_type = "k80"
        else:
            gpu_type = "none"
        cost += utils.get_cpu_cost("aws", num_cpus) + utils.get_gpu_cost("aws", gpu_type, num_gpus)
    return cost


def compute_tfs_slo_miss_rate(results, slo):
    # Skip the first trial
    lats = np.array(results["client_metrics"][0]["all_lats"][1:]).flatten()
    slo_miss_rate = np.sum(lats > slo) / len(lats)
    return slo_miss_rate


def load_tfs_results(path, slo):
    with open(path, "r") as f:
        results = json.load(f)

    arrival_process_fname = os.path.basename(results["arrival_process"]["filename"])
    lam, cv = get_lam_and_cv_from_fname(arrival_process_fname)
    cost = compute_tfs_cost(results)
    slo_miss_rate = compute_tfs_slo_miss_rate(results, slo)
    return {"cost": cost, "lambda": lam, "CV": cv, "slo_miss_rate": slo_miss_rate}

##########################################################
################ SINGLE PROCESS DRIVER ###################

def compute_spd_cost(results):
    node_configs = results["node_configs"]
    # All nodes use the same cpu set, so only get cpus once outside the for loop
    # Divide by 2 to convert from virtual cpus to physical cpus
    num_cpus = len(node_configs[0]["allocated_cpus"]) / 2
    cost = utils.get_cpu_cost("aws", num_cpus)
    for n in node_configs:
        num_gpus = len(n["gpus"])
        if num_gpus > 0:
            # SPD always uses V100s
            gpu_type = "v100"
        else:
            gpu_type = "none"
        cost += utils.get_gpu_cost("aws", gpu_type, num_gpus)
    return cost

def compute_spd_slo_miss_rate(results, slo):
    # Skip the first trial
    lats = np.array(results["client_metrics"][0]["all_lats"][1:])
    lats = np.hstack(lats)
    slo_miss_rate = np.sum(lats > slo) / len(lats)
    return slo_miss_rate

def load_spd_results(path, slo):
    with open(path, "r") as f:
        results = json.load(f)
    arrival_process_fname = os.path.basename(results["arrival_process"])
    lam, cv = get_lam_and_cv_from_fname(arrival_process_fname)
    cost = compute_spd_cost(results)
    slo_miss_rate = compute_spd_slo_miss_rate(results, slo)
    return {"cost": cost, "lambda": lam, "CV": cv, "slo_miss_rate": slo_miss_rate}

##########################################################
####################### INFERLINE ########################

def compute_inferline_cost(results):
    node_configs = results["node_configs"]
    cost = 0
    for n in node_configs:
        num_cpus = n["cpus_per_replica"] * n["num_replicas"]
        num_gpus = n["gpus_per_replica"] * n["num_replicas"]
        if num_gpus > 0:
            if "p3" in n["instance_type"]:
                gpu_type = "v100"
            elif "p2" in n["instance_type"]:
                gpu_type = "k80"
        else:
            gpu_type = "none"
        cloud = n["cloud"]
        cost += utils.get_cpu_cost(cloud, num_cpus) + utils.get_gpu_cost(cloud, gpu_type, num_gpus)
    return cost

def load_inferline_results(path, slo):
    with open(path, "r") as f:
        results = json.load(f)
    cv = 1.0
    base_name = os.path.basename(path)
    lam = int(base_name.split("_")[2])
    trials = results["throughput_results"]["client_metrics"][1:]
    lats = []
    for t in trials:
        datalists = t["data_lists"]
        for d in datalists:
            if list(d.keys())[0] == "e2e:prediction_latencies":
                items = d["e2e:prediction_latencies"]["items"]
                for i in items:
                    lats.append(float(list(i.values())[0]) / 1000.0 / 1000.0)
    lats = np.array(lats)
    slo_miss_rate = np.sum(lats > slo) / len(lats)
    cost = compute_inferline_cost(results)
    return {"cost": cost, "lambda": lam, "CV": cv, "slo_miss_rate": slo_miss_rate}


def load_e2e_experiments():
    experiments = [
        {"name": "TFS Peak", "slo": .35, "path": os.path.abspath("../TFS/image_driver_1/min_latency_arrival_process/v100-8xlarge/peak_350")},
        {"name": "TFS Mean", "slo": .35, "path": os.path.abspath("../TFS/image_driver_1/min_latency_arrival_process/v100-8xlarge/mean")},
        {"name": "TFS Mean", "slo": .50, "path": os.path.abspath("../TFS/image_driver_1/min_latency_arrival_process/v100-8xlarge/mean")},
        {"name": "TFS Peak", "slo": .50, "path": os.path.abspath("../TFS/image_driver_1/min_latency_arrival_process/v100-8xlarge/peak_500")},
        {"name": "SPD Mean Batching", "slo": .35, "path": os.path.abspath("../SPD/image_driver_1/v100-8xlarge/mean_provision_350/")},
        {"name": "SPD Peak Batching", "slo": .35, "path": os.path.abspath("../SPD/image_driver_1/v100-8xlarge/peak_provision_350/")},
        {"name": "SPD Mean No Batching", "slo": .35, "path": os.path.abspath("../SPD/image_driver_1/v100-8xlarge/mean_provision_min_lat/")},
        {"name": "SPD Peak No Batching", "slo": .35, "path": os.path.abspath("../SPD/image_driver_1/v100-8xlarge/peak_provision_min_lat/")},
        {"name": "Inferline", "slo": .35, "path": os.path.abspath("../results_cpp_benchmarker/e2e_results/image_driver_1/image_driver_one_slo_0.35_cv_1")},
        {"name": "Inferline", "slo": .50, "path": os.path.abspath("../results_cpp_benchmarker/e2e_results/image_driver_1/image_driver_one_slo_0.5_cv_1")},
        {"name": "Inferline", "slo": 1.0, "path": os.path.abspath("../results_cpp_benchmarker/e2e_results/image_driver_1/image_driver_one_slo_1.0_cv_1")}
    ]

    loaded_exps = []

    for exp in experiments:
        for f in os.listdir(exp["path"]):
            if f[-4:] == "json":
                try:
                    loaded = exp.copy()
                    if "TFS" in exp["name"]:
                        loaded.update(load_tfs_results(os.path.join(exp["path"], f), exp["slo"]))
                    elif "SPD" in exp["name"]:
                        loaded.update(load_spd_results(os.path.join(exp["path"], f), exp["slo"]))
                    elif "Inferline" in exp["name"]:
                        loaded.update(load_inferline_results(os.path.join(exp["path"], f), exp["slo"]))
                    loaded_exps.append(loaded)
                except json.JSONDecodeError:
                    print("Could not load {}".format(os.path.join(exp["path"], f)))
    df = pd.DataFrame(loaded_exps)
    return df

if __name__ == "__main__":
    print(load_e2e_experiments())
