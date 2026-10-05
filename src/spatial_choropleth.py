import json
import os
import duckdb
import geopandas as gpd
import pandas as pd
import plotly.express as px
import requests


def fetch_raw_data():
    """
    In-memory API extraction: Retrieves live 311 request logs from NYC Open Data (Socrata REST API)
    and dumps raw records to CSV for SQL processing.
    """
    print("[1/4] Fetching raw 311 Service Requests from NYC Open Data API...")
    socrata_api_url = (
        "https://data.cityofnewyork.us/resource/erm2-nwe9.json?"
        "$select=incident_zip,complaint_type,created_date,closed_date&"
        "$where=created_date >= '2024-01-01T00:00:00' AND incident_zip IS NOT NULL&"
        "$limit=15000"
    )

    response = requests.get(socrata_api_url)
    response.raise_for_status()
    df_raw = pd.DataFrame(response.json())

    os.makedirs("data/raw", exist_ok=True)
    raw_path = "data/raw/311_raw_sample.csv"
    df_raw.to_csv(raw_path, index=False)
    print(f"      Saved raw data to {raw_path}")


def process_with_sql():
    """
    SQL Analytics Processing: Uses DuckDB to query the raw CSV file using CTEs,
    timestamp conversions, aggregation functions, and HAVING filters.
    """
    print("[2/4] Running SQL transformations & aggregation in DuckDB...")

    con = duckdb.connect()

    sql_query = """
    WITH parsed_data AS (
        SELECT 
            LPAD(CAST(incident_zip AS VARCHAR), 5, '0') AS ZIPCODE,
            complaint_type,
            CAST(created_date AS TIMESTAMP) AS created_ts,
            CAST(closed_date AS TIMESTAMP) AS closed_ts
        FROM 'data/raw/311_raw_sample.csv'
        WHERE incident_zip IS NOT NULL 
          AND closed_date IS NOT NULL
    ),
    duration_calculated AS (
        SELECT 
            ZIPCODE,
            complaint_type,
            EPOCH(closed_ts - created_ts) / 3600.0 AS resolution_hours
        FROM parsed_data
        WHERE closed_ts >= created_ts
    )
    SELECT 
        ZIPCODE,
        COUNT(complaint_type) AS total_requests,
        ROUND(AVG(resolution_hours), 1) AS avg_resolution_hours,
        ROUND(MEDIAN(resolution_hours), 1) AS median_resolution_hours
    FROM duration_calculated
    GROUP BY ZIPCODE
    HAVING COUNT(complaint_type) >= 10
    ORDER BY total_requests DESC;
    """

    df_metrics = con.execute(sql_query).df()
    con.close()

    os.makedirs("data/processed", exist_ok=True)
    summary_path = "data/processed/zip_metrics_summary.csv"
    df_metrics.to_csv(summary_path, index=False)
    print(f"      Saved SQL query output to {summary_path}")

    return df_metrics


def fetch_geojson_boundaries():
    """
    Geospatial Processing: Loads NYC ZIP code polygon features into a GeoDataFrame
    and standardizes CRS to EPSG:4326 (latitude/longitude).
    """
    print("[3/4] Loading NYC ZIP Code GeoJSON polygon boundaries...")
    geojson_url = "https://raw.githubusercontent.com/fedeforrest/nyc-zip-code-tabulation-areas-polygons/master/zip_code_040114.geojson"

    gdf_zip = gpd.read_file(geojson_url)
    gdf_zip["ZIPCODE"] = gdf_zip["postalCode"].astype(str).str.zfill(5)

    if gdf_zip.crs != "EPSG:4326":
        gdf_zip = gdf_zip.to_crs(epsg=4326)

    return gdf_zip


def generate_choropleth_map(gdf_zip, df_metrics):
    """
    Visual Analytics: Performs spatial join between GeoPandas shapes and SQL metrics,
    and builds an interactive Plotly Mapbox choropleth.
    """
    print("[4/4] Joining SQL metrics with spatial polygons & building Mapbox visual...")

    gdf_merged = gdf_zip.merge(df_metrics, on="ZIPCODE", how="inner")
    geojson_dict = json.loads(gdf_merged.to_json())

    fig = px.choropleth_mapbox(
        gdf_merged,
        geojson=geojson_dict,
        locations="ZIPCODE",
        featureidkey="properties.ZIPCODE",
        color="avg_resolution_hours",
        color_continuous_scale="Viridis",
        range_color=(0, gdf_merged["avg_resolution_hours"].quantile(0.95)),
        mapbox_style="carto-positron",
        zoom=9.5,
        center={"lat": 40.7128, "lon": -74.0060},
        opacity=0.65,
        labels={
            "avg_resolution_hours": "Avg Resolution (Hrs)",
            "median_resolution_hours": "Median Resolution (Hrs)",
            "total_requests": "Total 311 Tickets",
            "ZIPCODE": "ZIP Code",
        },
        hover_data={
            "ZIPCODE": True,
            "total_requests": True,
            "avg_resolution_hours": True,
            "median_resolution_hours": True,
        },
        title="<b>NYC 311 Service Response Times by ZIP Code (SQL Aggregated)</b>",
    )

    fig.update_layout(
        margin={"r": 0, "t": 40, "l": 0, "b": 0},
        font=dict(family="Arial, sans-serif", size=12),
        title_font_size=16,
    )

    output_html = "nyc_311_spatial_map.html"
    fig.write_html(output_html)
    print(f"\nPipeline successfully completed! Exported HTML report to {output_html}")
    fig.show()


def main():
    try:
        fetch_raw_data()
        df_metrics = process_with_sql()
        gdf_zip = fetch_geojson_boundaries()
        generate_choropleth_map(gdf_zip, df_metrics)
    except Exception as e:
        print(f"\nPipeline execution failed: {e}")


if __name__ == "__main__":
    main()