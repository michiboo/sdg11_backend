from flask import Flask, jsonify
from flask_cors import CORS
from collections import OrderedDict
from shapely import geometry
from cityseer.metrics import networks
from cityseer.tools import graphs, io
import matplotlib.pyplot as plt
from cityseer.tools import plot
import utm
import base64
import osmnx as ox
import pandana
from celery import Celery


app = Flask(__name__)
CORS(app)
#Configure the redis server
app.config['CELERY_BROKER_URL'] = 'pyamqp://guest@localhost//'
app.config['CELERY_RESULT_BACKEND'] = 'rpc://guest@localhost//'
#creates a Celery object
celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)

@celery.task
def calculate_centrality(lng, lat):
    lng, lat = float(lng), float(lat)
    buffer = 5000
    plot_buffer = 3500
    # creates a WGS shapely polygon
    poly_wgs, poly_utm, _utm_zone_number, _utm_zone_letter = io.buffered_point_poly(lng, lat, buffer)
    plot_bbox = poly_utm.centroid.buffer(plot_buffer).bounds

    # generate OSM graph from polygon
    G_utm = io.osm_graph_from_poly(poly_wgs, simplify=True, remove_parallel=True, iron_edges=False)
    # decompose for higher resolution analysis
    G_decomp = graphs.nx_decompose(G_utm, 25)
    # prepare the data structures
    nodes_gdf, network_structure = graphs.network_structure_from_nx(G_decomp, crs=32629)

    # this will take around 5-8 minutes depending on the available resources
    # if you want to compute wider area centralities, e.g. 20km, then use less decomposition to speed up the computation
    nodes_gdf = networks.node_centrality(
        measures=["node_beta", "node_betweenness"],
        network_structure=network_structure,
        nodes_gdf=nodes_gdf,
        distances=[50],
    )
    bg_colour = "#111"
    d = 50
    b = networks.beta_from_distance(d)[0]
    avg_d = networks.avg_distance_for_beta(float(b))[0]
    print(
        f"""
    "Gravity" index / spatial impedance weighted (closeness-like) centrality:
    Avg walking tolerance: {avg_d:.2f}m
    Beta: {b:.3f} (spatial impedance factor)
    Max walking tolerance: {d:.1f}m
    """
    )
    fig, ax = plt.subplots(1, 1, figsize=(5, 5), dpi=200, facecolor=bg_colour)
    plot.plot_scatter(
        ax,
        network_structure.nodes.xs,
        network_structure.nodes.ys,
        nodes_gdf[f"cc_metric_node_beta_{d}"],
        bbox_extents=plot_bbox,
        cmap_key="magma",
        face_colour=bg_colour,
    )
    plt.savefig('./graph/tmp.png', bbox_inches='tight')
    with open('./graph/centrality_stat.txt', "r+") as f:
        f.seek(0)
        f.write(f"{float(avg_d)},{float(b)}, {float(d)}")
        f.truncate()

@app.route("/networkcentrality/<lng>/<lat>")
def networkCentrality(lng, lat):
    task = calculate_centrality.apply_async(args=[lng, lat])
    response = {"id": task.id}
    return jsonify(response)


@app.route("/getcentrality/<task_id>")
def getCentrality(task_id):
    task = calculate_walkability.AsyncResult(task_id).state
    if task == 'SUCCESS':
        with open("./graph/tmp.png", "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()) #
            f = open("./graph/centrality_stat.txt", "r")
            stat = f.readline().split(',') 
            f.close()
            response = {"image": encoded_string.decode('utf-8'), 'stat': [stat[0],stat[1], stat[2]]}
            return jsonify(response)
    else:
        response = {"status": task}
        return jsonify(response)
 


@celery.task
def calculate_walkability(lng, lat):
    lng, lat = float(lng), float(lat)
    buffer_dist = 5000
    crs = 3035
    graph = ox.graph_from_point((lat, lng), dist=buffer_dist, simplify=False, network_type="walk")

    # Project graph
    graph = ox.projection.project_graph(graph, to_crs=crs)

    # Select points of interest based on osm tags
    tags = {
        'amenity':[
            'cafe',
            'bar',
            'pub',
            'restaurant'
        ],
        'shop':[
            'bakery',
            'convenience',
            'supermarket',
            'mall',
            'department_store',
            'clothes',
            'fashion',
            'shoes'
        ],
        'leisure':[
            'fitness_centre'
        ]
    }

    # Get amentities from place
    pois = ox.geometries.geometries_from_point((lat, lng), dist=buffer_dist, tags=tags)
        
    # Project pois
    pois = pois.to_crs(epsg=crs)
    # Max time to walk in minutes (no routing to nodes further than this)
    walk_time = 15

    # Walking speed
    walk_speed = 4.5

    # Set a uniform walking speed on every edge
    for u, v, data in graph.edges(data=True):
        data['speed_kph'] = walk_speed
    graph = ox.add_edge_travel_times(graph)

    # Extract node/edge GeoDataFrames, retaining only necessary columns (for pandana)
    nodes = ox.graph_to_gdfs(graph, edges=False)[['x', 'y']]
    edges = ox.graph_to_gdfs(graph, nodes=False).reset_index()[['u', 'v', 'travel_time']]
    # Construct the pandana network model
    network = pandana.Network(
        node_x=nodes['x'],
        node_y=nodes['y'], 
        edge_from=edges['u'],
        edge_to=edges['v'],
        edge_weights=edges[['travel_time']]
    )

    # Extract centroids from the pois' geometries
    centroids = pois.centroid
    # Specify a max travel distance for analysis
    # Minutes -> seconds
    maxdist = walk_time * 60

    # Set the pois' locations on the network
    network.set_pois(
        category='pois',
        maxdist=maxdist,
        maxitems=10,
        x_col=centroids.x, 
        y_col=centroids.y
    )
    # calculate travel time to 10 nearest pois from each node in network
    distances = network.nearest_pois(
        distance=maxdist,
        category='pois',
        num_pois=10
    )

    distances.astype(int).head()


    # Set text parameters
    COLOR = 'white'
    plt.rcParams['text.color'] = COLOR
    plt.rcParams['axes.labelcolor'] = COLOR
    plt.rcParams['xtick.color'] = COLOR
    plt.rcParams['ytick.color'] = COLOR

    # Setup plot
    fig, ax = plt.subplots(figsize=(20,15))
    ax.set_axis_off()
    ax.set_aspect('equal')
    fig.set_facecolor((0,0,0))

    # Plot distance to nearest POI
    sc = ax.scatter(
        x=nodes['x'],
        y=nodes['y'], 
        c=distances[1],
        s=1,
        cmap='viridis_r',
    )

    # Colorbar
    cb = fig.colorbar(sc, ax=ax, shrink=0.8, ticks=[0, 300, 600, 900])
    cb.ax.tick_params(color='none', labelsize=20)
    cb.ax.set_yticklabels(['0', '5', '10', '>= 15'])
    cb.set_label('Walking time to nearest POI (minutes)', fontsize=20, fontweight='bold')
    # Remove empty space
    plt.tight_layout()
    # Save
    plt.savefig('./graph/walk_access.png')

@app.route("/walkability/<lng>/<lat>")
def initwalkability(lng, lat):
    task = calculate_walkability.apply_async(args=[lng, lat])
    response = {"id": task.id}
    return jsonify(response)

@app.route("/getwalkabilityres/<task_id>")
def getwalkabilityres(task_id):
    task = calculate_walkability.AsyncResult(task_id).state
    if task == 'SUCCESS':
        with open('./graph/walk_access.png', "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()) #
        response = {"image": encoded_string.decode('utf-8')}
        return jsonify(response)
    else:
        response = {"status": task}
        return jsonify(response)


if __name__ == "__main__":
    app.run(host='0.0.0.0', port='5000', ssl_context='adhoc')

    # testing local
    # app.run(host='0.0.0.0', port='5000')