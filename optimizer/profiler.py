import numpy as np
import single_node_profiles_cpp as snp
import networkx as nx
import copy
import math
from matplotlib import pyplot as plt


class LogicalDAG(object):

    SOURCE = "SOURCE"
    SINK = "SINK"

    def __init__(self, adj_list, reference_node):
        """
        Parameters:
        -----------
        adj_list : dict
            The DAG as represented by an adjacency list. Every DAG is required
            to have two special nodes, SOURCE and SINK. SOURCE must have at least
            one outgoing edge and no incoming edges. SINK must have at least one incoming
            edge and no outgoing edges. There must be a path from the SOURCE to every node
            in the graph and a path from every node in the graph to the SINK.
        reference_node : str
            Can be any node in the graph that all queries are sent to. This is used
            to calculate the scale factor of the rest of the nodes in the graph.

        """
        assert len(adj_list[LogicalDAG.SOURCE]) > 0 and len(adj_list[LogicalDAG.SINK]) == 0
        self.adj_list = adj_list
        graph = nx.DiGraph()
        for parent in adj_list:
            for child in adj_list[parent]:
                graph.add_edge(parent, child)
        self.nx_graph = graph
        self.reference_node = reference_node

    def get_nx_graph(self):
        return self.nx_graph

    def enumerate_paths(self):
        """
        Returns:
        --------
        Every unique path through the DAG.
        """

        paths = []

        def search(current_path, node):
            current_path.append(node)
            # Base case
            if node == LogicalDAG.SINK:
                paths.append(tuple(current_path))
            else:
                for next_node in self.adj_list[node]:
                    # We slice the list to copy it so each path gets its own copy
                    search(current_path[:], next_node)

        search([], LogicalDAG.SOURCE)
        assert(len(paths) >= 1)
        return paths

    def nodes(self):
        nodes = list(self.adj_list.keys())
        nodes.remove(LogicalDAG.SOURCE)
        nodes.remove(LogicalDAG.SINK)
        return nodes


class NodeConfig(object):

    def __init__(self, name, num_cpus, gpu_type, batch_size, num_replicas, cloud):
        """
        num_cpus : int
            The number of virtual cpus allocated to this node
        gpu_type : str
            Which type of GPU this node is using. Can be "none", "p100", "k80", "v100".
        batch_size : float
            The batch size for the node
        num_replicas : int
            The number of replicas of the node
        cloud : str
            The cloud service that was used. Can be either "gcp" or "aws".
        """
        self.name = name
        self.num_cpus = num_cpus
        self.gpu_type = gpu_type
        self.batch_size = batch_size
        self.num_replicas = num_replicas
        self.cloud = cloud

    def __repr__(self):
        return ("NodeConfig({name}, {num_cpus}, {gpu_type}, {batch_size}, {num_replicas}, "
                "{cloud})").format(
                    name=self.name,
                    num_cpus=self.num_cpus,
                    gpu_type=self.gpu_type,
                    batch_size=self.batch_size,
                    num_replicas=self.num_replicas,
                    cloud=self.cloud
        )


class NodeProfile(object):

    def __init__(self, name, profile, throughput_stage):
        """
        Parameters:
        -----------
        name : str
            The name of the node
        profile : DataFrame
            A dataframe containing the profile for this node. Each row in the dataframe
            represents the performance of the node under a single configuration. The profile
            should be generated by single_node_profiles.create_node_profile_df.
        """
        self.name = name
        self.throughput_stage = throughput_stage
        self.throughput_field = self.throughput_stage + "_mean_throughput_qps"
        self.profile = profile
        self.prune_profile()

    def enumerate_configs(self, max_replication_factor=1):
        """
        Enumerates all of the configurations under which this node was profiled.
        Intended for the optimizer to search over.

        Returns
        -------
        list(NodeConfig)
        """
        configs = []
        for i in range(1, max_replication_factor + 1):
            for entry in self.profile.itertuples():
                configs.append(NodeConfig(self.name,
                                          entry.num_cpus_per_replica,
                                          entry.gpu_type,
                                          entry.mean_batch_size,
                                          i,
                                          entry.cloud))
        return configs

    def check_monotonicity(self):

        resource_bundle_groups = self.profile.groupby(["cloud",
                                                       "gpu_type",
                                                       "num_cpus_per_replica"])
        for bundle, df in resource_bundle_groups:
            sorted_df = df.sort_values("mean_batch_size")
            if not np.all(np.diff(sorted_df[self.throughput_field]) >= 0):
                print("Profile for node {name} bundle {bundle} is non-monotonic".format(
                    name=self.name, bundle=bundle))

    def plot_profile(self):
        resource_bundle_groups = self.profile.groupby(["cloud",
                                                       "gpu_type",
                                                       "num_cpus_per_replica"])
        for bundle, df in resource_bundle_groups:
            title = "_".join([str(b) for b in bundle])
            sorted_df = df.sort_values("mean_batch_size")
            fig, (ax_thru, ax_lat) = plt.subplots(nrows=1, ncols=2, figsize=(14, 5))
            fig.suptitle(title)
            ax_thru.plot(sorted_df["mean_batch_size"],
                         sorted_df[self.throughput_field],
                         color="blue")
            ax_lat.plot(sorted_df["mean_batch_size"],
                        sorted_df["p99_latency"],
                        color="blue")
            non_monotonic_points_idx = (np.diff(sorted_df[self.throughput_field]) < 0)
            non_monotonic_points_idx = np.insert(non_monotonic_points_idx, 0, False)
            non_monotonic_points = sorted_df.loc[non_monotonic_points_idx]
            ax_thru.scatter(non_monotonic_points["mean_batch_size"],
                            non_monotonic_points[self.throughput_field],
                            color="red", s=60)
            ax_lat.scatter(non_monotonic_points["mean_batch_size"],
                           non_monotonic_points["p99_latency"],
                           color="red", s=60)
            ax_thru.set_xlabel("batch size")
            ax_thru.set_ylabel("mean throughput")
            ax_lat.set_xlabel("batch size")
            ax_lat.set_ylabel("p99 latency (s)")
            ax_lat.set_ylim(bottom=0)
            ax_lat.set_xlim(left=0)
            ax_thru.set_ylim(bottom=0)
            ax_thru.set_xlim(left=0)
        plt.show()

    def increase_batch_size(self, config):
        """
        Returns a config with a larger batch size or False if the batch size
        could not be increased.

        Parameters
        ----------
        config : NodeConfig
            The current config

        """
        resource_bundle_matches = self.profile[
            (self.profile.gpu_type == config.gpu_type)
            & (self.profile.num_cpus_per_replica == config.num_cpus)
            & (self.profile.cloud == config.cloud)]
        resource_bundle_matches = resource_bundle_matches.sort_values("mean_batch_size")
        lub = resource_bundle_matches['mean_batch_size'] >= math.floor(config.batch_size) + 1
        if sum(lub) == 0:
            return False
        idx_lub = resource_bundle_matches.loc[resource_bundle_matches.index[lub],
                                              'mean_batch_size'].idxmin()
        relevant_entry = resource_bundle_matches.loc[idx_lub]
        new_config = copy.deepcopy(config)
        new_config.batch_size = relevant_entry["mean_batch_size"]
        return new_config

    def estimate_performance(self, config):
        """
        Estimates the node's performance under the specified configuration.

        Parameters:
        -----------

        Returns:
        --------
        tuple : (p99_latency, throughput, cost)
            Returns estimated latency, throughput, and cost for this configuration.
            If there is not an exact batch size match, the profiler will perform linear
            interpolation.

        Raises:
        -------
        A RuntimeException will be raised if the node has not been profiled under the
        requested configuration.
        """
        resource_bundle_matches = self.profile[
            (self.profile.gpu_type == config.gpu_type)
            & (self.profile.num_cpus_per_replica == config.num_cpus)
            & (self.profile.cloud == config.cloud)]
        resource_bundle_matches = resource_bundle_matches.sort_values("mean_batch_size")
        if len(resource_bundle_matches) == 0:
            raise Exception("No profiles for node under provided configuration: {}".format(
                config))
        glb = resource_bundle_matches['mean_batch_size'] <= config.batch_size
        lub = resource_bundle_matches['mean_batch_size'] >= config.batch_size
        # We take the sum here instead of the length because glb and lub are boolean
        # arrays and we want to know whether there is at least one True entry in the array
        if sum(glb) > 0:
            idx_glb = resource_bundle_matches.loc[resource_bundle_matches.index[glb],
                                                  'mean_batch_size'].idxmax()
        else:
            idx_glb = None
        if sum(lub) > 0:
            idx_lub = resource_bundle_matches.loc[resource_bundle_matches.index[lub],
                                                  'mean_batch_size'].idxmin()
        else:
            idx_lub = None
        if idx_glb is None or idx_lub is None:
            if idx_glb is None:
                assert idx_lub is not None
                relevant_entry = resource_bundle_matches.loc[idx_lub]
            else:
                assert idx_glb is not None
                print("Warning: No profiles found with higher batch size than {}".format(config))
                relevant_entry = resource_bundle_matches.loc[idx_glb]
            estimated_thruput = relevant_entry[self.throughput_field] * config.num_replicas
            estimated_latency = relevant_entry["p99_latency"]
            cost = relevant_entry["cost"] * config.num_replicas
        else:
            relevant_entries = resource_bundle_matches.loc[idx_glb:idx_lub]
            assert np.all(np.diff(relevant_entries[self.throughput_field]) > 0)
            estimated_thruput = np.interp(config.batch_size,
                                          relevant_entries["mean_batch_size"],
                                          relevant_entries[self.throughput_field])
            estimated_thruput = estimated_thruput * config.num_replicas

            assert np.all(np.diff(relevant_entries["p99_latency"]) > 0)
            estimated_latency = np.interp(config.batch_size,
                                          relevant_entries["mean_batch_size"],
                                          relevant_entries["p99_latency"])
            # The cost for all the entries with the same resource bundle is the same,
            # so we just get it from the first entry
            cost = relevant_entries["cost"].iloc[0] * config.num_replicas
        return (estimated_latency, estimated_thruput, cost)

    def prune_profile(self):
        """
        Removes configurations from a single-node profile that are guaranteed
        to be sub-optimal.
        """
        pruned = self.profile.copy(deep=True)

        # Iterate over the original dataframe but filter the copy
        for index, row in self.profile.iterrows():
            # Keep any configs that have higher throughput, lower cost, or lower latency
            pruned = pruned[(pruned[self.throughput_field] >= row[self.throughput_field])
                            | (pruned.cost <= row["cost"])
                            | (pruned.p99_latency <= row["p99_latency"])]
        self.profile = pruned


def get_logical_pipeline(pipeline_name):
    # paths = [("tf-resnet-feats", "tf-kernel-svm"),
    #          ("inception", "tf-log-reg")]
    # root_node = "inception"
    if pipeline_name == "pipeline_one":
        adj_list = {
            LogicalDAG.SOURCE: ["tf-resnet-feats", "inception"],
            "tf-resnet-feats": ["tf-kernel-svm", ],
            "tf-kernel-svm": [LogicalDAG.SINK],
            "inception": ["tf-log-reg", ],
            "tf-log-reg": [LogicalDAG.SINK],
            LogicalDAG.SINK: []
        }
        return LogicalDAG(adj_list, "inception")

    if pipeline_name == "pipeline_two":
        # paths = [("tf-lang-detect",),
        #          ("tf-lang-detect", "tf-lstm"),
        #          ("tf-lang-detect", "tf-nmt", "tf-lstm")]
        # root_node = "tf-lang-detect"
        adj_list = {
            LogicalDAG.SOURCE: ["tf-lang-detect", ],
            "tf-lang-detect": ["tf-lstm", "tf-nmt", LogicalDAG.SINK],
            "tf-nmt": ["tf-lstm", ],
            "tf-lstm": [LogicalDAG.SINK, ],
            LogicalDAG.SINK: []
        }
        return LogicalDAG(adj_list, "tf-lang-detect")

    # Resnet Cascade
    elif pipeline_name == "pipeline_three":
        adj_list = {
            LogicalDAG.SOURCE: ["alexnet", ],
            "alexnet": ["res50", LogicalDAG.SINK],
            "res50": ["res152", LogicalDAG.SINK],
            "res152": [LogicalDAG.SINK],
            LogicalDAG.SINK: []
        }
        return LogicalDAG(adj_list, "alexnet")


def get_node_scale_factors(exp, reference_node):
    """
    Parameters
    ----------
    exp : dict
        A dict containing the results of an end-to-end experiment. The node
        scale_factor should be the same under any pipeline configuration, so the experiment
        JSON can be from any run and only needs to be computed once per logical
        pipeline.
    reference_node : str
        The name of a node in the DAG that all queries are sent to.
        This is a workaround to get the total number of queries sent because
        we don't currently record that (though we should).

    Returns
    -------
    dict :
        A dict where the keys are the node names and the values are the scale factors
    """
    for client in exp["client_metrics"]:
        if "all_metrics" in client:
            clipper_metrics = client["all_metrics"]
            break
    counts = {}
    node_names = [n["name"] for n in exp["node_configs"]]
    if reference_node not in node_names:
        print("reference node not in pipeline config")
    for m in node_names:
        counts[m] = 0.0

    for trial in clipper_metrics:
        counters = trial["counters"]
        count_key = "model:{name}:1:num_predictions"
        for c in counters:
            for m in node_names:
                if list(c.keys())[0] == count_key.format(name=m):
                    counts[m] += float(c[count_key.format(name=m)]["count"])

    total_count = counts[reference_node]
    for m in counts:
        counts[m] = counts[m] / total_count

    return counts


def estimate_pipeline_performance_for_config(dag,
                                             scale_factors,
                                             node_configs,
                                             single_node_profiles):
    """
    Estimate the end to end performance for a pipeline under a
    specific configuration.

    dag : LogicalDAG
        The logical pipeline structure
    scale_factors : dict
        A dict with the scale factors for each node in the pipeline
    node_configs : dict(str, NodeConfig)
        A dict with the physical configurations for each node in the pipeline.
    single_node_profiles : dict (str, NodeProfile)
        A dict with the profiles for each node in the pipeline.

    Returns:
    --------
    tuple(dict, str) :
        Returns the estimated performance and the name of the bottleneck node.
        Estimated performance is a dict of estimated latency, throughput, and cost for the pipeline
        under the specified configuration (and workload via the scale factors).
    """
    paths = dag.enumerate_paths()
    bottleneck_thruput = None
    bottleneck_node = None
    total_cost = 0.0
    max_latency = None
    for path in paths:
        path_latency = 0
        for node in path:
            # The source and sink nodes don't contribute to perf so
            # we skip them
            if node == LogicalDAG.SOURCE or node == LogicalDAG.SINK:
                continue
            prof = single_node_profiles[node]
            conf = node_configs[node]
            lat, thru, cost = prof.estimate_performance(conf)
            scaled_thru = thru / scale_factors[node]
            path_latency += lat
            if bottleneck_thruput is None:
                bottleneck_thruput = scaled_thru
                bottleneck_node = node
            # bottleneck_thruput = min(bottleneck_thruput, scaled_thru)
            if bottleneck_thruput > scaled_thru:
                bottleneck_thruput = scaled_thru
                bottleneck_node = node

            total_cost += cost
        # Update latency at the end of the path
        if max_latency is None:
            max_latency = path_latency
        max_latency = max(max_latency, path_latency)
    return ({
        "latency": max_latency,
        "throughput": bottleneck_thruput,
        "cost": total_cost
    }, bottleneck_node)


def get_node_configs_from_experiment(exp):
    """
    Extract the physical node configs from an end-to-end pipeline run.

    Parameters
    ----------
    exp : dict
        A dict containing the results of an end-to-end experiment.

    Returns
    -------
    dict(str, NodeConfig)
        A dict of NodeConfig objects that can be used by the optimizer to
        estimate the end to end performance for this configuration of the pipeline.
    """

    raw_configs = exp["node_configs"]
    node_configs = {}

    for node in raw_configs:
        name = node["name"]
        num_replicas = node["num_replicas"]
        num_cpus = node["cpus_per_replica"]
        batch_size = node["batch_size"]
        cloud, gpu_type = snp.get_gpu_type(node)
        node_configs[name] = NodeConfig(name,
                                        num_cpus,
                                        gpu_type,
                                        batch_size,
                                        num_replicas,
                                        cloud)

    return node_configs


def is_valid_pipeline_config(node_configs):
    """
    Currently the only condition for a configuration to be valid is that the all nodes are run
    in the same cloud.

    Returns
    -------
    bool :
        True if valid, else False.
    """
    clouds = iter([c.cloud for n, c in node_configs.items()])
    first = next(clouds)
    return all(first == rest for rest in clouds)
